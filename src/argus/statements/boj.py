"""BOJ — Bank of Japan Statement on Monetary Policy extractor (Phase 6).

Extracts the facts of a Bank of Japan "Statement on Monetary Policy" from the
normalized document, answering "what does the Policy Board explicitly state or
decide in the statement?":

- the decision date (the leading release-date paragraph / the heading)
- the short-term policy target — the uncollateralized overnight call rate the
  Bank "conducts market operations so that … will be formed at around X
  percent" (an explicit numeric target → ``policy_rate / value``)
- the decision wording ("At the Monetary Policy Meeting held today, the Policy
  Board decided to …", incl. the explicit vote sentence), verbatim
- forward guidance ("will continue with monetary easing … as long as
  necessary"), verbatim
- the statement's price / growth / risk assessment: quantitative value claims
  (explicit reference period) and explicit risk orientations, or verbatim text

Deliberately NOT extracted (Phase 6 boundary):

- the decision is the statement's own (the BoJ fuses decision + statement) — no
  separate Phase 5 decision subject is fabricated beyond the vote/decision
  wording above
- the Outlook for Economic Activity and Prices (projections) — Phase 9
- individual member opinions / dissents beyond the verbatim vote sentence —
  Phase 8
- hawkish/dovish interpretation, forex, trading — never

Design rules

- No fact is invented. A value/orientation is only produced when the source
  states it, with exact verbatim provenance.
- "around X percent" is retained as the explicit numeric target the source
  states; the "2% price stability target" phrasing is never mined as a value.
- Confidence: ``HIGH`` for quantitative values and categorical risk
  orientations, ``MEDIUM`` for verbatim assessments.
"""

from __future__ import annotations

import re

from ..classification.base import Confidence
from ..documents.base import NormalizedDocument
from ..facts import (
    METHOD_REGEX,
    ExtractionResult,
    Fact,
    FactLocation,
    FactPeriod,
    FactValue,
    LocationKind,
    PeriodKind,
    ValueKind,
    categorical,
    date_value,
    percentage,
)
from ..normalize import parse_datetime
from .base import StatementExtractor

EXTRACTION_VERSION = "6.1.0"

SUBJECT_MONETARY_POLICY = "monetary_policy"
SUBJECT_POLICY_RATE = "policy_rate"
SUBJECT_DECISION = "monetary_policy_decision"
SUBJECT_INFLATION = "inflation"
SUBJECT_INFLATION_EXPECTATIONS = "inflation_expectations"
SUBJECT_GROWTH = "growth"
SUBJECT_GDP = "gdp"
SUBJECT_RISK = "risk"
SUBJECT_INFLATION_RISK = "inflation_risk"
SUBJECT_GROWTH_RISK = "growth_risk"
SUBJECT_POLICY_GUIDANCE = "policy_guidance"

PREDICATE_DATE = "date"
PREDICATE_VALUE = "value"
PREDICATE_ASSESSMENT = "assessment"
PREDICATE_STATEMENT = "statement"

_RATE_ITEM = r"[0-9]+(?:\.[0-9]+)?"
_MONTH_WORDS = "January|February|March|April|May|June|July|August|September|October|November|December"

# "June 16, 2026" or "16 June 2026".
_DATE_TOKEN = re.compile(
    rf"\b(?:[0-9]{{1,2}}\s+(?:{_MONTH_WORDS})\s+[0-9]{{4}}|(?:{_MONTH_WORDS})\s+[0-9]{{1,2}},\s+[0-9]{{4}})\b",
    re.IGNORECASE,
)

# The short-term policy target: "… so that the uncollateralized overnight call
# rate will be formed at around 0.5 percent". "around" marks the target as an
# approximate level — kept as an explicit source-stated numeric target. The real
# source spells the unit as both "%" and the word "percent", so both are accepted.
_LEVEL = re.compile(
    rf"uncollateralized\s+overnight\s+call\s+rate[^.]*?\b(?:at\s+around|at)\s+"
    rf"(?P<token>{_RATE_ITEM}\s*(?:%|percent))",
    re.IGNORECASE,
)

# The decision wording: "1. At the Monetary Policy Meeting held today, the
# Policy Board decided to …".
_DECISION_STATEMENT = re.compile(r"\bdecided\s+(?:today\s+)?(?:to|that)\b|^at\s+the\s+monetary\s+policy\s+meeting", re.IGNORECASE)

# The explicit vote sentence is part of the BoJ decision language.
_VOTE_STATEMENT = re.compile(r"\bvote\s+was\b|\bunanimous\s+vote\b|\bballot\b", re.IGNORECASE)

_GUIDANCE_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bwill\s+continue\s+(?:to\s+)?(?:with\s+)?(?:monetary\s+easing|its\s+monetary|quantitative|easing)\b", re.IGNORECASE),
    re.compile(r"\bfor\s+as\s+long\s+as\s+necessary\b", re.IGNORECASE),
    re.compile(r"\bwill\s+not\s+hesitate\s+to\s+take\s+additional\b", re.IGNORECASE),
    re.compile(r"\bstands?\s+ready\s+to\b", re.IGNORECASE),
    re.compile(r"\bdata-dependent\b", re.IGNORECASE),
)

# Explicit value claim gate (a percentage immediately after a claim verb, with
# an optional reference year).
_VALUE_GATE = re.compile(
    r"(?:projected|expected|forecast|is\s+projected|is\s+expected)\s+(?:to\s+)?"
    r"(?:reach|be|stand|grow|remain|average|stay)\s+"
    r"|(?:stood at|rose to|fell to|increased to|decreased to|reached)\s+",
    re.IGNORECASE,
)
_PERCENT_WITH_YEAR = re.compile(
    rf"(?P<token>{_RATE_ITEM}\s*%)\s+(?:in|for|during)\s+(?:the\s+)?(?P<year>20[0-9]{{2}})\b",
    re.IGNORECASE,
)
_PERCENT_ONLY = re.compile(rf"(?P<token>{_RATE_ITEM}\s*%)", re.IGNORECASE)

# The 2% price-stability target is never a value fact; the value gate already
# excludes it (no claim verb), but this hard-guard keeps "around X percent"
# from leaking into the price narrative.
_TARGET_PHRASE = re.compile(r"\b2%\s+price\s+stability\s+target\b|\bthe\s+2%\s+target\b", re.IGNORECASE)

_RISK_ORIENTATION = re.compile(r"\b(?:downside|upside|two-sided|symmetric|broadly\s+balanced)\s+risks?\b|\btilted\b", re.IGNORECASE)


def _split_sentences(text: str) -> list[str]:
    # Break on sentence end AND on paragraph boundaries (blank lines): PDF
    # extraction lays the BoJ statement out as paragraphs, not sentences.
    return [part.strip() for part in re.split(r"(?<=\.)\s+|\n\s*\n", text or "") if part.strip()]


class BojStatementExtractor(StatementExtractor):
    bank = "boj"
    extraction_version = EXTRACTION_VERSION

    def extract(self, publication, document: NormalizedDocument) -> ExtractionResult:
        result = ExtractionResult(
            publication_id=publication.id or publication.dedup_key or "",
            document_id=document.document_id,
        )
        if not document.sections:
            result.warnings.append("no_sections")
            return result

        decision_date = self._decision_date(document)
        if decision_date is None:
            result.warnings.append("no_decision_date")
        if decision_date is not None:
            iso_date, raw, index = decision_date
            result.add(
                Fact(
                    publication_id=result.publication_id,
                    document_id=document.document_id,
                    subject=SUBJECT_DECISION,
                    predicate=PREDICATE_DATE,
                    value=date_value(iso_date, source_text=raw),
                    effective_date=parse_datetime(iso_date),
                    source_location=FactLocation(LocationKind.SECTION, section=index),
                    source_text=raw,
                    extraction_method=METHOD_REGEX,
                    extraction_version=EXTRACTION_VERSION,
                    confidence=Confidence.HIGH,
                )
            )

        levels = self._policy_target_levels(document)
        for index, token, value, source in levels:
            result.add(
                Fact(
                    publication_id=result.publication_id,
                    document_id=document.document_id,
                    subject=SUBJECT_POLICY_RATE,
                    predicate=PREDICATE_VALUE,
                    value=percentage(value, source_text=token),
                    source_location=FactLocation(LocationKind.SECTION, section=index),
                    source_text=source,
                    extraction_method=METHOD_REGEX,
                    extraction_version=EXTRACTION_VERSION,
                    confidence=Confidence.HIGH,
                )
            )

        counters: dict[str, int] = {}
        for index, section in enumerate(document.sections):
            for sentence in _split_sentences(section.text or ""):
                if _DECISION_STATEMENT.search(sentence) or _VOTE_STATEMENT.search(sentence):
                    result.add(self._text_fact(result, document, index, sentence, SUBJECT_DECISION, PREDICATE_STATEMENT, counters))
                elif self._matches(_GUIDANCE_ANCHORS, sentence):
                    result.add(self._text_fact(result, document, index, sentence, SUBJECT_POLICY_GUIDANCE, PREDICATE_STATEMENT, counters))
                elif _RISK_ORIENTATION.search(sentence):
                    self._add_risk_fact(result, document, index, sentence, counters)
                elif self._matches_value_claim(sentence):
                    self._add_value_fact(result, document, index, sentence, counters)

        return result

    # ------------------------------------------------------------------
    @staticmethod
    def _matches(anchors: tuple[re.Pattern, ...], sentence: str) -> bool:
        return any(anchor.search(sentence) for anchor in anchors)

    @staticmethod
    def _matches_value_claim(sentence: str) -> bool:
        return bool(_VALUE_GATE.search(sentence)) and not _TARGET_PHRASE.search(sentence)

    @classmethod
    def _text_fact(cls, result, document, index: int, sentence: str, subject: str, predicate: str, counters: dict) -> Fact:
        ordinal = counters.get(subject, 0)
        counters[subject] = ordinal + 1
        return Fact(
            publication_id=result.publication_id,
            document_id=document.document_id,
            subject=subject,
            predicate=predicate,
            value=FactValue(ValueKind.TEXT, value=sentence, source_text=sentence),
            effective_date=None,
            source_location=FactLocation(LocationKind.SECTION, section=index),
            source_text=sentence,
            extraction_method=METHOD_REGEX,
            extraction_version=EXTRACTION_VERSION,
            confidence=Confidence.MEDIUM,
            identity_qualifier=f"{subject}:{ordinal}",
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _decision_date(document) -> tuple[str, str, int] | None:
        for index, section in enumerate(document.sections):
            for candidate in (section.heading or "", section.text or ""):
                match = _DATE_TOKEN.search(candidate)
                if match:
                    dt = parse_datetime(match.group(0))
                    if dt is not None:
                        return dt.date().isoformat(), match.group(0), index
        return None

    @staticmethod
    def _policy_target_levels(document) -> list:
        found: list = []
        for index, section in enumerate(document.sections):
            match = _LEVEL.search(section.text or "")
            if not match:
                continue
            token = match.group("token")
            found.append((index, token, float(re.match(r"[0-9.]+", token).group(0)), _sentence_around(section.text, match.start())))
            break  # the first explicit target statement is authoritative
        return found

    # ------------------------------------------------------------------
    @classmethod
    def _add_risk_fact(cls, result, document, index: int, sentence: str, counters: dict) -> None:
        lower = sentence.lower()
        if "inflation" in lower:
            subject = SUBJECT_INFLATION_RISK
        elif "growth" in lower or "activity" in lower or "gdp" in lower or "economy" in lower:
            subject = SUBJECT_GROWTH_RISK
        else:
            subject = SUBJECT_RISK
        orientation = None
        if re.search(r"\b(?:broadly\s+)?balanced\b|\btwo-sided\b|\bsymmetric\b", lower):
            orientation = "balanced"
        elif re.search(r"\bdownside\b", lower):
            orientation = "downside"
        elif re.search(r"\bupside\b", lower):
            orientation = "upside"
        ordinal = counters.get(subject, 0)
        counters[subject] = ordinal + 1
        if orientation is not None:
            result.add(
                Fact(
                    publication_id=result.publication_id,
                    document_id=document.document_id,
                    subject=subject,
                    predicate=PREDICATE_ASSESSMENT,
                    value=categorical(orientation, source_text=sentence),
                    effective_date=None,
                    source_location=FactLocation(LocationKind.SECTION, section=index),
                    source_text=sentence,
                    extraction_method=METHOD_REGEX,
                    extraction_version=EXTRACTION_VERSION,
                    confidence=Confidence.HIGH,
                    identity_qualifier=f"{subject}:{ordinal}",
                )
            )
        else:
            result.add(cls._text_fact(result, document, index, sentence, subject, PREDICATE_ASSESSMENT, counters))

    # ------------------------------------------------------------------
    @classmethod
    def _add_value_fact(cls, result, document, index: int, sentence: str, counters: dict) -> None:
        lower = sentence.lower()
        if "inflation expectations" in lower:
            subject = SUBJECT_INFLATION_EXPECTATIONS
        elif "inflation" in lower or "prices" in lower or "cpi" in lower:
            subject = SUBJECT_INFLATION
        elif "gdp" in lower or "growth" in lower:
            subject = SUBJECT_GDP
        else:
            return
        year_match = _PERCENT_WITH_YEAR.search(sentence)
        if year_match:
            token = year_match.group("token")
            period = FactPeriod(PeriodKind.YEAR, year_match.group("year"), label=sentence[year_match.start("year") : year_match.end("year")])
        else:
            token_match = _PERCENT_ONLY.search(sentence)
            if token_match:
                token = token_match.group("token")
                period = None
            else:
                return
        ordinal = counters.get(subject, 0)
        counters[subject] = ordinal + 1
        result.add(
            Fact(
                publication_id=result.publication_id,
                document_id=document.document_id,
                subject=subject,
                predicate=PREDICATE_VALUE,
                value=percentage(float(re.match(r"[0-9.]+", token).group(0)), source_text=token),
                period=period,
                effective_date=None,
                source_location=FactLocation(LocationKind.SECTION, section=index),
                source_text=sentence,
                extraction_method=METHOD_REGEX,
                extraction_version=EXTRACTION_VERSION,
                confidence=Confidence.HIGH,
                identity_qualifier=f"{subject}:{ordinal}",
            )
        )


def _sentence_around(text: str, position: int) -> str:
    start = 0
    previous = text.rfind(". ", 0, position)
    if previous != -1:
        start = previous + 2
    end = text.find(".", position)
    end = len(text) if end == -1 else end + 1
    return text[start:end].strip()