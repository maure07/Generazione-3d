"""Correzione delle normali e dell'orientamento delle facce.

Una mesh stampabile deve avere:

1. **winding coerente** fra facce adiacenti (nessun triangolo "girato");
2. **normali rivolte verso l'esterno** (volume positivo);
3. nessuna normale nulla (facce degeneri).

Lo slicer usa le normali per distinguere interno ed esterno: normali invertite
producono pareti mancanti o riempimenti impazziti.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import trimesh

from .io import is_empty

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class NormalsReport:
    """Esito della correzione delle normali."""

    winding_fixed: bool = False
    inversion_fixed: bool = False
    flipped_faces: int = 0
    degenerate_removed: int = 0
    consistent: bool = False
    outward: bool = False

    def message_it(self) -> str:
        if not (self.winding_fixed or self.inversion_fixed or self.degenerate_removed):
            return "Normali già corrette"
        parti: list[str] = []
        if self.winding_fixed:
            parti.append(f"winding riallineato ({self.flipped_faces} facce)")
        if self.inversion_fixed:
            parti.append("orientamento invertito verso l'esterno")
        if self.degenerate_removed:
            parti.append(f"{self.degenerate_removed} facce degeneri rimosse")
        return "Normali corrette: " + ", ".join(parti)


def _count_inconsistent_edges(mesh: trimesh.Trimesh) -> int:
    """Numero di spigoli condivisi con orientamento incoerente.

    Due facce adiacenti corrette percorrono lo spigolo comune in direzioni
    opposte; se lo percorrono nello stesso verso il winding è incoerente.
    """
    edges = mesh.edges_sorted
    if len(edges) == 0:
        return 0
    directed = mesh.edges
    # Chiave canonica per spigolo + segno della direzione percorsa.
    forward = directed[:, 0] < directed[:, 1]
    keys = edges[:, 0].astype(np.int64) * (mesh.vertices.shape[0] + 1) + edges[:, 1]
    order = np.argsort(keys, kind="stable")
    keys_sorted = keys[order]
    fwd_sorted = forward[order]

    # Cerca coppie consecutive con la stessa chiave e stesso verso.
    same_key = keys_sorted[1:] == keys_sorted[:-1]
    same_dir = fwd_sorted[1:] == fwd_sorted[:-1]
    return int(np.count_nonzero(same_key & same_dir))


def remove_degenerate_faces(mesh: trimesh.Trimesh, area_epsilon: float = 1e-10) -> int:
    """Elimina i triangoli con area (quasi) nulla. Ritorna quanti ne ha tolti."""
    if is_empty(mesh):
        return 0
    areas = mesh.area_faces
    keep = areas > area_epsilon
    removed = int(np.count_nonzero(~keep))
    if removed:
        mesh.update_faces(keep)
        mesh.remove_unreferenced_vertices()
    return removed


def fix_normals(mesh: trimesh.Trimesh, force_outward: bool = True) -> tuple[trimesh.Trimesh, NormalsReport]:
    """Rende coerenti e rivolte verso l'esterno le normali della mesh.

    Args:
        mesh: mesh da correggere (viene lavorata su una copia).
        force_outward: se ``True`` inverte l'intera mesh quando il volume
            risulta negativo, garantendo normali uscenti.

    Returns:
        La mesh corretta e un report descrittivo.
    """
    report = NormalsReport()
    if is_empty(mesh):
        return mesh, report

    work = mesh.copy()
    report.degenerate_removed = remove_degenerate_faces(work)

    before = _count_inconsistent_edges(work)
    if before > 0:
        faces_before = work.faces.copy()
        try:
            trimesh.repair.fix_winding(work)
        except Exception as exc:  # pragma: no cover - mesh patologiche
            logger.warning("fix_winding fallito: %s", exc)
        report.winding_fixed = True
        report.flipped_faces = int(np.count_nonzero((faces_before != work.faces).any(axis=1)))

    if force_outward and work.is_watertight:
        volume = float(work.volume)
        if volume < 0:
            try:
                trimesh.repair.fix_inversion(work)
                report.inversion_fixed = True
            except Exception as exc:  # pragma: no cover
                logger.warning("fix_inversion fallito: %s", exc)
                work.invert()
                report.inversion_fixed = True

    # Ricalcola le cache derivate dopo le modifiche topologiche.
    work._cache.clear()
    report.consistent = _count_inconsistent_edges(work) == 0
    report.outward = bool(not work.is_watertight or float(work.volume) >= 0)
    return work, report


def has_inverted_normals(mesh: trimesh.Trimesh) -> bool:
    """True se la mesh è chiusa ma con volume negativo (tutto invertito)."""
    if is_empty(mesh) or not mesh.is_watertight:
        return False
    return float(mesh.volume) < 0
