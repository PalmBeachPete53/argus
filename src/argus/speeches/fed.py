"""Federal Reserve Board — Speech / Remarks extractor (Phase 4.x).

Extracts the facts of a Federal Reserve speech from the normalized document.
The Fed publishes individual remarks by the Chair, Vice Chair, governors and
regional presidents under ``/newsevents/speeches/``; each speech has an
explicit speaker (attributed in-page and in the metadata author).

Bank-specific knowledge kept here (everything else is the shared structural
pipeline in ``_pipeline.py`` / ``_shared.py``):

- Federal Reserve section-heading vocabulary (``_IGNORE_HEADINGS`` /
  ``_ECONOMIC_HEADINGS``);
- the FOMC guidance phrasing ("as appropriate", "meeting by meeting",
  "data dependent", "will be patient", …);
- the policy vocabulary specific to the Fed ("the FOMC", "the Federal
  Reserve", "the Committee", "the funds rate", "interest rates", …).

Speeches are the individual communications of one official: the explicit
speaker label is preserved verbatim in ``Fact.speaker``, never inferred, and a
speech is never mistaken for a collective FOMC decision (those are Phase 4.1/6/9, gated on their own publication types).
"""

from __future__ import annotations

import re

from ._pipeline import SpeechExtractorBase

EXTRACTION_VERSION = "11.0.0"

COVERAGE_SOURCE = (
    "https://www.federalreserve.gov/newsevents/speeches.htm (Board speeches); "
    "classified generically via the 'speech' TypeRule (url /newsevents/speeches/ ) "
    "and title 'speech'/'remarks'."
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
    "labor market", "labour market", "employment", "wages", "wage developments",
    "financial stability", "financial conditions", "financial developments",
    "financial markets", "financial system",
    "economic outlook", "economic activity", "real economy", "growth",
    "economic growth", "output", "us economy",
    "external environment", "international environment", "global economy",
    "international outlook", "world economy", "overview", "summary",
    "economic analysis", "economic",
})

# Federal Reserve forward-guidance phrasing — Fed vernacular, distinct
# vocabulary, but the same structural "guidance" slot in the pipeline.
_GUIDANCE_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bast\s+appropriate\b", re.IGNORECASE),
    re.compile(r"\bwill\s+be\s+patient\b", re.IGNORECASE),
    re.compile(r"\bmeeting\s+by\s+meeting\b", re.IGNORECASE),
    re.compile(r"\bdata[ -]?dependent\b", re.IGNORECASE),
    re.compile(r"\bdepends?\s+(?:on|upon)\s+the\s+data\b", re.IGNORECASE),
    re.compile(r"\bwill\s+not\s+hesitate\s+to\b", re.IGNORECASE),
    re.compile(r"\bstand(?:s|ing)?\s+ready\s+to\b", re.IGNORECASE),
    re.compile(r"\bwill\s+continue\s+to\s+(?:monitor|assess|evaluate)\b", re.IGNORECASE),
    re.compile(r"\bfor\s+as\s+long\s+as\s+(?:necessary|needed)\b", re.IGNORECASE),
    re.compile(r"\bfuture\s+(?:meetings?|decisions?|policy)\b", re.IGNORECASE),
    re.compile(r"\bwill\s+take\s+into\s+account\b", re.IGNORECASE),
)

_POLICY_TERM = re.compile(
    r"\b(?:"
    r"monetary(?:\s+policy)?"
    r"|monetary\s+policy\s+stance"
    r"|interest\s+rates?"
    r"|federal\s+funds\s+rate"
    r"|the\s+fed(?:eral\s+reserve)?"
    r"|the\s+fomc"
    r"|the\s+committee"
    r"|policy\s+rates?"
    r"|federal\s+reserve"
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


class FedSpeechExtractor(SpeechExtractorBase):
    bank = "fed"
    extraction_version = EXTRACTION_VERSION

    IGNORE_HEADINGS = _IGNORE_HEADINGS
    ECONOMIC_HEADINGS = _ECONOMIC_HEADINGS
    GUIDANCE_ANCHORS = _GUIDANCE_ANCHORS
    POLICY_TERM = _POLICY_TERM
    POLICY_STANCE = _POLICY_STANCE
    POLICY_STANCE_PHRASE = _POLICY_STANCE_PHRASE
