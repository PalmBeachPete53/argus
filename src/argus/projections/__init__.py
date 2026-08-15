"""Phase 9 — type-specific economic projections extraction public API."""

from .base import (
    PROJECTIONS_PUBLICATION_TYPES,
    ProjectionsExtractor,
    extract_projections,
    extract_projections_batch,
    get_extractor,
)
from .ecb import (
    EXTRACTION_VERSION,
    SUBJECT_CORE_INFLATION,
    SUBJECT_GDP,
    SUBJECT_INFLATION,
    EcbProjectionsExtractor,
)
from .fed import (
    SUBJECT_POLICY_RATE,
    SUBJECT_UNEMPLOYMENT,
    FedSepExtractor,
)

__all__ = [
    "PROJECTIONS_PUBLICATION_TYPES",
    "ProjectionsExtractor",
    "EcbProjectionsExtractor",
    "FedSepExtractor",
    "SUBJECT_POLICY_RATE",
    "SUBJECT_UNEMPLOYMENT",
    "EXTRACTION_VERSION",
    "SUBJECT_INFLATION",
    "SUBJECT_CORE_INFLATION",
    "SUBJECT_GDP",
    "extract_projections",
    "extract_projections_batch",
    "get_extractor",
]