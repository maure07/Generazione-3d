"""Endpoint dei progetti: creazione, modifica, immagini, cronologia, undo/redo."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from ...domain.models import (
    CreateProjectRequest,
    ImageRef,
    Project,
    ProjectSummary,
    UpdateProjectRequest,
)
from ...projects import ProjectNotFound, autosave, store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["progetti"])

#: Estensioni immagine accettate.
ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
#: Dimensione massima di un'immagine caricata.
MAX_IMAGE_BYTES = 25 * 1024 * 1024


def _get(project_id: str) -> Project:
    try:
        return store.get(project_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("", response_model=list[ProjectSummary], summary="Cronologia dei progetti")
def list_projects() -> list[ProjectSummary]:
    """Elenca tutti i progetti, dal più recente."""
    return store.list_summaries()


@router.post("", response_model=Project, status_code=201, summary="Crea un progetto")
def create_project(request: CreateProjectRequest) -> Project:
    """Crea un nuovo progetto vuoto."""
    return store.create(
        name=request.name,
        prompt=request.prompt,
        negative_prompt=request.negative_prompt,
        settings=request.settings,
    )


@router.get("/{project_id}", response_model=Project, summary="Dettaglio di un progetto")
def get_project(project_id: str) -> Project:
    return _get(project_id)


@router.patch("/{project_id}", response_model=Project, summary="Modifica un progetto")
def update_project(project_id: str, request: UpdateProjectRequest) -> Project:
    """Aggiorna i campi indicati, lasciando invariati gli altri."""
    project = _get(project_id)
    changed: list[str] = []

    if request.name is not None:
        project.name = request.name.strip() or project.name
        changed.append("nome")
    if request.prompt is not None:
        project.prompt = request.prompt
        changed.append("descrizione")
    if request.negative_prompt is not None:
        project.negative_prompt = request.negative_prompt
        changed.append("esclusioni")
    if request.settings is not None:
        project.settings = request.settings
        changed.append("impostazioni")
    if request.notes is not None:
        project.notes = request.notes
        changed.append("note")
    if request.tags is not None:
        project.tags = request.tags
        changed.append("etichette")

    if changed:
        store.save(project, snapshot_label=f"Modifica: {', '.join(changed)}")
    return project


@router.delete("/{project_id}", status_code=204, summary="Elimina un progetto")
def delete_project(project_id: str) -> None:
    try:
        store.delete(project_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Immagini
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/images", response_model=ImageRef, summary="Carica un'immagine"
)
async def upload_image(
    project_id: str,
    file: UploadFile = File(..., description="Immagine del soggetto"),
    view: str = Query("auto", description="Vista: front, back, left, right, top, auto"),
) -> ImageRef:
    """Carica un'immagine nel progetto."""
    _get(project_id)

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Formato immagine non supportato: {suffix or 'sconosciuto'}. "
                f"Usare {', '.join(sorted(ALLOWED_IMAGE_SUFFIXES))}"
            ),
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Il file caricato è vuoto")
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Immagine troppo grande ({len(content) / 1e6:.1f} MB, massimo 25 MB)",
        )

    return store.add_image(project_id, file.filename or "immagine.png", content, view)


@router.delete(
    "/{project_id}/images/{image_id}", status_code=204, summary="Rimuove un'immagine"
)
def delete_image(project_id: str, image_id: str) -> None:
    _get(project_id)
    if not store.remove_image(project_id, image_id):
        raise HTTPException(status_code=404, detail="Immagine non trovata nel progetto")


@router.get("/{project_id}/images/{image_id}/file", summary="Scarica un'immagine")
def get_image_file(project_id: str, image_id: str) -> FileResponse:
    """Restituisce il file immagine, per l'anteprima nell'interfaccia."""
    project = _get(project_id)
    for image in project.images:
        if image.id == image_id:
            path = Path(image.path)
            if not path.exists():
                raise HTTPException(status_code=404, detail="File dell'immagine non trovato")
            return FileResponse(path, filename=image.filename)
    raise HTTPException(status_code=404, detail="Immagine non trovata nel progetto")


# ---------------------------------------------------------------------------
# Cronologia, undo/redo
# ---------------------------------------------------------------------------


@router.get("/{project_id}/history", summary="Cronologia delle modifiche")
def get_history(project_id: str) -> list[dict]:
    """Elenco degli snapshot disponibili per undo/redo."""
    _get(project_id)
    return store.history(project_id)


@router.post("/{project_id}/undo", response_model=Project, summary="Annulla l'ultima modifica")
def undo(project_id: str) -> Project:
    _get(project_id)
    project = store.undo(project_id)
    if project is None:
        raise HTTPException(status_code=409, detail="Non c'è nulla da annullare")
    return project


@router.post("/{project_id}/redo", response_model=Project, summary="Ripete la modifica annullata")
def redo(project_id: str) -> Project:
    _get(project_id)
    project = store.redo(project_id)
    if project is None:
        raise HTTPException(status_code=409, detail="Non c'è nulla da ripetere")
    return project


@router.post("/{project_id}/save", response_model=Project, summary="Salvataggio esplicito")
def save_now(project_id: str) -> Project:
    """Forza il salvataggio immediato del progetto."""
    project = _get(project_id)
    store.save(project, snapshot_label="Salvataggio manuale")
    autosave.mark_dirty(project)
    return project
