"""BOE — Bank of England (Monetary Policy Committee) policy decision extractor.

Extracts the facts of a Bank of England monetary policy decision from the
normalized decision statement, answering "what did the MPC explicitly decide or
announce as part of the decision?":

- the decision date (the leading release-date line, per the Bank's layout)
- Bank Rate — a single policy rate (the MPC's instrument), level only
- explicit changes ("reduce Bank Rate by 0.25 percentage points") — sign
  preserved as basis points (0.25 pp ≡ 25 bps, a unit conversion; the verbatim
  "0.25 percentage points" wording is preserved in provenance)
- the decision wording ("… voted to maintain Bank Rate at 5.25%"), verbatim
- forward guidance as part of the decision, verbatim

Deliberately NOT extracted (Phase 4.1 boundary):

- the vote split / individual preferences of MPC members — Phase 4.4 (minutes);
  never fabricated here
- the Macroeconomic Projections / Monetary Policy Report — Phase 4.5 / Phase 4.6
- a monetary "risk assessment" — not part of the Bank Rate decision release

Design rules

- No fact is invented. A level/delta/date is only produced when the source
  states it, and every Fact preserves an exact verbatim supporting passage and
  value wording.
- A hold ("voted to maintain Bank Rate at 5.25%") produces a level fact only —
  never a fabricated zero delta.
- Confidence is ``HIGH`` for every fact.
- "percentage points" are converted to basis points only for the canonical
  ``change`` value kind (BASIS_POINTS); the source wording is preserved
  verbatim.
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

EXTRACTION_VERSION = "5.4.0"

# Canonical BoE instrument subject (Phase 4.1 vocabulary extension).
SUBJECT_BANK_RATE = "bank_rate"
SUBJECT_DECISION = "monetary_policy_decision"
SUBJECT_POLICY_GUIDANCE = "policy_guidance"

PREDICATE_DATE = "date"
PREDICATE_VALUE = "value"
PREDICATE_CHANGE = "change"
PREDICATE_STATEMENT = "statement"

_RATE_ITEM = r"[0-9]+(?:\.[0-9]+)?"
_MONTH_WORDS = "January|February|March|April|May|June|July|August|September|October|November|December"

# "20 August 2026" (day-month-year).
_DATE_TOKEN = re.compile(rf"\b[0-9]{{1,2}}\s+(?:{_MONTH_WORDS})\s+[0-9]{{4}}\b", re.IGNORECASE)

# Bank Rate level: "… maintain Bank Rate at 5.25%", "… set Bank Rate at 4.75%".
_IN_SENTENCE = r"(?:[^.]|\.(?!\s))*?"  # a '.' is a decimal, not a sentence break
_LEVEL = re.compile(
    rf"\bbank\s+rate\b{_IN_SENTENCE}\b(?:to|at)\s+(?P<token>{_RATE_ITEM}\s*%)",
    re.IGNORECASE,
)
# Also "… Bank Rate was reduced from 5.25% to 5.00%".
_LEVEL_TO = re.compile(
    rf"\bbank\s+rate\b{_IN_SENTENCE}\b(?:to|at)\s+(?P<token>{_RATE_ITEM}\s*%)",
    re.IGNORECASE,
)

_DIRECTIONAL = r"(?:lower|decrease|reduce|cut|drop|ease|increase|raise|hike|lift)"
# "… Bank Rate by 0.25 percentage points" / "… by 25 basis points".
_CHANGE = re.compile(
    rf"\b(?P<verb>{_DIRECTIONAL})\w*\s+(?:bank\s+rate\b)[^.]*?\bby\s+"
    rf"(?P<amount>{_RATE_ITEM}\s*(?:percentage\s+points?|basis\s+points?))",
    re.IGNORECASE,
)

# The decision wording: "… voted to …" / "… decided to …" sentences that refer
# to Bank Rate or the policy stance.
_DECISION_STATEMENT = re.compile(r"\b(?:voted|decided)\s+(?:today\s+)?(?:to|that|by)\b", re.IGNORECASE)
_DECISION_CONTEXT = re.compile(r"\bbank\s+rate\b|\bmonetary\s+policy\b|\bcommittee\b", re.IGNORECASE)

_GUIDANCE_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bwill\s+continue\s+to\s+(?:monitor|assess|review)\b", re.IGNORECASE),
    re.compile(r"\bstands?\s+ready\s+to\s+adjust\b", re.IGNORECASE),
    re.compile(r"\bdata-dependent\b", re.IGNORECASE),
    re.compile(r"\bmeeting\s+by\s+meeting\b", re.IGNORECASE),
    re.compile(r"\bfor\s+as\s+long\s+as\s+necessary\b", re.IGNORECASE),
    re.compile(r"\bwill\s+be\s+guided\s+by\b", re.IGNORECASE),
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


class BoeDecisionExtractor(DecisionExtractor):
    bank = "boe"
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
                    subject=SUBJECT_BANK_RATE,
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
            result.warnings.append("no_bank_rate")

        for ordinal, (index, source, amount, delta) in enumerate(self._rate_changes(document)):
            result.add(
                Fact(
                    publication_id=result.publication_id,
                    document_id=document.document_id,
                    subject=SUBJECT_BANK_RATE,
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
            match = _LEVEL_TO.search(section.text or "")
            if not match:
                continue
            token = match.group("token")
            found.append((index, token, float(re.match(r"[0-9.]+", token).group(0)), _sentence_around(section.text, match.start())))
            break  # the first resolvable Bank Rate statement is authoritative
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
                sign = -1 if re.search(r"\b(?:lower|decrease|reduce|cut|drop|ease)\b", verb) else 1
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