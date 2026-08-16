"""BOC — Bank of Canada (FAD) policy interest rate decision extractor.

Extracts the facts of a Bank of Canada announcement of its policy interest rate
(the "target for the overnight rate" / "policy interest rate") from the
normalized press release:

- the decision date (the leading release-date line)
- the policy rate level — a single rate, percentage
- explicit changes ("lowering its policy interest rate by 25 basis points to
  4.75 per cent") — sign preserved as basis points; a hold never fabricates a
  delta
- the decision wording, verbatim
- forward guidance / policy-commitment sentences, verbatim

Deliberately NOT extracted (Phase 5 boundary):

- the "key drivers and outlook" narrative (inflation/growth analysis) — later
  phases
- the Monetary Policy Report — Phase 10
- votes / hawkish-dovish / forex — never

Design rules

- No fact is invented. A level/delta/date is only produced when the source
  states it, with exact verbatim provenance.
- Percentage tokens accept both "%" and "per cent" (the Bank's wording).
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

EXTRACTION_VERSION = "5.6.0"

SUBJECT_POLICY_RATE = "policy_rate"
SUBJECT_DECISION = "monetary_policy_decision"
SUBJECT_POLICY_GUIDANCE = "policy_guidance"

PREDICATE_DATE = "date"
PREDICATE_VALUE = "value"
PREDICATE_CHANGE = "change"
PREDICATE_STATEMENT = "statement"

_RATE_ITEM = r"[0-9]+(?:\.[0-9]+)?"
_MONTH_WORDS = "January|February|March|April|May|June|July|August|September|October|November|December"

# "24 July 2026" or "July 24, 2026".
_DATE_TOKEN = re.compile(
    rf"\b(?:[0-9]{{1,2}}\s+(?:{_MONTH_WORDS})\s+[0-9]{{4}}|(?:{_MONTH_WORDS})\s+[0-9]{{1,2}},\s+[0-9]{{4}})\b",
    re.IGNORECASE,
)

# Level: "… lowering its policy interest rate by 25 basis points to 4.75 per
# cent", "… target for the overnight rate at 4.50 percent", "… at 4.75%".
_LEVEL = re.compile(
    rf"(?:policy\s+interest\s+rate|target\s+for\s+the\s+overnight\s+rate|overnight\s+rate\s+target)\b"
    rf"[^.]*?\b(?:to|at)\s+(?P<token>{_RATE_ITEM}\s*(?:%|per\s+cent(?:s)?))",
    re.IGNORECASE,
)

_DIRECTIONAL = r"(?:lower|decrease|reduce|cut|drop|ease|increase|raise|hike|lift)"
_CHANGE = re.compile(
    rf"\b(?P<verb>{_DIRECTIONAL})\w*\s+(?:its\s+|the\s+)?(?:policy\s+interest\s+rate|target\s+for\s+the\s+overnight\s+rate)\b"
    rf"[^.]*?\bby\s+(?P<amount>{_RATE_ITEM}\s*basis\s+points?)",
    re.IGNORECASE,
)

_DECISION_STATEMENT = re.compile(
    r"\b(?:announced|decided|is\s+lowering|is\s+raising|will\s+lower|will\s+raise|held|maintaining|kept)\b",
    re.IGNORECASE,
)
_DECISION_CONTEXT = re.compile(r"\b(?:policy\s+interest\s+rate|overnight\s+rate|target\s+for\s+the\s+overnight\s+rate)\b", re.IGNORECASE)

_GUIDANCE_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bwill\s+continue\s+(?:to\s+)?(?:monitor|assess|adjust)\b", re.IGNORECASE),
    re.compile(r"\bstands?\s+ready\s+to\s+adjust\b", re.IGNORECASE),
    re.compile(r"\bdata-dependent\b", re.IGNORECASE),
    re.compile(r"\bmeeting\s+by\s+meeting\b", re.IGNORECASE),
    re.compile(r"\bpolicy\s+will\s+need\s+to\s+remain\b", re.IGNORECASE),
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


class BocDecisionExtractor(DecisionExtractor):
    bank = "boc"
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
        if not levels:
            result.warnings.append("no_policy_rate")

        for ordinal, (index, source, amount, delta) in enumerate(self._rate_changes(document)):
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
                    identity_qualifier=f"change:{ordinal}",
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