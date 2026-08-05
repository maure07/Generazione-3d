"""Interfaccia comune degli esportatori."""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from pathlib import Path

import trimesh

from ..domain.enums import ExportFormat
from ..domain.models import ExportedFile

logger = logging.getLogger(__name__)


class ExportError(RuntimeError):
    """Errore di esportazione con messaggio già in italiano."""


@dataclass(slots=True)
class ExportItem:
    """Un pezzo da esportare."""

    part_id: str
    name: str
    mesh: trimesh.Trimesh
    color_hex: str | None = None
    ams_slot: int | None = None
    metadata: dict[str, str] = field(default_factory=dict)


class Exporter(abc.ABC):
    """Base di un esportatore verso un formato di file."""

    #: Formato prodotto.
    format: ExportFormat
    #: Il formato conserva colori/materiali.
    supports_color: bool = False
    #: Il formato può contenere più oggetti in un unico file.
    supports_multi_object: bool = False

    @abc.abstractmethod
    def export(
        self, items: list[ExportItem], destination: Path, combined: bool = False
    ) -> list[ExportedFile]:
        """Scrive i file.

        Args:
            items: pezzi da esportare.
            destination: cartella di destinazione.
            combined: se ``True`` produce un unico file con tutti i pezzi
                (quando il formato lo consente).

        Returns:
            Elenco dei file prodotti.
        """

    def _prepare(self, destination: Path) -> Path:
        destination.mkdir(parents=True, exist_ok=True)
        return destination

    def _describe(self, path: Path, part_id: str | None, combined: bool) -> ExportedFile:
        return ExportedFile(
            format=self.format,
            path=str(path),
            filename=path.name,
            size_bytes=path.stat().st_size if path.exists() else 0,
            part_id=part_id,
            contains_all_parts=combined,
        )


def safe_filename(name: str, fallback: str = "pezzo") -> str:
    """Trasforma un nome di pezzo in un nome di file valido su Windows.

    Rimuove i caratteri vietati (``\\ / : * ? " < > |``), gli spazi doppi e i
    punti finali, che su Windows renderebbero il file inaccessibile.
    """
    invalid = '\\/:*?"<>|'
    cleaned = "".join("_" if c in invalid else c for c in name).strip()
    cleaned = " ".join(cleaned.split()).replace(" ", "_")
    cleaned = cleaned.rstrip(". ")
    if not cleaned:
        return fallback
    # Nomi riservati di Windows.
    reserved = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if cleaned.upper() in reserved:
        cleaned = f"_{cleaned}"
    return cleaned[:100]
