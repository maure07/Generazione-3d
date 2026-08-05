"""Plugin di esempio: incastro a coda di rondine e lessico aggiuntivo.

Copiare questo file in ``<dati>/plugins/`` per vederlo caricato all'avvio.
Serve come modello di riferimento per scrivere estensioni proprie.
"""

from __future__ import annotations

import numpy as np
import trimesh

from printready.domain.enums import IssueCode, PartType, Severity
from printready.domain.models import Issue

PLUGIN_NAME = "Esempio incastri e lessico"
PLUGIN_VERSION = "1.0"
PLUGIN_AUTHOR = "PrintReady AI"
PLUGIN_DESCRIPTION_IT = (
    "Mostra come estendere il lessico semantico e aggiungere una regola "
    "di stampabilità personalizzata."
)


def dovetail_pin(
    origin: np.ndarray, direction: np.ndarray, width_mm: float, length_mm: float
) -> trimesh.Trimesh:
    """Spina a coda di rondine: resiste alla trazione lungo l'asse.

    Il profilo si allarga verso la punta, così una volta inserita la spina non
    può sfilarsi nella direzione di inserimento.
    """
    half = width_mm / 2.0
    profile = np.array(
        [
            [0.0, 0.0],
            [half * 0.6, 0.0],
            [half, length_mm],
            [0.0, length_mm],
        ]
    )
    pin = trimesh.creation.revolve(profile, sections=6)
    rotation = trimesh.geometry.align_vectors(np.array([0.0, 0.0, 1.0]), direction)
    if rotation is not None:
        transform = np.array(rotation, dtype=np.float64)
        transform[:3, 3] = origin
        pin.apply_transform(transform)
    return pin


def rule_base_stability(mesh: trimesh.Trimesh, printer) -> list[Issue]:
    """Verifica che il pezzo appoggi in modo stabile sul piatto.

    Un'area di appoggio inferiore al 2% dell'ingombro rende probabile il
    distacco durante la stampa.
    """
    if len(mesh.faces) == 0:
        return []

    z_min = float(mesh.bounds[0][2])
    tolerance = printer.layer_height_mm
    on_plate = np.abs(np.asarray(mesh.triangles_center)[:, 2] - z_min) < tolerance
    contact_area = float(np.asarray(mesh.area_faces)[on_plate].sum())

    extents = mesh.extents
    footprint = float(extents[0] * extents[1])
    if footprint <= 0:
        return []

    ratio = contact_area / footprint
    if ratio >= 0.02:
        return []

    return [
        Issue(
            code=IssueCode.ISLAND,
            severity=Severity.WARNING,
            message_it=(
                f"Appoggio sul piatto di soli {contact_area:.1f} mm² "
                f"({ratio * 100:.1f}% dell'ingombro): aggiungere un brim"
            ),
        )
    ]


def register(api) -> None:
    """Punto d'ingresso richiesto dal caricatore di plugin."""
    api.add_semantic_terms(
        PartType.WEAPON,
        {"bastone runico", "grimorio", "falcione", "balestra", "warhammer"},
    )
    api.add_semantic_terms(
        PartType.ACCESSORY,
        {"faretra", "borraccia", "lanterna", "tomo", "sacca"},
    )
    api.add_printability_rule(IssueCode.ISLAND, rule_base_stability)
    api.log("Coda di rondine e controllo di stabilità registrati")
