"""Reserve Bank of New Zealand — Speech extractor (Phase 4.x).

Extracts the facts of an RBNZ speech by the Governor, Deputy Governor or MPHC
members, published under ``/hub/speeches``. Each RBNZ speech has an explicit
speaker and sections. RBNZ monetary-policy-statement and OCR decision
documents are Phases 5/8 (gated), never mined here.

Bank-specific knowledge kept here:

- RBNZ section-heading vocabulary;
- RBNZ forward-guidance phrasing ("will keep the OCR", "for as long as
  necessary", "the Committee will continue", "data dependent", "will depend
  on the data");
- RBNZ policy vocabulary ("the Reserve Bank of New Zealand", "the MPC" /
  "the Monetary Policy Committee", "the Committee", "the official cash rate"
  / "the OCR", "interest rates").

Speeches are individual communications of one official: the explicit speaker
is preserved verbatim in ``Fact.speaker``, never inferred.
"""

from __future__ import annotations

import re

from ._pipeline import SpeechExtractorBase

EXTRACTION_VERSION = "11.0.0"

COVERAGE_SOURCE = (
    "https://www.rbnz.govt.nz/hub/speeches (RBNZ speeches); classified "
    "generically via the 'speech' TypeRule (url /speeches/ and title "
    "'speech'/'remarks')."
)

_IGNORE_HEADINGS = frozenset({
    "speech", "speech by", "remarks", "address", "keynote speech",
    "about the speaker", "speaker biography", "biography", "biographical note",
    "acknowledgements", "acknowledgments", "thanks", "thank you",
    "closing remarks", "concluding remarks", "closing",
    "questions and answers", "questions", "question", "answers", "q&a",
    "references", "bibliography", "notes", "endnotes", "annex", "appendix",
    "legal notice", "disclaimer", "copyright", "imprint", "glossary",
})

_ECONOMIC_HEADINGS = frozenset({
    "monetary policy", "monetary policy stance", "policy considerations",
    "policy stance", "risk assessment", "risks", "risk",
    "inflation", "price developments", "price stability", "prices",
    "labour market", "labor market", "employment", "wages", "wage developments",
    "financial stability", "financial conditions", "financial developments",
    "financial markets", "financial system",
    "economic outlook", "economic activity", "real economy", "growth",
    "economic growth", "output", "new zealand economy",
    "external environment", "international environment", "global economy",
    "world economy", "overview", "summary", "executive summary",
    "economic analysis", "economic",
})

# RBNZ forward-guidance phrasing.
_GUIDANCE_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bwill\s+keep\s+the\s+ocr\b", re.IGNORECASE),
    re.compile(r"\bwill\s+continue\s+to\s+(?:monitor|assess|hold)\b", re.IGNORECASE),
    re.compile(r"\bfor\s+as\s+long\s+as\s+necessary\b", re.IGNORECASE),
    re.compile(r"\bdata[ -]?dependent\b", re.IGNORECASE),
    re.compile(r"\bdepend(?:s|ing)?\s+on\s+the\s+data\b", re.IGNORECASE),
    re.compile(r"\bwill\s+be\s+guided\s+by\b", re.IGNORECASE),
    re.compile(r"\bwill\s+take\s+(?:account|into\s+account)\s+of\b", re.IGNORECASE),
    re.compile(r"\bwill\s+be\s+patient\b", re.IGNORECASE),
)

_POLICY_TERM = re.compile(
    r"\b(?:"
    r"monetary(?:\s+policy)?"
    r"|monetary\s+policy\s+stance"
    r"|policy\s+interest\s+rate"
    r"|interest\s+rates?"
    r"|the\s+official\s+cash\s+rate"
    r"|the\s+ocr\b"
    r"|the\s+monetary\s+policy\s+committee"
    r"|the\s+mpc\b"
    r"|the\s+committee"
    r"|the\s+reserve\s+bank\s+of\s+new\s+zealand"
    r"|the\s+bank\b(?!\s+rate)"
    r")\b",
    re.IGNORECASE,
)
_POLICY_STANCE = re.compile(
    r"\bstance\b"
    r"|\bdecided\s+to\b"
    r"|\bdecision(?:s)?\b"
    r"|\bappropriate\b"
    r"|\brestrictive\b"
    r"|\baccommodative\b"
    r"|\btightening\b"
    r"|\beasing\b",
    re.IGNORECASE,
)
_POLICY_STANCE_PHRASE = re.compile(
    r"\b(?:monetary\s+policy\s+stance|policy\s+stance)\b"
    r"|\b(?:appropriate|restrictive|accommodative|neutral|loose)\s+stance\s+of\s+(?:monetary\s+)?policy\b",
    re.IGNORECASE,
)


class RbnzSpeechExtractor(SpeechExtractorBase):
    bank = "rbnz"
    extraction_version = EXTRACTION_VERSION

    IGNORE_HEADINGS = _IGNORE_HEADINGS
    ECONOMIC_HEADINGS = _ECONOMIC_HEADINGS
    GUIDANCE_ANCHORS = _GUIDANCE_ANCHORS
    POLICY_TERM = _POLICY_TERM
    POLICY_STANCE = _POLICY_STANCE
    POLICY_STANCE_PHRASE = _POLICY_STANCE_PHRASE
