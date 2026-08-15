"""Reserve Bank of Australia — Speech extractor (Phase 4.x).

Extracts the facts of an RBA speech by the Governor, Deputy Governor or
Assistant Governors, published under ``/speeches/…``. Each RBA speech has an
explicit speaker and sections. RBA statement-on-monetary-policy and decision
documents are Phases 5/8 (gated), never mined here.

Bank-specific knowledge kept here:

- RBA section-heading vocabulary;
- RBA forward-guidance phrasing ("will continue to…", "as the outlook
  evolves", "data dependent", "will be guided by the data");
- RBA policy vocabulary ("the Reserve Bank of Australia", "the RBA",
  "the Board", "the cash rate", "monetary policy").

Speeches are individual communications of one official: the explicit speaker
is preserved verbatim in ``Fact.speaker``, never inferred.
"""

from __future__ import annotations

import re

from ._pipeline import SpeechExtractorBase

EXTRACTION_VERSION = "11.0.0"

COVERAGE_SOURCE = (
    "https://www.rba.gov.au/speeches/… (RBA speeches); classified generically "
    "via the 'speech' TypeRule (url /speeches/ and title 'speech'/'remarks')."
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
    "economic growth", "output", "australian economy",
    "external environment", "international environment", "global economy",
    "world economy", "overview", "summary", "executive summary",
    "economic analysis", "economic",
})

# RBA forward-guidance phrasing.
_GUIDANCE_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bwill\s+continue\s+to\s+(?:monitor|assess|act)\b", re.IGNORECASE),
    re.compile(r"\bas\s+the\s+outlook\s+evolves\b", re.IGNORECASE),
    re.compile(r"\bdata[ -]?dependent\b", re.IGNORECASE),
    re.compile(r"\bwill\s+be\s+guided\s+by\b", re.IGNORECASE),
    re.compile(r"\bnot\s+(?:pre-?commit|pre-?determin(?:e|ing))\b", re.IGNORECASE),
    re.compile(r"\bfor\s+as\s+long\s+as\s+necessary\b", re.IGNORECASE),
    re.compile(r"\bwill\s+take\s+(?:account|into\s+account)\s+of\b", re.IGNORECASE),
    re.compile(r"\bwill\s+be\s+responsive\b", re.IGNORECASE),
)

_POLICY_TERM = re.compile(
    r"\b(?:"
    r"monetary(?:\s+policy)?"
    r"|monetary\s+policy\s+stance"
    r"|policy\s+interest\s+rate"
    r"|interest\s+rates?"
    r"|the\s+cash\s+rate"
    r"|the\s+reserve\s+bank\s+of\s+australia"
    r"|the\s+rba\b"
    r"|the\s+board\b"
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


class RbaSpeechExtractor(SpeechExtractorBase):
    bank = "rba"
    extraction_version = EXTRACTION_VERSION

    IGNORE_HEADINGS = _IGNORE_HEADINGS
    ECONOMIC_HEADINGS = _ECONOMIC_HEADINGS
    GUIDANCE_ANCHORS = _GUIDANCE_ANCHORS
    POLICY_TERM = _POLICY_TERM
    POLICY_STANCE = _POLICY_STANCE
    POLICY_STANCE_PHRASE = _POLICY_STANCE_PHRASE
