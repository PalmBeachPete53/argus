"""FED — Federal Open Market Committee monetary policy decision extractor.

Extracts the facts of a Federal Open Market Committee (FOMC) monetary policy
decision from the normalized statement, answering "what did the FOMC explicitly
decide or announce as part of the decision?":

- the decision date (the leading release-date line, per the Fed's statement
  layout; never an arbitrary date elsewhere)
- the federal funds **target range** — the Fed announces a range, not single
  rates, so the level is a ``ValueKind.RANGE`` (min/max), never a single
  percentage
- explicit changes ("lowered the target range by 25 basis points") — sign
  preserved as basis points, never invented for a hold
- the decision wording — the statement's "The Federal Open Market Committee …
  decided to …" sentences, verbatim
- forward guidance as part of the decision, verbatim (never classified, never
  interpreted)

Deliberately NOT extracted (Phase 5 boundary):

- individual governors' dissents — a separate matter handled in the FOMC
  minutes (Phase 8); never fabricated as a vote
- the macro-economic justification / projections — separate publications
  (Phase 6 / Phase 9)
- the FOMC's "Implementation Note" (reserve balances / balance sheet) — a
  distinct publication

Design rules

- No fact is invented. A level/range/delta/date is only produced when the
  source states it, and every Fact preserves an exact verbatim supporting
  passage (``source_text``) and value wording (``FactValue.source_text``).
- The target range is the Fed's own structure — min/max are read from the
  source ("4.50 to 4.75 percent"), never mixed and never converted to a single
  "mid" rate.
- A hold ("remains at 4.25 to 4.50 percent") produces level facts only, never
  a fabricated zero basis-point change.
- Confidence is ``HIGH`` for every fact: each fact is deterministically
  identified from explicit source wording.
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
    FactValue,
    LocationKind,
    ValueKind,
    basis_points,
    date_value,
    range_value,
)
from ..normalize import parse_datetime
from .base import DecisionExtractor

EXTRACTION_VERSION = "5.3.0"

# Canonical Fed instrument subject (Phase 5 vocabulary extension, documented in
# docs/EXTRACTORS.md): the federal funds *target range*.
SUBJECT_POLICY_RATE = "policy_rate"
SUBJECT_DECISION = "monetary_policy_decision"
SUBJECT_POLICY_GUIDANCE = "policy_guidance"

PREDICATE_DATE = "date"
PREDICATE_VALUE = "value"
PREDICATE_CHANGE = "change"
PREDICATE_STATEMENT = "statement"

_RATE_ITEM = r"[0-9]+(?:\.[0-9]+)?"
_MONTH_WORDS = "January|February|March|April|May|June|July|August|September|October|November|December"

# A release-date token, verbatim, as the Fed states it ("September 18, 2026").
_DATE_TOKEN = re.compile(rf"\b(?:{_MONTH_WORDS})\s+[0-9]{{1,2}},\s+[0-9]{{4}}\b", re.IGNORECASE)

# The target range level:
#   "… lower the target range for the federal funds rate by 25 basis points to
#     4.50 to 4.75 percent."
#   "… the target range … remains at 4.25 to 4.50 percent."
_RANGE_LEVEL = re.compile(
    rf"federal\s+funds\s+rate[^.]*?\b(?:to|at)\s+(?P<min>{_RATE_ITEM})\s+to\s+(?P<max>{_RATE_ITEM})\s+percent",
    re.IGNORECASE,
)

_DIRECTIONAL = r"(?:lower|decrease|reduce|cut|drop|ease|increase|raise|hike|lift)"
_AMOUNT = rf"{_RATE_ITEM}\s+basis\s+points?"
# An explicit change sentence, e.g. "lower the target range for the federal
# funds rate by 25 basis points".
_CHANGE = re.compile(
    rf"\b(?P<verb>{_DIRECTIONAL})\w*\s+(?:the\s+)?(?:target\s+range\s+for\s+the\s+)?federal\s+funds\s+rate"
    rf"[^.]*?\bby\s+(?P<amount>{_AMOUNT})",
    re.IGNORECASE,
)

# The decision wording: the statement's "… decided to …" sentences, verbatim.
_DECISION_STATEMENT = re.compile(r"\bdecided\s+(?:today\s+)?(?:to|that)\b", re.IGNORECASE)

# Explicit forward-guidance anchors (narrow — never macro analysis).
_GUIDANCE_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\badditional\s+policy\s+firming\b", re.IGNORECASE),
    re.compile(r"\bwill\s+keep\s+(?:the\s+)?(?:target\s+range\s+for\s+the\s+)?federal\s+funds\s+rate\b", re.IGNORECASE),
    re.compile(r"\bfor\s+as\s+long\s+as\s+necessary\b", re.IGNORECASE),
    re.compile(r"\bdata-dependent\b", re.IGNORECASE),
    re.compile(r"\bwill\s+carefully\s+assess\b", re.IGNORECASE),
    re.compile(r"\bstands?\s+ready\s+to\s+adjust\b", re.IGNORECASE),
)


def _split_sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=\.)\s+", text or "") if part.strip()]


class FedDecisionExtractor(DecisionExtractor):
    bank = "fed"
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
            dt = parse_datetime(iso_date)
            result.add(
                Fact(
                    publication_id=result.publication_id,
                    document_id=document.document_id,
                    subject=SUBJECT_DECISION,
                    predicate=PREDICATE_DATE,
                    value=date_value(iso_date, source_text=raw),
                    effective_date=dt,
                    source_location=FactLocation(LocationKind.SECTION, section=index),
                    source_text=raw,
                    extraction_method=METHOD_REGEX,
                    extraction_version=EXTRACTION_VERSION,
                    confidence=Confidence.HIGH,
                )
            )

        levels = self._target_range_levels(document)
        for index, min_val, max_val, token, source in levels:
            result.add(
                Fact(
                    publication_id=result.publication_id,
                    document_id=document.document_id,
                    subject=SUBJECT_POLICY_RATE,
                    predicate=PREDICATE_VALUE,
                    value=range_value(min_val, max_val, unit="percent", source_text=token),
                    source_location=FactLocation(LocationKind.SECTION, section=index),
                    source_text=source,
                    extraction_method=METHOD_REGEX,
                    extraction_version=EXTRACTION_VERSION,
                    confidence=Confidence.HIGH,
                )
            )
        if not levels:
            result.warnings.append("no_policy_rate")

        for index, source, amount, delta in self._range_changes(document):
            result.add(
                Fact(
                    publication_id=result.publication_id,
                    document_id=document.document_id,
                    subject=SUBJECT_POLICY_RATE,
                    predicate=PREDICATE_CHANGE,
                    value=basis_points(delta, source_text=amount),
                    source_location=FactLocation(LocationKind.SECTION, section=index),
                    source_text=source,
                    extraction_method=METHOD_REGEX,
                    extraction_version=EXTRACTION_VERSION,
                    confidence=Confidence.HIGH,
                )
            )

        for ordinal, index, sentence in self._decision_wordings(document):
            result.add(
                Fact(
                    publication_id=result.publication_id,
                    document_id=document.document_id,
                    subject=SUBJECT_DECISION,
                    predicate=PREDICATE_STATEMENT,
                    value=FactValue(ValueKind.TEXT, value=sentence, source_text=sentence),
                    source_location=FactLocation(LocationKind.SECTION, section=index),
                    source_text=sentence,
                    extraction_method=METHOD_REGEX,
                    extraction_version=EXTRACTION_VERSION,
                    confidence=Confidence.HIGH,
                    identity_qualifier=f"statement:{ordinal}",
                )
            )

        for ordinal, index, sentence in self._forward_guidance(document):
            result.add(
                Fact(
                    publication_id=result.publication_id,
                    document_id=document.document_id,
                    subject=SUBJECT_POLICY_GUIDANCE,
                    predicate=PREDICATE_STATEMENT,
                    value=FactValue(ValueKind.TEXT, value=sentence, source_text=sentence),
                    source_location=FactLocation(LocationKind.SECTION, section=index),
                    source_text=sentence,
                    extraction_method=METHOD_REGEX,
                    extraction_version=EXTRACTION_VERSION,
                    confidence=Confidence.HIGH,
                    identity_qualifier=f"guidance:{ordinal}",
                )
            )
        return result

    # ------------------------------------------------------------------
    # decision date
    # ------------------------------------------------------------------
    @staticmethod
    def _decision_date(document) -> tuple[str, str, int] | None:
        """Release date from the leading heading-less paragraphs (the Fed's
        "For release at 2:00 p.m. EDT" + "June 18, 2026" layout). Never an
        arbitrary date inside the statement body."""
        for index, section in enumerate(document.sections):
            match = _DATE_TOKEN.search(section.heading or "")
            if match:
                dt = parse_datetime(match.group(0))
                if dt is not None:
                    return dt.date().isoformat(), match.group(0), index
        for index, section in enumerate(document.sections):
            if not section.heading:
                match = _DATE_TOKEN.search(section.text or "")
                if match:
                    dt = parse_datetime(match.group(0))
                    if dt is not None:
                        return dt.date().isoformat(), match.group(0), index
            else:
                match = _DATE_TOKEN.search(section.heading)
                if match:
                    dt = parse_datetime(match.group(0))
                    if dt is not None:
                        return dt.date().isoformat(), match.group(0), index
        return None

    # ------------------------------------------------------------------
    # target range / changes
    # ------------------------------------------------------------------
    @staticmethod
    def _target_range_levels(document) -> list:
        """One federal funds target-range level, from the explicit range
        statement. Range bounds are read from the source, never assumed."""
        found: list = []
        for index, section in enumerate(document.sections):
            match = _RANGE_LEVEL.search(section.text or "")
            if not match:
                continue
            start = match.start()
            previous = section.text.rfind(". ", 0, start)
            if previous != -1:
                start = previous + 2
            end = section.text.find(".", match.start())
            end = len(section.text) if end == -1 else end + 1
            sentence = section.text[start:end].strip()
            found.append(
                (
                    index,
                    float(match.group("min")),
                    float(match.group("max")),
                    match.group(0),
                    sentence,
                )
            )
            break  # the first resolvable range statement is authoritative
        return found

    @staticmethod
    def _range_changes(document) -> list:
        """Explicit, directional target-range changes ("by N basis points").
        Sign: lowering/reducing verbs negative, raising/increasing positive."""
        changes: list = []
        for index, section in enumerate(document.sections):
            text = section.text or ""
            for match in _CHANGE.finditer(text):
                start = match.start()
                previous = text.rfind(". ", 0, start)
                if previous != -1:
                    start = previous + 2
                sentence_end = text.find(".", match.start())
                end = len(text) if sentence_end == -1 else sentence_end + 1
                sentence = text[start:end].strip()
                verb = match.group("verb").lower()
                sign = -1 if re.search(r"(?:lower|decrease|reduce|cut|drop|ease)", verb) else 1
                changes.append((index, sentence, match.group("amount"), round(sign * float(re.match(r"[0-9.]+", match.group("amount")).group(0)), 2)))
        return changes

    # ------------------------------------------------------------------
    # decision wording / forward guidance
    # ------------------------------------------------------------------
    @staticmethod
    def _decision_wordings(document) -> list[tuple[int, int, str]]:
        found: list[tuple[int, int, str]] = []
        for index, section in enumerate(document.sections):
            for sentence in _split_sentences(section.text or ""):
                if _DECISION_STATEMENT.search(sentence) and re.search(
                    r"\b(federal\s+funds|target\s+range|monetary\s+policy)\b", sentence, re.IGNORECASE
                ):
                    found.append((len(found), index, sentence))
        return found

    @staticmethod
    def _forward_guidance(document) -> list[tuple[int, int, str]]:
        found: list[tuple[int, int, str]] = []
        for index, section in enumerate(document.sections):
            for sentence in _split_sentences(section.text or ""):
                for anchor in _GUIDANCE_ANCHORS:
                    if anchor.search(sentence):
                        found.append((len(found), index, sentence))
                        break
        return found