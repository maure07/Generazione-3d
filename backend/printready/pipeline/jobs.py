"""Gestione dei job: coda, esecuzione parallela, annullamento, batch.

Il ``JobManager`` è il punto d'ingresso usato dall'API: mantiene lo stato di
ogni job, limita il parallelismo (le operazioni geometriche sono pesanti) e
consente l'elaborazione batch di più progetti contemporaneamente.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

from ..config import get_settings
from ..domain.enums import JobState, StepId
from ..domain.models import GenerationSettings, JobStatus, PipelineReport, Project
from .context import PipelineContext
from .orchestrator import PipelineOrchestrator

logger = logging.getLogger(__name__)


@dataclass
class JobRecord:
    """Stato interno di un job."""

    job_id: str
    project_id: str
    state: JobState = JobState.PENDING
    task: asyncio.Task | None = None
    context: PipelineContext | None = None
    report: PipelineReport | None = None
    error_it: str | None = None
    #: Ultimo avanzamento noto (aggiornato dagli eventi).
    progress: float = 0.0
    current_step: StepId | None = None
    message_it: str = ""

    def to_status(self) -> JobStatus:
        return JobStatus(
            job_id=self.job_id,
            project_id=self.project_id,
            state=self.state,
            current_step=self.current_step,
            progress=self.progress,
            message_it=self.message_it,
            report=self.report,
            error_it=self.error_it,
        )


class JobManager:
    """Coordina l'esecuzione dei job della pipeline."""

    def __init__(self, orchestrator: PipelineOrchestrator | None = None) -> None:
        self.orchestrator = orchestrator or PipelineOrchestrator()
        self._jobs: dict[str, JobRecord] = {}
        self._semaphore: asyncio.Semaphore | None = None
        self._lock = asyncio.Lock()

    def _get_semaphore(self) -> asyncio.Semaphore:
        # Creato pigramente per legarsi al loop corrente.
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(get_settings().max_parallel_jobs)
        return self._semaphore

    # -- avvio -------------------------------------------------------------

    async def submit(
        self,
        project: Project,
        settings: GenerationSettings | None = None,
        resume_from: StepId | None = None,
        on_finished=None,
    ) -> JobRecord:
        """Accoda un nuovo job e ne restituisce subito il record.

        Args:
            project: progetto da elaborare.
            settings: impostazioni che sovrascrivono quelle del progetto.
            resume_from: riprende la pipeline da un passo specifico.
            on_finished: callback ``(JobRecord) -> None`` invocata a fine job.
        """
        job_id = uuid.uuid4().hex
        record = JobRecord(job_id=job_id, project_id=project.id)

        async with self._lock:
            self._jobs[job_id] = record

        record.task = asyncio.create_task(
            self._run(record, project, settings, resume_from, on_finished),
            name=f"pipeline-{job_id[:8]}",
        )
        return record

    async def submit_batch(
        self,
        projects: list[Project],
        settings: GenerationSettings | None = None,
        max_parallel: int = 2,
        on_finished=None,
    ) -> list[JobRecord]:
        """Accoda più progetti; il parallelismo effettivo resta limitato dal
        semaforo globale, quindi ``max_parallel`` può solo restringerlo."""
        local = asyncio.Semaphore(max(1, max_parallel))
        records: list[JobRecord] = []

        for project in projects:
            record = await self.submit(
                project, settings, on_finished=on_finished
            )
            original = record.task

            async def limited(task=original, sem=local):
                async with sem:
                    return await task

            # Avvolgiamo il task per rispettare il limite del batch.
            record.task = asyncio.create_task(limited())
            records.append(record)

        return records

    async def _run(
        self,
        record: JobRecord,
        project: Project,
        settings: GenerationSettings | None,
        resume_from: StepId | None,
        on_finished,
    ) -> None:
        """Esegue il job dentro il semaforo di parallelismo."""
        async with self._get_semaphore():
            if record.state == JobState.CANCELLED:
                return
            record.state = JobState.RUNNING
            self._watch_events(record)

            try:
                report = await self.orchestrator.run(
                    project,
                    settings,
                    job_id=record.job_id,
                    resume_from=resume_from,
                )
                record.report = report
                record.state = report.state
                record.error_it = report.error_it
                record.progress = 1.0 if report.state == JobState.COMPLETED else record.progress
                record.message_it = report.summary_it or report.error_it or ""
            except Exception as exc:  # pragma: no cover - difesa estrema
                logger.exception("Job %s terminato con errore imprevisto", record.job_id[:8])
                record.state = JobState.FAILED
                record.error_it = str(exc)

        if on_finished is not None:
            try:
                maybe = on_finished(record)
                if asyncio.iscoroutine(maybe):
                    await maybe
            except Exception:  # pragma: no cover
                logger.exception("Callback di fine job fallita")

    def _watch_events(self, record: JobRecord) -> None:
        """Aggiorna il record ascoltando gli eventi del proprio job."""
        from ..domain.events import bus

        async def follow() -> None:
            async for event in bus.subscribe(job_id=record.job_id):
                payload = event.payload
                if event.type == "step_started":
                    record.current_step = StepId(payload["step"])
                    record.progress = float(payload.get("progress", record.progress))
                    record.message_it = payload.get("label_it", "")
                elif event.type == "step_progress":
                    record.message_it = payload.get("message_it", record.message_it)
                elif event.type == "job_finished":
                    break

        task = asyncio.create_task(follow(), name=f"watch-{record.job_id[:8]}")
        # L'osservatore muore da solo alla fine del job; il riferimento evita
        # che il garbage collector lo raccolga prima.
        record.__dict__.setdefault("_watchers", []).append(task)

    # -- interrogazione e controllo ---------------------------------------

    def get(self, job_id: str) -> JobRecord | None:
        return self._jobs.get(job_id)

    def status(self, job_id: str) -> JobStatus | None:
        record = self._jobs.get(job_id)
        return record.to_status() if record else None

    def list_jobs(self, project_id: str | None = None) -> list[JobStatus]:
        records = self._jobs.values()
        if project_id is not None:
            records = [r for r in records if r.project_id == project_id]
        return [r.to_status() for r in records]

    async def cancel(self, job_id: str) -> bool:
        """Richiede l'annullamento di un job in corso."""
        record = self._jobs.get(job_id)
        if record is None:
            return False
        if record.state in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED):
            return False

        record.state = JobState.CANCELLED
        if record.context is not None:
            record.context.cancelled = True
        if record.task is not None and not record.task.done():
            # La cancellazione cooperativa passa dal flag; il task di asyncio
            # viene cancellato solo se il flag non basta (es. attesa di rete).
            record.task.cancel()
        logger.info("Job %s annullato", job_id[:8])
        return True

    def cleanup(self, keep_last: int = 100) -> int:
        """Dimentica i job terminati più vecchi, mantenendo gli ultimi ``keep_last``."""
        finished = [
            (job_id, record)
            for job_id, record in self._jobs.items()
            if record.state in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED)
        ]
        removed = 0
        if len(finished) > keep_last:
            for job_id, _ in finished[: len(finished) - keep_last]:
                del self._jobs[job_id]
                removed += 1
        return removed


#: Gestore condiviso a livello di applicazione.
job_manager = JobManager()
