"""Esportazione verso i formati di stampa e CAD."""

from __future__ import annotations

import logging
from pathlib import Path

from ..domain.enums import ExportFormat
from ..domain.models import ExportedFile
from .base import ExportError, Exporter, ExportItem, safe_filename  # noqa: F401
from .mesh_formats import GLBExporter, OBJExporter, STLExporter
from .slicers import (  # noqa: F401
    SLICER_PROFILES,
    SlicerProfile,
    build_assembly_instructions,
    recommended_formats,
    slicer_notes_it,
)
from .step import STEPExporter
from .threemf import ThreeMFExporter

logger = logging.getLogger(__name__)

#: Esportatore associato a ogni formato.
EXPORTERS: dict[ExportFormat, type[Exporter]] = {
    ExportFormat.STL: STLExporter,
    ExportFormat.OBJ: OBJExporter,
    ExportFormat.GLB: GLBExporter,
    ExportFormat.THREEMF: ThreeMFExporter,
    ExportFormat.STEP: STEPExporter,
}


def get_exporter(fmt: ExportFormat) -> Exporter:
    """Istanzia l'esportatore per il formato richiesto."""
    exporter_class = EXPORTERS.get(fmt)
    if exporter_class is None:
        raise ExportError(f"Formato di esportazione non supportato: {fmt.value}")
    return exporter_class()


def export_all(
    items: list[ExportItem],
    formats: list[ExportFormat],
    destination: Path,
    combined: bool = True,
) -> list[ExportedFile]:
    """Esporta i pezzi in tutti i formati richiesti.

    Ogni formato finisce in una sottocartella dedicata, così i file omonimi non
    si sovrascrivono e l'utente trova subito ciò che gli serve.

    Args:
        items: pezzi da esportare.
        formats: formati desiderati.
        destination: cartella radice di esportazione.
        combined: produce anche il file unico con tutti i pezzi, dove il
            formato lo consente.

    Returns:
        Elenco di tutti i file prodotti.
    """
    produced: list[ExportedFile] = []

    for fmt in formats:
        try:
            exporter = get_exporter(fmt)
        except ExportError as exc:
            logger.warning("%s", exc)
            continue

        folder = destination / fmt.value
        try:
            produced.extend(exporter.export(items, folder, combined=combined))
        except ExportError as exc:
            # Un formato che fallisce non deve impedire gli altri.
            logger.error("Esportazione %s non riuscita: %s", fmt.value, exc)
        except Exception as exc:  # pragma: no cover
            logger.exception("Errore imprevisto esportando in %s: %s", fmt.value, exc)

    return produced


def register_exporter(fmt: ExportFormat, exporter_class: type[Exporter]) -> None:
    """Registra un esportatore personalizzato (punto di aggancio per i plugin)."""
    EXPORTERS[fmt] = exporter_class
    logger.info("Esportatore registrato per il formato %s", fmt.value)
