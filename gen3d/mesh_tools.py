"""Post-processing geometrico: pulizia, riparazione watertight, normali.

Tutte le funzioni operano su `trimesh.Trimesh` e sono pure (non toccano
disco), cosi' da poter essere testate senza alcun modello AI: bastano
primitive trimesh (box, sfere, ecc.) per validare l'intera catena di
riparazione, vedi tests/test_mesh_tools.py.
"""
from __future__ import annotations

import logging

import numpy as np
import trimesh

from .config import MeshCleanupConfig

logger = logging.getLogger("gen3d.mesh_tools")

try:
    import pymeshfix  # type: ignore

    _HAS_PYMESHFIX = True
except ImportError:  # pragma: no cover - dipendenza opzionale
    _HAS_PYMESHFIX = False


class MeshRepairError(RuntimeError):
    """La mesh non e' stata resa watertight nonostante tutti i tentativi."""


def load_mesh(path: str) -> trimesh.Trimesh:
    mesh = trimesh.load(path, force="mesh", process=False)
    if not isinstance(mesh, trimesh.Trimesh):
        raise TypeError(f"{path} non contiene una mesh singola valida (letto: {type(mesh)})")
    return mesh


def basic_clean(mesh: trimesh.Trimesh, cfg: MeshCleanupConfig) -> trimesh.Trimesh:
    """Rimuove artefatti elementari: vertici/facce degenerate, duplicati, NaN."""
    mesh = mesh.copy()
    mesh.remove_infinite_values()
    mesh.merge_vertices(digits_vertex=_digits_from_tolerance(cfg.merge_vertex_tolerance_mm))
    mask = mesh.unique_faces() & mesh.nondegenerate_faces()
    mesh.update_faces(mask)
    mesh.remove_unreferenced_vertices()
    return mesh


def _digits_from_tolerance(tolerance_mm: float) -> int:
    if tolerance_mm <= 0:
        return 8
    return max(0, int(round(-np.log10(tolerance_mm))))


def remove_small_components(mesh: trimesh.Trimesh, ratio: float) -> trimesh.Trimesh:
    """Elimina componenti sconnesse piccole (rumore/artefatti isolati)."""
    if ratio <= 0:
        return mesh
    components = mesh.split(only_watertight=False)
    if len(components) <= 1:
        return mesh
    volumes = []
    for comp in components:
        try:
            vol = abs(comp.volume) if comp.is_watertight else comp.convex_hull.volume
        except Exception:
            vol = comp.convex_hull.volume
        volumes.append(vol)
    max_vol = max(volumes) if volumes else 0.0
    if max_vol <= 0:
        return mesh
    keep = [c for c, v in zip(components, volumes) if v >= ratio * max_vol]
    if not keep:
        keep = [components[int(np.argmax(volumes))]]
    logger.info("remove_small_components: %d/%d componenti mantenute", len(keep), len(components))
    return trimesh.util.concatenate(keep)


def fix_normals(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Corregge l'orientamento delle normali (coerenza + verso l'esterno)."""
    mesh = mesh.copy()
    trimesh.repair.fix_winding(mesh)
    trimesh.repair.fix_inversion(mesh, multibody=True)
    trimesh.repair.fix_normals(mesh, multibody=True)
    return mesh


def fill_holes(mesh: trimesh.Trimesh, max_hole_edges: int = 200) -> trimesh.Trimesh:
    mesh = mesh.copy()
    trimesh.repair.fill_holes(mesh)
    return mesh


def make_watertight(mesh: trimesh.Trimesh, cfg: MeshCleanupConfig) -> trimesh.Trimesh:
    """Pipeline di riparazione progressiva fino a ottenere una mesh watertight.

    Ordine dei tentativi (dal piu' economico al piu' aggressivo):
      1. pulizia base + fill_holes + fix_normals (trimesh)
      2. pymeshfix (se installato) per buchi/self-intersection complesse
      3. fallback: convex hull della mesh ripulita (ultima spiaggia, avvisa
         l'utente perche' altera la geometria)
    """
    mesh = basic_clean(mesh, cfg)
    mesh = remove_small_components(mesh, cfg.remove_small_components_ratio)

    if cfg.fill_holes:
        mesh = fill_holes(mesh, cfg.max_hole_edges)
    if cfg.fix_normals:
        mesh = fix_normals(mesh)

    if mesh.is_watertight:
        return mesh

    if _HAS_PYMESHFIX:
        logger.info("make_watertight: trimesh non basta, provo pymeshfix")
        mesh = _repair_with_pymeshfix(mesh)
        if mesh.is_watertight:
            return mesh

    logger.warning(
        "make_watertight: la mesh resta non-watertight dopo tutti i tentativi "
        "automatici; ricorro all'involucro convesso come ultima risorsa."
    )
    hull = mesh.convex_hull
    if not hull.is_watertight:
        raise MeshRepairError("Impossibile ottenere una mesh watertight nemmeno con il convex hull")
    return hull


def _repair_with_pymeshfix(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    fixer = pymeshfix.MeshFix(mesh.vertices, mesh.faces)
    fixer.repair(verbose=False)
    return trimesh.Trimesh(vertices=fixer.v, faces=fixer.f, process=True)


def smooth(mesh: trimesh.Trimesh, iterations: int) -> trimesh.Trimesh:
    """Laplacian smoothing leggero. iterations=0 preserva gli spigoli originali."""
    if iterations <= 0:
        return mesh
    mesh = mesh.copy()
    trimesh.smoothing.filter_taubin(mesh, iterations=iterations)
    return mesh


def decimate(mesh: trimesh.Trimesh, target_faces: int | None) -> trimesh.Trimesh:
    if not target_faces or len(mesh.faces) <= target_faces:
        return mesh
    try:
        return mesh.simplify_quadric_decimation(face_count=target_faces)
    except Exception as exc:  # alcune build di trimesh richiedono `open3d`/`fast-simplification`
        logger.warning("decimate: simplify_quadric_decimation fallita (%s), mesh invariata", exc)
        return mesh


def normalize_scale(mesh: trimesh.Trimesh, target_size_mm: float) -> trimesh.Trimesh:
    """Riscala la mesh in modo che il lato piu' lungo del bbox sia target_size_mm."""
    mesh = mesh.copy()
    extents = mesh.extents
    longest = float(np.max(extents)) if extents is not None else 0.0
    if longest <= 0:
        return mesh
    scale = target_size_mm / longest
    mesh.apply_scale(scale)
    mesh.apply_translation(-mesh.bounds[0])  # ancoraggio a origine (>= 0)
    return mesh


def clean_and_repair(mesh: trimesh.Trimesh, cfg: MeshCleanupConfig) -> trimesh.Trimesh:
    """Pipeline completa di post-processing usata dall'orchestratore."""
    mesh = make_watertight(mesh, cfg)
    mesh = smooth(mesh, cfg.smooth_iterations)
    mesh = decimate(mesh, cfg.target_faces)
    if not mesh.is_watertight:
        # smoothing/decimazione in rari casi introducono micro-buchi: un
        # secondo passaggio di riparazione e' economico e sicuro.
        mesh = make_watertight(mesh, cfg)
    return mesh


def validate_printability(mesh: trimesh.Trimesh, max_bbox_mm: tuple[float, float, float]) -> list[str]:
    """Controlli rapidi pre-export. Ritorna una lista di problemi (vuota se OK)."""
    issues: list[str] = []
    if not mesh.is_watertight:
        issues.append("mesh non watertight")
    if mesh.volume <= 0:
        issues.append("volume nullo o negativo (normali probabilmente invertite)")
    extents = mesh.extents
    for axis, (size, limit) in enumerate(zip(extents, max_bbox_mm)):
        if size > limit:
            issues.append(f"asse {'xyz'[axis]}: {size:.1f}mm supera il piano di stampa ({limit:.1f}mm)")
    if len(mesh.faces) == 0:
        issues.append("mesh vuota")
    return issues
