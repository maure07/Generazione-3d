"""Controllo e correzione automatica della stampabilità."""

from .analyzer import (  # noqa: F401
    PrintabilityAnalyzer,
    PrintabilityReport,
    aggregate_score,
)
from .autofix import AutoFixer, AutoFixReport, summarize_issues  # noqa: F401
from .rules import ALL_RULES, RULE_INFO, register_rule  # noqa: F401
