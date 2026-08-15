"""Norges Bank — Speech extractor (Phase 4.x).

Extracts the facts of a Norges Bank speech by the Governor, Deputy Governor or
Executive Board members, published under ``/en/…/speeches-and-articles/``. Each
speech has an explicit speaker and sections. Norges Bank's collective
decisions / MPR are Phases 5/8 (gated), never mined here.

Bank-specific knowledge kept here:

- Norges Bank section-heading vocabulary;
- Norges Bank forward-guidance phrasing ("will continue to…", "as
  appropriate", "will assess", "for as long as", "will depend on");
- Norges Bank policy vocabulary ("Norges Bank", "the Executive Board" / "the
  Committee", "the policy rate", "interest rates", "monetary policy").

Speeches are individual communications of one official: the explicit speaker
is preserved verbatim in ``Fact.speaker``, never inferred.
"""

from __future__ import annotations

import re

from ._pipeline import SpeechExtractorBase

EXTRACTION_VERSION = "11.0.0"

COVERAGE_SOURCE = (
    "https://www.norges-bank.no/en/…/speeches-and-articles/ (Norges Bank "
    "speeches); classified generically via the 'speech' TypeRule (url /speech/ "
    "and title 'speech'/'remarks')."
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
    "economic growth", "output", "norwegian economy",
    "external environment", "international environment", "global economy",
    "world economy", "overview", "summary", "executive summary",
    "economic analysis", "economic",
})

# Norges Bank forward-guidance phrasing.
_GUIDANCE_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bwill\s+continue\s+to\s+(?:monitor|assess|act)\b", re.IGNORECASE),
    re.compile(r"\bas\s+appropriate\b", re.IGNORECASE),
    re.compile(r"\bwill\s+assess\b", re.IGNORECASE),
    re.compile(r"\bfor\s+as\s+long\s+as\b", re.IGNORECASE),
    re.compile(r"\bwill\s+depend\s+on\b", re.IGNORECASE),
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
    r"|norges\s+bank"
    r"|the\s+executive\s+board"
    r"|the\s+committee\b(?!\s+rate)"
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


class NorgesSpeechExtractor(SpeechExtractorBase):
    bank = "norges"
    extraction_version = EXTRACTION_VERSION

    IGNORE_HEADINGS = _IGNORE_HEADINGS
    ECONOMIC_HEADINGS = _ECONOMIC_HEADINGS
    GUIDANCE_ANCHORS = _GUIDANCE_ANCHORS
    POLICY_TERM = _POLICY_TERM
    POLICY_STANCE = _POLICY_STANCE
    POLICY_STANCE_PHRASE = _POLICY_STANCE_PHRASE
