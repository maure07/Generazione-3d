"""Metriche geometriche: statistiche, curvatura, spessori, angoli di sbalzo.

Tutte le funzioni sono pure e non modificano la mesh in ingresso.
"""

from __future__ import annotations

import logging

import numpy as np
import trimesh

from ..domain.models import BoundsInfo, MeshStats
from .io import is_empty

logger = logging.getLogger(__name__)


def compute_stats(mesh: trimesh.Trimesh) -> MeshStats:
    """Raccoglie le statistiche principali di una mesh."""
    if is_empty(mesh):
        return MeshStats()

    bounds = None
    try:
        b = mesh.bounds
        bounds = BoundsInfo(min=tuple(float(x) for x in b[0]), max=tuple(float(x) for x in b[1]))
    except Exception:  # pragma: no cover
        pass

    watertight = bool(mesh.is_watertight)
    try:
        components = int(len(mesh.split(only_watertight=False)))
    except Exception:  # pragma: no cover
        components = 1

    return MeshStats(
        vertices=int(len(mesh.vertices)),
        faces=int(len(mesh.faces)),
        components=components,
        volume_mm3=float(abs(mesh.volume)) if watertight else 0.0,
        area_mm2=float(mesh.area),
        watertight=watertight,
        winding_consistent=bool(mesh.is_winding_consistent),
        euler_number=int(mesh.euler_number),
        bounds=bounds,
    )


def per_vertex_curvature(mesh: trimesh.Trimesh, radius_factor: float = 0.01) -> np.ndarray:
    """Curvatura approssimata per vertice, normalizzata in [0, 1].

    Usa la deviazione angolare fra la normale del vertice e quelle dei vertici
    adiacenti: è veloce, robusta su mesh rumorose e sufficiente per guidare la
    decimazione adattiva.
    """
    if is_empty(mesh) or len(mesh.vertices) == 0:
        return np.zeros(0)

    edges = mesh.edges_unique
    if len(edges) == 0:
        return np.zeros(len(mesh.vertices))

    normals = np.asarray(mesh.vertex_normals, dtype=np.float64)
    if normals.shape[0] != len(mesh.vertices):  # pragma: no cover
        return np.zeros(len(mesh.vertices))

    dots = (normals[edges[:, 0]] * normals[edges[:, 1]]).sum(axis=1)
    angles = np.arccos(np.clip(dots, -1.0, 1.0))

    accum = np.zeros(len(mesh.vertices), dtype=np.float64)
    counts = np.zeros(len(mesh.vertices), dtype=np.float64)
    np.add.at(accum, edges[:, 0], angles)
    np.add.at(accum, edges[:, 1], angles)
    np.add.at(counts, edges[:, 0], 1.0)
    np.add.at(counts, edges[:, 1], 1.0)
    counts[counts == 0] = 1.0

    curvature = accum / counts
    maximum = float(curvature.max())
    if maximum <= 1e-12:
        return np.zeros_like(curvature)
    return curvature / maximum


def face_overhang_angles(mesh: trimesh.Trimesh, build_direction: np.ndarray | None = None) -> np.ndarray:
    """Angolo di sbalzo di ogni faccia, in gradi.

    0° = faccia rivolta verso il basso e perfettamente orizzontale (sbalzo
    massimo); 90° = faccia verticale (nessun sbalzo).
    """
    if is_empty(mesh):
        return np.zeros(0)

    if build_direction is None:
        build_direction = np.array([0.0, 0.0, 1.0])
    build_direction = build_direction / np.linalg.norm(build_direction)

    normals = np.asarray(mesh.face_normals, dtype=np.float64)
    cos_theta = normals @ build_direction
    # Solo le facce con normale verso il basso creano sbalzo.
    angle = np.degrees(np.arcsin(np.clip(-cos_theta, -1.0, 1.0)))
    return 90.0 - np.clip(angle, 0.0, 90.0)


def overhang_faces(
    mesh: trimesh.Trimesh, max_overhang_deg: float = 45.0, build_direction: np.ndarray | None = None
) -> np.ndarray:
    """Maschera booleana delle facce che richiedono supporti."""
    if is_empty(mesh):
        return np.zeros(0, dtype=bool)

    if build_direction is None:
        build_direction = np.array([0.0, 0.0, 1.0])
    build_direction = build_direction / np.linalg.norm(build_direction)

    normals = np.asarray(mesh.face_normals, dtype=np.float64)
    # Angolo fra la normale e il piano orizzontale, per le sole facce rivolte giù.
    downward = normals @ build_direction
    tilt_deg = np.degrees(np.arccos(np.clip(-downward, -1.0, 1.0)))
    # tilt 0° = faccia perfettamente rivolta verso il basso.
    return (downward < 0) & (tilt_deg < (90.0 - max_overhang_deg))


def face_wall_thickness(
    mesh: trimesh.Trimesh, max_distance_mm: float = 10.0, max_samples: int | None = None
) -> np.ndarray:
    """Spessore locale misurato al centro di ogni faccia.

    Da ogni baricentro si lancia un raggio lungo la normale entrante e si misura
    la distanza al primo impatto: è lo spessore della parete in quel punto.
    Il campionamento per faccia è molto più affidabile di quello per vertice,
    perché la normale di faccia è esatta mentre quella di vertice è mediata
    (sugli spigoli vivi punterebbe in diagonale, sovrastimando lo spessore).

    Args:
        mesh: mesh da misurare.
        max_distance_mm: oltre questa distanza lo spessore è considerato
            non misurabile (``inf``).
        max_samples: se indicato e la mesh ha più facce, si misura solo un
            sottoinsieme regolarmente distribuito. Il ray casting è la
            operazione più costosa dell'intera analisi, e per *rilevare* le
            pareti sottili un campione è sufficiente; per *correggerle* serve
            invece la misura completa, quindi si lascia il parametro a ``None``.

    Returns:
        Array di spessori in millimetri, uno per faccia; ``inf`` dove non
        misurabile o non campionato.
    """
    if is_empty(mesh):
        return np.zeros(0)

    normals = np.asarray(mesh.face_normals, dtype=np.float64)
    centers = np.asarray(mesh.triangles_center, dtype=np.float64)
    thickness = np.full(len(centers), np.inf, dtype=np.float64)

    selection = np.arange(len(centers))
    if max_samples is not None and len(centers) > max_samples:
        step = int(np.ceil(len(centers) / max_samples))
        selection = selection[::step]

    # Micro-offset verso l'interno per non colpire la faccia di partenza.
    origins = centers[selection] - normals[selection] * 1e-5

    try:
        locations, index_ray, index_tri = mesh.ray.intersects_location(
            ray_origins=origins, ray_directions=-normals[selection], multiple_hits=True
        )
    except Exception as exc:  # pragma: no cover
        logger.debug("Ray casting non disponibile: %s", exc)
        return thickness

    if len(index_ray) == 0:
        return thickness

    # `index_ray` numera i raggi lanciati (cioè il sottoinsieme campionato):
    # serve sia così, per risalire all'origine, sia tradotto in indice di faccia.
    face_of_ray = selection[index_ray]

    # Solo le facce *affacciate* delimitano una parete. Senza questo filtro, su
    # uno spigolo vivo il raggio colpisce una faccia quasi complanare a distanza
    # praticamente nulla e lo spessore risulterebbe zero anche su un pezzo massiccio.
    facing = (normals[index_tri] * normals[face_of_ray]).sum(axis=1) < -0.3
    if not facing.any():
        return thickness

    distances = np.linalg.norm(locations[facing] - origins[index_ray[facing]], axis=1)
    # Impatti a distanza trascurabile sono la faccia di partenza stessa.
    valid = distances > 1e-4
    if not valid.any():
        return thickness

    np.minimum.at(thickness, face_of_ray[facing][valid], distances[valid])
    thickness[thickness > max_distance_mm] = np.inf
    return thickness


def wall_thickness(
    mesh: trimesh.Trimesh, max_distance_mm: float = 10.0, max_samples: int | None = None
) -> np.ndarray:
    """Spessore locale per vertice, derivato da quello delle facce incidenti.

    Args:
        mesh: mesh da misurare.
        max_distance_mm: limite oltre il quale lo spessore è ``inf``.
        max_samples: tetto sul numero di raggi lanciati (vedi
            :func:`face_wall_thickness`).

    Returns:
        Array di spessori in millimetri; ``inf`` dove non misurabile.
    """
    if is_empty(mesh):
        return np.zeros(0)

    face_values = face_wall_thickness(mesh, max_distance_mm, max_samples=max_samples)
    thickness = np.full(len(mesh.vertices), np.inf, dtype=np.float64)
    measurable = np.isfinite(face_values)
    if not measurable.any():
        return thickness

    faces = mesh.faces[measurable]
    values = face_values[measurable]
    # Al vertice assegniamo lo spessore minimo fra le facce che lo toccano:
    # per la stampabilità conta il punto più critico, non la media.
    for column in range(3):
        np.minimum.at(thickness, faces[:, column], values)
    return thickness


def estimate_print_time_min(
    mesh: trimesh.Trimesh, layer_height_mm: float = 0.2, speed_mm3_s: float = 12.0, infill: float = 0.15
) -> float:
    """Stima grossolana del tempo di stampa in minuti.

    Modello semplificato: volume del perimetro (superficie × 2 estrusioni) più
    il riempimento, diviso per la portata volumetrica media.
    """
    if is_empty(mesh):
        return 0.0

    shell_volume = float(mesh.area) * 0.8  # ≈ 2 perimetri da 0.4 mm
    solid_volume = float(abs(mesh.volume)) if mesh.is_watertight else 0.0
    infill_volume = max(0.0, solid_volume - shell_volume) * float(infill)
    total = shell_volume + infill_volume
    if speed_mm3_s <= 0:
        return 0.0
    return total / speed_mm3_s / 60.0


def estimate_filament_g(mesh: trimesh.Trimesh, density_g_cm3: float = 1.24, infill: float = 0.15) -> float:
    """Stima del filamento consumato in grammi (PLA di default)."""
    if is_empty(mesh) or not mesh.is_watertight:
        return 0.0
    shell_volume = float(mesh.area) * 0.8
    solid_volume = float(abs(mesh.volume))
    effective = min(solid_volume, shell_volume + max(0.0, solid_volume - shell_volume) * infill)
    return effective / 1000.0 * density_g_cm3


def bounding_box_fits(mesh: trimesh.Trimesh, bed_size_mm: tuple[float, float, float]) -> bool:
    """Verifica che il pezzo entri nel volume di stampa (con rotazione libera in XY)."""
    if is_empty(mesh):
        return True
    extents = sorted(float(x) for x in mesh.extents)
    bed = sorted(float(x) for x in bed_size_mm)
    return all(e <= b for e, b in zip(extents, bed))
