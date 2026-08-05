"""Regole di stampabilità FDM.

Ogni regola è una funzione pura che riceve una mesh e un profilo di stampante e
restituisce l'elenco dei problemi trovati. Le regole sono registrate in
``ALL_RULES`` e possono essere estese dai plugin: aggiungere un controllo non
richiede di toccare l'analizzatore.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import trimesh

from ..domain.enums import IssueCode, Severity
from ..domain.models import Issue, PrinterProfile
from ..mesh.io import is_empty
from ..mesh.metrics import bounding_box_fits, face_wall_thickness, overhang_faces
from ..mesh.validate import count_edge_incidence, detect_self_intersections

logger = logging.getLogger(__name__)

#: Firma di una regola di stampabilità.
Rule = Callable[[trimesh.Trimesh, PrinterProfile], list[Issue]]

#: Numero massimo di raggi lanciati per il rilevamento delle pareti sottili.
#: Con 12.000 campioni l'analisi resta sotto il secondo anche su mesh dense,
#: mantenendo una copertura statistica ampiamente sufficiente.
THIN_WALL_SAMPLES = 12_000


@dataclass(slots=True)
class RuleInfo:
    """Metadati di una regola, mostrati nell'interfaccia in modalità esperto."""

    code: IssueCode
    name_it: str
    description_it: str
    auto_fixable: bool


def _locations(points: np.ndarray, limit: int = 40) -> list[tuple[float, float, float]]:
    """Campiona alcune posizioni da mostrare nell'anteprima 3D."""
    if len(points) == 0:
        return []
    if len(points) <= limit:
        selected = points
    else:
        step = max(1, len(points) // limit)
        selected = points[::step][:limit]
    return [tuple(float(v) for v in p) for p in selected]


# ---------------------------------------------------------------------------
# Regole
# ---------------------------------------------------------------------------


def rule_thin_walls(mesh: trimesh.Trimesh, printer: PrinterProfile) -> list[Issue]:
    """Pareti più sottili del minimo estrudibile: non verrebbero stampate."""
    if is_empty(mesh):
        return []

    minimum = printer.min_printable_wall
    # Il ray casting è la misura più costosa dell'analisi: per *rilevare* le
    # pareti sottili basta un campione, e l'analisi viene ripetuta dopo ogni
    # correzione automatica. La misura completa resta nel correttore.
    thickness = face_wall_thickness(
        mesh, max_distance_mm=minimum * 5.0, max_samples=THIN_WALL_SAMPLES
    )
    measurable = np.isfinite(thickness)
    if not measurable.any():
        return []

    thin = measurable & (thickness < minimum)
    count = int(np.count_nonzero(thin))
    if count == 0:
        return []

    # Proporzione dell'area interessata sul campione misurato: distingue un
    # artefatto isolato da un problema diffuso.
    areas = np.asarray(mesh.area_faces)
    ratio = float(areas[thin].sum() / max(areas[measurable].sum(), 1e-9))
    severity = Severity.ERROR if ratio > 0.02 else Severity.WARNING
    thinnest = float(thickness[thin].min())

    return [
        Issue(
            code=IssueCode.THIN_WALL,
            severity=severity,
            message_it=(
                f"{count} zone con parete sotto {minimum:.2f} mm "
                f"(minimo rilevato {thinnest:.2f} mm, {ratio * 100:.1f}% della superficie)"
            ),
            count=count,
            locations=_locations(np.asarray(mesh.triangles_center)[thin]),
        )
    ]


def rule_overhangs(mesh: trimesh.Trimesh, printer: PrinterProfile) -> list[Issue]:
    """Sbalzi oltre l'angolo stampabile: richiedono supporti."""
    if is_empty(mesh):
        return []

    mask = overhang_faces(mesh, printer.max_overhang_deg)
    if not mask.any():
        return []

    # Le facce appoggiate sul piatto guardano verso il basso ma non sono
    # sbalzi: sono la base del pezzo. Senza questa esclusione ogni modello
    # con un fondo piatto risulterebbe difettoso.
    z_min = float(mesh.bounds[0][2])
    on_plate = np.abs(np.asarray(mesh.triangles_center)[:, 2] - z_min) <= max(
        printer.layer_height_mm, 0.05
    )
    mask = mask & ~on_plate

    count = int(np.count_nonzero(mask))
    if count == 0:
        return []

    areas = np.asarray(mesh.area_faces)
    ratio = float(areas[mask].sum() / max(areas.sum(), 1e-9))
    if ratio < 0.01:
        return []

    severity = Severity.WARNING if ratio < 0.15 else Severity.ERROR
    return [
        Issue(
            code=IssueCode.OVERHANG,
            severity=severity,
            message_it=(
                f"{ratio * 100:.1f}% della superficie supera i {printer.max_overhang_deg:.0f}° "
                "di sbalzo: servono supporti o una diversa orientazione"
            ),
            count=count,
            locations=_locations(np.asarray(mesh.triangles_center)[mask]),
        )
    ]


def rule_islands(mesh: trimesh.Trimesh, printer: PrinterProfile) -> list[Issue]:
    """Isole: porzioni che iniziano a mezz'aria senza nulla sotto.

    Si campiona il modello a strati e si cerca il materiale che compare a una
    quota senza avere alcun appoggio nello strato precedente.
    """
    if is_empty(mesh):
        return []

    z_min, z_max = float(mesh.bounds[0][2]), float(mesh.bounds[1][2])
    height = z_max - z_min
    if height <= printer.layer_height_mm * 4:
        return []

    steps = int(min(120, max(12, height / max(printer.layer_height_mm * 8, 0.1))))
    heights = np.linspace(z_min + height * 0.02, z_max - height * 0.02, steps)

    previous_polygons = None
    islands: list[tuple[float, float, float]] = []

    for z in heights:
        try:
            section = mesh.section(plane_origin=[0, 0, float(z)], plane_normal=[0, 0, 1])
            if section is None:
                previous_polygons = None
                continue
            planar, transform = section.to_2D()
            polygons = planar.polygons_full
        except Exception:  # pragma: no cover - sezione degenere
            previous_polygons = None
            continue

        if polygons is None:
            previous_polygons = None
            continue

        if previous_polygons:
            for polygon in polygons:
                # Un'isola non tocca nessuna area dello strato inferiore.
                if not any(polygon.intersects(prev) for prev in previous_polygons):
                    centroid = polygon.centroid
                    point = np.array([centroid.x, centroid.y, 0.0, 1.0])
                    world = (transform @ point)[:3]
                    islands.append(tuple(float(v) for v in world))

        previous_polygons = list(polygons)

    if not islands:
        return []

    return [
        Issue(
            code=IssueCode.ISLAND,
            severity=Severity.ERROR,
            message_it=(
                f"{len(islands)} isole iniziano a mezz'aria: senza supporti "
                "il materiale cadrebbe sul piatto"
            ),
            count=len(islands),
            locations=islands[:40],
        )
    ]


def rule_open_surfaces(mesh: trimesh.Trimesh, printer: PrinterProfile) -> list[Issue]:
    """Superfici aperte: lo slicer non sa cosa è dentro e cosa è fuori."""
    if is_empty(mesh):
        return []
    open_edges, _ = count_edge_incidence(mesh)
    if open_edges == 0:
        return []
    return [
        Issue(
            code=IssueCode.OPEN_SURFACE,
            severity=Severity.CRITICAL,
            message_it=f"{open_edges} spigoli aperti: il modello non è un solido chiuso",
            count=open_edges,
        )
    ]


def rule_non_manifold(mesh: trimesh.Trimesh, printer: PrinterProfile) -> list[Issue]:
    """Spigoli condivisi da più di due facce: geometria ambigua."""
    if is_empty(mesh):
        return []
    _, non_manifold = count_edge_incidence(mesh)
    if non_manifold == 0:
        return []
    return [
        Issue(
            code=IssueCode.NON_MANIFOLD,
            severity=Severity.ERROR,
            message_it=(
                f"{non_manifold} spigoli non manifold: alcuni slicer rifiutano il file"
            ),
            count=non_manifold,
        )
    ]


def rule_inverted_normals(mesh: trimesh.Trimesh, printer: PrinterProfile) -> list[Issue]:
    """Normali invertite: pareti mancanti o riempimento all'esterno."""
    if is_empty(mesh):
        return []

    issues: list[Issue] = []
    if not mesh.is_winding_consistent:
        issues.append(
            Issue(
                code=IssueCode.INVERTED_NORMALS,
                severity=Severity.ERROR,
                message_it="Orientamento delle facce incoerente fra triangoli adiacenti",
            )
        )
    if mesh.is_watertight and float(mesh.volume) < 0:
        issues.append(
            Issue(
                code=IssueCode.INVERTED_NORMALS,
                severity=Severity.ERROR,
                message_it="Volume negativo: l'intera mesh è rivolta verso l'interno",
            )
        )
    return issues


def rule_self_intersections(mesh: trimesh.Trimesh, printer: PrinterProfile) -> list[Issue]:
    """Compenetrazioni fra triangoli dello stesso pezzo."""
    if is_empty(mesh):
        return []
    count = detect_self_intersections(mesh)
    if count == 0:
        return []
    return [
        Issue(
            code=IssueCode.SELF_INTERSECTION,
            severity=Severity.WARNING,
            message_it=(
                f"{count} coppie di triangoli si compenetrano: possibili errori di slicing"
            ),
            count=count,
        )
    ]


def rule_floating_shells(mesh: trimesh.Trimesh, printer: PrinterProfile) -> list[Issue]:
    """Gusci staccati dal corpo principale."""
    if is_empty(mesh):
        return []
    try:
        components = mesh.split(only_watertight=False)
    except Exception:  # pragma: no cover
        return []
    if len(components) <= 1:
        return []
    return [
        Issue(
            code=IssueCode.FLOATING_SHELL,
            severity=Severity.WARNING,
            message_it=(
                f"Il pezzo contiene {len(components)} gusci separati: "
                "verificare che siano tutti voluti"
            ),
            count=len(components),
        )
    ]


def rule_size(mesh: trimesh.Trimesh, printer: PrinterProfile) -> list[Issue]:
    """Il pezzo entra nel volume di stampa?"""
    if is_empty(mesh):
        return []
    if bounding_box_fits(mesh, printer.bed_size_mm):
        return []
    extents = tuple(round(float(v), 1) for v in mesh.extents)
    return [
        Issue(
            code=IssueCode.OVERSIZED,
            severity=Severity.ERROR,
            message_it=(
                f"Il pezzo misura {extents[0]}×{extents[1]}×{extents[2]} mm e non entra "
                f"nel piatto {printer.bed_size_mm}: ridurre la scala o dividerlo"
            ),
        )
    ]


def rule_tiny_features(mesh: trimesh.Trimesh, printer: PrinterProfile) -> list[Issue]:
    """Dettagli sotto la risoluzione della stampante: andrebbero persi."""
    if is_empty(mesh):
        return []

    minimum = printer.min_feature_mm
    try:
        components = mesh.split(only_watertight=False)
    except Exception:  # pragma: no cover
        return []

    tiny = [c for c in components if float(np.max(c.extents)) < minimum]
    if not tiny:
        return []

    return [
        Issue(
            code=IssueCode.UNPRINTABLE_FEATURE,
            severity=Severity.WARNING,
            message_it=(
                f"{len(tiny)} dettagli più piccoli di {minimum:.2f} mm: "
                "non sarebbero riprodotti dall'ugello"
            ),
            count=len(tiny),
            locations=_locations(np.array([c.bounds.mean(axis=0) for c in tiny])),
        )
    ]


#: Registro delle regole attive. I plugin possono aggiungerne altre.
ALL_RULES: dict[IssueCode, Rule] = {
    IssueCode.OPEN_SURFACE: rule_open_surfaces,
    IssueCode.NON_MANIFOLD: rule_non_manifold,
    IssueCode.INVERTED_NORMALS: rule_inverted_normals,
    IssueCode.SELF_INTERSECTION: rule_self_intersections,
    IssueCode.FLOATING_SHELL: rule_floating_shells,
    IssueCode.THIN_WALL: rule_thin_walls,
    IssueCode.OVERHANG: rule_overhangs,
    IssueCode.ISLAND: rule_islands,
    IssueCode.OVERSIZED: rule_size,
    IssueCode.UNPRINTABLE_FEATURE: rule_tiny_features,
}


#: Descrizioni per l'interfaccia (modalità esperto).
RULE_INFO: list[RuleInfo] = [
    RuleInfo(IssueCode.OPEN_SURFACE, "Superfici aperte", "Il solido deve essere chiuso", True),
    RuleInfo(IssueCode.NON_MANIFOLD, "Geometria non manifold", "Spigoli con più di due facce", True),
    RuleInfo(IssueCode.INVERTED_NORMALS, "Normali invertite", "Facce rivolte verso l'interno", True),
    RuleInfo(
        IssueCode.SELF_INTERSECTION,
        "Autointersezioni",
        "Triangoli dello stesso pezzo che si compenetrano",
        True,
    ),
    RuleInfo(IssueCode.FLOATING_SHELL, "Mesh flottanti", "Gusci staccati dal corpo", True),
    RuleInfo(IssueCode.THIN_WALL, "Pareti sottili", "Sotto lo spessore minimo estrudibile", True),
    RuleInfo(IssueCode.OVERHANG, "Sbalzi eccessivi", "Oltre l'angolo stampabile senza supporti", True),
    RuleInfo(IssueCode.ISLAND, "Isole", "Materiale che inizia a mezz'aria", False),
    RuleInfo(IssueCode.OVERSIZED, "Fuori volume", "Il pezzo non entra nel piatto", True),
    RuleInfo(
        IssueCode.UNPRINTABLE_FEATURE,
        "Dettagli non stampabili",
        "Sotto la risoluzione dell'ugello",
        True,
    ),
]


def register_rule(code: IssueCode, rule: Rule) -> None:
    """Aggiunge o sostituisce una regola (punto di aggancio per i plugin)."""
    ALL_RULES[code] = rule
    logger.info("Regola di stampabilità registrata: %s", code.value)
