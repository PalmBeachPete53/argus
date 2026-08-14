"""Phase 12 — temporal / cross-publication change analysis.

This package derives ``FactChange`` relations between existing Facts over time.
It is strictly descriptive (never an economic interpretation) and never
mutates the source Facts.
"""

from .analyzer import FactChangeAnalyzer, analyze_changes
from .base import CHANGE_TYPES, ChangeType, FactChange, FactChangeResult
from .identity import change_id_of

__all__ = [
    "ChangeType",
    "CHANGE_TYPES",
    "FactChange",
    "FactChangeResult",
    "FactChangeAnalyzer",
    "change_id_of",
    "analyze_changes",
]