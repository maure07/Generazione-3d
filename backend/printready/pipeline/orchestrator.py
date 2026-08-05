"""Orchestratore della pipeline: esegue i passi in sequenza e produce il rapporto.

Responsabilità:

* eseguire i passi nell'ordine corretto, saltando quelli disattivati;
* isolare gli errori — un passo non critico che fallisce non ferma il lavoro;
* delegare i passi CPU-intensivi a un thread, così il loop asyncio (e quindi
  l'interfaccia) resta reattivo;
* comporre il ``PipelineReport`` finale con statistiche, pezzi e file prodotti.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..config import get_settings
from ..domain.enums import JobState, StepId
from ..domain.models import GenerationSettings, PipelineReport, Project
from ..mesh.metrics import compute_stats
from .context import PipelineCancelled, PipelineContext
from .steps import (
    AIGenerationStep,
    AMSStep,
    AutoRepairStep,
    CloseHolesStep,
    DecimateStep,
    ExportStep,
    FixNormalsStep,
    JoineryStep,
    LoadImagesStep,
    OptimizeTrianglesStep,
    PipelineStep,
    PrintabilityStep,
    ReadPromptStep,
    RemoveDuplicatesStep,
    RemoveFloatersStep,
    SegmentationStep,
    SolidifyStep,
    ValidateStep,
)

logger = logging.getLogger(__name__)

#: Sequenza predefinita del workflow (l'ordine è parte del contratto).
DEFAULT_STEPS: list[type[PipelineStep]] = [
    LoadImagesStep,
    ReadPromptStep,
    AIGenerationStep,
    AutoRepairStep,
    SolidifyStep,
    CloseHolesStep,
    FixNormalsStep,
    RemoveDuplicatesStep,
    RemoveFloatersStep,
    OptimizeTrianglesStep,
    DecimateStep,
    ValidateStep,
    PrintabilityStep,
    SegmentationStep,
    JoineryStep,
    AMSStep,
    ExportStep,
]

#: Passi il cui fallimento non interrompe la pipeline (degradano il risultato).
NON_CRITICAL: set[StepId] = {
    StepId.SOLIDIFY,
    StepId.OPTIMIZE_TRIANGLES,
    StepId.SEGMENTATION,
    StepId.JOINERY,
    StepId.AMS_OPTIMIZE,
}


class PipelineOrchestrator:
    """Esegue il workflow completo su un progetto."""

    def __init__(self, steps: list[type[PipelineStep]] | None = None) -> None:
        self.step_classes = steps or list(DEFAULT_STEPS)
        self._extra_steps: list[tuple[StepId, type[PipelineStep]]] = []

    def insert_step_after(self, anchor: StepId, step_class: type[PipelineStep]) -> None:
        """Inserisce un passo personalizzato dopo un passo esistente (per i plugin)."""
        self._extra_steps.append((anchor, step_class))

    def _resolve_steps(self, resume_from: StepId | None) -> list[PipelineStep]:
        """Istanzia i passi, applicando inserimenti dei plugin e ripresa parziale."""
        classes = list(self.step_classes)
        for anchor, extra in self._extra_steps:
            for index, cls in enumerate(classes):
                if cls.step_id == anchor:
                    classes.insert(index + 1, extra)
                    break

        steps = [cls() for cls in classes]
        if resume_from is not None:
            for index, step in enumerate(steps):
                if step.step_id == resume_from:
                    return steps[index:]
        return steps

    async def run(
        self,
        project: Project,
        settings: GenerationSettings | None = None,
        job_id: str | None = None,
        resume_from: StepId | None = None,
        context_seed: PipelineContext | None = None,
    ) -> PipelineReport:
        """Esegue la pipeline e restituisce il rapporto completo.

        Args:
            project: progetto con immagini, prompt e impostazioni.
            settings: impostazioni che sovrascrivono quelle del progetto.
            job_id: identificatore del job (generato se assente).
            resume_from: riprende da un passo specifico (modalità esperto).
            context_seed: contesto preesistente da cui ripartire (per la ripresa).

        Returns:
            Il rapporto della pipeline, anche in caso di errore (con lo stato
            ``FAILED`` e il messaggio in ``error_it``).
        """
        job_id = job_id or uuid.uuid4().hex
        settings = settings or project.settings
        app_settings = get_settings()

        work_dir = app_settings.exports_dir / project.id / job_id[:12]
        work_dir.mkdir(parents=True, exist_ok=True)

        report = PipelineReport(job_id=job_id, project_id=project.id, state=JobState.RUNNING)

        if context_seed is not None:
            context = context_seed
            context.report = report
            context.settings = settings
        else:
            context = PipelineContext(
                job_id=job_id,
                project=project,
                settings=settings,
                work_dir=work_dir,
                report=report,
            )

        steps = self._resolve_steps(resume_from)
        total = len(steps)
        context.emit("job_started", {"total_steps": total})

        try:
            for index, step in enumerate(steps):
                if not step.should_run(context):
                    logger.debug("Passo %s saltato dalle impostazioni", step.step_id.value)
                    continue

                result = context.begin_step(step.step_id, total, index)
                try:
                    if isinstance(step, AIGenerationStep):
                        message, details = await step.run_async(context)
                    else:
                        # I passi geometrici sono CPU-bound: girano in un thread
                        # per non congelare il loop degli eventi.
                        message, details = await asyncio.to_thread(step.run, context)
                except PipelineCancelled:
                    raise
                except Exception as exc:
                    error = _friendly_error(exc)
                    context.fail_step(result, error)
                    if step.step_id in NON_CRITICAL:
                        logger.warning(
                            "Passo non critico %s fallito, la pipeline continua: %s",
                            step.step_id.value,
                            error,
                        )
                        continue
                    raise

                context.end_step(result, message, details)

            report.state = JobState.COMPLETED
            report.summary_it = self._summary(context)
        except PipelineCancelled:
            report.state = JobState.CANCELLED
            report.error_it = "Elaborazione annullata dall'utente"
        except Exception as exc:
            report.state = JobState.FAILED
            report.error_it = _friendly_error(exc)
            logger.exception("Pipeline %s fallita", job_id[:8])
        finally:
            if context.mesh is not None:
                report.stats_after = compute_stats(context.mesh)
            report.finished_at = datetime.now(timezone.utc)
            context.emit(
                "job_finished",
                {
                    "state": report.state.value,
                    "summary_it": report.summary_it,
                    "error_it": report.error_it,
                    "printability_score": report.printability_score,
                },
            )

        return report

    def _summary(self, context: PipelineContext) -> str:
        """Compone il riepilogo finale in italiano."""
        report = context.report
        parti = len(report.parts) or len(context.parts)
        pezzi = f"{parti} pezzi" if parti != 1 else "1 pezzo"
        incastri = len(report.connectors)
        files = len(report.exports)

        frasi = [f"Modello pronto: {pezzi}"]
        if incastri:
            frasi.append(f"{incastri} incastri")
        if report.ams_plan and report.ams_plan.slots:
            frasi.append(f"{len(report.ams_plan.slots)} colori AMS")
        frasi.append(f"punteggio di stampabilità {report.printability_score:.0f}/100")
        if files:
            frasi.append(f"{files} file esportati")
        return ", ".join(frasi) + "."


def _friendly_error(exc: Exception) -> str:
    """Estrae un messaggio comprensibile da un'eccezione."""
    from ..ai.base import ProviderError
    from ..exporters.base import ExportError
    from ..mesh.io import MeshLoadError

    if isinstance(exc, (ProviderError, ExportError, MeshLoadError, ValueError)):
        return str(exc)
    return f"Errore interno ({exc.__class__.__name__}): {exc}"
