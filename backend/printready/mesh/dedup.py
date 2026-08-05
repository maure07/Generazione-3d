"""Rimozione di facce duplicate, vertici doppi e geometria ridondante.

Le mesh generate dall'AI contengono spesso triangoli coincidenti (stessa terna
di vertici) o quasi coincidenti. Lo slicer li interpreta come pareti a spessore
zero e produce artefatti; vanno quindi eliminati prima di ogni altra fase.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import trimesh

from .io import is_empty

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DedupReport:
    """Conteggi delle entità rimosse."""

    duplicate_faces: int = 0
    degenerate_faces: int = 0
    merged_vertices: int = 0
    unreferenced_vertices: int = 0

    @property
    def total(self) -> int:
        return (
            self.duplicate_faces
            + self.degenerate_faces
            + self.merged_vertices
            + self.unreferenced_vertices
        )

    def message_it(self) -> str:
        if self.total == 0:
            return "Nessuna geometria duplicata trovata"
        return (
            f"Rimosse {self.duplicate_faces} facce duplicate, "
            f"{self.degenerate_faces} degeneri, "
            f"{self.merged_vertices} vertici fusi"
        )


def _unique_face_mask(faces: np.ndarray) -> np.ndarray:
    """Maschera booleana delle facce uniche a meno di permutazione dei vertici."""
    if len(faces) == 0:
        return np.zeros(0, dtype=bool)
    canonical = np.sort(faces, axis=1)
    # `np.unique` su righe: usiamo una vista strutturata per efficienza.
    _, first_index = np.unique(canonical, axis=0, return_index=True)
    mask = np.zeros(len(faces), dtype=bool)
    mask[first_index] = True
    return mask


def remove_duplicate_faces(
    mesh: trimesh.Trimesh,
    merge_distance: float = 1e-5,
    remove_degenerate: bool = True,
) -> tuple[trimesh.Trimesh, DedupReport]:
    """Fonde i vertici coincidenti ed elimina facce duplicate e degeneri.

    Args:
        mesh: mesh in ingresso (non modificata).
        merge_distance: distanza sotto la quale due vertici sono considerati
            lo stesso punto, in millimetri.
        remove_degenerate: rimuove anche i triangoli con due o tre vertici uguali.

    Returns:
        Mesh ripulita e report con i conteggi.
    """
    report = DedupReport()
    if is_empty(mesh):
        return mesh, report

    work = mesh.copy()
    v_before = len(work.vertices)

    if merge_distance > 0:
        try:
            work.merge_vertices(merge_tex=True, merge_norm=True, digits_vertex=_digits(merge_distance))
        except TypeError:  # pragma: no cover - firma diversa in alcune versioni
            work.merge_vertices()
    report.merged_vertices = max(0, v_before - len(work.vertices))

    if remove_degenerate:
        faces = work.faces
        non_degenerate = (
            (faces[:, 0] != faces[:, 1])
            & (faces[:, 1] != faces[:, 2])
            & (faces[:, 0] != faces[:, 2])
        )
        report.degenerate_faces = int(np.count_nonzero(~non_degenerate))
        if report.degenerate_faces:
            work.update_faces(non_degenerate)

    unique_mask = _unique_face_mask(work.faces)
    report.duplicate_faces = int(np.count_nonzero(~unique_mask))
    if report.duplicate_faces:
        work.update_faces(unique_mask)

    v_pre_clean = len(work.vertices)
    work.remove_unreferenced_vertices()
    report.unreferenced_vertices = max(0, v_pre_clean - len(work.vertices))

    work._cache.clear()
    return work, report


def _digits(distance: float) -> int:
    """Converte una distanza di fusione in numero di cifre decimali."""
    if distance <= 0:
        return 8
    return int(max(0, min(12, round(-np.log10(distance)))))


def remove_duplicate_vertices_only(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Fonde solo i vertici coincidenti mantenendo intatta la lista facce."""
    work = mesh.copy()
    work.merge_vertices()
    return work
