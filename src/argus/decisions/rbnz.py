"""RBNZ — Reserve Bank of New Zealand Official Cash Rate decision extractor.

Extracts the facts of an RBNZ Official Cash Rate (OCR) decision from the
normalized media release:

- the decision date (the leading release-date line)
- the OCR level — a single policy rate, percentage
- explicit changes ("reduce the Official Cash Rate (OCR) by 50 basis points to
  4.25 percent") — sign preserved as basis points; a hold never fabricates a
  delta
- the decision wording ("The Monetary Policy Committee today agreed to …"),
  verbatim
- forward guidance / OCR-prospect sentences, verbatim

Deliberately NOT extracted (Phase 4.1 boundary):

- the Monetary Policy Statement (economic outlook / projections) — Phase 4.5/10
- votes / dissents — Phase 4.4 (minutes); hawkish-dovish / forex — never

Design rules

- No fact is invented. A level/delta/date is only produced when the source
  states it, with exact verbatim provenance.
- The **Official Cash Rate (OCR)** is the RBNZ's own instrument name — it is
  kept as its own canonical subject (``official_cash_rate``), never merged with
  another bank's policy rate.
- Confidence is ``HIGH`` for every fact.
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
    percentage,
)
from ..normalize import parse_datetime
from .base import DecisionExtractor

EXTRACTION_VERSION = "5.8.0"

SUBJECT_OCR = "official_cash_rate"
SUBJECT_DECISION = "monetary_policy_decision"
SUBJECT_POLICY_GUIDANCE = "policy_guidance"

PREDICATE_DATE = "date"
PREDICATE_VALUE = "value"
PREDICATE_CHANGE = "change"
PREDICATE_STATEMENT = "statement"

_RATE_ITEM = r"[0-9]+(?:\.[0-9]+)?"
_MONTH_WORDS = "January|February|March|April|May|June|July|August|September|October|November|December"

_DATE_TOKEN = re.compile(rf"\b[0-9]{{1,2}}\s+(?:{_MONTH_WORDS})\s+[0-9]{{4}}\b", re.IGNORECASE)

# Level: "… reduce the Official Cash Rate (OCR) by 50 basis points to 4.25
# percent", "… keep the OCR at 5.50 percent".
_LEVEL = re.compile(
    rf"(?:official\s+cash\s+rate\b|ocr\b)(?:[^.]*?\b(?:to|at)\s+)?(?P<token>{_RATE_ITEM}\s*(?:%|per\s+cent(?:s)?))",
    re.IGNORECASE,
)

_DIRECTIONAL = r"(?:lower|decrease|reduce|cut|drop|ease|increase|raise|hike|lift)"
_CHANGE = re.compile(
    rf"\b(?P<verb>{_DIRECTIONAL})\w*\s+(?:the\s+)?(?:official\s+cash\s+rate|ocr)\b[^.]*?\bby\s+"
    rf"(?P<amount>{_RATE_ITEM}\s*basis\s+points?)",
    re.IGNORECASE,
)

_DECISION_STATEMENT = re.compile(r"\b(?:agreed|decided|voted)\s+(?:today\s+)?(?:to|that|by)\b", re.IGNORECASE)
_DECISION_CONTEXT = re.compile(r"\bofficial\s+cash\s+rate\b|\bocr\b|\bmonetary\s+policy\s+committee\b", re.IGNORECASE)

_GUIDANCE_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bwill\s+continue\s+(?:to\s+)?(?:assess|monitor)\b", re.IGNORECASE),
    re.compile(r"\bmeeting\s+by\s+meeting\b", re.IGNORECASE),
    re.compile(r"\bdata-dependent\b", re.IGNORECASE),
    re.compile(r"\bprospects\s+for\s+(?:the\s+)?(?:ocr|official\s+cash\s+rate)\b", re.IGNORECASE),
    re.compile(r"\bfuture\s+(?:ocr\s+)?decisions\b", re.IGNORECASE),
)


def _split_sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=\.)\s+", text or "") if part.strip()]


def _sentence_around(text: str, position: int) -> str:
    start = 0
    previous = text.rfind(". ", 0, position)
    if previous != -1:
        start = previous + 2
    end = text.find(".", position)
    end = len(text) if end == -1 else end + 1
    return text[start:end].strip()


class RbnzDecisionExtractor(DecisionExtractor):
    bank = "rbnz"
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

        levels = self._rate_levels(document)
        if levels:
            index, token, value, source = levels[0]
            result.add(
                Fact(
                    publication_id=result.publication_id,
                    document_id=document.document_id,
                    subject=SUBJECT_OCR,
                    predicate=PREDICATE_VALUE,
                    value=percentage(value, source_text=token),
                    source_location=FactLocation(LocationKind.SECTION, section=index),
                    source_text=source,
                    extraction_method=METHOD_REGEX,
                    extraction_version=EXTRACTION_VERSION,
                    confidence=Confidence.HIGH,
                )
            )
        else:
            result.warnings.append("no_official_cash_rate")

        for index, source, amount, delta in self._rate_changes(document):
            result.add(
                Fact(
                    publication_id=result.publication_id,
                    document_id=document.document_id,
                    subject=SUBJECT_OCR,
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
    def _rate_levels(document) -> list:
        found: list = []
        for index, section in enumerate(document.sections):
            match = _LEVEL.search(section.text or "")
            if not match:
                continue
            token = match.group("token")
            found.append((index, token, float(re.match(r"[0-9.]+", token).group(0)), _sentence_around(section.text, match.start())))
            break
        return found

    @staticmethod
    def _rate_changes(document) -> list:
        changes: list = []
        for index, section in enumerate(document.sections):
            text = section.text or ""
            for match in _CHANGE.finditer(text):
                verb = match.group("verb").lower()
                sign = -1 if re.search(r"(?:lower|decrease|reduce|cut|drop|ease)", verb) else 1
                amount = match.group("amount")
                changes.append((index, _sentence_around(text, match.start()), amount, round(sign * float(re.match(r"[0-9.]+", amount).group(0)), 2)))
        return changes

    @staticmethod
    def _decision_wordings(document) -> list[tuple[int, int, str]]:
        found: list[tuple[int, int, str]] = []
        for index, section in enumerate(document.sections):
            for sentence in _split_sentences(section.text or ""):
                if _DECISION_STATEMENT.search(sentence) and _DECISION_CONTEXT.search(sentence):
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