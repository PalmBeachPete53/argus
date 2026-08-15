"""Phase 11 — type-specific speech extraction public API."""

from .base import (
    SPEECH_PUBLICATION_TYPES,
    SpeechExtractor,
    extract_speech,
    extract_speech_batch,
    get_extractor,
)
from .fed import FedSpeechExtractor
from .boe import BoeSpeechExtractor
from .boj import BojSpeechExtractor
from .snb import SnbSpeechExtractor
from .boc import BocSpeechExtractor
from .rba import RbaSpeechExtractor
from .rbnz import RbnzSpeechExtractor
from .norges import NorgesSpeechExtractor
from .riksbank import RiksbankSpeechExtractor
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
    EcbSpeechExtractor,
)

__all__ = [
    "SPEECH_PUBLICATION_TYPES",
    "SpeechExtractor",
    "EcbSpeechExtractor",
    "FedSpeechExtractor",
    "BoeSpeechExtractor",
    "BojSpeechExtractor",
    "SnbSpeechExtractor",
    "BocSpeechExtractor",
    "RbaSpeechExtractor",
    "RbnzSpeechExtractor",
    "NorgesSpeechExtractor",
    "RiksbankSpeechExtractor",
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
    "extract_speech",
    "extract_speech_batch",
    "get_extractor",
]
