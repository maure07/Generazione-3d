"""Controllo errori STL: verifica formale della validità del solido.

Questo modulo **non corregge** nulla: produce l'elenco dei difetti presenti,
che la pipeline usa per decidere se ripetere la riparazione o segnalare il
problema all'utente. Le correzioni vivono in ``repair.py`` e
``printability/autofix.py``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import trimesh

from ..domain.enums import IssueCode, Severity
from ..domain.models import Issue
from .io import is_empty

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ValidationReport:
    """Esito completo della validazione di un solido."""

    valid: bool = False
    watertight: bool = False
    winding_consistent: bool = False
    positive_volume: bool = False
    manifold_edges: bool = False
    no_self_intersections: bool = True
    open_edges: int = 0
    non_manifold_edges: int = 0
    duplicate_faces: int = 0
    degenerate_faces: int = 0
    components: int = 1
    volume_mm3: float = 0.0
    issues: list[Issue] = field(default_factory=list)

    def message_it(self) -> str:
        if self.valid:
            return "STL valido: solido chiuso, manifold e orientato correttamente"
        problemi = [issue.label_it for issue in self.issues if issue.severity != Severity.INFO]
        if not problemi:
            return "STL con anomalie minori"
        return "Problemi rilevati: " + ", ".join(dict.fromkeys(problemi))


def count_edge_incidence(mesh: trimesh.Trimesh) -> tuple[int, int]:
    """Conta gli spigoli aperti (1 faccia) e non manifold (>2 facce).

    Returns:
        (spigoli_aperti, spigoli_non_manifold)
    """
    if is_empty(mesh):
        return 0, 0
    edges = mesh.edges_sorted
    if len(edges) == 0:
        return 0, 0
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return int(np.count_nonzero(counts == 1)), int(np.count_nonzero(counts > 2))


def count_duplicate_faces(mesh: trimesh.Trimesh) -> int:
    """Numero di facce ripetute (stessa terna di vertici, in qualunque ordine)."""
    if is_empty(mesh):
        return 0
    canonical = np.sort(mesh.faces, axis=1)
    unique = np.unique(canonical, axis=0)
    return int(len(canonical) - len(unique))


def count_degenerate_faces(mesh: trimesh.Trimesh, epsilon: float = 1e-10) -> int:
    """Numero di triangoli con area praticamente nulla."""
    if is_empty(mesh):
        return 0
    return int(np.count_nonzero(mesh.area_faces <= epsilon))


def detect_self_intersections(mesh: trimesh.Trimesh, max_faces: int = 300_000) -> int:
    """Rileva le autointersezioni fra triangoli non adiacenti.

    Usa l'indice spaziale per trovare le coppie di triangoli i cui volumi di
    ingombro si sovrappongono, scarta quelle che condividono un vertice (sono
    adiacenti, non compenetrate) e conferma le restanti con il test di
    intersezione triangolo-triangolo di Möller, valutato in forma vettoriale.

    Su mesh oltre ``max_faces`` il controllo viene saltato e la cosa è
    registrata nel log: meglio nessun risultato che un blocco dell'interfaccia.
    """
    if is_empty(mesh):
        return 0
    if len(mesh.faces) > max_faces:
        logger.info(
            "Controllo autointersezioni saltato: mesh troppo densa (%d facce)", len(mesh.faces)
        )
        return 0

    try:
        pairs = _candidate_pairs(mesh)
    except Exception as exc:  # pragma: no cover
        logger.debug("Ricerca coppie candidate fallita: %s", exc)
        return 0

    if len(pairs) == 0:
        return 0

    faces = np.asarray(mesh.faces)
    # Scarto vettoriale delle coppie adiacenti: due triangoli che condividono
    # anche un solo vertice si toccano per costruzione.
    a_faces = faces[pairs[:, 0]]
    b_faces = faces[pairs[:, 1]]
    shares = np.zeros(len(pairs), dtype=bool)
    for column in range(3):
        shares |= (a_faces[:, column][:, None] == b_faces).any(axis=1)

    pairs = pairs[~shares]
    if len(pairs) == 0:
        return 0

    triangles = mesh.vertices[faces]
    return int(np.count_nonzero(_triangles_intersect_batch(triangles[pairs[:, 0]], triangles[pairs[:, 1]])))


def _candidate_pairs(mesh: trimesh.Trimesh, limit: int = 200_000) -> np.ndarray:
    """Coppie di facce con AABB sovrapposti (candidate all'intersezione).

    Usa l'indice R-tree quando disponibile; altrimenti ricade su una griglia
    hash spaziale in numpy, così il controllo funziona anche senza ``rtree``.
    """
    bounds = np.hstack([mesh.triangles.min(axis=1), mesh.triangles.max(axis=1)])
    try:
        tree = mesh.triangles_tree
    except Exception as exc:
        logger.debug("R-tree non disponibile (%s): uso la griglia spaziale", exc)
        return _candidate_pairs_grid(mesh, bounds, limit)

    pairs: set[tuple[int, int]] = set()
    for i, bound in enumerate(bounds):
        for j in tree.intersection(bound):
            j = int(j)
            if j <= i:
                continue
            pairs.add((i, j))
            if len(pairs) >= limit:
                return np.array(sorted(pairs), dtype=np.int64)
    if not pairs:
        return np.zeros((0, 2), dtype=np.int64)
    return np.array(sorted(pairs), dtype=np.int64)


def _candidate_pairs_grid(mesh: trimesh.Trimesh, bounds: np.ndarray, limit: int) -> np.ndarray:
    """Riserva senza R-tree: hashing su griglia uniforme dei bounding box.

    La dimensione della cella è la mediana dell'estensione dei triangoli, così
    ogni triangolo ricade in poche celle e il costo resta lineare.
    """
    sizes = bounds[:, 3:] - bounds[:, :3]
    cell = float(np.median(sizes.max(axis=1)))
    if cell <= 1e-9:
        return np.zeros((0, 2), dtype=np.int64)

    buckets: dict[tuple[int, int, int], list[int]] = {}
    lo = np.floor(bounds[:, :3] / cell).astype(np.int64)
    hi = np.floor(bounds[:, 3:] / cell).astype(np.int64)

    for i in range(len(bounds)):
        for x in range(lo[i, 0], hi[i, 0] + 1):
            for y in range(lo[i, 1], hi[i, 1] + 1):
                for z in range(lo[i, 2], hi[i, 2] + 1):
                    buckets.setdefault((x, y, z), []).append(i)

    pairs: set[tuple[int, int]] = set()
    for members in buckets.values():
        if len(members) < 2:
            continue
        for a_idx in range(len(members)):
            for b_idx in range(a_idx + 1, len(members)):
                a, b = members[a_idx], members[b_idx]
                if a > b:
                    a, b = b, a
                # Conferma la sovrapposizione reale degli AABB.
                if (bounds[a, 3:] < bounds[b, :3]).any() or (bounds[b, 3:] < bounds[a, :3]).any():
                    continue
                pairs.add((a, b))
                if len(pairs) >= limit:
                    return np.array(sorted(pairs), dtype=np.int64)

    if not pairs:
        return np.zeros((0, 2), dtype=np.int64)
    return np.array(sorted(pairs), dtype=np.int64)


def _triangles_intersect_batch(
    t1: np.ndarray, t2: np.ndarray, eps: float = 1e-9
) -> np.ndarray:
    """Test di intersezione triangolo-triangolo (Möller) su interi array.

    Args:
        t1: array ``(N, 3, 3)`` dei primi triangoli di ogni coppia.
        t2: array ``(N, 3, 3)`` dei secondi triangoli.
        eps: tolleranza numerica.

    Returns:
        Maschera booleana ``(N,)``: ``True`` dove i due triangoli si compenetrano.
    """
    n = len(t1)
    if n == 0:
        return np.zeros(0, dtype=bool)

    # Distanze dei vertici di t1 dal piano di t2 e viceversa.
    normal2 = np.cross(t2[:, 1] - t2[:, 0], t2[:, 2] - t2[:, 0])
    offset2 = -np.einsum("ij,ij->i", normal2, t2[:, 0])
    dist1 = np.einsum("nij,nj->ni", t1, normal2) + offset2[:, None]

    normal1 = np.cross(t1[:, 1] - t1[:, 0], t1[:, 2] - t1[:, 0])
    offset1 = -np.einsum("ij,ij->i", normal1, t1[:, 0])
    dist2 = np.einsum("nij,nj->ni", t2, normal1) + offset1[:, None]

    # Se un triangolo sta tutto da un lato del piano dell'altro, niente contatto.
    alive = ~(
        (dist1 > eps).all(axis=1)
        | (dist1 < -eps).all(axis=1)
        | (dist2 > eps).all(axis=1)
        | (dist2 < -eps).all(axis=1)
    )
    if not alive.any():
        return np.zeros(n, dtype=bool)

    direction = np.cross(normal1, normal2)
    axis = np.argmax(np.abs(direction), axis=1)
    axis_magnitude = np.abs(direction[np.arange(n), axis])
    # Triangoli complanari: non li trattiamo come compenetrazione (sono
    # duplicati o facce sovrapposte, già coperti da un altro controllo).
    alive &= axis_magnitude >= eps
    if not alive.any():
        return np.zeros(n, dtype=bool)

    interval1 = _plane_interval_batch(t1, dist1, axis)
    interval2 = _plane_interval_batch(t2, dist2, axis)

    valid = alive & np.isfinite(interval1).all(axis=1) & np.isfinite(interval2).all(axis=1)
    overlap = ~(
        (interval1[:, 1] < interval2[:, 0] - eps) | (interval2[:, 1] < interval1[:, 0] - eps)
    )
    return valid & overlap


def _plane_interval_batch(
    tri: np.ndarray, dist: np.ndarray, axis: np.ndarray
) -> np.ndarray:
    """Intervalli di intersezione dei triangoli con la retta comune ai due piani.

    Returns:
        Array ``(N, 2)`` con minimo e massimo; ``±inf`` dove l'intervallo non è
        definito (nessuno spigolo attraversa il piano).
    """
    n = len(tri)
    projection = tri[np.arange(n)[:, None], np.arange(3)[None, :], axis[:, None]]

    lows = np.full(n, np.inf)
    highs = np.full(n, -np.inf)

    for a, b in ((0, 1), (1, 2), (2, 0)):
        da, db = dist[:, a], dist[:, b]

        # Spigolo che attraversa il piano: punto di taglio interpolato.
        crossing = da * db < 0
        if crossing.any():
            denominator = da[crossing] - db[crossing]
            ratio = np.divide(
                da[crossing],
                denominator,
                out=np.zeros_like(denominator),
                where=np.abs(denominator) > 1e-15,
            )
            value = projection[crossing, a] + ratio * (
                projection[crossing, b] - projection[crossing, a]
            )
            lows[crossing] = np.minimum(lows[crossing], value)
            highs[crossing] = np.maximum(highs[crossing], value)

        # Vertice esattamente sul piano.
        touching = np.abs(da) < 1e-12
        if touching.any():
            value = projection[touching, a]
            lows[touching] = np.minimum(lows[touching], value)
            highs[touching] = np.maximum(highs[touching], value)

    return np.stack([lows, highs], axis=1)


def validate_stl(
    mesh: trimesh.Trimesh, check_self_intersections: bool = True
) -> ValidationReport:
    """Esegue tutti i controlli formali su una mesh destinata alla stampa.

    Args:
        mesh: mesh da validare.
        check_self_intersections: il test è il più costoso; può essere disattivato.

    Returns:
        Report con l'elenco dei difetti sotto forma di ``Issue``.
    """
    report = ValidationReport()
    if is_empty(mesh):
        report.issues.append(
            Issue(
                code=IssueCode.ZERO_VOLUME,
                severity=Severity.CRITICAL,
                message_it="La mesh è vuota: nessuna geometria da stampare",
            )
        )
        return report

    open_edges, non_manifold = count_edge_incidence(mesh)
    report.open_edges = open_edges
    report.non_manifold_edges = non_manifold
    report.duplicate_faces = count_duplicate_faces(mesh)
    report.degenerate_faces = count_degenerate_faces(mesh)
    report.watertight = bool(mesh.is_watertight)
    report.winding_consistent = bool(mesh.is_winding_consistent)
    report.manifold_edges = non_manifold == 0
    report.volume_mm3 = float(abs(mesh.volume)) if report.watertight else 0.0
    report.positive_volume = report.volume_mm3 > 1e-6

    try:
        report.components = int(len(mesh.split(only_watertight=False)))
    except Exception:  # pragma: no cover
        report.components = 1

    if open_edges:
        report.issues.append(
            Issue(
                code=IssueCode.OPEN_SURFACE,
                severity=Severity.ERROR,
                message_it=f"{open_edges} spigoli di bordo aperti: il solido non è chiuso",
                count=open_edges,
            )
        )
    if non_manifold:
        report.issues.append(
            Issue(
                code=IssueCode.NON_MANIFOLD,
                severity=Severity.ERROR,
                message_it=f"{non_manifold} spigoli condivisi da più di due facce",
                count=non_manifold,
            )
        )
    if report.duplicate_faces:
        report.issues.append(
            Issue(
                code=IssueCode.DUPLICATE_FACE,
                severity=Severity.WARNING,
                message_it=f"{report.duplicate_faces} facce duplicate",
                count=report.duplicate_faces,
            )
        )
    if report.degenerate_faces:
        report.issues.append(
            Issue(
                code=IssueCode.DEGENERATE_FACE,
                severity=Severity.WARNING,
                message_it=f"{report.degenerate_faces} facce degeneri (area nulla)",
                count=report.degenerate_faces,
            )
        )
    if report.watertight and not report.winding_consistent:
        report.issues.append(
            Issue(
                code=IssueCode.INVERTED_NORMALS,
                severity=Severity.ERROR,
                message_it="Orientamento delle facce incoerente: normali da correggere",
            )
        )
    if report.watertight and float(mesh.volume) < 0:
        report.issues.append(
            Issue(
                code=IssueCode.INVERTED_NORMALS,
                severity=Severity.ERROR,
                message_it="Volume negativo: la mesh è rivolta verso l'interno",
            )
        )
    if report.watertight and not report.positive_volume:
        report.issues.append(
            Issue(
                code=IssueCode.ZERO_VOLUME,
                severity=Severity.CRITICAL,
                message_it="Volume nullo: la geometria non è un solido stampabile",
            )
        )
    if report.components > 1:
        report.issues.append(
            Issue(
                code=IssueCode.FLOATING_SHELL,
                severity=Severity.INFO,
                message_it=f"{report.components} gusci separati presenti nel modello",
                count=report.components,
            )
        )

    if check_self_intersections:
        intersections = detect_self_intersections(mesh)
        report.no_self_intersections = intersections == 0
        if intersections:
            report.issues.append(
                Issue(
                    code=IssueCode.SELF_INTERSECTION,
                    severity=Severity.ERROR,
                    message_it=f"{intersections} coppie di triangoli si compenetrano",
                    count=intersections,
                )
            )

    report.valid = (
        report.watertight
        and report.winding_consistent
        and report.manifold_edges
        and report.positive_volume
        and report.duplicate_faces == 0
        and report.degenerate_faces == 0
    )
    return report
