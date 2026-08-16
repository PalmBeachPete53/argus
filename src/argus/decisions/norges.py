"""NORGES — Norges Bank policy rate decision extractor.

Extracts the facts of a Norges Bank policy rate decision from the normalized
press release, answering "what did the Monetary Policy and Financial Stability
Committee explicitly decide?":

- the decision date (the leading release-date line)
- the policy rate level — a single rate, percentage
- explicit changes ("cut the policy rate by 0.25 percentage points to 3.50
  percent") — sign preserved as basis points (0.25 pp ≡ 25 bps); a hold never
  fabricates a delta
- the decision wording ("… decided to lower the policy rate …"), verbatim
- forward guidance / policy-path sentences, verbatim

Deliberately NOT extracted (Phase 5 boundary):

- the rationale / outlook narrative ("A restrictive monetary policy stance …")
  — later phases
- the Monetary Policy Report — Phase 10
- votes / dissents — Phase 8; hawkish-dovish / forex — never

Design rules

- No fact is invented. A level/delta/date is only produced when the source
  states it, with exact verbatim provenance.
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

EXTRACTION_VERSION = "5.9.0"

SUBJECT_POLICY_RATE = "policy_rate"
SUBJECT_DECISION = "monetary_policy_decision"
SUBJECT_POLICY_GUIDANCE = "policy_guidance"

PREDICATE_DATE = "date"
PREDICATE_VALUE = "value"
PREDICATE_CHANGE = "change"
PREDICATE_STATEMENT = "statement"

_RATE_ITEM = r"[0-9]+(?:\.[0-9]+)?"
_MONTH_WORDS = "January|February|March|April|May|June|July|August|September|October|November|December"

# "3 May 2026" (day-month-year).
_DATE_TOKEN = re.compile(rf"\b[0-9]{{1,2}}\s+(?:{_MONTH_WORDS})\s+[0-9]{{4}}\b", re.IGNORECASE)

# Level: "… lower the policy rate to 3.50 percent", "… prepared to keep the
# policy rate at 4.5 percent".
_LEVEL = re.compile(
    rf"\bpolicy\s+rate\b(?:[^.]|\.(?!\s))*?\b(?:to|at)\s+(?P<token>{_RATE_ITEM}\s*(?:%|per\s+cent(?:s)?))",
    re.IGNORECASE,
)

_DIRECTIONAL = r"(?:lower|decrease|reduce|cut|drop|ease|increase|raise|hike|lift)"
_CHANGE = re.compile(
    rf"\b(?P<verb>{_DIRECTIONAL})\w*\s+(?:the\s+)?policy\s+rate\b[^.]*?\bby\s+"
    rf"(?P<amount>{_RATE_ITEM}\s*(?:percentage\s+points?|basis\s+points?))",
    re.IGNORECASE,
)

_DECISION_STATEMENT = re.compile(r"\b(?:decided|announced)\s+(?:today\s+)?(?:to|that)\b", re.IGNORECASE)
_DECISION_CONTEXT = re.compile(r"\bpolicy\s+rate\b|\bcommittee\b|\bmonetary\s+policy\b|\bnorges\s+bank\b", re.IGNORECASE)

_GUIDANCE_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bwill\s+be\s+kept\s+around\b", re.IGNORECASE),
    re.compile(r"\bpolicy\s+rate\s+will\s+(?:be|not)\b", re.IGNORECASE),
    re.compile(r"\bstands?\s+ready\s+to\s+adjust\b", re.IGNORECASE),
    re.compile(r"\bwill\s+continue\s+to\s+(?:assess|monitor|keep)\b", re.IGNORECASE),
    re.compile(r"\bthe\s+policy\s+rate\s+will\s+not\s+be\s+changed\b", re.IGNORECASE),
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


class NorgesDecisionExtractor(DecisionExtractor):
    bank = "norges"
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
        else:
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
                amount = match.group("amount")
                magnitude = float(re.match(r"[0-9.]+", amount).group(0))
                if "basis points" in amount.lower():
                    delta = magnitude
                else:  # "percentage points" → basis points (1 pp = 100 bps)
                    delta = magnitude * 100
                verb = match.group("verb").lower()
                sign = -1 if re.search(r"(?:lower|decrease|reduce|cut|drop|ease)", verb) else 1
                changes.append((index, _sentence_around(text, match.start()), amount, round(sign * delta, 2)))
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