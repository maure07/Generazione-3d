"""Analisi della stampabilità: esegue le regole e calcola un punteggio.

Il punteggio (0-100) è pensato per essere leggibile a colpo d'occhio
nell'interfaccia:

* **90-100** — pronto per la stampa;
* **70-89** — stampabile, con qualche accorgimento (supporti, orientamento);
* **50-69** — richiede correzioni;
* **sotto 50** — non stampabile senza intervento.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import trimesh

from ..domain.enums import IssueCode, Severity
from ..domain.models import Issue, PrinterProfile
from ..mesh.io import is_empty
from ..mesh.metrics import estimate_filament_g, estimate_print_time_min
from .rules import ALL_RULES

logger = logging.getLogger(__name__)

#: Penalità applicata al punteggio per ogni livello di gravità.
SEVERITY_PENALTY: dict[Severity, float] = {
    Severity.INFO: 0.0,
    Severity.WARNING: 4.0,
    Severity.ERROR: 14.0,
    Severity.CRITICAL: 40.0,
}


@dataclass(slots=True)
class PrintabilityReport:
    """Esito dell'analisi di stampabilità di un pezzo o dell'intero modello."""

    score: float = 100.0
    issues: list[Issue] = field(default_factory=list)
    part_id: str | None = None
    part_name: str = ""
    estimated_time_min: float = 0.0
    estimated_filament_g: float = 0.0
    printable: bool = True

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == Severity.CRITICAL)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == Severity.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == Severity.WARNING)

    def verdict_it(self) -> str:
        """Giudizio sintetico in italiano."""
        if self.score >= 90:
            return "Pronto per la stampa"
        if self.score >= 70:
            return "Stampabile con accorgimenti"
        if self.score >= 50:
            return "Richiede correzioni"
        return "Non stampabile senza intervento"

    def message_it(self) -> str:
        if not self.issues:
            return f"Analisi completata: nessun problema (punteggio {self.score:.0f}/100)"
        return (
            f"{self.verdict_it()} — punteggio {self.score:.0f}/100: "
            f"{self.critical_count} critici, {self.error_count} errori, "
            f"{self.warning_count} avvisi"
        )


class PrintabilityAnalyzer:
    """Esegue tutte le regole di stampabilità su una mesh."""

    def __init__(
        self, printer: PrinterProfile | None = None, skip: set[IssueCode] | None = None
    ) -> None:
        """
        Args:
            printer: profilo della stampante di destinazione.
            skip: codici di regola da non eseguire (utile per l'anteprima rapida).
        """
        self.printer = printer or PrinterProfile()
        self.skip = skip or set()

    def analyze(
        self, mesh: trimesh.Trimesh, part_id: str | None = None, part_name: str = ""
    ) -> PrintabilityReport:
        """Analizza una mesh e restituisce il rapporto con il punteggio."""
        report = PrintabilityReport(part_id=part_id, part_name=part_name)

        if is_empty(mesh):
            report.score = 0.0
            report.printable = False
            report.issues.append(
                Issue(
                    code=IssueCode.ZERO_VOLUME,
                    severity=Severity.CRITICAL,
                    message_it="Pezzo vuoto: nessuna geometria",
                    part_id=part_id,
                )
            )
            return report

        for code, rule in ALL_RULES.items():
            if code in self.skip:
                continue
            try:
                found = rule(mesh, self.printer)
            except Exception as exc:
                logger.warning("Regola %s fallita: %s", code.value, exc)
                continue
            for issue in found:
                issue.part_id = part_id
                report.issues.append(issue)

        report.score = self._score(report.issues)
        report.printable = report.critical_count == 0
        report.estimated_time_min = estimate_print_time_min(
            mesh, layer_height_mm=self.printer.layer_height_mm
        )
        report.estimated_filament_g = estimate_filament_g(mesh)

        logger.debug("Stampabilità di %s: %s", part_name or "modello", report.message_it())
        return report

    def analyze_parts(self, parts: dict[str, tuple[str, trimesh.Trimesh]]) -> list[PrintabilityReport]:
        """Analizza più pezzi.

        Args:
            parts: mappa ``part_id -> (nome, mesh)``.
        """
        return [
            self.analyze(mesh, part_id=part_id, part_name=name)
            for part_id, (name, mesh) in parts.items()
        ]

    def _score(self, issues: list[Issue]) -> float:
        """Calcola il punteggio a partire dai problemi trovati.

        Ogni codice di problema pesa una sola volta al massimo della sua gravità:
        cento pareti sottili sono un problema solo, non cento.
        """
        worst_by_code: dict[IssueCode, Severity] = {}
        order = [Severity.INFO, Severity.WARNING, Severity.ERROR, Severity.CRITICAL]

        for issue in issues:
            current = worst_by_code.get(issue.code)
            if current is None or order.index(issue.severity) > order.index(current):
                worst_by_code[issue.code] = issue.severity

        penalty = sum(SEVERITY_PENALTY[severity] for severity in worst_by_code.values())
        return float(max(0.0, min(100.0, 100.0 - penalty)))


def aggregate_score(reports: list[PrintabilityReport]) -> float:
    """Punteggio complessivo del modello: il pezzo peggiore domina.

    Un modello si stampa solo se **tutti** i pezzi si stampano, quindi la media
    sarebbe fuorviante: pesiamo la media con il minimo.
    """
    if not reports:
        return 0.0
    scores = [r.score for r in reports]
    minimum = min(scores)
    average = sum(scores) / len(scores)
    return float(round(0.6 * minimum + 0.4 * average, 1))
