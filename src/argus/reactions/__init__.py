"""Phase 6 — empirical policy reaction analysis.

This package derives ``PolicyReaction`` relations between existing Phase 5
``FactChange`` objects: a condition-side change temporally followed (within a
documented window) by a policy-side change. It is strictly **inferred** (never
a Fact, never causal, never a stance/trading signal) and never mutates the
source ``FactChange`` / ``Fact`` objects.
"""

from .analyzer import PolicyReactionAnalyzer, analyze_reactions
from .base import (
    CONDITION_SUBJECTS,
    DEFAULT_MAX_LAG_DAYS,
    REACTION_SUBJECTS,
    PolicyReaction,
    PolicyReactionResult,
)
from .identity import reaction_id_of

__all__ = [
    "CONDITION_SUBJECTS",
    "REACTION_SUBJECTS",
    "DEFAULT_MAX_LAG_DAYS",
    "PolicyReaction",
    "PolicyReactionResult",
    "PolicyReactionAnalyzer",
    "reaction_id_of",
    "analyze_reactions",
]