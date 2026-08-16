"""Phase 7 — monetary policy state analysis.

This package synthesizes a historised, dated ``MonetaryPolicyState`` from the
existing Phase 5 ``FactChange`` relations, reusing Phase 6's later-side (legacy "reaction-side")
vocabulary as the observable policy dimensions. Each state entry is a derived,
dated observation of one policy dimension of one bank (``synthesized=True``
constant, never a Fact, never a stance, never a forecast, never a
trading/forex signal) and never mutates the source ``FactChange`` / ``Fact``
objects.
"""

from .analyzer import MonetaryPolicyStateAnalyzer, analyze_policy_state
from .base import (
    STATE_EXCLUDED_PREDICATES,
    STATE_SUBJECTS,
    MonetaryPolicyState,
    MonetaryPolicyStateResult,
)
from .identity import state_id_of

__all__ = [
    "STATE_SUBJECTS",
    "STATE_EXCLUDED_PREDICATES",
    "MonetaryPolicyState",
    "MonetaryPolicyStateResult",
    "MonetaryPolicyStateAnalyzer",
    "state_id_of",
    "analyze_policy_state",
]