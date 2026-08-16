"""Phase 6 — temporal relationship analysis (legacy name "policy reaction analysis").

This package derives :class:`TemporalRelationship` relations (legacy class name
``PolicyReaction``; concept: Temporal Relationship) between existing Phase 5
``FactChange`` objects: an earlier change temporally followed (within a
documented window) by a later change. It is strictly **inferred** (never a Fact,
never causal, never a stance/trading signal) and never mutates the source
``FactChange`` / ``Fact`` objects.

The legacy ``PolicyReaction`` / ``PolicyReactionAnalyzer`` / ``analyze_reactions``
/ ``reaction_id_of`` / ``CONDITION_SUBJECTS`` / ``REACTION_SUBJECTS`` names are
still exported as compatibility aliases.
"""

from .analyzer import (
    PolicyReactionAnalyzer,
    TemporalRelationshipAnalyzer,
    analyze_reactions,
    analyze_temporal_relationships,
)
from .base import (
    CONDITION_SUBJECTS,
    DEFAULT_MAX_LAG_DAYS,
    EARLIER_SUBJECTS,
    LATER_SUBJECTS,
    REACTION_SUBJECTS,
    TEMPORAL_RELATIONSHIP_SUBJECTS,
    PolicyReaction,
    PolicyReactionResult,
    TemporalRelationship,
    TemporalRelationshipResult,
)
from .identity import reaction_id_of, temporal_relationship_id_of

__all__ = [
    # canonical
    "TemporalRelationship",
    "TemporalRelationshipResult",
    "TemporalRelationshipAnalyzer",
    "analyze_temporal_relationships",
    "temporal_relationship_id_of",
    "TEMPORAL_RELATIONSHIP_SUBJECTS",
    "EARLIER_SUBJECTS",
    "LATER_SUBJECTS",
    "DEFAULT_MAX_LAG_DAYS",
    # legacy aliases
    "PolicyReaction",
    "PolicyReactionResult",
    "PolicyReactionAnalyzer",
    "analyze_reactions",
    "reaction_id_of",
    "CONDITION_SUBJECTS",
    "REACTION_SUBJECTS",
]
