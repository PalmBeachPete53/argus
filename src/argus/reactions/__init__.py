"""Phase 6 — temporal relationship analysis (legacy name "policy reaction analysis").

This package derives ``PolicyReaction`` relations (legacy class name; concept:
Temporal Relationship) between existing Phase 5 ``FactChange`` objects: an
earlier change temporally followed (within a documented window) by a later
change. It is strictly **inferred** (never
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