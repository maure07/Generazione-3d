"""Estrazione e quantizzazione dei colori del modello.

Un sistema AMS/MMU ha pochi slot (tipicamente 4): i colori del modello vanno
quindi ridotti a quella tavolozza. La riduzione è pesata sul **volume** di ogni
pezzo, non sul semplice conteggio, perché è il volume a determinare quanto
filamento di quel colore serve davvero.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import trimesh

from ..mesh.io import is_empty

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ColorSlot:
    """Uno slot di filamento del sistema multimateriale."""

    index: int
    hex: str
    name_it: str
    volume_mm3: float = 0.0
    part_ids: list[str] | None = None

    def __post_init__(self) -> None:
        if self.part_ids is None:
            self.part_ids = []


#: Nomi italiani dei colori di base, per etichettare gli slot in modo leggibile.
COLOR_NAMES_IT: list[tuple[tuple[int, int, int], str]] = [
    ((0, 0, 0), "Nero"),
    ((255, 255, 255), "Bianco"),
    ((128, 128, 128), "Grigio"),
    ((255, 0, 0), "Rosso"),
    ((0, 128, 0), "Verde"),
    ((0, 0, 255), "Blu"),
    ((255, 255, 0), "Giallo"),
    ((255, 165, 0), "Arancione"),
    ((128, 0, 128), "Viola"),
    ((255, 192, 203), "Rosa"),
    ((139, 69, 19), "Marrone"),
    ((0, 255, 255), "Ciano"),
    ((245, 222, 179), "Beige"),
    ((255, 220, 177), "Incarnato"),
]


def hex_to_rgb(value: str) -> np.ndarray:
    """Converte ``#rrggbb`` in array RGB 0-255."""
    value = value.lstrip("#")
    if len(value) != 6:
        return np.array([128.0, 128.0, 128.0])
    return np.array([int(value[i : i + 2], 16) for i in (0, 2, 4)], dtype=np.float64)


def rgb_to_hex(rgb: np.ndarray) -> str:
    """Converte RGB 0-255 in ``#rrggbb``."""
    values = np.clip(np.asarray(rgb, dtype=np.float64), 0, 255).astype(int)
    return "#{:02x}{:02x}{:02x}".format(*values[:3])


def name_color_it(rgb: np.ndarray) -> str:
    """Nome italiano del colore di base più vicino."""
    reference = np.array([c for c, _ in COLOR_NAMES_IT], dtype=np.float64)
    distances = np.linalg.norm(reference - np.asarray(rgb, dtype=np.float64), axis=1)
    return COLOR_NAMES_IT[int(np.argmin(distances))][1]


def part_color(mesh: trimesh.Trimesh, fallback: str = "#c8c8c8") -> str:
    """Colore rappresentativo di un pezzo (media dei colori di vertice)."""
    from ..segmentation.segmenter import vertex_colors

    colors = vertex_colors(mesh)
    if colors is None:
        return fallback
    return rgb_to_hex(colors.mean(axis=0))


def quantize_colors(
    part_colors: dict[str, str], part_volumes: dict[str, float], max_colors: int
) -> tuple[list[ColorSlot], dict[str, int]]:
    """Riduce i colori dei pezzi al numero di slot disponibili.

    L'algoritmo è un k-means pesato sul volume: gli slot si posizionano dove
    c'è più materiale, così i pezzi grandi ottengono il colore più fedele.

    Args:
        part_colors: mappa ``part_id -> colore HEX``.
        part_volumes: mappa ``part_id -> volume in mm³``.
        max_colors: numero di slot disponibili nel sistema AMS.

    Returns:
        ``(slot, assegnazione)`` dove l'assegnazione mappa ``part_id -> indice slot``.
    """
    if not part_colors:
        return [], {}

    part_ids = list(part_colors)
    colors = np.array([hex_to_rgb(part_colors[p]) for p in part_ids], dtype=np.float64)
    weights = np.array([max(part_volumes.get(p, 0.0), 1e-6) for p in part_ids], dtype=np.float64)

    unique_colors = np.unique(colors, axis=0)
    k = int(min(max_colors, len(unique_colors)))
    if k <= 1:
        centre = np.average(colors, axis=0, weights=weights)
        slot = ColorSlot(
            index=0,
            hex=rgb_to_hex(centre),
            name_it=name_color_it(centre),
            volume_mm3=float(weights.sum()),
            part_ids=list(part_ids),
        )
        return [slot], {p: 0 for p in part_ids}

    centers = _weighted_kmeans(colors, weights, k)
    distances = np.linalg.norm(colors[:, None, :] - centers[None, :, :], axis=2)
    labels = np.argmin(distances, axis=1)

    slots: list[ColorSlot] = []
    assignment: dict[str, int] = {}
    used_index = 0

    for cluster in range(k):
        members = [part_ids[i] for i in range(len(part_ids)) if labels[i] == cluster]
        if not members:
            continue
        volume = float(sum(part_volumes.get(p, 0.0) for p in members))
        slot = ColorSlot(
            index=used_index,
            hex=rgb_to_hex(centers[cluster]),
            name_it=name_color_it(centers[cluster]),
            volume_mm3=volume,
            part_ids=members,
        )
        slots.append(slot)
        for part_id in members:
            assignment[part_id] = used_index
        used_index += 1

    # Gli slot più usati vanno per primi: aiuta la lettura del piano di stampa.
    slots.sort(key=lambda s: -s.volume_mm3)
    remap = {slot.index: new for new, slot in enumerate(slots)}
    for slot in slots:
        slot.index = remap[slot.index]
    assignment = {part: remap[old] for part, old in assignment.items()}

    logger.debug("Colori quantizzati in %d slot AMS", len(slots))
    return slots, assignment


def _weighted_kmeans(
    data: np.ndarray, weights: np.ndarray, k: int, iterations: int = 30, seed: int = 0
) -> np.ndarray:
    """K-means con campioni pesati (il peso è il volume del pezzo)."""
    rng = np.random.default_rng(seed)
    probabilities = weights / weights.sum()
    start = rng.choice(len(data), size=min(k, len(data)), replace=False, p=probabilities)
    centers = data[start].astype(np.float64).copy()

    for _ in range(iterations):
        distances = np.linalg.norm(data[:, None, :] - centers[None, :, :], axis=2)
        labels = np.argmin(distances, axis=1)
        moved = False
        for cluster in range(len(centers)):
            mask = labels == cluster
            if not mask.any():
                continue
            new_center = np.average(data[mask], axis=0, weights=weights[mask])
            if not np.allclose(new_center, centers[cluster]):
                centers[cluster] = new_center
                moved = True
        if not moved:
            break

    return centers


def extract_part_colors(
    parts: dict[str, tuple[str, trimesh.Trimesh]], declared: dict[str, str] | None = None
) -> dict[str, str]:
    """Determina il colore di ogni pezzo.

    Args:
        parts: mappa ``part_id -> (nome, mesh)``.
        declared: colori già noti (per esempio dalla segmentazione cromatica).

    Returns:
        Mappa ``part_id -> colore HEX``.
    """
    declared = declared or {}
    result: dict[str, str] = {}

    for part_id, (name, mesh) in parts.items():
        if part_id in declared and declared[part_id]:
            result[part_id] = declared[part_id]
        elif is_empty(mesh):
            result[part_id] = "#c8c8c8"
        else:
            result[part_id] = part_color(mesh)
    return result
