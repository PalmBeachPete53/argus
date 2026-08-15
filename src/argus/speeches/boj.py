"""Bank of Japan — Speech extractor (Phase 4.x).

Extracts the facts of a Bank of Japan speech by the Governor, Deputy Governors
or Policy Board members, published (in English) under ``/en/announcements/
press/koen_…/`` (koen = speech). Each speech has an explicit speaker and
sections. BoJ collective decisions and outlook reports are Phases 5/9 (gated
on their own publication types), never mined here.

Bank-specific knowledge kept here:

- BoJ section-heading vocabulary;
- BoJ forward/sideways-guidance phrasing ("as long as necessary",
  "will continue to…", "will be guided by data", "patiently");
- BoJ policy vocabulary ("the Bank of Japan", "the Bank", "the Policy Board",
  "short-term policy interest rates", "the yield", "policy rate").

Note on classification: BoJ English speech pages carry ``speech`` in their
title and the generic ``koen`` URL path is not an obvious English token, so
reliable classification relies on the title signal; this is documented in
``COVERAGE_SOURCE`` rather than forced.
"""

from __future__ import annotations

import re

from ._pipeline import SpeechExtractorBase

EXTRACTION_VERSION = "11.0.0"

COVERAGE_SOURCE = (
    "https://www.boj.or.jp/en/announcements/press/koen_….htm (BoJ speeches); "
    "classification relies on the 'speech'/'remarks' title signal (the koen "
    "URL token is Japanese, not an English 'speech'), documented rather than "
    "forced."
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
    "economic growth", "output", "japanese economy",
    "external environment", "international environment", "global economy",
    "world economy", "overview", "summary", "executive summary",
    "economic analysis", "economic",
})

# BoJ forward/sideways-guidance phrasing.
_GUIDANCE_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bfor\s+as\s+long\s+as\s+necessary\b", re.IGNORECASE),
    re.compile(r"\bwill\s+continue\s+with\b", re.IGNORECASE),
    re.compile(r"\bwill\s+be\s+guided\s+by\b", re.IGNORECASE),
    re.compile(r"\bindependently\s+as\s+(?:appropriate|necessary)\b", re.IGNORECASE),
    re.compile(r"\bdoes\s+not\s+pre-?commit\b", re.IGNORECASE),
    re.compile(r"\bwill\s+take\s+(?:account|into\s+account)\s+of\b", re.IGNORECASE),
    re.compile(r"\bfor\s+the\s+time\s+being\b", re.IGNORECASE),
    re.compile(r"\bpatiently\b", re.IGNORECASE),
    re.compile(r"\bwhile\s+examining\b", re.IGNORECASE),
)

_POLICY_TERM = re.compile(
    r"\b(?:"
    r"monetary(?:\s+policy)?"
    r"|monetary\s+policy\s+stance"
    r"|short[- ]term\s+policy\s+interest\s+rates?"
    r"|policy\s+interest\s+rates?"
    r"|policy\s+rates?"
    r"|the\s+policy\s+board"
    r"|the\s+bank\s+of\s+japan"
    r"|the\s+bank\b(?!\s+rate)"
    r"|yields?\s+on\s+government\s+bonds"
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
    r"|\beasing\b"
    r"|\beased\b",
    re.IGNORECASE,
)
_POLICY_STANCE_PHRASE = re.compile(
    r"\b(?:monetary\s+policy\s+stance|policy\s+stance)\b",
    re.IGNORECASE,
)


class BojSpeechExtractor(SpeechExtractorBase):
    bank = "boj"
    extraction_version = EXTRACTION_VERSION

    IGNORE_HEADINGS = _IGNORE_HEADINGS
    ECONOMIC_HEADINGS = _ECONOMIC_HEADINGS
    GUIDANCE_ANCHORS = _GUIDANCE_ANCHORS
    POLICY_TERM = _POLICY_TERM
    POLICY_STANCE = _POLICY_STANCE
    POLICY_STANCE_PHRASE = _POLICY_STANCE_PHRASE
