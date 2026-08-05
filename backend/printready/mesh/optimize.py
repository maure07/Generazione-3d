"""Ottimizzazione topologica e riduzione poligoni.

Due operazioni distinte, spesso confuse fra loro:

* **Ottimizzazione dei triangoli** (`optimize_triangles`): non cambia il numero
  di poligoni in modo significativo, ma migliora la *qualità* della maglia —
  elimina i triangoli "a scheggia" (sliver), collassa gli spigoli microscopici
  e riequilibra gli angoli. Una maglia regolare produce percorsi utensile più
  puliti e riduce gli artefatti dello slicer.

* **Riduzione poligoni** (`decimate`): abbassa il conteggio facce fino al
  budget richiesto. La modalità adattiva conserva la densità dove la curvatura
  è alta (volti, incisioni, mani) e riduce aggressivamente le zone piatte.

Motori supportati, in ordine di preferenza: ``fast_simplification`` →
``open3d`` → implementazione QEM interna (sempre disponibile).
"""

from __future__ import annotations

import heapq
import logging
from dataclasses import dataclass

import numpy as np
import trimesh

from .io import is_empty
from .metrics import per_vertex_curvature

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OptimizeReport:
    """Esito dell'ottimizzazione dei triangoli."""

    slivers_removed: int = 0
    short_edges_collapsed: int = 0
    edges_flipped: int = 0
    faces_before: int = 0
    faces_after: int = 0

    def message_it(self) -> str:
        if self.slivers_removed == 0 and self.short_edges_collapsed == 0 and self.edges_flipped == 0:
            return "Maglia già regolare"
        return (
            f"Ottimizzati i triangoli: {self.slivers_removed} schegge rimosse, "
            f"{self.short_edges_collapsed} spigoli micro collassati, "
            f"{self.edges_flipped} spigoli riorientati"
        )


@dataclass(slots=True)
class DecimateReport:
    """Esito della riduzione poligoni."""

    faces_before: int = 0
    faces_after: int = 0
    target_faces: int = 0
    engine: str = "none"
    adaptive: bool = False
    hausdorff_estimate_mm: float = 0.0

    @property
    def reduction_pct(self) -> float:
        if self.faces_before == 0:
            return 0.0
        return 100.0 * (1.0 - self.faces_after / self.faces_before)

    def message_it(self) -> str:
        if self.faces_after >= self.faces_before:
            return f"Nessuna riduzione necessaria ({self.faces_before} triangoli)"
        return (
            f"Triangoli ridotti da {self.faces_before} a {self.faces_after} "
            f"(-{self.reduction_pct:.1f}%, scarto max ≈ {self.hausdorff_estimate_mm:.3f} mm)"
        )


# ---------------------------------------------------------------------------
# Ottimizzazione qualità della maglia
# ---------------------------------------------------------------------------


def triangle_quality(mesh: trimesh.Trimesh) -> np.ndarray:
    """Qualità di ogni triangolo in [0, 1] (1 = equilatero, 0 = degenere).

    Metrica: ``4*sqrt(3)*area / (l1² + l2² + l3²)``, standard in mesh generation.
    """
    if is_empty(mesh):
        return np.zeros(0)
    tri = mesh.vertices[mesh.faces]
    e0 = tri[:, 1] - tri[:, 0]
    e1 = tri[:, 2] - tri[:, 1]
    e2 = tri[:, 0] - tri[:, 2]
    lengths_sq = (e0**2).sum(1) + (e1**2).sum(1) + (e2**2).sum(1)
    areas = mesh.area_faces
    with np.errstate(divide="ignore", invalid="ignore"):
        quality = 4.0 * np.sqrt(3.0) * areas / lengths_sq
    return np.nan_to_num(quality, nan=0.0, posinf=0.0, neginf=0.0)


def optimize_triangles(
    mesh: trimesh.Trimesh,
    min_quality: float = 0.02,
    min_edge_mm: float = 0.01,
    smooth_iterations: int = 1,
) -> tuple[trimesh.Trimesh, OptimizeReport]:
    """Migliora la regolarità della maglia senza alterarne la forma.

    Args:
        mesh: mesh da ottimizzare.
        min_quality: sotto questa qualità un triangolo è considerato una scheggia.
        min_edge_mm: spigoli più corti vengono collassati.
        smooth_iterations: passate di rilassamento tangenziale (0 = disattivato).

    Returns:
        Mesh ottimizzata e report.
    """
    report = OptimizeReport()
    if is_empty(mesh):
        return mesh, report

    work = mesh.copy()
    report.faces_before = len(work.faces)

    # 1) Collassa gli spigoli microscopici fondendo i vertici entro tolleranza.
    if min_edge_mm > 0:
        v_before = len(work.vertices)
        digits = int(max(0, min(12, round(-np.log10(min_edge_mm)))))
        try:
            work.merge_vertices(digits_vertex=digits)
        except TypeError:  # pragma: no cover
            work.merge_vertices()
        report.short_edges_collapsed = max(0, v_before - len(work.vertices))

    # 2) Elimina schegge e facce degeneri.
    quality = triangle_quality(work)
    keep = quality > min_quality
    report.slivers_removed = int(np.count_nonzero(~keep))
    if report.slivers_removed and np.count_nonzero(keep) > 4:
        work.update_faces(keep)
        work.remove_unreferenced_vertices()

    # 3) Rilassamento tangenziale: sposta i vertici sul piano tangente, così la
    #    maglia si regolarizza senza far "sgonfiare" il modello.
    if smooth_iterations > 0:
        work = tangential_relaxation(work, iterations=smooth_iterations)

    work._cache.clear()
    report.faces_after = len(work.faces)
    return work, report


def tangential_relaxation(
    mesh: trimesh.Trimesh, iterations: int = 1, strength: float = 0.35
) -> trimesh.Trimesh:
    """Rilassamento laplaciano proiettato sul piano tangente.

    Preserva il volume (a differenza dello smoothing laplaciano classico) perché
    la componente normale dello spostamento viene annullata.
    """
    if is_empty(mesh) or iterations <= 0:
        return mesh

    work = mesh.copy()
    edges = work.edges_unique
    if len(edges) == 0:
        return work

    n_v = len(work.vertices)
    counts = np.bincount(edges.ravel(), minlength=n_v).astype(np.float64)
    counts[counts == 0] = 1.0

    for _ in range(iterations):
        acc = np.zeros((n_v, 3), dtype=np.float64)
        np.add.at(acc, edges[:, 0], work.vertices[edges[:, 1]])
        np.add.at(acc, edges[:, 1], work.vertices[edges[:, 0]])
        centroid = acc / counts[:, None]
        delta = (centroid - work.vertices) * strength

        normals = np.asarray(work.vertex_normals, dtype=np.float64)
        normal_component = (delta * normals).sum(axis=1, keepdims=True) * normals
        work.vertices = work.vertices + (delta - normal_component)
        work._cache.clear()

    return work


# ---------------------------------------------------------------------------
# Riduzione poligoni
# ---------------------------------------------------------------------------


def _decimate_fast_simplification(
    mesh: trimesh.Trimesh, target_faces: int
) -> trimesh.Trimesh | None:
    """Riduzione tramite la libreria ``fast_simplification`` (QEM in C++)."""
    try:
        import fast_simplification
    except ImportError:
        return None

    reduction = 1.0 - (target_faces / max(1, len(mesh.faces)))
    reduction = float(np.clip(reduction, 0.0, 0.999))
    if reduction <= 0.0:
        return mesh.copy()

    try:
        vertices, faces = fast_simplification.simplify(
            np.asarray(mesh.vertices, dtype=np.float32),
            np.asarray(mesh.faces, dtype=np.int32),
            reduction,
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("fast_simplification fallita: %s", exc)
        return None

    if len(faces) == 0:
        return None
    return trimesh.Trimesh(vertices=np.asarray(vertices, dtype=np.float64), faces=faces, process=False)


def _decimate_open3d(mesh: trimesh.Trimesh, target_faces: int) -> trimesh.Trimesh | None:
    """Riduzione tramite Open3D, se installato."""
    try:
        import open3d as o3d
    except ImportError:
        return None

    try:
        o3d_mesh = o3d.geometry.TriangleMesh(
            o3d.utility.Vector3dVector(np.asarray(mesh.vertices)),
            o3d.utility.Vector3iVector(np.asarray(mesh.faces)),
        )
        simplified = o3d_mesh.simplify_quadric_decimation(int(target_faces))
        faces = np.asarray(simplified.triangles)
        if len(faces) == 0:
            return None
        return trimesh.Trimesh(
            vertices=np.asarray(simplified.vertices), faces=faces, process=False
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("Decimazione Open3D fallita: %s", exc)
        return None


def _decimate_qem_internal(
    mesh: trimesh.Trimesh,
    target_faces: int,
    protect_mask: np.ndarray | None = None,
    max_collapses: int = 400_000,
) -> trimesh.Trimesh | None:
    """Implementazione interna QEM (Garland & Heckbert) a collasso di spigoli.

    Sempre disponibile: non richiede dipendenze native. È più lenta dei motori
    compilati, quindi viene usata solo come riserva o per la fase adattiva su
    un sottoinsieme ridotto di spigoli.

    Args:
        mesh: mesh da ridurre.
        target_faces: numero di facce desiderato.
        protect_mask: vertici da non collassare (es. zone ad alta curvatura).
        max_collapses: limite di sicurezza sul numero di operazioni.
    """
    vertices = np.asarray(mesh.vertices, dtype=np.float64).copy()
    faces = np.asarray(mesh.faces, dtype=np.int64).copy()
    n_v = len(vertices)
    if n_v == 0 or len(faces) <= target_faces:
        return mesh.copy()

    protect = (
        np.zeros(n_v, dtype=bool) if protect_mask is None else np.asarray(protect_mask, dtype=bool)
    )

    # --- Quadriche per vertice --------------------------------------------
    tri = vertices[faces]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    valid = lengths > 1e-12
    normals[valid] /= lengths[valid][:, None]
    d = -(normals * tri[:, 0]).sum(axis=1)
    planes = np.hstack([normals, d[:, None]])  # (F, 4)
    areas = lengths / 2.0

    quadrics = np.zeros((n_v, 4, 4), dtype=np.float64)
    weighted = planes[:, :, None] * planes[:, None, :] * areas[:, None, None]
    for k in range(3):
        np.add.at(quadrics, faces[:, k], weighted)

    # --- Strutture dinamiche ----------------------------------------------
    alive_face = np.ones(len(faces), dtype=bool)
    alive_vertex = np.ones(n_v, dtype=bool)
    vertex_faces: list[set[int]] = [set() for _ in range(n_v)]
    for fi, face in enumerate(faces):
        for vi in face:
            vertex_faces[vi].add(fi)

    def edge_target(a: int, b: int) -> tuple[float, np.ndarray]:
        """Costo e posizione ottimale per il collasso dello spigolo (a, b)."""
        q = quadrics[a] + quadrics[b]
        A = q[:3, :3]
        bvec = -q[:3, 3]
        try:
            pos = np.linalg.solve(A + np.eye(3) * 1e-10, bvec)
        except np.linalg.LinAlgError:
            pos = (vertices[a] + vertices[b]) / 2.0
        v4 = np.append(pos, 1.0)
        cost = float(v4 @ q @ v4)
        return max(cost, 0.0), pos

    heap: list[tuple[float, int, int, int]] = []
    version = np.zeros(n_v, dtype=np.int64)

    edges = trimesh.geometry.faces_to_edges(faces)
    edges = np.unique(np.sort(edges, axis=1), axis=0)
    for a, b in edges:
        a, b = int(a), int(b)
        if protect[a] and protect[b]:
            continue
        cost, _ = edge_target(a, b)
        heapq.heappush(heap, (cost, a, b, 0))

    faces_alive = len(faces)
    collapses = 0

    while faces_alive > target_faces and heap and collapses < max_collapses:
        cost, a, b, stamp = heapq.heappop(heap)
        if not alive_vertex[a] or not alive_vertex[b]:
            continue
        if stamp != version[a] + version[b]:
            continue  # voce obsoleta

        shared = vertex_faces[a] & vertex_faces[b]
        if not shared:
            continue

        _, new_pos = edge_target(a, b)
        if protect[b] and not protect[a]:
            a, b = b, a
        if protect[b]:
            new_pos = vertices[b]

        # Collassa b su a.
        vertices[a] = new_pos
        quadrics[a] = quadrics[a] + quadrics[b]

        for fi in list(vertex_faces[b]):
            if not alive_face[fi]:
                continue
            faces[fi][faces[fi] == b] = a
            f = faces[fi]
            if f[0] == f[1] or f[1] == f[2] or f[0] == f[2]:
                alive_face[fi] = False
                faces_alive -= 1
                for vi in set(f.tolist()):
                    vertex_faces[vi].discard(fi)
            else:
                vertex_faces[a].add(fi)
        vertex_faces[b].clear()
        alive_vertex[b] = False
        version[a] += 1
        collapses += 1

        # Reinserisce gli spigoli incidenti aggiornati.
        neighbours: set[int] = set()
        for fi in vertex_faces[a]:
            neighbours.update(int(x) for x in faces[fi])
        neighbours.discard(a)
        for nb in neighbours:
            if not alive_vertex[nb]:
                continue
            if protect[a] and protect[nb]:
                continue
            c, _ = edge_target(a, nb)
            heapq.heappush(heap, (c, a, nb, version[a] + version[nb]))

    kept_faces = faces[alive_face]
    if len(kept_faces) == 0:
        return None

    result = trimesh.Trimesh(vertices=vertices, faces=kept_faces, process=False)
    result.remove_unreferenced_vertices()
    result._cache.clear()
    return result


def _estimate_deviation(original: trimesh.Trimesh, reduced: trimesh.Trimesh, samples: int = 2000) -> float:
    """Stima lo scostamento massimo (Hausdorff approssimato) in millimetri.

    Metodo preferito: distanza punto-superficie esatta (richiede ``rtree``).
    Riserva: distanza al vertice più vicino tramite KD-tree — leggermente
    sovrastimata ma sempre disponibile.
    """
    if is_empty(original) or is_empty(reduced):
        return 0.0

    n_src = len(original.vertices)
    if n_src == 0:
        return 0.0
    n = int(min(samples, n_src))
    idx = np.random.default_rng(0).choice(n_src, size=n, replace=False)
    points = original.vertices[idx]

    try:
        _, distance, _ = reduced.nearest.on_surface(points)
        return float(np.percentile(distance, 99))
    except Exception as exc:
        logger.debug("Distanza punto-superficie non disponibile (%s): uso il KD-tree", exc)

    try:
        from scipy.spatial import cKDTree

        tree = cKDTree(np.asarray(reduced.vertices))
        distance, _ = tree.query(points, k=1)
        return float(np.percentile(distance, 99))
    except Exception:  # pragma: no cover
        return 0.0


def decimate(
    mesh: trimesh.Trimesh,
    target_faces: int,
    preserve_detail: float = 0.7,
    adaptive: bool = True,
) -> tuple[trimesh.Trimesh, DecimateReport]:
    """Riduce il numero di triangoli conservando il dettaglio percepito.

    In modalità adattiva la riduzione avviene in due fasi:

    1. una passata veloce fino a un obiettivo intermedio;
    2. una passata QEM interna che **protegge i vertici ad alta curvatura**,
       togliendo poligoni solo dalle superfici piatte.

    Args:
        mesh: mesh da ridurre.
        target_faces: budget finale di triangoli.
        preserve_detail: 0 = riduzione massima, 1 = massima conservazione.
            Influenza sia la soglia di curvatura protetta sia il target
            intermedio.
        adaptive: abilita la seconda fase curvatura-dipendente.

    Returns:
        Mesh ridotta e report.
    """
    report = DecimateReport(target_faces=int(target_faces), adaptive=adaptive)
    if is_empty(mesh):
        return mesh, report

    report.faces_before = len(mesh.faces)
    if report.faces_before <= target_faces:
        report.faces_after = report.faces_before
        report.engine = "nessuna"
        return mesh.copy(), report

    detail = float(np.clip(preserve_detail, 0.0, 1.0))

    # Obiettivo intermedio: con detail alto ci si ferma prima e si lascia il
    # lavoro fine alla fase adattiva.
    if adaptive:
        intermediate = int(target_faces * (1.0 + 0.6 * detail))
        intermediate = min(report.faces_before, max(target_faces, intermediate))
    else:
        intermediate = target_faces

    result = _decimate_fast_simplification(mesh, intermediate)
    engine = "fast_simplification"
    if result is None:
        result = _decimate_open3d(mesh, intermediate)
        engine = "open3d"
    if result is None:
        result = _decimate_qem_internal(mesh, intermediate)
        engine = "qem_interno"
    if result is None:
        logger.warning("Nessun motore di decimazione disponibile: mesh invariata")
        report.faces_after = report.faces_before
        return mesh.copy(), report

    if adaptive and len(result.faces) > target_faces:
        curvature = per_vertex_curvature(result)
        if curvature.size:
            # Proteggiamo la frazione di vertici più "spigolosi".
            protected_fraction = 0.15 + 0.45 * detail
            threshold = float(np.quantile(curvature, 1.0 - protected_fraction))
            protect = curvature >= threshold
            refined = _decimate_qem_internal(result, target_faces, protect_mask=protect)
            if refined is not None and len(refined.faces) > 0:
                result = refined
                engine = f"{engine}+qem_adattivo"

    if len(result.faces) > target_faces * 1.15:
        # La fase adattiva può fermarsi prima: rifinitura uniforme finale.
        final = _decimate_fast_simplification(result, target_faces)
        if final is not None:
            result = final

    result._cache.clear()
    report.faces_after = len(result.faces)
    report.engine = engine
    report.hausdorff_estimate_mm = _estimate_deviation(mesh, result)
    return result, report
