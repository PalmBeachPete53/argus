"""Compatibility shim for ``argus.reactions.identity``.

The canonical implementation lives in :mod:`argus.temporal_relationships.identity`.
"""

from ..temporal_relationships.identity import (
    reaction_id_of,
    temporal_relationship_id_of,
)

__all__ = ["reaction_id_of", "temporal_relationship_id_of"]
