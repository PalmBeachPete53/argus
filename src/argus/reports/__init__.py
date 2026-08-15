"""Phase 10 — type-specific monetary policy report extraction public API."""

from .base import (
    REPORT_PUBLICATION_TYPES,
    ReportsExtractor,
    extract_report,
    extract_report_batch,
    get_extractor,
)
from .ecb import (
    EXTRACTION_VERSION,
    SUBJECT_CORE_INFLATION,
    SUBJECT_FINANCIAL_CONDITIONS,
    SUBJECT_FISCAL_POLICY,
    SUBJECT_GDP,
    SUBJECT_GROWTH,
    SUBJECT_GROWTH_RISK,
    SUBJECT_INFLATION,
    SUBJECT_INFLATION_EXPECTATIONS,
    SUBJECT_INFLATION_RISK,
    SUBJECT_LABOUR_MARKET,
    SUBJECT_MONETARY_POLICY,
    SUBJECT_POLICY_GUIDANCE,
    SUBJECT_RISK,
    SUBJECT_UNEMPLOYMENT,
    SUBJECT_WAGES,
    EcbReportsExtractor,
)
from .norges import (
    SUBJECT_POLICY_RATE_PROJECTION,
    NorgesReportExtractor,
)

__all__ = [
    "REPORT_PUBLICATION_TYPES",
    "ReportsExtractor",
    "EcbReportsExtractor",
    "NorgesReportExtractor",
    "SUBJECT_POLICY_RATE_PROJECTION",
    "EXTRACTION_VERSION",
    "SUBJECT_INFLATION",
    "SUBJECT_CORE_INFLATION",
    "SUBJECT_INFLATION_EXPECTATIONS",
    "SUBJECT_GROWTH",
    "SUBJECT_GDP",
    "SUBJECT_LABOUR_MARKET",
    "SUBJECT_UNEMPLOYMENT",
    "SUBJECT_WAGES",
    "SUBJECT_FINANCIAL_CONDITIONS",
    "SUBJECT_RISK",
    "SUBJECT_INFLATION_RISK",
    "SUBJECT_GROWTH_RISK",
    "SUBJECT_MONETARY_POLICY",
    "SUBJECT_POLICY_GUIDANCE",
    "SUBJECT_FISCAL_POLICY",
    "extract_report",
    "extract_report_batch",
    "get_extractor",
]
