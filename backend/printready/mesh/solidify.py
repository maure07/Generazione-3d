"""Solidificazione: da superficie a solido stampabile.

Copre tre casi che si presentano regolarmente con le mesh generate dall'AI:

* **superficie aperta** (un guscio senza spessore) → viene estrusa lungo le
  normali generando un solido con spessore controllato e bordi cuciti;
* **modello pieno da svuotare** → guscio interno per risparmiare filamento,
  con fori di drenaggio opzionali;
* **pareti troppo sottili** → ispessimento locale fino allo spessore minimo
  stampabile della stampante selezionata.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import trimesh

from .booleans import boolean_difference, boolean_union
from .holes import boundary_loops, close_holes
from .io import is_empty
from .metrics import wall_thickness

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SolidifyReport:
    """Esito della solidificazione."""

    was_open: bool = False
    extruded: bool = False
    hollowed: bool = False
    thickened_vertices: int = 0
    drain_holes: int = 0
    thickness_mm: float = 0.0
    watertight: bool = False
    volume_before_mm3: float = 0.0
    volume_after_mm3: float = 0.0

    def message_it(self) -> str:
        parti: list[str] = []
        if self.extruded:
            parti.append(f"superficie estrusa a {self.thickness_mm:.2f} mm")
        if self.thickened_vertices:
            parti.append(f"{self.thickened_vertices} vertici ispessiti")
        if self.hollowed:
            parti.append(f"modello svuotato ({self.drain_holes} fori di drenaggio)")
        if not parti:
            return "Modello già solido: nessuna modifica necessaria"
        return "Solidificazione: " + ", ".join(parti)


def _vertex_normals(mesh: trimesh.Trimesh) -> np.ndarray:
    """Normali di vertice normalizzate e prive di NaN."""
    normals = np.asarray(mesh.vertex_normals, dtype=np.float64).copy()
    lengths = np.linalg.norm(normals, axis=1)
    bad = (lengths < 1e-9) | ~np.isfinite(lengths)
    if bad.any():
        # Fallback: direzione dal baricentro verso il vertice.
        fallback = mesh.vertices[bad] - mesh.centroid
        norm = np.linalg.norm(fallback, axis=1, keepdims=True)
        norm[norm < 1e-9] = 1.0
        normals[bad] = fallback / norm
        lengths[bad] = 1.0
    return normals / lengths[:, None]


def extrude_surface(mesh: trimesh.Trimesh, thickness_mm: float) -> trimesh.Trimesh:
    """Trasforma un guscio aperto in un solido di spessore ``thickness_mm``.

    Il metodo costruisce due copie della superficie spostate di ±spessore/2
    lungo le normali di vertice, inverte quella interna e cuce i bordi aperti
    con una fascia di quadrilateri triangolati.

    Args:
        mesh: superficie (tipicamente non stagna).
        thickness_mm: spessore finale del solido, in millimetri.

    Returns:
        Il solido risultante.
    """
    if is_empty(mesh):
        return mesh

    half = float(thickness_mm) / 2.0
    normals = _vertex_normals(mesh)
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    n_v = len(vertices)

    outer = vertices + normals * half
    inner = vertices - normals * half

    # Faccia interna con winding invertito per avere normali uscenti.
    faces_outer = faces
    faces_inner = faces[:, ::-1] + n_v

    new_faces = [faces_outer, faces_inner]

    # Cucitura dei bordi aperti: banda di due triangoli per ogni spigolo.
    for loop in boundary_loops(mesh):
        n = len(loop)
        for i in range(n):
            a = loop[i]
            b = loop[(i + 1) % n]
            new_faces.append(np.array([[a, b, b + n_v], [a, b + n_v, a + n_v]], dtype=np.int64))

    result = trimesh.Trimesh(
        vertices=np.vstack([outer, inner]),
        faces=np.vstack(new_faces),
        process=False,
    )
    try:
        trimesh.repair.fix_winding(result)
        if result.is_watertight and float(result.volume) < 0:
            result.invert()
    except Exception as exc:  # pragma: no cover
        logger.debug("Riallineamento dopo estrusione fallito: %s", exc)
    return result


#: Frazione dello spessore minimo oltre la quale la deformazione locale non è
#: più lo strumento adatto: gonfiare un vertice di più di così fa ripiegare la
#: superficie su se stessa invece di ispessirla.
MAX_LOCAL_THICKENING_RATIO = 0.35

#: Tetto sui raggi lanciati per misurare lo spessore durante la correzione.
#: Il ray casting in puro Python di trimesh gestisce qualche migliaio di raggi
#: al secondo: senza un limite, una mesh densa bloccherebbe l'interfaccia per
#: minuti. Installando ``embreex`` il limite diventa ininfluente.
THICKENING_RAY_SAMPLES = 40_000


def thicken_thin_walls(
    mesh: trimesh.Trimesh, min_wall_mm: float, max_iterations: int = 2
) -> tuple[trimesh.Trimesh, int]:
    """Gonfia localmente le zone più sottili dello spessore minimo stampabile.

    Il metodo sposta i vertici lungo le normali, quindi è adatto a **correzioni
    modeste**: pareti che mancano di qualche decimo di millimetro. Uno scarto
    molto più grande (per esempio una lastra da 0,5 mm da portare a 0,8 mm)
    richiederebbe uno spostamento paragonabile alla geometria stessa e la
    superficie si ripiegherebbe; in quel caso lo spostamento viene limitato e
    ciò che resta sottile viene lasciato al rapporto di stampabilità, che
    suggerirà di riscalare il pezzo.

    Args:
        mesh: mesh chiusa da correggere.
        min_wall_mm: spessore minimo desiderato.
        max_iterations: numero massimo di passate. Ogni passata aggiunge
            materiale: due bastano nella pratica.

    Returns:
        (mesh corretta, numero di vertici spostati).
    """
    if is_empty(mesh) or min_wall_mm <= 0:
        return mesh, 0

    work = _ensure_resolution(mesh, min_wall_mm)
    moved_total = 0
    # Piccola tolleranza: senza di essa l'errore di misura del ray casting
    # tiene la condizione sempre vera e ogni passata gonfia ancora il pezzo.
    target = min_wall_mm * 0.98
    max_offset = min_wall_mm * MAX_LOCAL_THICKENING_RATIO

    for _ in range(max_iterations):
        thickness = wall_thickness(
            work, max_distance_mm=min_wall_mm * 4.0, max_samples=THICKENING_RAY_SAMPLES
        )
        thin = np.isfinite(thickness) & (thickness < target)
        if not thin.any():
            break

        deficit = np.zeros(len(work.vertices), dtype=np.float64)
        deficit[thin] = np.minimum((min_wall_mm - thickness[thin]) / 2.0, max_offset)
        normals = _vertex_normals(work)

        # Diffusione del delta sui vertici vicini per evitare gradini.
        deficit = _smooth_scalar(work, deficit, iterations=2)
        work.vertices = work.vertices + normals * deficit[:, None]
        work._cache.clear()
        moved_total += int(np.count_nonzero(thin))

    return work, moved_total


def _ensure_resolution(
    mesh: trimesh.Trimesh, target_edge_mm: float, max_faces: int = 120_000
) -> trimesh.Trimesh:
    """Suddivide la mesh se i triangoli sono troppo grandi per deformarla localmente.

    L'ispessimento sposta i vertici lungo le normali: su una geometria con pochi
    vertici radi (per esempio un parallelepipedo a 8 vertici) non esiste alcun
    vertice *dentro* la zona sottile da spostare, e il risultato sarebbe una
    deformazione globale invece di una correzione locale.

    Args:
        mesh: mesh da valutare.
        target_edge_mm: lunghezza di spigolo desiderata.
        max_faces: limite di sicurezza sul numero di facce risultanti.

    Returns:
        La mesh suddivisa, o una copia dell'originale se la suddivisione non
        serve o risulterebbe troppo pesante.
    """
    work = mesh.copy()
    if len(work.faces) == 0 or target_edge_mm <= 0:
        return work

    edge_vectors = work.vertices[work.edges_unique[:, 0]] - work.vertices[work.edges_unique[:, 1]]
    longest = float(np.linalg.norm(edge_vectors, axis=1).max()) if len(edge_vectors) else 0.0
    if longest <= target_edge_mm * 2.0:
        return work

    # Stima del costo: ogni suddivisione quadruplica le facce.
    factor = (longest / max(target_edge_mm, 1e-6)) ** 2
    if len(work.faces) * factor > max_faces:
        logger.info(
            "Suddivisione preventiva saltata: produrrebbe oltre %d facce", max_faces
        )
        return work

    try:
        subdivided = work.subdivide_to_size(max_edge=float(target_edge_mm))
    except Exception as exc:  # pragma: no cover
        logger.debug("Suddivisione preventiva fallita: %s", exc)
        return work

    if is_empty(subdivided):
        return work
    logger.debug(
        "Mesh suddivisa da %d a %d facce per l'ispessimento locale",
        len(work.faces),
        len(subdivided.faces),
    )
    return subdivided


def _smooth_scalar(mesh: trimesh.Trimesh, values: np.ndarray, iterations: int = 1) -> np.ndarray:
    """Media un campo scalare definito sui vertici lungo gli spigoli."""
    if iterations <= 0 or len(values) == 0:
        return values
    edges = mesh.edges_unique
    if len(edges) == 0:
        return values

    out = values.astype(np.float64).copy()
    counts = np.bincount(edges.ravel(), minlength=len(values)).astype(np.float64)
    counts[counts == 0] = 1.0
    for _ in range(iterations):
        acc = np.zeros_like(out)
        np.add.at(acc, edges[:, 0], out[edges[:, 1]])
        np.add.at(acc, edges[:, 1], out[edges[:, 0]])
        neigh = acc / counts
        out = np.maximum(out, 0.5 * out + 0.5 * neigh)
    return out


def hollow(
    mesh: trimesh.Trimesh,
    shell_thickness_mm: float,
    drain_holes: int = 2,
    drain_diameter_mm: float = 4.0,
) -> tuple[trimesh.Trimesh, int]:
    """Svuota un solido lasciando un guscio, con fori di drenaggio opzionali.

    Args:
        mesh: solido chiuso.
        shell_thickness_mm: spessore del guscio da mantenere.
        drain_holes: quanti fori praticare sul fondo (0 = nessuno).
        drain_diameter_mm: diametro dei fori.

    Returns:
        (mesh svuotata, numero di fori effettivamente creati).
    """
    if is_empty(mesh) or shell_thickness_mm <= 0:
        return mesh, 0
    if not mesh.is_watertight:
        logger.info("Svuotamento saltato: la mesh non è stagna")
        return mesh, 0

    inner = mesh.copy()
    normals = _vertex_normals(inner)
    inner.vertices = inner.vertices - normals * float(shell_thickness_mm)
    inner._cache.clear()

    # Se l'offset collassa (modello più sottile del guscio) non svuotiamo.
    if not inner.is_watertight or abs(float(inner.volume)) < 1e-6:
        logger.info("Svuotamento saltato: parete più sottile del guscio richiesto")
        return mesh, 0

    inner.invert()
    result = boolean_difference(mesh, [_reinvert(inner)])

    holes_made = 0
    if drain_holes > 0:
        result, holes_made = _drill_drain_holes(result, drain_holes, drain_diameter_mm)

    return result, holes_made


def _reinvert(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Ripristina l'orientamento uscente (utility per il flusso di svuotamento)."""
    copy = mesh.copy()
    copy.invert()
    return copy


def _drill_drain_holes(
    mesh: trimesh.Trimesh, count: int, diameter_mm: float
) -> tuple[trimesh.Trimesh, int]:
    """Pratica ``count`` fori cilindrici verticali sul fondo del modello."""
    bounds_min, bounds_max = mesh.bounds
    z_min = float(bounds_min[2])
    height = float(bounds_max[2] - bounds_min[2])
    center_xy = (bounds_min[:2] + bounds_max[:2]) / 2.0
    radius_ring = float(min(bounds_max[0] - bounds_min[0], bounds_max[1] - bounds_min[1])) * 0.22

    cutters: list[trimesh.Trimesh] = []
    for i in range(count):
        angle = 2.0 * np.pi * i / max(1, count)
        offset = np.array([np.cos(angle), np.sin(angle)]) * (radius_ring if count > 1 else 0.0)
        cyl = trimesh.creation.cylinder(radius=diameter_mm / 2.0, height=height * 0.5, sections=32)
        cyl.apply_translation(
            [center_xy[0] + offset[0], center_xy[1] + offset[1], z_min + height * 0.2]
        )
        cutters.append(cyl)

    if not cutters:
        return mesh, 0
    return boolean_difference(mesh, cutters), len(cutters)


def solidify(
    mesh: trimesh.Trimesh,
    thickness_mm: float = 1.6,
    min_wall_mm: float = 0.8,
    make_hollow: bool = False,
    drain_holes: int = 2,
    drain_diameter_mm: float = 4.0,
) -> tuple[trimesh.Trimesh, SolidifyReport]:
    """Punto d'ingresso della solidificazione: sceglie la strategia adatta.

    Args:
        mesh: mesh in ingresso.
        thickness_mm: spessore da usare per estrusione o guscio.
        min_wall_mm: spessore minimo stampabile (per l'ispessimento locale).
        make_hollow: se ``True`` svuota il solido finale.
        drain_holes: numero di fori di drenaggio se svuotato.
        drain_diameter_mm: diametro dei fori di drenaggio.

    Returns:
        Mesh solida e report.
    """
    report = SolidifyReport(thickness_mm=thickness_mm)
    if is_empty(mesh):
        return mesh, report

    report.volume_before_mm3 = abs(float(mesh.volume)) if mesh.is_watertight else 0.0
    work = mesh

    if not work.is_watertight:
        report.was_open = True
        loops = boundary_loops(work)
        # Molti buchi piccoli → conviene chiuderli; guscio vero → estrusione.
        if _looks_like_open_shell(work, loops):
            work = extrude_surface(work, thickness_mm)
            report.extruded = True
        else:
            work, _ = close_holes(work)

    work, moved = thicken_thin_walls(work, min_wall_mm)
    report.thickened_vertices = moved

    if make_hollow:
        work, holes = hollow(work, thickness_mm, drain_holes, drain_diameter_mm)
        report.hollowed = holes >= 0 and work is not mesh
        report.drain_holes = holes

    work._cache.clear()
    report.watertight = bool(work.is_watertight)
    report.volume_after_mm3 = abs(float(work.volume)) if work.is_watertight else 0.0
    return work, report


def _looks_like_open_shell(mesh: trimesh.Trimesh, loops: list[list[int]]) -> bool:
    """Euristica: distingue un guscio sottile da un solido con qualche buco.

    Un guscio ha pochi bordi ma molto estesi rispetto alla dimensione del
    modello; un solido bucato ha bordi corti e localizzati.
    """
    if not loops:
        return False
    diagonal = float(np.linalg.norm(mesh.extents))
    if diagonal <= 0:
        return False

    longest = 0.0
    for loop in loops:
        pts = mesh.vertices[loop]
        perimeter = float(np.linalg.norm(np.diff(np.vstack([pts, pts[:1]]), axis=0), axis=1).sum())
        longest = max(longest, perimeter)
    return longest > diagonal * 1.2
