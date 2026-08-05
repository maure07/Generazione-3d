"""Endpoint di generazione: avvio pipeline, stato dei job, batch, annullamento."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from ...ai.registry import registry
from ...domain.models import (
    BatchGenerateRequest,
    GenerateRequest,
    JobStatus,
    PipelineReport,
)
from ...pipeline import job_manager
from ...projects import ProjectNotFound, store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/generate", tags=["generazione"])


def _save_report(record) -> None:
    """Salva il rapporto nel progetto al termine del job."""
    if record.report is None:
        return
    try:
        project = store.get(record.project_id)
        project.last_report = record.report
        store.save(project)
    except ProjectNotFound:  # pragma: no cover - progetto eliminato nel frattempo
        logger.warning("Progetto %s non più esistente: rapporto non salvato", record.project_id[:8])


@router.post("", response_model=JobStatus, status_code=202, summary="Avvia la generazione")
async def start_generation(request: GenerateRequest) -> JobStatus:
    """Avvia la pipeline completa su un progetto.

    La risposta è immediata: l'avanzamento va seguito su
    ``GET /api/generate/{job_id}`` o via WebSocket su ``/ws/jobs``.
    """
    try:
        project = store.get(request.project_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not project.images:
        raise HTTPException(
            status_code=400,
            detail="Caricare almeno un'immagine prima di avviare la generazione",
        )

    settings = request.settings or project.settings
    if request.settings is not None:
        project.settings = request.settings
        store.save(project, snapshot_label="Impostazioni di generazione aggiornate")

    record = await job_manager.submit(
        project,
        settings,
        resume_from=request.steps_from,
        on_finished=_save_report,
    )
    return record.to_status()


@router.post(
    "/batch", response_model=list[JobStatus], status_code=202, summary="Generazione in batch"
)
async def start_batch(request: BatchGenerateRequest) -> list[JobStatus]:
    """Avvia la pipeline su più progetti contemporaneamente."""
    projects = []
    mancanti: list[str] = []

    for project_id in request.project_ids:
        try:
            projects.append(store.get(project_id))
        except ProjectNotFound:
            mancanti.append(project_id)

    if not projects:
        raise HTTPException(
            status_code=404, detail=f"Nessun progetto valido fra quelli indicati: {mancanti}"
        )
    if mancanti:
        logger.warning("Progetti ignorati nel batch perché inesistenti: %s", mancanti)

    records = await job_manager.submit_batch(
        projects,
        request.settings,
        max_parallel=request.max_parallel,
        on_finished=_save_report,
    )
    return [r.to_status() for r in records]


@router.get("/jobs", response_model=list[JobStatus], summary="Elenco dei job")
def list_jobs(project_id: str | None = None) -> list[JobStatus]:
    """Stato di tutti i job, opzionalmente filtrati per progetto."""
    return job_manager.list_jobs(project_id)


@router.get("/{job_id}", response_model=JobStatus, summary="Stato di un job")
def get_job(job_id: str) -> JobStatus:
    status = job_manager.status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Job non trovato: {job_id}")
    return status


@router.get("/{job_id}/report", response_model=PipelineReport, summary="Rapporto completo")
def get_report(job_id: str) -> PipelineReport:
    """Rapporto dettagliato di un job concluso."""
    record = job_manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job non trovato: {job_id}")
    if record.report is None:
        raise HTTPException(
            status_code=409, detail="Il job non è ancora terminato: rapporto non disponibile"
        )
    return record.report


@router.post("/{job_id}/cancel", summary="Annulla un job")
async def cancel_job(job_id: str) -> dict:
    """Richiede l'annullamento di un job in corso."""
    if not await job_manager.cancel(job_id):
        raise HTTPException(
            status_code=409,
            detail="Il job non esiste o è già terminato",
        )
    return {"annullato": True, "job_id": job_id}


@router.get("/providers/list", summary="Provider AI disponibili")
def list_providers() -> list[dict]:
    """Elenca i generatori 3D con il loro stato di configurazione."""
    return registry.describe_all()
