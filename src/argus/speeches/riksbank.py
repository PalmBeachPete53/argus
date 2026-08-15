"""Riksbank — Speech extractor (Phase 4.x).

Extracts the facts of a Riksbank speech (tal) by the Governor, the
First/Deputy Governors or Executive Board members, published on the English
site under ``/en-gb/press-och-publicerat/tal/`` (tal = speech). Each speech has
an explicit speaker and sections. Riksbank's collective decisions / minutes are
Phases 5/8 (gated), never mined here.

Bank-specific knowledge kept here:

- Riksbank section-heading vocabulary;
- Riksbank forward-guidance phrasing ("will continue to…", "as necessary",
  "will assess", "for as long as", "depending on");
- Riksbank policy vocabulary ("the Riksbank", "the Executive Board", "the
  policy rate", "interest rates", "monetary policy").

Note on classification: the English Riksbank speech pages use "tal" in the URL
and are best signalled by the title "speech"/"remarks"/"tal"; this is
documented in ``COVERAGE_SOURCE`` rather than forced.
"""

from __future__ import annotations

import re

from ._pipeline import SpeechExtractorBase

EXTRACTION_VERSION = "11.0.0"

COVERAGE_SOURCE = (
    "https://www.riksbank.se/en-gb/press-och-publicerat/tal/ (Riksbank "
    "speeches); classification relies on the 'speech'/'remarks'/'tal' title "
    "signal (URL uses the Swedish 'tal'), documented rather than forced."
)

_IGNORE_HEADINGS = frozenset({
    "speech", "speech by", "remarks", "address", "keynote speech", "tal",
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
    "economic growth", "output", "swedish economy",
    "external environment", "international environment", "global economy",
    "world economy", "overview", "summary", "executive summary",
    "economic analysis", "economic",
})

# Riksbank forward-guidance phrasing.
_GUIDANCE_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bwill\s+continue\s+to\s+(?:monitor|assess|act)\b", re.IGNORECASE),
    re.compile(r"\bas\s+necessary\b", re.IGNORECASE),
    re.compile(r"\bwill\s+assess\b", re.IGNORECASE),
    re.compile(r"\bfor\s+as\s+long\s+as\b", re.IGNORECASE),
    re.compile(r"\bdepend(?:s|ing)?\s+on\b", re.IGNORECASE),
    re.compile(r"\bwill\s+be\s+guided\s+by\b", re.IGNORECASE),
    re.compile(r"\bdata[ -]?dependent\b", re.IGNORECASE),
    re.compile(r"\bwill\s+take\s+(?:account|into\s+account)\s+of\b", re.IGNORECASE),
)

_POLICY_TERM = re.compile(
    r"\b(?:"
    r"monetary(?:\s+policy)?"
    r"|monetary\s+policy\s+stance"
    r"|policy\s+rates?"
    r"|interest\s+rates?"
    r"|the\s+policy\s+rate"
    r"|the\s+riksbank\b"
    r"|the\s+executive\s+board"
    r"|the\s+board\b(?!\s+rate)"
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


class RiksbankSpeechExtractor(SpeechExtractorBase):
    bank = "riksbank"
    extraction_version = EXTRACTION_VERSION

    IGNORE_HEADINGS = _IGNORE_HEADINGS
    ECONOMIC_HEADINGS = _ECONOMIC_HEADINGS
    GUIDANCE_ANCHORS = _GUIDANCE_ANCHORS
    POLICY_TERM = _POLICY_TERM
    POLICY_STANCE = _POLICY_STANCE
    POLICY_STANCE_PHRASE = _POLICY_STANCE_PHRASE
