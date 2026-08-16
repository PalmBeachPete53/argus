"""Phase 8 — forex fundamentals analysis.

This package synthesizes the forex fundamentals layer: a ``ForexFundamental``
is a derived, dated observation of one fundamental dimension of one economy
(currency), established by one ``MonetaryPolicyState`` (Phase 7 — monetary
dimensions) or one ``Fact`` (Phase 4 — macro dimensions); a
``ForexDifferential`` is a derived, dated arithmetic comparison of two
fundamentals of two different economies on an explicitly declared shared
dimension (``synthesized=True`` constant, never a new fact, never a stance,
never a forecast, never a fair value, never a trading/forex signal). The layer
never mutates its source objects.
"""

from .analyzer import ForexFundamentalsAnalyzer, analyze_forex_fundamentals
from .base import (
    FUNDAMENTAL_EXCLUDED_PREDICATES,
    FUNDAMENTAL_SUBJECTS,
    MACRO_SUBJECTS,
    MONETARY_SUBJECTS,
    SOURCE_FACT,
    SOURCE_MONETARY_STATE,
    ForexDifferential,
    ForexFundamental,
    ForexFundamentalResult,
)
from .identity import differential_id_of, fundamental_id_of

__all__ = [
    "FUNDAMENTAL_SUBJECTS",
    "FUNDAMENTAL_EXCLUDED_PREDICATES",
    "MACRO_SUBJECTS",
    "MONETARY_SUBJECTS",
    "SOURCE_FACT",
    "SOURCE_MONETARY_STATE",
    "ForexFundamental",
    "ForexDifferential",
    "ForexFundamentalResult",
    "ForexFundamentalsAnalyzer",
    "fundamental_id_of",
    "differential_id_of",
    "analyze_forex_fundamentals",
]