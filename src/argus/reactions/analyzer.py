"""Compatibility shim for ``argus.reactions.analyzer``.

The canonical implementation lives in :mod:`argus.temporal_relationships.analyzer`.
"""

from ..temporal_relationships.analyzer import (
    PolicyReactionAnalyzer,
    TemporalRelationshipAnalyzer,
    analyze_reactions,
    analyze_temporal_relationships,
)

__all__ = [
    "TemporalRelationshipAnalyzer",
    "PolicyReactionAnalyzer",
    "analyze_temporal_relationships",
    "analyze_reactions",
]
