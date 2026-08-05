"""Correzione automatica dei problemi di stampabilità.

Ogni correttore è associato a un codice di problema e viene applicato solo se il
problema è stato effettivamente rilevato. Dopo ogni correzione l'analisi viene
ripetuta: se il punteggio non migliora, la modifica viene **annullata**. Questo
principio — mai peggiorare — è ciò che rende sicuro l'automatismo.

Alcuni problemi non sono correggibili in automatico senza cambiare l'intento
dell'utente (le isole richiedono supporti, decisione dello slicer): in quei casi
il correttore documenta il motivo invece di intervenire.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import trimesh

from ..domain.enums import IssueCode, Severity
from ..domain.models import Issue, PrinterProfile
from ..mesh.components import remove_floating_shells
from ..mesh.dedup import remove_duplicate_faces
from ..mesh.holes import close_holes
from ..mesh.io import is_empty
from ..mesh.normals import fix_normals
from ..mesh.repair import auto_repair
from ..mesh.solidify import thicken_thin_walls
from .analyzer import PrintabilityAnalyzer, PrintabilityReport

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AutoFixReport:
    """Esito delle correzioni automatiche."""

    applied_it: list[str] = field(default_factory=list)
    reverted_it: list[str] = field(default_factory=list)
    not_fixable_it: list[str] = field(default_factory=list)
    score_before: float = 0.0
    score_after: float = 0.0
    issues_before: int = 0
    issues_after: int = 0

    @property
    def improvement(self) -> float:
        return self.score_after - self.score_before

    def message_it(self) -> str:
        if not self.applied_it:
            return f"Nessuna correzione applicata (punteggio {self.score_after:.0f}/100)"
        return (
            f"Correzioni applicate: {len(self.applied_it)}; "
            f"punteggio da {self.score_before:.0f} a {self.score_after:.0f}/100"
        )


class AutoFixer:
    """Applica le correzioni automatiche a una mesh."""

    def __init__(self, printer: PrinterProfile | None = None) -> None:
        self.printer = printer or PrinterProfile()
        self.analyzer = PrintabilityAnalyzer(self.printer)

    def fix(
        self, mesh: trimesh.Trimesh, report: PrintabilityReport | None = None
    ) -> tuple[trimesh.Trimesh, AutoFixReport]:
        """Corregge i problemi rilevati, annullando ciò che non migliora.

        Args:
            mesh: mesh da correggere.
            report: analisi già eseguita; se assente viene calcolata.

        Returns:
            (mesh corretta, rapporto delle correzioni).
        """
        result = AutoFixReport()
        if is_empty(mesh):
            result.not_fixable_it.append("Mesh vuota: nulla da correggere")
            return mesh, result

        current = mesh
        analysis = report or self.analyzer.analyze(mesh)
        result.score_before = analysis.score
        result.issues_before = len(analysis.issues)

        codes = {issue.code for issue in analysis.issues}

        for code, (name_it, fixer) in self._fixers().items():
            if code not in codes:
                continue

            candidate = self._try(fixer, current, name_it, result)
            if candidate is None:
                continue

            new_analysis = self.analyzer.analyze(candidate)
            if new_analysis.score >= analysis.score:
                current = candidate
                analysis = new_analysis
                result.applied_it.append(name_it)
            else:
                result.reverted_it.append(
                    f"{name_it} (avrebbe peggiorato il punteggio da "
                    f"{analysis.score:.0f} a {new_analysis.score:.0f})"
                )

        for issue in analysis.issues:
            if issue.code == IssueCode.ISLAND:
                result.not_fixable_it.append(
                    "Isole: vanno gestite con i supporti dello slicer o "
                    "riorientando il pezzo, non modificando la geometria"
                )
            elif issue.code == IssueCode.OVERSIZED:
                result.not_fixable_it.append(
                    "Pezzo fuori volume: ridurre la scala o aumentare la segmentazione"
                )

        result.score_after = analysis.score
        result.issues_after = len(analysis.issues)
        current._cache.clear()
        logger.info("Correzione automatica: %s", result.message_it())
        return current, result

    # -- singoli correttori ------------------------------------------------

    def _fixers(self):
        """Mappa codice → (etichetta italiana, funzione correttiva).

        L'ordine conta: prima si chiude e si ripara la topologia, poi si agisce
        sulla forma. Correggere lo spessore di una mesh aperta non ha senso.
        """
        return {
            IssueCode.DUPLICATE_FACE: ("Rimozione facce duplicate", self._fix_duplicates),
            IssueCode.OPEN_SURFACE: ("Chiusura delle superfici aperte", self._fix_open),
            IssueCode.NON_MANIFOLD: ("Riparazione geometria non manifold", self._fix_manifold),
            IssueCode.INVERTED_NORMALS: ("Correzione delle normali", self._fix_normals),
            IssueCode.FLOATING_SHELL: ("Rimozione delle mesh flottanti", self._fix_floaters),
            IssueCode.SELF_INTERSECTION: (
                "Risoluzione delle autointersezioni",
                self._fix_self_intersections,
            ),
            IssueCode.THIN_WALL: ("Ispessimento delle pareti sottili", self._fix_thin_walls),
            IssueCode.UNPRINTABLE_FEATURE: (
                "Rimozione dei dettagli non stampabili",
                self._fix_tiny_features,
            ),
            IssueCode.OVERHANG: ("Riorientamento per ridurre gli sbalzi", self._fix_overhangs),
        }

    def _try(self, fixer, mesh: trimesh.Trimesh, name_it: str, result: AutoFixReport):
        """Esegue un correttore isolando le eccezioni."""
        try:
            return fixer(mesh)
        except Exception as exc:
            logger.warning("Correttore '%s' fallito: %s", name_it, exc)
            result.reverted_it.append(f"{name_it} (errore: {exc})")
            return None

    def _fix_duplicates(self, mesh: trimesh.Trimesh) -> trimesh.Trimesh:
        fixed, _ = remove_duplicate_faces(mesh)
        return fixed

    def _fix_open(self, mesh: trimesh.Trimesh) -> trimesh.Trimesh:
        fixed, _ = close_holes(mesh)
        return fixed

    def _fix_manifold(self, mesh: trimesh.Trimesh) -> trimesh.Trimesh:
        fixed, _ = auto_repair(mesh, max_passes=2)
        return fixed

    def _fix_normals(self, mesh: trimesh.Trimesh) -> trimesh.Trimesh:
        fixed, _ = fix_normals(mesh, force_outward=True)
        return fixed

    def _fix_floaters(self, mesh: trimesh.Trimesh) -> trimesh.Trimesh:
        fixed, _ = remove_floating_shells(mesh, min_volume_ratio=0.02)
        return fixed

    def _fix_self_intersections(self, mesh: trimesh.Trimesh) -> trimesh.Trimesh:
        """Le autointersezioni si risolvono facendo ricalcolare il solido a manifold3d."""
        from ..mesh.holes import _manifold_repair

        repaired = _manifold_repair(mesh)
        return repaired if repaired is not None else mesh

    def _fix_thin_walls(self, mesh: trimesh.Trimesh) -> trimesh.Trimesh:
        fixed, _ = thicken_thin_walls(mesh, self.printer.min_printable_wall)
        return fixed

    def _fix_tiny_features(self, mesh: trimesh.Trimesh) -> trimesh.Trimesh:
        """Elimina i frammenti sotto la risoluzione dell'ugello."""
        try:
            components = mesh.split(only_watertight=False)
        except Exception:  # pragma: no cover
            return mesh
        if len(components) <= 1:
            return mesh

        minimum = self.printer.min_feature_mm
        keep = [c for c in components if float(np.max(c.extents)) >= minimum]
        if not keep or len(keep) == len(components):
            return mesh
        if len(keep) == 1:
            return keep[0]
        return trimesh.util.concatenate(keep)

    def _fix_overhangs(self, mesh: trimesh.Trimesh) -> trimesh.Trimesh:
        """Cerca l'orientamento che riduce la superficie in sbalzo.

        Si provano rotazioni attorno a X e Y e si sceglie quella con la minor
        area in sbalzo, purché il pezzo resti appoggiato stabilmente.
        """
        from ..mesh.metrics import overhang_faces

        best_mesh = mesh
        best_area = _overhang_area(mesh, self.printer.max_overhang_deg)
        if best_area <= 0:
            return mesh

        for axis in ([1.0, 0.0, 0.0], [0.0, 1.0, 0.0]):
            for degrees in (90.0, 180.0, 270.0):
                candidate = mesh.copy()
                rotation = trimesh.transformations.rotation_matrix(
                    np.radians(degrees), axis, mesh.centroid
                )
                candidate.apply_transform(rotation)
                # Riappoggia sul piatto.
                candidate.apply_translation([0.0, 0.0, -float(candidate.bounds[0][2])])
                area = _overhang_area(candidate, self.printer.max_overhang_deg)
                if area < best_area * 0.75:  # miglioramento significativo
                    best_area, best_mesh = area, candidate

        return best_mesh


def _overhang_area(mesh: trimesh.Trimesh, max_overhang_deg: float) -> float:
    """Area totale delle facce in sbalzo, in mm²."""
    from ..mesh.metrics import overhang_faces

    if is_empty(mesh):
        return 0.0
    mask = overhang_faces(mesh, max_overhang_deg)
    if not mask.any():
        return 0.0
    return float(np.asarray(mesh.area_faces)[mask].sum())


def summarize_issues(issues: list[Issue]) -> str:
    """Riepilogo testuale dei problemi, raggruppati per gravità."""
    if not issues:
        return "Nessun problema rilevato"

    by_severity: dict[Severity, list[str]] = {}
    for issue in issues:
        by_severity.setdefault(issue.severity, []).append(issue.label_it)

    parti: list[str] = []
    for severity in (Severity.CRITICAL, Severity.ERROR, Severity.WARNING, Severity.INFO):
        labels = by_severity.get(severity)
        if labels:
            unici = ", ".join(dict.fromkeys(labels))
            parti.append(f"{severity.label_it}: {unici}")
    return "; ".join(parti)
