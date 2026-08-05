"""Contesto condiviso fra i passi della pipeline.

Il contesto è l'unico stato mutabile che attraversa il workflow: ogni passo
legge ciò che gli serve, scrive il proprio risultato e lascia il resto intatto.
Questo rende i passi componibili e testabili singolarmente, e permette a un
plugin di inserirsi in qualunque punto della catena.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import trimesh

from ..ai.semantic import PromptAnalysis
from ..domain.enums import JobState, StepId
from ..domain.events import Event, bus
from ..domain.models import (
    AMSPlan,
    ConnectorInfo,
    GenerationSettings,
    Issue,
    PipelineReport,
    Project,
    StepResult,
)
from ..segmentation.segmenter import Part

logger = logging.getLogger(__name__)


class PipelineCancelled(RuntimeError):
    """Sollevata quando l'utente annulla il job in corso."""


@dataclass
class PipelineContext:
    """Stato condiviso di una esecuzione della pipeline."""

    job_id: str
    project: Project
    settings: GenerationSettings
    work_dir: Path
    report: PipelineReport

    #: Mesh unica corrente (prima della segmentazione).
    mesh: trimesh.Trimesh | None = None
    #: Pezzi risultanti dalla segmentazione.
    parts: list[Part] = field(default_factory=list)
    #: Analisi semantica del prompt.
    prompt_analysis: PromptAnalysis | None = None
    #: Pezzi già separati forniti dal provider AI.
    provider_parts: dict[str, trimesh.Trimesh] = field(default_factory=dict)
    #: Incastri creati.
    connectors: list[ConnectorInfo] = field(default_factory=list)
    #: Piano colori AMS.
    ams_plan: AMSPlan | None = None
    #: Fattore di scala applicato al modello.
    scale_factor: float = 1.0
    #: Dati liberi a disposizione dei plugin.
    extra: dict[str, Any] = field(default_factory=dict)

    #: Flag di annullamento, impostato dall'API.
    cancelled: bool = False
    #: Passo attualmente in esecuzione.
    current_step: StepId | None = None
    _step_started: float = 0.0

    # -- gestione dei passi ------------------------------------------------

    def begin_step(self, step: StepId, total_steps: int, index: int) -> StepResult:
        """Registra l'inizio di un passo e notifica l'interfaccia."""
        self.check_cancelled()
        self.current_step = step
        self._step_started = time.time()

        result = StepResult(step=step, state=JobState.RUNNING)
        result.started_at = _now()
        self.report.steps.append(result)

        self.emit(
            "step_started",
            {
                "step": step.value,
                "label_it": step.label_it,
                "index": index,
                "total": total_steps,
                "progress": index / max(1, total_steps),
            },
        )
        logger.info("[%s] %s", self.job_id[:8], step.label_it)
        return result

    def end_step(
        self,
        result: StepResult,
        message_it: str,
        details: dict[str, Any] | None = None,
        issues: list[Issue] | None = None,
    ) -> None:
        """Chiude un passo con esito positivo."""
        result.state = JobState.COMPLETED
        result.finished_at = _now()
        result.duration_s = round(time.time() - self._step_started, 3)
        result.message_it = message_it
        if details:
            result.details.update(details)
        if issues:
            result.issues.extend(issues)
            self.report.issues.extend(issues)

        self.emit(
            "step_completed",
            {
                "step": result.step.value,
                "label_it": result.step.label_it,
                "message_it": message_it,
                "duration_s": result.duration_s,
            },
        )

    def fail_step(self, result: StepResult, message_it: str) -> None:
        """Chiude un passo con esito negativo."""
        result.state = JobState.FAILED
        result.finished_at = _now()
        result.duration_s = round(time.time() - self._step_started, 3)
        result.message_it = message_it
        self.emit(
            "step_failed",
            {"step": result.step.value, "label_it": result.step.label_it, "error_it": message_it},
        )
        logger.error("[%s] %s: %s", self.job_id[:8], result.step.label_it, message_it)

    def progress(self, value: float, message_it: str) -> None:
        """Notifica un avanzamento all'interno del passo corrente."""
        self.emit(
            "step_progress",
            {
                "step": self.current_step.value if self.current_step else None,
                "progress": float(max(0.0, min(1.0, value))),
                "message_it": message_it,
            },
        )

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Pubblica un evento sul bus applicativo."""
        bus.publish_nowait(
            Event(
                type=event_type,
                job_id=self.job_id,
                project_id=self.project.id,
                payload=payload,
            )
        )

    def check_cancelled(self) -> None:
        """Interrompe la pipeline se l'utente ha annullato il job."""
        if self.cancelled:
            raise PipelineCancelled("Elaborazione annullata dall'utente")

    # -- accesso ai dati ---------------------------------------------------

    def parts_map(self) -> dict[str, tuple[str, trimesh.Trimesh]]:
        """Mappa ``part_id -> (nome, mesh)`` usata da AMS ed esportatori."""
        return {p.id: (p.name, p.mesh) for p in self.parts}

    def colors_map(self) -> dict[str, str]:
        """Colori dichiarati dalla segmentazione, per pezzo."""
        return {p.id: p.color_hex for p in self.parts if p.color_hex}

    def ensure_parts(self) -> list[Part]:
        """Restituisce i pezzi, creandone uno unico se la segmentazione è saltata."""
        if self.parts:
            return self.parts
        if self.mesh is None:
            return []

        import uuid

        from ..domain.enums import PartType

        self.parts = [
            Part(
                id=uuid.uuid4().hex,
                name="Modello completo",
                part_type=PartType.UNKNOWN,
                mesh=self.mesh,
                confidence=1.0,
            )
        ]
        return self.parts

    def output_dir(self, name: str) -> Path:
        """Sottocartella di lavoro, creata al volo."""
        path = self.work_dir / name
        path.mkdir(parents=True, exist_ok=True)
        return path


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
