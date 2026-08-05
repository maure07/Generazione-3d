"""Etichettatura semantica dei pezzi: dalla geometria al nome della parte.

L'assegnazione combina tre fonti di evidenza, ognuna con un peso:

* **posizione verticale** — dove sta il pezzo lungo l'altezza della figura;
* **posizione relativa nella regione** — davanti/dietro, sopra/sotto, laterale;
* **prompt dell'utente** — quali parti sono state esplicitamente richieste.

Il risultato è sempre accompagnato da una confidenza, così l'interfaccia può
evidenziare i pezzi incerti e lasciare all'utente la correzione manuale.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import trimesh

from ..ai.semantic import PromptAnalysis
from ..domain.enums import PartType
from .anatomy import AnatomyAnalysis, label_by_height

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LabelHypothesis:
    """Ipotesi di etichetta con la relativa confidenza."""

    part_type: PartType
    confidence: float
    reason_it: str


def _relative_position(mesh: trimesh.Trimesh, region: trimesh.Trimesh) -> tuple[float, float, float]:
    """Posizione del baricentro del pezzo dentro il bounding box della regione.

    Returns:
        Terna in [0, 1] su (X = destra, Y = dietro, Z = alto).
    """
    r_min, r_max = region.bounds
    size = np.maximum(r_max - r_min, 1e-9)
    center = mesh.bounds.mean(axis=0)
    return tuple(float(v) for v in np.clip((center - r_min) / size, 0.0, 1.0))


def label_head_subpart(
    part: trimesh.Trimesh,
    head: trimesh.Trimesh,
    prompt: PromptAnalysis,
    color_hex: str | None = None,
) -> LabelHypothesis:
    """Classifica un sotto-pezzo della testa (capelli, cappello, barba, occhi...).

    La testa è la regione con più componenti distinti in un modello stile Funko,
    quindi merita una logica dedicata basata su posizione e volume relativo.
    """
    x, y, z = _relative_position(part, head)
    volume_ratio = _volume_ratio(part, head)
    width_ratio = float(part.extents[0] / max(head.extents[0], 1e-9))

    def wanted(candidate: PartType) -> float:
        """Bonus se il prompt cita esplicitamente questa parte."""
        return 0.18 * prompt.confidence_for(candidate)

    # Cappello: sta in cima e sporge lateralmente più della testa.
    if z > 0.72 and width_ratio > 0.85:
        return LabelHypothesis(
            PartType.HAT,
            min(0.95, 0.68 + wanted(PartType.HAT)),
            "in cima alla testa e più largo del cranio",
        )

    # Capelli: coprono la calotta, spesso estesi verso la nuca.
    if z > 0.55 and volume_ratio > 0.04:
        confidence = 0.6 + wanted(PartType.HAIR)
        if y > 0.5:
            confidence += 0.08  # spostati verso la nuca
        return LabelHypothesis(PartType.HAIR, min(0.95, confidence), "calotta superiore della testa")

    # Occhi: piccoli, sul davanti, a metà altezza del volto.
    if volume_ratio < 0.03 and y < 0.42 and 0.35 < z < 0.72:
        return LabelHypothesis(
            PartType.EYES,
            min(0.9, 0.6 + wanted(PartType.EYES)),
            "elemento piccolo sulla parte frontale del volto",
        )

    # Sopracciglia: come gli occhi ma più in alto e più sottili.
    if volume_ratio < 0.015 and y < 0.42 and z >= 0.6:
        return LabelHypothesis(
            PartType.EYEBROWS,
            min(0.85, 0.55 + wanted(PartType.EYEBROWS)),
            "elemento sottile sopra gli occhi",
        )

    # Barba: sul davanti, nella metà bassa del volto.
    if y < 0.5 and z < 0.42:
        candidate = PartType.BEARD if volume_ratio > 0.02 else PartType.MUSTACHE
        return LabelHypothesis(
            candidate,
            min(0.9, 0.58 + wanted(candidate)),
            "elemento nella parte bassa e frontale del volto",
        )

    # Orecchie: laterali, piccole, a metà altezza.
    if (x < 0.18 or x > 0.82) and volume_ratio < 0.06:
        return LabelHypothesis(
            PartType.EARS,
            min(0.85, 0.6 + wanted(PartType.EARS)),
            "elemento laterale della testa",
        )

    return LabelHypothesis(PartType.HEAD, 0.5, "porzione principale della testa")


def label_generic_part(
    part: trimesh.Trimesh,
    whole: trimesh.Trimesh,
    anatomy: AnatomyAnalysis,
    prompt: PromptAnalysis,
) -> LabelHypothesis:
    """Classifica un pezzo qualsiasi del modello usando quota e forma."""
    center = part.bounds.mean(axis=0)
    ratio = anatomy.ratio_of(float(center[2]))
    volume_ratio = _volume_ratio(part, whole)
    extents = part.extents
    elongation = float(max(extents) / max(np.median(extents), 1e-9))

    by_height = label_by_height(ratio, anatomy)
    confidence = 0.55

    # Un pezzo molto allungato e staccato in alto è quasi sempre un'arma.
    if elongation > 3.5 and volume_ratio < 0.12 and prompt.confidence_for(PartType.WEAPON) > 0:
        return LabelHypothesis(PartType.WEAPON, 0.75, "elemento allungato e separato dal corpo")

    # Pezzo molto piccolo e periferico: accessorio o decorazione.
    if volume_ratio < 0.01:
        candidate = (
            PartType.ACCESSORY
            if prompt.confidence_for(PartType.ACCESSORY) > 0
            else PartType.DECORATION
        )
        return LabelHypothesis(candidate, 0.55, "elemento minuto separato dal corpo principale")

    # Un disco largo e basso alla base è la basetta.
    if ratio < 0.08 and extents[2] < max(extents[0], extents[1]) * 0.4:
        return LabelHypothesis(PartType.BASE, 0.85, "elemento largo e piatto alla base")

    if prompt.confidence_for(by_height) > 0:
        confidence += 0.2
    return LabelHypothesis(by_height, min(0.9, confidence), f"quota {ratio * 100:.0f}% dell'altezza")


def infer_side(part: trimesh.Trimesh, whole: trimesh.Trimesh, threshold: float = 0.08) -> str:
    """Determina se il pezzo sta a sinistra, a destra o al centro.

    La convenzione è quella dello spettatore: X negativo = sinistra.
    """
    center_x = float(part.bounds.mean(axis=0)[0])
    whole_center_x = float(whole.bounds.mean(axis=0)[0])
    width = float(max(whole.extents[0], 1e-9))
    offset = (center_x - whole_center_x) / width

    if offset < -threshold:
        return "left"
    if offset > threshold:
        return "right"
    return "center"


def side_label_it(side: str) -> str:
    """Traduce il lato in italiano per i nomi dei file e l'interfaccia."""
    return {"left": "sinistra", "right": "destra", "center": ""}.get(side, "")


def build_part_name(part_type: PartType, side: str, index: int, used: set[str]) -> str:
    """Compone un nome leggibile e univoco per il pezzo."""
    base = part_type.label_it
    suffix = side_label_it(side)
    name = f"{base} {suffix}".strip()

    if name not in used:
        used.add(name)
        return name

    counter = 2
    while f"{name} {counter}" in used:
        counter += 1
    unique = f"{name} {counter}"
    used.add(unique)
    return unique


def _volume_ratio(part: trimesh.Trimesh, whole: trimesh.Trimesh) -> float:
    """Volume del pezzo rispetto al tutto, con riserva sul bounding box."""
    def measure(mesh: trimesh.Trimesh) -> float:
        if mesh.is_watertight:
            return abs(float(mesh.volume))
        return float(np.prod(mesh.extents))

    total = measure(whole)
    if total <= 1e-9:
        return 0.0
    return measure(part) / total
