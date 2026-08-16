"""Compatibility shim for ``argus.reactions.base``.

The canonical implementation lives in :mod:`argus.temporal_relationships.base`.
All names are re-exported unchanged; new code should import from
``argus.temporal_relationships``.
"""

from ..temporal_relationships.base import (
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

__all__ = [
    "CONDITION_SUBJECTS",
    "DEFAULT_MAX_LAG_DAYS",
    "EARLIER_SUBJECTS",
    "LATER_SUBJECTS",
    "REACTION_SUBJECTS",
    "TEMPORAL_RELATIONSHIP_SUBJECTS",
    "PolicyReaction",
    "PolicyReactionResult",
    "TemporalRelationship",
    "TemporalRelationshipResult",
]
