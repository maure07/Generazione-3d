"""Geometrie degli incastri: spine, sedi e alloggiamenti per magneti.

Ogni funzione costruisce la primitiva **già orientata e posizionata**: riceve il
punto di contatto e la direzione dell'incastro e restituisce una mesh pronta per
l'operazione booleana (unione sul maschio, sottrazione sulla femmina).

Convenzione: ``direction`` punta **dal pezzo maschio verso il pezzo femmina**,
cioè nel verso in cui la spina sporge.
"""

from __future__ import annotations

import logging

import numpy as np
import trimesh

from ..domain.enums import JoineryType
from .tolerance import ConnectorDimensions

logger = logging.getLogger(__name__)

#: Numero di segmenti dei cilindri: 48 dà una superficie liscia senza appesantire.
CYLINDER_SECTIONS = 48


def _align_to(direction: np.ndarray, origin: np.ndarray) -> np.ndarray:
    """Matrice 4×4 che porta l'asse +Z sulla direzione data, traslando in origin."""
    direction = np.asarray(direction, dtype=np.float64)
    norm = np.linalg.norm(direction)
    if norm < 1e-9:
        direction = np.array([0.0, 0.0, 1.0])
    else:
        direction = direction / norm

    rotation = trimesh.geometry.align_vectors(np.array([0.0, 0.0, 1.0]), direction)
    if rotation is None:  # pragma: no cover - vettori già allineati
        rotation = np.eye(4)
    transform = np.array(rotation, dtype=np.float64)
    transform[:3, 3] = np.asarray(origin, dtype=np.float64)
    return transform


def cylindrical_pin(
    origin: np.ndarray,
    direction: np.ndarray,
    diameter_mm: float,
    length_mm: float,
    chamfer_mm: float = 0.0,
    embed_mm: float | None = None,
) -> trimesh.Trimesh:
    """Spina cilindrica, l'incastro più semplice e resistente.

    Args:
        origin: punto sulla superficie di contatto.
        direction: verso di sporgenza della spina.
        diameter_mm: diametro della spina.
        length_mm: lunghezza sporgente.
        chamfer_mm: smusso conico in punta, per l'imbocco.
        embed_mm: quanto la spina affonda nel pezzo maschio (per l'unione
            booleana serve una compenetrazione). Default: metà diametro.
    """
    embed = float(embed_mm if embed_mm is not None else diameter_mm * 0.5)
    total = float(length_mm) + embed
    radius = float(diameter_mm) / 2.0

    pin = trimesh.creation.cylinder(radius=radius, height=total, sections=CYLINDER_SECTIONS)
    # Il cilindro nasce centrato sull'origine: lo spostiamo perché l'estremità
    # arretrata resti dentro al pezzo maschio.
    pin.apply_translation([0.0, 0.0, total / 2.0 - embed])

    if chamfer_mm > 0:
        pin = _apply_tip_chamfer(pin, radius, total - embed, chamfer_mm, embed)

    pin.apply_transform(_align_to(direction, origin))
    return pin


def conical_pin(
    origin: np.ndarray,
    direction: np.ndarray,
    diameter_mm: float,
    length_mm: float,
    taper_ratio: float = 0.75,
    embed_mm: float | None = None,
) -> trimesh.Trimesh:
    """Spina conica: si autocentra e tollera piccoli disallineamenti.

    Args:
        taper_ratio: rapporto fra diametro in punta e diametro alla base
            (0,75 = la punta è il 75% della base).
    """
    embed = float(embed_mm if embed_mm is not None else diameter_mm * 0.5)
    base_radius = float(diameter_mm) / 2.0
    tip_radius = base_radius * float(np.clip(taper_ratio, 0.2, 1.0))
    total = float(length_mm) + embed

    # Tronco di cono costruito per rivoluzione del profilo (r, z).
    profile = np.array(
        [
            [0.0, -embed],
            [base_radius, -embed],
            [tip_radius, float(length_mm)],
            [0.0, float(length_mm)],
        ]
    )
    pin = trimesh.creation.revolve(profile, sections=CYLINDER_SECTIONS)
    if pin is None or len(pin.faces) == 0:  # pragma: no cover
        logger.warning("Rivoluzione del cono fallita: uso una spina cilindrica")
        return cylindrical_pin(origin, direction, diameter_mm, length_mm, embed_mm=embed)

    pin.apply_transform(_align_to(direction, origin))
    return pin


def square_pin(
    origin: np.ndarray,
    direction: np.ndarray,
    size_mm: float,
    length_mm: float,
    embed_mm: float | None = None,
) -> trimesh.Trimesh:
    """Incastro quadrato: impedisce la rotazione con un solo elemento.

    Args:
        size_mm: lato della sezione quadrata (equivalente al diametro).
    """
    embed = float(embed_mm if embed_mm is not None else size_mm * 0.5)
    total = float(length_mm) + embed

    pin = trimesh.creation.box(extents=[float(size_mm), float(size_mm), total])
    pin.apply_translation([0.0, 0.0, total / 2.0 - embed])
    pin.apply_transform(_align_to(direction, origin))
    return pin


def magnet_socket(
    origin: np.ndarray,
    direction: np.ndarray,
    diameter_mm: float,
    height_mm: float,
    recess_mm: float = 0.2,
    inward: bool = True,
) -> trimesh.Trimesh:
    """Alloggiamento cilindrico per un magnete al neodimio.

    Args:
        recess_mm: quanto il magnete resta incassato sotto la superficie, così
            i due pezzi combaciano senza che i magneti facciano da distanziale.
        inward: se ``True`` la cavità viene scavata nel verso opposto a
            ``direction`` (dentro il pezzo).
    """
    depth = float(height_mm) + float(recess_mm)
    radius = float(diameter_mm) / 2.0
    axis = np.asarray(direction, dtype=np.float64)
    if inward:
        axis = -axis

    # Un filo di sovrametallo in bocca evita facce complanari nella booleana.
    cavity = trimesh.creation.cylinder(
        radius=radius, height=depth + 0.2, sections=CYLINDER_SECTIONS
    )
    cavity.apply_translation([0.0, 0.0, (depth + 0.2) / 2.0 - 0.1])
    cavity.apply_transform(_align_to(axis, origin))
    return cavity


def socket_for(
    pin_type: JoineryType,
    origin: np.ndarray,
    direction: np.ndarray,
    dimensions: ConnectorDimensions,
) -> trimesh.Trimesh:
    """Costruisce la cavità corrispondente a una spina, con la tolleranza inclusa.

    La cavità è scavata nel pezzo femmina, quindi si sviluppa nel verso
    **opposto** a ``direction``.
    """
    axis = -np.asarray(direction, dtype=np.float64)
    depth = dimensions.socket_depth_mm
    # Sovrametallo in imbocco: garantisce che la booleana tagli davvero la pelle.
    overshoot = 0.2

    if pin_type == JoineryType.SQUARE_PIN:
        size = dimensions.female_diameter_mm
        cavity = trimesh.creation.box(extents=[size, size, depth + overshoot])
        cavity.apply_translation([0.0, 0.0, (depth + overshoot) / 2.0 - overshoot])
        cavity.apply_transform(_align_to(axis, origin))
        return cavity

    if pin_type == JoineryType.CONICAL_PIN:
        base_radius = dimensions.female_diameter_mm / 2.0
        tip_radius = base_radius * float(np.clip(dimensions.taper_ratio, 0.2, 1.0))
        profile = np.array(
            [
                [0.0, -overshoot],
                [base_radius, -overshoot],
                [tip_radius, depth],
                [0.0, depth],
            ]
        )
        cavity = trimesh.creation.revolve(profile, sections=CYLINDER_SECTIONS)
        if cavity is not None and len(cavity.faces) > 0:
            cavity.apply_transform(_align_to(axis, origin))
            return cavity
        logger.warning("Sede conica non generata: uso una sede cilindrica")

    radius = dimensions.female_diameter_mm / 2.0
    cavity = trimesh.creation.cylinder(
        radius=radius, height=depth + overshoot, sections=CYLINDER_SECTIONS
    )
    cavity.apply_translation([0.0, 0.0, (depth + overshoot) / 2.0 - overshoot])
    cavity.apply_transform(_align_to(axis, origin))

    if dimensions.chamfer_mm > 0:
        chamfer = _mouth_chamfer(radius, dimensions.chamfer_mm)
        chamfer.apply_transform(_align_to(axis, origin))
        cavity = trimesh.util.concatenate([cavity, chamfer])

    return cavity


def build_pin(
    pin_type: JoineryType,
    origin: np.ndarray,
    direction: np.ndarray,
    dimensions: ConnectorDimensions,
) -> trimesh.Trimesh | None:
    """Costruisce la spina del tipo richiesto.

    Returns:
        La mesh della spina, oppure ``None`` per gli incastri magnetici (che non
        hanno parte maschio: si scava una sede su entrambi i pezzi).
    """
    if pin_type == JoineryType.MAGNET:
        return None
    if pin_type == JoineryType.NONE:
        return None

    if pin_type == JoineryType.CONICAL_PIN:
        return conical_pin(
            origin,
            direction,
            dimensions.male_diameter_mm,
            dimensions.length_mm,
            dimensions.taper_ratio,
        )
    if pin_type == JoineryType.SQUARE_PIN:
        return square_pin(
            origin, direction, dimensions.male_diameter_mm, dimensions.length_mm
        )
    return cylindrical_pin(
        origin,
        direction,
        dimensions.male_diameter_mm,
        dimensions.length_mm,
        dimensions.chamfer_mm,
    )


# ---------------------------------------------------------------------------
# Dettagli geometrici di rifinitura
# ---------------------------------------------------------------------------


def _apply_tip_chamfer(
    pin: trimesh.Trimesh, radius: float, tip_z: float, chamfer_mm: float, embed: float
) -> trimesh.Trimesh:
    """Smussa la punta della spina intersecandola con un cono di invito."""
    from ..mesh.booleans import boolean_intersection

    chamfer = min(chamfer_mm, radius * 0.6)
    if chamfer <= 0:
        return pin

    profile = np.array(
        [
            [0.0, -embed - 1.0],
            [radius, -embed - 1.0],
            [radius, tip_z - chamfer],
            [radius - chamfer, tip_z],
            [0.0, tip_z],
        ]
    )
    shaped = trimesh.creation.revolve(profile, sections=CYLINDER_SECTIONS)
    if shaped is None or len(shaped.faces) == 0:  # pragma: no cover
        return pin

    result = boolean_intersection([pin, shaped])
    return result if result is not None and len(result.faces) > 0 else shaped


def _mouth_chamfer(radius: float, chamfer_mm: float) -> trimesh.Trimesh:
    """Cono di invito all'imbocco della sede, per facilitare il montaggio."""
    profile = np.array(
        [
            [0.0, -0.01],
            [radius + chamfer_mm, -0.01],
            [radius, chamfer_mm],
            [0.0, chamfer_mm],
        ]
    )
    cone = trimesh.creation.revolve(profile, sections=CYLINDER_SECTIONS)
    if cone is None:  # pragma: no cover
        return trimesh.creation.cylinder(radius=radius, height=0.01)
    return cone
