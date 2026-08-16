"""Bank of England — Speech extractor (Phase 4.x).

Extracts the facts of a Bank of England speech (Governor, Deputy Governors,
MPC members, staff) published under ``/speech/…``. Each BoE speech has an
explicit speaker and sections; the Bank's collective decision documents are
Phase 4.1/8 (gated on their own publication types), never mined here.

Bank-specific knowledge kept here:

- BoE section-heading vocabulary;
- BoE forward-guidance phrasing ("for as long as necessary",
  "monetary policy will need to remain restrictive", "depending on the data",
  "will continue to");
- BoE policy vocabulary ("the Bank of England", "the Monetary Policy
  Committee" / "MPC", "the Committee", "Bank Rate", "interest rates").

Speeches are the individual communications of one official: the explicit
speaker is preserved verbatim in ``Fact.speaker``, never inferred.
"""

from __future__ import annotations

import re

from ._pipeline import SpeechExtractorBase

EXTRACTION_VERSION = "11.0.0"

COVERAGE_SOURCE = (
    "https://www.bankofengland.co.uk/speech/… (BoE speeches); classified via "
    "the 'speech' TypeRule. BoE speech URLs use the singular '/speech/' slug, "
    "which the generic rule does not match (it matches the plural 'speeches/'), "
    "so classification relies on the 'speech'/'remarks' title signal — "
    "documented rather than forced."
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
    "financial markets", "money and credit", "financial system",
    "economic outlook", "economic activity", "real economy", "growth",
    "economic growth", "output", "uk economy",
    "external environment", "international environment", "global economy",
    "world economy", "overview", "summary", "executive summary",
    "economic analysis", "economic",
})

# BoE forward-guidance phrasing.
_GUIDANCE_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bfor\s+as\s+long\s+as\s+necessary\b", re.IGNORECASE),
    re.compile(r"\b(?:will|may)\s+need\s+to\s+remain\s+restrictive\b", re.IGNORECASE),
    re.compile(r"\bdepend(?:s|ing)?\s+on\s+the\s+data\b", re.IGNORECASE),
    re.compile(r"\bdata[ -]?dependent\b", re.IGNORECASE),
    re.compile(r"\bwill\s+continue\s+to\s+(?:monitor|assess|act)\b", re.IGNORECASE),
    re.compile(r"\bstand(?:s|ing)?\s+ready\s+to\b", re.IGNORECASE),
    re.compile(r"\bmeeting\s+by\s+meeting\b", re.IGNORECASE),
    re.compile(r"\bwill\s+not\s+pre-?commit\b", re.IGNORECASE),
    re.compile(r"\bfuture\s+(?:policy\s+)?decisions\b", re.IGNORECASE),
    re.compile(r"\bwill\s+be\s+guided\s+by\b", re.IGNORECASE),
)

_POLICY_TERM = re.compile(
    r"\b(?:"
    r"monetary(?:\s+policy)?"
    r"|monetary\s+policy\s+stance"
    r"|interest\s+rates?"
    r"|bank\s+rate"
    r"|the\s+monetary\s+policy\s+committee"
    r"|the\s+mpc"
    r"|the\s+committee"
    r"|the\s+bank\s+of\s+england"
    r"|the\s+bank\b(?!\s+rate)"
    r"|policy\s+rates?"
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


class BoeSpeechExtractor(SpeechExtractorBase):
    bank = "boe"
    extraction_version = EXTRACTION_VERSION

    IGNORE_HEADINGS = _IGNORE_HEADINGS
    ECONOMIC_HEADINGS = _ECONOMIC_HEADINGS
    GUIDANCE_ANCHORS = _GUIDANCE_ANCHORS
    POLICY_TERM = _POLICY_TERM
    POLICY_STANCE = _POLICY_STANCE
    POLICY_STANCE_PHRASE = _POLICY_STANCE_PHRASE
