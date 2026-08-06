"""Taglio del modello lungo piani e generazione di connettori perno/sede.

Algoritmo per ogni piano di taglio:
  1. Split della mesh lungo il piano con capping automatico (le due meta'
     restano watertight singolarmente, trimesh `slice_plane(cap=True)`).
  2. Calcolo della sezione trasversale sul piano (poligono 2D via shapely)
     per piazzare i perni in punti sicuri (dentro il materiale, lontano
     dai bordi di almeno `min_pin_edge_distance_mm`).
  3. Per ogni punto: costruzione di un cilindro maschio (unito alla meta'
     "positiva") e di un cilindro femmina/sede piu' largo di `tolerance_mm`
     (sottratto dalla meta' "negativa").
  4. Boolean union/difference con fallback a catena di engine
     (manifold -> blender -> scad) per non fallire su mesh complesse.

Le due meta' risultanti si incastrano fisicamente dopo la stampa: il
perno (maschio) entra nella sede (femmina) con il gioco radiale configurato.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import trimesh

from .config import CutterConfig
from . import mesh_tools

logger = logging.getLogger("gen3d.cutter")

try:
    from shapely.geometry import Point, Polygon, MultiPolygon
    from shapely.ops import unary_union

    _HAS_SHAPELY = True
except ImportError:  # pragma: no cover
    _HAS_SHAPELY = False


class BooleanOperationError(RuntimeError):
    """Nessun engine booleano disponibile e' riuscito a completare l'operazione."""


class CuttingError(RuntimeError):
    """Il taglio non ha prodotto due parti valide (es. piano fuori dal solido)."""


@dataclass
class CutPlane:
    origin: np.ndarray
    normal: np.ndarray

    @classmethod
    def from_bounds_fraction(cls, mesh: trimesh.Trimesh, axis: str, fraction: float = 0.5) -> "CutPlane":
        axis_idx = {"x": 0, "y": 1, "z": 2}[axis.lower()]
        normal = np.zeros(3)
        normal[axis_idx] = 1.0
        lo, hi = mesh.bounds[0][axis_idx], mesh.bounds[1][axis_idx]
        origin = mesh.bounds[0].copy()
        origin[axis_idx] = lo + (hi - lo) * fraction
        return cls(origin=origin, normal=normal)


@dataclass
class CutPart:
    mesh: trimesh.Trimesh
    label: str


def _boolean(op: str, meshes: list[trimesh.Trimesh], engines: list[str]) -> trimesh.Trimesh:
    last_error: Exception | None = None
    for engine in engines:
        try:
            if op == "union":
                result = trimesh.boolean.union(meshes, engine=engine)
            elif op == "difference":
                result = trimesh.boolean.difference(meshes, engine=engine)
            else:
                raise ValueError(op)
            if result is not None and len(result.faces) > 0:
                return result
        except BaseException as exc:  # gli engine esterni possono sollevare di tutto
            logger.warning("boolean(%s) con engine=%s fallito: %s", op, engine, exc)
            last_error = exc
            continue
    raise BooleanOperationError(
        f"Operazione booleana '{op}' fallita con tutti gli engine {engines}: {last_error}"
    )


def _align_z_to(direction: np.ndarray, center: np.ndarray) -> np.ndarray:
    """Matrice di trasformazione che porta l'asse Z su `direction`, centrata in `center`."""
    direction = direction / np.linalg.norm(direction)
    transform = trimesh.geometry.align_vectors([0, 0, 1], direction)
    transform[:3, 3] = center
    return transform


def _make_cylinder(radius: float, height: float, center: np.ndarray, direction: np.ndarray) -> trimesh.Trimesh:
    transform = _align_z_to(direction, center)
    return trimesh.creation.cylinder(radius=radius, height=height, sections=48, transform=transform)


def _cross_section_polygon(mesh: trimesh.Trimesh, plane: CutPlane):
    """Ritorna (polygon_2d, to_3d_transform) della sezione del solido sul piano."""
    section = mesh.section(plane_origin=plane.origin, plane_normal=plane.normal)
    if section is None:
        raise CuttingError("Il piano di taglio non interseca la mesh")
    planar, to_3d = section.to_2D()
    polygons = [p for p in planar.polygons_full if p.is_valid and p.area > 1e-6]
    if not polygons:
        raise CuttingError("Sezione di taglio degenere (area nulla)")
    merged = unary_union(polygons) if _HAS_SHAPELY else polygons[0]
    return merged, to_3d


def _pin_positions_2d(polygon, n_pins: int, edge_margin: float) -> list:
    """Sceglie n_pins punti 2D dentro il poligono, lontani dai bordi."""
    eroded = polygon.buffer(-edge_margin)
    if eroded.is_empty:
        eroded = polygon.buffer(-edge_margin * 0.3)
    if eroded.is_empty:
        return [polygon.representative_point()]

    polys = list(eroded.geoms) if isinstance(eroded, MultiPolygon) else [eroded]
    target = max(polys, key=lambda p: p.area)

    if n_pins <= 1:
        return [target.centroid]

    coords = np.array(target.exterior.coords)
    centered = coords - coords.mean(axis=0)
    if len(centered) >= 2:
        _, _, vt = np.linalg.svd(centered, full_matrices=False)
        major_axis = vt[0]
    else:
        major_axis = np.array([1.0, 0.0])

    centroid = np.array(target.centroid.coords[0])
    proj = centered @ major_axis
    span = proj.max() - proj.min() if len(proj) else 0.0
    half_span = max(span * 0.35, edge_margin)

    offsets = np.linspace(-half_span, half_span, n_pins)
    points = []
    for off in offsets:
        candidate = Point(centroid + major_axis * off)
        if target.contains(candidate):
            points.append(candidate)
    if not points:
        points = [target.centroid]
    return points


def split_with_connectors(
    mesh: trimesh.Trimesh,
    plane: CutPlane,
    cfg: CutterConfig,
) -> tuple[trimesh.Trimesh, trimesh.Trimesh]:
    """Taglia `mesh` lungo `plane` e applica perni/sedi. Ritorna (parte_positiva, parte_negativa)."""
    if not _HAS_SHAPELY:
        raise RuntimeError("shapely e' richiesto da cutter.split_with_connectors (pip install shapely)")

    normal = plane.normal / np.linalg.norm(plane.normal)

    part_pos = mesh.slice_plane(plane_origin=plane.origin, plane_normal=normal, cap=True)
    part_neg = mesh.slice_plane(plane_origin=plane.origin, plane_normal=-normal, cap=True)
    if part_pos is None or part_neg is None or len(part_pos.faces) == 0 or len(part_neg.faces) == 0:
        raise CuttingError("Il piano di taglio non produce due parti valide")

    polygon_2d, to_3d = _cross_section_polygon(mesh, plane)
    pin_points_2d = _pin_positions_2d(polygon_2d, cfg.n_pins, cfg.min_pin_edge_distance_mm)

    pin_radius = cfg.pin_diameter_mm / 2.0
    socket_radius = pin_radius + cfg.tolerance_mm
    embed_depth = cfg.pin_length_mm * cfg.embed_ratio
    protrude_depth = cfg.pin_length_mm - embed_depth
    socket_depth = protrude_depth + cfg.socket_extra_depth_mm

    male_pins: list[trimesh.Trimesh] = []
    female_sockets: list[trimesh.Trimesh] = []
    for pt2d in pin_points_2d:
        center3d = to_3d @ np.array([pt2d.x, pt2d.y, 0.0, 1.0])
        center3d = center3d[:3]

        pin_center = center3d + normal * (embed_depth - protrude_depth) / 2.0
        male_pins.append(_make_cylinder(pin_radius, cfg.pin_length_mm, pin_center, normal))

        socket_start_offset = 0.5  # piccola sovrapposizione oltre il piano per una sottrazione pulita
        socket_center = center3d - normal * (socket_depth / 2.0 - socket_start_offset)
        female_sockets.append(_make_cylinder(socket_radius, socket_depth + socket_start_offset, socket_center, normal))

    part_pos_final = _boolean("union", [part_pos, *male_pins], cfg.boolean_engines) if male_pins else part_pos
    part_neg_final = _boolean("difference", [part_neg, *female_sockets], cfg.boolean_engines) if female_sockets else part_neg

    return part_pos_final, part_neg_final


def auto_split_axis(mesh: trimesh.Trimesh, max_bbox_mm: tuple[float, float, float]) -> str | None:
    """Sceglie l'asse lungo cui l'oggetto eccede di piu' il piano di stampa. None se ci sta gia'."""
    extents = mesh.extents
    ratios = [extents[i] / max_bbox_mm[i] for i in range(3)]
    worst = int(np.argmax(ratios))
    if ratios[worst] <= 1.0:
        return None
    return "xyz"[worst]


def split_recursive(
    mesh: trimesh.Trimesh,
    cfg: CutterConfig,
    cleanup_cfg,
    n_parts: int,
    axis: str = "z",
) -> list[CutPart]:
    """Divide la mesh in `n_parts` pezzi lungo `axis`, con connettori a ogni taglio.

    Ogni pezzo viene ripulito/riparato subito dopo il taglio (mesh_tools),
    cosi' un eventuale fallimento booleano sul pezzo N non si propaga
    silenziosamente ai tagli successivi.
    """
    if n_parts < 1:
        raise ValueError("n_parts deve essere >= 1")
    if n_parts == 1:
        return [CutPart(mesh=mesh, label="part_1")]

    parts: list[CutPart] = []
    remaining = mesh
    fractions = np.linspace(0, 1, n_parts + 1)[1:-1]  # tagli interni equidistanti sul remaining corrente

    for i in range(n_parts - 1):
        plane = CutPlane.from_bounds_fraction(remaining, axis, fraction=1.0 / (n_parts - i))
        pos, neg = split_with_connectors(remaining, plane, cfg)
        pos = mesh_tools.clean_and_repair(pos, cleanup_cfg)
        neg = mesh_tools.clean_and_repair(neg, cleanup_cfg)
        parts.append(CutPart(mesh=neg, label=f"part_{i + 1}"))
        remaining = pos

    parts.append(CutPart(mesh=remaining, label=f"part_{n_parts}"))
    return parts
