"""Compatibility shim for ``argus.reactions``.

The canonical implementation lives in :mod:`argus.temporal_relationships`.
All public names are re-exported unchanged; new code should import from
``argus.temporal_relationships``.
"""

from ..temporal_relationships import *

from ..temporal_relationships import __all__ as _canonical_all

__all__ = list(_canonical_all)
