"""Riparazione automatica: orchestrazione di tutte le correzioni di base.

`auto_repair` applica in sequenza le riparazioni non distruttive, verificando
il risultato dopo ogni passo. Se una riparazione peggiora la mesh (per esempio
riduce il volume oltre una soglia, o azzera le facce) viene annullata: meglio
un difetto noto che un modello rovinato.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import trimesh

from ..domain.enums import IssueCode, Severity
from ..domain.models import Issue
from .components import remove_floating_shells
from .dedup import remove_duplicate_faces
from .holes import close_holes
from .io import is_empty
from .normals import fix_normals
from .validate import validate_stl

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RepairReport:
    """Riepilogo delle riparazioni effettuate."""

    actions_it: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    watertight_before: bool = False
    watertight_after: bool = False
    faces_before: int = 0
    faces_after: int = 0
    reverted_steps: list[str] = field(default_factory=list)
    valid: bool = False

    def message_it(self) -> str:
        if not self.actions_it:
            return "Nessuna riparazione necessaria"
        return "Riparazione completata: " + "; ".join(self.actions_it)


def _safe_step(
    name: str,
    mesh: trimesh.Trimesh,
    func,
    report: RepairReport,
    min_volume_ratio: float = 0.5,
) -> trimesh.Trimesh:
    """Esegue un passo di riparazione annullandolo se degrada la mesh.

    Args:
        name: nome del passo (per i log e il report).
        mesh: mesh corrente.
        func: callable che riceve la mesh e ritorna ``(mesh, report_parziale)``.
        report: report cumulativo da aggiornare.
        min_volume_ratio: se il volume scende sotto questa frazione l'operazione
            viene annullata.
    """
    volume_before = abs(float(mesh.volume)) if mesh.is_watertight else 0.0
    try:
        result, sub_report = func(mesh)
    except Exception as exc:
        logger.warning("Passo di riparazione '%s' fallito: %s", name, exc)
        report.reverted_steps.append(name)
        return mesh

    if is_empty(result):
        logger.warning("Passo '%s' ha svuotato la mesh: annullato", name)
        report.reverted_steps.append(name)
        return mesh

    volume_after = abs(float(result.volume)) if result.is_watertight else 0.0
    if volume_before > 1e-6 and volume_after > 0 and volume_after < volume_before * min_volume_ratio:
        logger.warning(
            "Passo '%s' ha ridotto il volume da %.1f a %.1f mm³: annullato",
            name,
            volume_before,
            volume_after,
        )
        report.reverted_steps.append(name)
        return mesh

    message = getattr(sub_report, "message_it", None)
    if callable(message):
        text = message()
        if text and "Nessun" not in text and "già" not in text:
            report.actions_it.append(text)
    return result


def auto_repair(
    mesh: trimesh.Trimesh,
    merge_distance_mm: float = 1e-3,
    remove_floaters_ratio: float = 0.02,
    close_all_holes: bool = True,
    max_passes: int = 2,
) -> tuple[trimesh.Trimesh, RepairReport]:
    """Ripara la mesh applicando la sequenza standard di correzioni.

    Sequenza (ripetuta fino a ``max_passes`` volte, o finché la mesh non è valida):

    1. rimozione facce duplicate e degeneri, fusione vertici coincidenti;
    2. eliminazione dei gusci flottanti;
    3. chiusura dei buchi;
    4. correzione di winding e normali;
    5. validazione finale.

    Args:
        mesh: mesh da riparare.
        merge_distance_mm: tolleranza di fusione dei vertici.
        remove_floaters_ratio: soglia di volume per la rimozione dei floater.
        close_all_holes: se ``False`` salta la chiusura buchi.
        max_passes: numero massimo di iterazioni della sequenza.

    Returns:
        Mesh riparata e report.
    """
    report = RepairReport()
    if is_empty(mesh):
        report.issues.append(
            Issue(
                code=IssueCode.ZERO_VOLUME,
                severity=Severity.CRITICAL,
                message_it="Mesh vuota: impossibile riparare",
            )
        )
        return mesh, report

    work = mesh.copy()
    report.faces_before = len(work.faces)
    report.watertight_before = bool(work.is_watertight)

    for iteration in range(max_passes):
        work = _safe_step(
            "duplicati",
            work,
            lambda m: remove_duplicate_faces(m, merge_distance=merge_distance_mm),
            report,
        )
        if remove_floaters_ratio > 0:
            work = _safe_step(
                "flottanti",
                work,
                lambda m: remove_floating_shells(m, min_volume_ratio=remove_floaters_ratio),
                report,
                min_volume_ratio=0.3,
            )
        if close_all_holes:
            work = _safe_step("buchi", work, close_holes, report)
        work = _safe_step("normali", work, fix_normals, report)

        validation = validate_stl(work, check_self_intersections=False)
        if validation.valid:
            logger.debug("Mesh valida dopo %d passata/e", iteration + 1)
            break

    final_validation = validate_stl(work, check_self_intersections=False)
    report.issues = final_validation.issues
    report.valid = final_validation.valid
    report.watertight_after = final_validation.watertight
    report.faces_after = len(work.faces)

    if report.watertight_after and not report.watertight_before:
        report.actions_it.append("modello reso stagno")

    work._cache.clear()
    return work, report


def repair_scene(
    parts: dict[str, trimesh.Trimesh], **kwargs
) -> tuple[dict[str, trimesh.Trimesh], dict[str, RepairReport]]:
    """Applica ``auto_repair`` a ogni pezzo di una scena segmentata."""
    repaired: dict[str, trimesh.Trimesh] = {}
    reports: dict[str, RepairReport] = {}
    for name, part in parts.items():
        fixed, report = auto_repair(part, **kwargs)
        repaired[name] = fixed
        reports[name] = report
    return repaired, reports


def normalize_scale(
    mesh: trimesh.Trimesh, target_height_mm: float, axis: int = 2
) -> tuple[trimesh.Trimesh, float]:
    """Scala uniformemente la mesh all'altezza desiderata e la appoggia sul piano.

    Args:
        mesh: mesh da scalare.
        target_height_mm: altezza finale lungo ``axis``.
        axis: asse verticale (2 = Z).

    Returns:
        (mesh scalata, fattore di scala applicato).
    """
    if is_empty(mesh) or target_height_mm <= 0:
        return mesh, 1.0

    extents = mesh.extents
    current = float(extents[axis])
    if current <= 1e-9:
        return mesh, 1.0

    factor = float(target_height_mm) / current
    work = mesh.copy()
    work.apply_scale(factor)

    # Centra in XY e appoggia sul piano di stampa.
    bounds_min, bounds_max = work.bounds
    center_xy = (bounds_min[:2] + bounds_max[:2]) / 2.0
    work.apply_translation([-center_xy[0], -center_xy[1], -bounds_min[2]])
    work._cache.clear()
    return work, factor


def align_to_build_plate(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Trasla la mesh in modo che Z minimo sia 0, mantenendo XY centrato."""
    if is_empty(mesh):
        return mesh
    work = mesh.copy()
    bounds_min, bounds_max = work.bounds
    center_xy = (bounds_min[:2] + bounds_max[:2]) / 2.0
    work.apply_translation([-center_xy[0], -center_xy[1], -float(bounds_min[2])])
    return work
