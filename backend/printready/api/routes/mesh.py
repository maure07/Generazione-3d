"""Endpoint per l'anteprima 3D e l'analisi su richiesta.

Servono al visualizzatore del frontend: scaricare la geometria di un pezzo in
GLB (leggero e con i colori) e chiedere analisi puntuali senza rilanciare
l'intera pipeline.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response

from ...domain.enums import ExportFormat
from ...domain.models import MeshStats, PrinterProfile
from ...mesh.io import load_mesh
from ...mesh.metrics import compute_stats
from ...mesh.validate import validate_stl
from ...pipeline import job_manager
from ...printability import PrintabilityAnalyzer
from ...projects import ProjectNotFound, store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/mesh", tags=["mesh"])


def _report_of(job_id: str):
    record = job_manager.get(job_id)
    if record is None or record.report is None:
        raise HTTPException(status_code=404, detail="Nessun rapporto disponibile per questo job")
    return record.report


@router.get("/{job_id}/preview", summary="Anteprima 3D del modello completo")
def preview(job_id: str) -> FileResponse:
    """Restituisce il GLB combinato, ideale per il visualizzatore WebGL."""
    report = _report_of(job_id)
    for file in report.exports:
        if file.format == ExportFormat.GLB and Path(file.path).exists():
            return FileResponse(
                file.path, media_type="model/gltf-binary", filename=file.filename
            )
    raise HTTPException(
        status_code=404,
        detail="Anteprima non disponibile: aggiungere GLB ai formati di esportazione",
    )


@router.get("/{job_id}/parts/{part_id}/file", summary="Scarica un pezzo")
def part_file(
    job_id: str,
    part_id: str,
    fmt: ExportFormat = Query(ExportFormat.STL, description="Formato desiderato"),
) -> FileResponse:
    """Scarica il file di un singolo pezzo nel formato richiesto."""
    report = _report_of(job_id)
    for file in report.exports:
        if file.part_id == part_id and file.format == fmt and Path(file.path).exists():
            return FileResponse(file.path, filename=file.filename)
    raise HTTPException(
        status_code=404,
        detail=f"Pezzo non disponibile in formato {fmt.value.upper()}",
    )


@router.get("/{job_id}/files", summary="Elenco dei file esportati")
def list_files(job_id: str) -> list[dict]:
    """Tutti i file prodotti dal job, con dimensioni e suggerimenti per lo slicer."""
    report = _report_of(job_id)
    return [
        {
            "formato": f.format.value,
            "nome": f.filename,
            "percorso": f.path,
            "dimensione_byte": f.size_bytes,
            "part_id": f.part_id,
            "completo": f.contains_all_parts,
            "suggerimento_it": f.slicer_hint_it,
            "esiste": Path(f.path).exists(),
        }
        for f in report.exports
    ]


@router.get("/{job_id}/instructions", summary="Istruzioni di montaggio")
def instructions(job_id: str) -> Response:
    """Istruzioni di montaggio in formato Markdown."""
    report = _report_of(job_id)
    for file in report.exports:
        # I file stanno in <export>/<formato>/, le istruzioni in <export>/:
        # si controllano entrambi i livelli senza dipendere dal formato.
        directory = Path(file.path).parent
        for candidate in (directory / "istruzioni_montaggio.md", directory.parent / "istruzioni_montaggio.md"):
            if candidate.exists():
                return Response(
                    candidate.read_text(encoding="utf-8"),
                    media_type="text/markdown; charset=utf-8",
                )
    raise HTTPException(status_code=404, detail="Istruzioni non disponibili per questo job")


@router.post("/analyze", response_model=dict, summary="Analizza un file 3D esistente")
def analyze_file(
    path: str = Query(..., description="Percorso assoluto del file da analizzare"),
    project_id: str | None = Query(
        None, description="Progetto da cui prendere il profilo di stampa"
    ),
) -> dict:
    """Analizza un file già presente su disco senza avviare la pipeline.

    Il percorso deve trovarsi dentro la cartella dati dell'applicazione: questo
    endpoint non è un lettore di file arbitrari del sistema.
    """
    from ...config import get_settings

    target = Path(path).resolve()
    data_root = get_settings().data_dir.resolve()
    try:
        target.relative_to(data_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=403,
            detail="È possibile analizzare solo i file contenuti nella cartella dati",
        ) from exc

    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File non trovato: {target.name}")

    printer = PrinterProfile()
    if project_id:
        try:
            printer = store.get(project_id).settings.printer
        except ProjectNotFound:
            pass

    try:
        mesh = load_mesh(target)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    stats: MeshStats = compute_stats(mesh)
    validation = validate_stl(mesh, check_self_intersections=True)
    printability = PrintabilityAnalyzer(printer).analyze(mesh, part_name=target.name)

    return {
        "file": target.name,
        "statistiche": stats.model_dump(mode="json"),
        "validazione": {
            "valido": validation.valid,
            "messaggio_it": validation.message_it(),
            "problemi": [i.model_dump(mode="json") for i in validation.issues],
        },
        "stampabilita": {
            "punteggio": printability.score,
            "giudizio_it": printability.verdict_it(),
            "messaggio_it": printability.message_it(),
            "tempo_stimato_min": round(printability.estimated_time_min, 1),
            "filamento_stimato_g": round(printability.estimated_filament_g, 1),
            "problemi": [i.model_dump(mode="json") for i in printability.issues],
        },
    }
