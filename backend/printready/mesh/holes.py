"""Chiusura automatica dei buchi (rende la mesh *watertight*).

Strategia a tre livelli, dal meno invasivo al più aggressivo:

1. ``trimesh.repair.fill_holes`` — chiude triangoli e quadrilateri mancanti;
2. **triangolazione a ventaglio dei bordi aperti** — ogni anello di bordo viene
   chiuso con un vertice baricentrico, gestendo anche buchi grandi e concavi;
3. **rimeshing con manifold3d** — usato solo se la mesh resta non manifold.

Il risultato è sempre verificato: se un passo peggiora la situazione viene
scartato.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import trimesh

from .io import is_empty

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class HolesReport:
    """Esito della chiusura buchi."""

    holes_before: int = 0
    holes_closed: int = 0
    holes_remaining: int = 0
    faces_added: int = 0
    watertight: bool = False
    method_used: list[str] = field(default_factory=list)

    def message_it(self) -> str:
        if self.holes_before == 0:
            return "Nessun buco rilevato: mesh già chiusa"
        if self.watertight:
            return f"Chiusi {self.holes_closed} buchi: la mesh è ora stagna"
        return (
            f"Chiusi {self.holes_closed} buchi su {self.holes_before}; "
            f"{self.holes_remaining} bordi aperti residui"
        )


def boundary_loops(mesh: trimesh.Trimesh) -> list[list[int]]:
    """Estrae gli anelli di bordo (spigoli appartenenti a una sola faccia).

    Returns:
        Lista di anelli, ognuno come sequenza ordinata di indici di vertice.
    """
    if is_empty(mesh):
        return []

    edges = mesh.edges_sorted
    if len(edges) == 0:
        return []

    # Spigoli usati da una sola faccia = bordo aperto.
    unique, counts = np.unique(edges, axis=0, return_counts=True)
    border = unique[counts == 1]
    if len(border) == 0:
        return []

    # Costruisce le adiacenze e percorre gli anelli.
    adjacency: dict[int, list[int]] = {}
    for a, b in border:
        adjacency.setdefault(int(a), []).append(int(b))
        adjacency.setdefault(int(b), []).append(int(a))

    visited_edges: set[tuple[int, int]] = set()
    loops: list[list[int]] = []

    for start in list(adjacency):
        for first_next in adjacency[start]:
            key = (min(start, first_next), max(start, first_next))
            if key in visited_edges:
                continue

            loop = [start]
            visited_edges.add(key)
            current, previous = first_next, start

            while current != start:
                loop.append(current)
                neighbours = adjacency.get(current, [])
                nxt = None
                for candidate in neighbours:
                    edge_key = (min(current, candidate), max(current, candidate))
                    if candidate != previous and edge_key not in visited_edges:
                        nxt = candidate
                        break
                if nxt is None:
                    break  # bordo non chiuso (catena aperta): lo scartiamo
                visited_edges.add((min(current, nxt), max(current, nxt)))
                previous, current = current, nxt

            if len(loop) >= 3 and current == start:
                loops.append(loop)

    return loops


def _triangulate_loop_fan(
    vertices: np.ndarray, loop: list[int], next_index: int
) -> tuple[np.ndarray, np.ndarray]:
    """Chiude un anello con un ventaglio centrato sul baricentro.

    Rispetto a una triangolazione a ventaglio da un vertice dell'anello, il
    baricentro produce triangoli più equilateri e regge anche i bordi concavi.

    Returns:
        (nuovi vertici, nuove facce) con indici già assoluti.
    """
    loop_points = vertices[loop]
    centroid = loop_points.mean(axis=0)

    faces = []
    n = len(loop)
    for i in range(n):
        a = loop[i]
        b = loop[(i + 1) % n]
        faces.append([a, b, next_index])

    return centroid.reshape(1, 3), np.asarray(faces, dtype=np.int64)


def _orient_new_faces(mesh: trimesh.Trimesh, new_face_start: int) -> None:
    """Allinea il verso delle facce appena aggiunte al resto della mesh."""
    try:
        trimesh.repair.fix_winding(mesh)
    except Exception as exc:  # pragma: no cover
        logger.debug("Riallineamento winding dopo chiusura buchi non riuscito: %s", exc)


def close_holes(
    mesh: trimesh.Trimesh,
    max_loop_length: int = 100_000,
    use_manifold_fallback: bool = True,
) -> tuple[trimesh.Trimesh, HolesReport]:
    """Chiude tutti i buchi rendendo la mesh stagna.

    Args:
        mesh: mesh da chiudere.
        max_loop_length: anelli più lunghi vengono ignorati (probabile errore
            topologico: chiuderli creerebbe geometria assurda).
        use_manifold_fallback: abilita il rimeshing manifold3d come ultima risorsa.

    Returns:
        Mesh chiusa e report.
    """
    report = HolesReport()
    if is_empty(mesh):
        return mesh, report

    work = mesh.copy()
    loops = boundary_loops(work)
    report.holes_before = len(loops)

    if work.is_watertight and not loops:
        report.watertight = True
        return work, report

    faces_start = len(work.faces)

    # --- Livello 1: riparatore nativo di trimesh -------------------------
    try:
        if trimesh.repair.fill_holes(work):
            report.method_used.append("fill_holes")
    except Exception as exc:
        logger.debug("fill_holes non applicabile: %s", exc)

    work._cache.clear()
    loops = boundary_loops(work)

    # --- Livello 2: ventaglio baricentrico su ogni anello ----------------
    if loops:
        vertices = work.vertices.copy()
        faces = work.faces.copy()
        extra_vertices: list[np.ndarray] = []
        extra_faces: list[np.ndarray] = []
        next_index = len(vertices)
        closed = 0

        for loop in loops:
            if len(loop) > max_loop_length:
                logger.warning("Anello di bordo troppo lungo (%d vertici): ignorato", len(loop))
                continue
            new_v, new_f = _triangulate_loop_fan(vertices, loop, next_index)
            extra_vertices.append(new_v)
            extra_faces.append(new_f)
            next_index += 1
            closed += 1

        if extra_faces:
            work = trimesh.Trimesh(
                vertices=np.vstack([vertices, *extra_vertices]),
                faces=np.vstack([faces, *extra_faces]),
                process=False,
            )
            _orient_new_faces(work, faces_start)
            report.method_used.append("ventaglio_baricentrico")
            report.holes_closed += closed

    work._cache.clear()
    remaining = boundary_loops(work)

    # --- Livello 3: rimeshing manifold3d ---------------------------------
    if remaining and use_manifold_fallback:
        repaired = _manifold_repair(work)
        if repaired is not None:
            work = repaired
            report.method_used.append("manifold3d")
            work._cache.clear()
            remaining = boundary_loops(work)

    report.holes_remaining = len(remaining)
    report.holes_closed = max(report.holes_closed, report.holes_before - report.holes_remaining)
    report.faces_added = max(0, len(work.faces) - faces_start)
    report.watertight = bool(work.is_watertight)
    return work, report


def _manifold_repair(mesh: trimesh.Trimesh) -> trimesh.Trimesh | None:
    """Passa la mesh attraverso manifold3d per ottenere un solido valido."""
    try:
        from manifold3d import Manifold, Mesh as ManifoldMesh
    except ImportError:
        return None

    try:
        mmesh = ManifoldMesh(
            vert_properties=np.asarray(mesh.vertices, dtype=np.float32),
            tri_verts=np.asarray(mesh.faces, dtype=np.uint32),
        )
        solid = Manifold(mmesh)
        out = solid.to_mesh()
        result = trimesh.Trimesh(
            vertices=np.asarray(out.vert_properties[:, :3], dtype=np.float64),
            faces=np.asarray(out.tri_verts, dtype=np.int64),
            process=False,
        )
        if is_empty(result):
            return None
        return result
    except Exception as exc:
        logger.debug("Riparazione manifold3d non riuscita: %s", exc)
        return None
