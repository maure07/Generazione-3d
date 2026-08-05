"""Gestione dei componenti connessi e rimozione delle mesh flottanti.

Le generazioni AI producono spesso piccoli gusci staccati ("floaters"):
frammenti di rumore che lo slicer trasformerebbe in isole non stampabili.
Qui li individuiamo e li eliminiamo con criteri di volume e area.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import trimesh

from .io import is_empty

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class FloatersReport:
    """Esito della rimozione dei gusci flottanti."""

    components_before: int = 0
    components_after: int = 0
    removed: int = 0
    removed_volume_mm3: float = 0.0
    removed_faces: int = 0

    def message_it(self) -> str:
        if self.removed == 0:
            return f"Nessuna mesh flottante ({self.components_before} componenti mantenuti)"
        return (
            f"Rimosse {self.removed} mesh flottanti "
            f"({self.removed_faces} facce, {self.removed_volume_mm3:.1f} mm³)"
        )


def split_components(
    mesh: trimesh.Trimesh, only_watertight: bool = False
) -> list[trimesh.Trimesh]:
    """Divide la mesh nei suoi componenti connessi.

    Args:
        mesh: mesh da dividere.
        only_watertight: se ``True`` restituisce solo i componenti chiusi.

    Returns:
        Lista di mesh; la lista contiene la mesh originale se è già connessa.
    """
    if is_empty(mesh):
        return []
    try:
        parts = mesh.split(only_watertight=only_watertight)
    except Exception as exc:  # pragma: no cover - mesh patologiche
        logger.warning("Split dei componenti fallito: %s", exc)
        return [mesh]

    parts = [p for p in parts if not is_empty(p)]
    if not parts:
        return [mesh]
    return list(parts)


def component_volume(mesh: trimesh.Trimesh) -> float:
    """Volume robusto: usa il volume se la mesh è chiusa, altrimenti l'AABB."""
    if is_empty(mesh):
        return 0.0
    if mesh.is_watertight:
        return abs(float(mesh.volume))
    size = mesh.extents
    return float(np.prod(size)) if size is not None else 0.0


def remove_floating_shells(
    mesh: trimesh.Trimesh,
    min_volume_ratio: float = 0.02,
    min_face_count: int = 12,
    keep_largest_min: int = 1,
) -> tuple[trimesh.Trimesh, FloatersReport]:
    """Elimina i componenti connessi troppo piccoli per essere significativi.

    Un componente viene rimosso se il suo volume è inferiore a
    ``min_volume_ratio`` volte quello del componente più grande **oppure** se ha
    meno di ``min_face_count`` facce.

    Args:
        mesh: mesh da ripulire.
        min_volume_ratio: soglia relativa di volume (0.02 = 2%).
        min_face_count: numero minimo di facce per considerare valido un guscio.
        keep_largest_min: numero minimo di componenti da conservare comunque.

    Returns:
        Mesh ripulita e report.
    """
    report = FloatersReport()
    if is_empty(mesh):
        return mesh, report

    components = split_components(mesh, only_watertight=False)
    report.components_before = len(components)
    report.components_after = len(components)

    if len(components) <= 1:
        return mesh, report

    volumes = np.array([component_volume(c) for c in components], dtype=np.float64)
    largest = float(volumes.max()) if volumes.size else 0.0
    if largest <= 0:
        return mesh, report

    threshold = largest * float(min_volume_ratio)
    keep_mask = (volumes >= threshold) & np.array(
        [len(c.faces) >= min_face_count for c in components]
    )

    # Garantisce di non svuotare mai il modello.
    if np.count_nonzero(keep_mask) < keep_largest_min:
        order = np.argsort(-volumes)
        keep_mask = np.zeros(len(components), dtype=bool)
        keep_mask[order[:keep_largest_min]] = True

    kept = [c for c, k in zip(components, keep_mask) if k]
    dropped = [c for c, k in zip(components, keep_mask) if not k]

    report.removed = len(dropped)
    report.removed_faces = int(sum(len(c.faces) for c in dropped))
    report.removed_volume_mm3 = float(sum(component_volume(c) for c in dropped))
    report.components_after = len(kept)

    if not dropped:
        return mesh, report
    if len(kept) == 1:
        return kept[0], report
    return trimesh.util.concatenate(kept), report


def largest_component(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Restituisce il solo componente connesso di volume maggiore."""
    components = split_components(mesh)
    if len(components) <= 1:
        return mesh
    volumes = [component_volume(c) for c in components]
    return components[int(np.argmax(volumes))]


def count_components(mesh: trimesh.Trimesh) -> int:
    """Numero di componenti connessi (economico: usa il grafo delle facce)."""
    if is_empty(mesh):
        return 0
    try:
        return int(len(trimesh.graph.connected_components(mesh.face_adjacency, nodes=np.arange(len(mesh.faces)))))
    except Exception:  # pragma: no cover
        return len(split_components(mesh))
