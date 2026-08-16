"""Phase 4.2 — type-specific monetary policy statement extraction public API."""

from .base import (
    STATEMENT_PUBLICATION_TYPE,
    StatementExtractor,
    extract_statement,
    extract_statement_batch,
    get_extractor,
)
from .ecb import (
    EXTRACTION_VERSION,
    SUBJECT_CORE_INFLATION,
    SUBJECT_FINANCIAL_CONDITIONS,
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
    EcbMonetaryPolicyStatementExtractor,
)
from .boj import BojStatementExtractor
from .boc import BocStatementExtractor
from .boe import BoeStatementExtractor
from .fed import FedStatementExtractor
from .rba import RbaStatementExtractor
from .rbnz import RbnzStatementExtractor
from .riksbank import RiksbankStatementExtractor
from .snb import SnbStatementExtractor

__all__ = [
    "STATEMENT_PUBLICATION_TYPE",
    "StatementExtractor",
    "EcbMonetaryPolicyStatementExtractor",
    "BojStatementExtractor",
    "BocStatementExtractor",
    "BoeStatementExtractor",
    "FedStatementExtractor",
    "RbaStatementExtractor",
    "RbnzStatementExtractor",
    "RiksbankStatementExtractor",
    "SnbStatementExtractor",
    "EXTRACTION_VERSION",
    "SUBJECT_MONETARY_POLICY",
    "SUBJECT_INFLATION",
    "SUBJECT_CORE_INFLATION",
    "SUBJECT_INFLATION_EXPECTATIONS",
    "SUBJECT_GROWTH",
    "SUBJECT_GDP",
    "SUBJECT_LABOUR_MARKET",
    "SUBJECT_UNEMPLOYMENT",
    "SUBJECT_WAGES",
    "SUBJECT_FINANCIAL_CONDITIONS",
    "SUBJECT_INFLATION_RISK",
    "SUBJECT_GROWTH_RISK",
    "SUBJECT_RISK",
    "SUBJECT_POLICY_GUIDANCE",
    "extract_statement",
    "extract_statement_batch",
    "get_extractor",
]