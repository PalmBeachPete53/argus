"""Swiss National Bank — Speech extractor (Phase 4.x).

Extracts the facts of an SNB speech by the Chairman, Vice Chairman or a
Governing Board member, published under ``/en/mmr/speeches/…``. Each SNB
speech has an explicit speaker and sections. The SNB's collective decisions
(monetary policy assessment) are Phase 5/8 (gated), never mined here.

Bank-specific knowledge kept here:

- SNB section-heading vocabulary;
- SNB forward-guidance phrasing ("will continue to…", "as necessary",
  "monetary policy remains", "will decide at the next assessment", "flexible
  approach");
- SNB policy vocabulary ("the Swiss National Bank", "the SNB", "the Governing
  Board", "the policy rate", "interest rates").

Speeches are individual communications of one official: the explicit speaker
is preserved verbatim in ``Fact.speaker``, never inferred.
"""

from __future__ import annotations

import re

from ._pipeline import SpeechExtractorBase

EXTRACTION_VERSION = "11.0.0"

COVERAGE_SOURCE = (
    "https://www.snb.ch/en/mmr/speeches/… (SNB speeches); classified "
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
    "economic growth", "output", "swiss economy",
    "external environment", "international environment", "global economy",
    "world economy", "overview", "summary", "executive summary",
    "economic analysis", "economic",
})

# SNB forward-guidance phrasing.
_GUIDANCE_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bwill\s+continue\s+to\s+(?:monitor|assess|act)\b", re.IGNORECASE),
    re.compile(r"\bas\s+necessary\b", re.IGNORECASE),
    re.compile(r"\bwill\s+decide\s+at\s+the\s+next\s+(?:monetary\s+policy\s+assessment|assessment)\b", re.IGNORECASE),
    re.compile(r"\bwill\s+be\s+guided\s+by\b", re.IGNORECASE),
    re.compile(r"\bdoes\s+not\s+pre-?commit\b", re.IGNORECASE),
    re.compile(r"\bwill\s+take\s+(?:account|into\s+account)\s+of\b", re.IGNORECASE),
    re.compile(r"\bfor\s+as\s+long\s+as\s+necessary\b", re.IGNORECASE),
    re.compile(r"\bwill\s+remain\s+flexible\b", re.IGNORECASE),
)

_POLICY_TERM = re.compile(
    r"\b(?:"
    r"monetary(?:\s+policy)?"
    r"|monetary\s+policy\s+stance"
    r"|policy\s+rates?"
    r"|interest\s+rates?"
    r"|the\s+swiss\s+national\s+bank"
    r"|the\s+snb\b"
    r"|the\s+governing\s+board"
    r"|the\s+board\b"
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


class SnbSpeechExtractor(SpeechExtractorBase):
    bank = "snb"
    extraction_version = EXTRACTION_VERSION

    IGNORE_HEADINGS = _IGNORE_HEADINGS
    ECONOMIC_HEADINGS = _ECONOMIC_HEADINGS
    GUIDANCE_ANCHORS = _GUIDANCE_ANCHORS
    POLICY_TERM = _POLICY_TERM
    POLICY_STANCE = _POLICY_STANCE
    POLICY_STANCE_PHRASE = _POLICY_STANCE_PHRASE
