"""Phase 4.3 — type-specific press conference extraction public API."""

from .base import (
    PRESS_CONFERENCE_PUBLICATION_TYPE,
    PressConferenceExtractor,
    extract_press_conference,
    extract_press_conference_batch,
    get_extractor,
)
from .boe import (
    EXTRACTION_VERSION as BOE_EXTRACTION_VERSION,
    BoEPressConferenceExtractor,
)
from .ecb import (
    EXTRACTION_VERSION,
    SUBJECT_CORE_INFLATION,
    SUBJECT_FINANCIAL_CONDITIONS,
    SUBJECT_GDP,
    SUBJECT_GROWTH,
    SUBJECT_GROWTH_RISK,
    SUBJECT_INFLATION,
    SUBJECT_INFLATION_DRIVER,
    SUBJECT_INFLATION_EXPECTATIONS,
    SUBJECT_INFLATION_RISK,
    SUBJECT_LABOUR_MARKET,
    SUBJECT_MONETARY_POLICY,
    SUBJECT_POLICY_GUIDANCE,
    SUBJECT_RISK,
    SUBJECT_UNEMPLOYMENT,
    SUBJECT_WAGES,
    EcbPressConferenceExtractor,
)
from .fed import (
    EXTRACTION_VERSION as FED_EXTRACTION_VERSION,
    FedPressConferenceExtractor,
)

__all__ = [
    "PRESS_CONFERENCE_PUBLICATION_TYPE",
    "PressConferenceExtractor",
    "EcbPressConferenceExtractor",
    "FedPressConferenceExtractor",
    "BoEPressConferenceExtractor",
    "EXTRACTION_VERSION",
    "FED_EXTRACTION_VERSION",
    "BOE_EXTRACTION_VERSION",
    "SUBJECT_INFLATION",
    "SUBJECT_CORE_INFLATION",
    "SUBJECT_INFLATION_EXPECTATIONS",
    "SUBJECT_INFLATION_DRIVER",
    "SUBJECT_GROWTH",
    "SUBJECT_GDP",
    "SUBJECT_LABOUR_MARKET",
    "SUBJECT_UNEMPLOYMENT",
    "SUBJECT_WAGES",
    "SUBJECT_MONETARY_POLICY",
    "SUBJECT_RISK",
    "SUBJECT_INFLATION_RISK",
    "SUBJECT_GROWTH_RISK",
    "SUBJECT_FINANCIAL_CONDITIONS",
    "SUBJECT_POLICY_GUIDANCE",
    "extract_press_conference",
    "extract_press_conference_batch",
    "get_extractor",
]
