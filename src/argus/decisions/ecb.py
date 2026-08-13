"""ECB — monetary policy decision extractor.

Extracts the core facts of an ECB monetary policy decision from the normalized
document text:

- decision date (normally the standalone date paragraph before the H1)
- the three key ECB interest rates:
  ``deposit_facility_rate``, ``main_refinancing_rate``, ``marginal_lending_rate``
- explicit rate changes (direction + magnitude, e.g. "lowered by 25 basis
  points") — sign preserved as basis points (negative = easing)
- effective date when explicitly stated ("with effect from …")

No fact is invented: a value is only produced when the text states it, and every
Fact carries ``source_text`` + ``source_location`` (section index) pointing into
the normalized document.
"""

from __future__ import annotations

import re
from datetime import datetime

from ..classification.base import Confidence
from ..documents.base import NormalizedDocument
from ..facts import (
    METHOD_REGEX,
    ExtractionResult,
    Fact,
    FactLocation,
    LocationKind,
    basis_points,
    date_value,
    percentage,
)
from ..normalize import parse_datetime
from .base import DecisionExtractor

EXTRACTION_VERSION = "5.0.0"

# ---------------------------------------------------------------------------
# Canonical Phase 5 subjects (controlled vocabulary, see docs/EXTRACTORS.md).
# ---------------------------------------------------------------------------
SUBJECT_DECISION = "monetary_policy_decision"
SUBJECT_DEPOSIT_FACILITY = "deposit_facility_rate"
SUBJECT_MAIN_REFINANCING = "main_refinancing_rate"
SUBJECT_MARGINAL_LENDING = "marginal_lending_rate"

PREDICATE_DATE = "date"
PREDICATE_VALUE = "value"
PREDICATE_CHANGE = "change"

# Instrument phrase → canonical subject, in ECB's canonical announcement order.
_INSTRUMENT_SUBJECTS: tuple[tuple[str, str], ...] = (
    ("main refinancing operations", SUBJECT_MAIN_REFINANCING),
    ("marginal lending facility", SUBJECT_MARGINAL_LENDING),
    ("deposit facility", SUBJECT_DEPOSIT_FACILITY),
)
_CANONICAL_ORDER = [s for _, s in _INSTRUMENT_SUBJECTS]

_MODIFIERS: dict[str, int] = {
    "lower": -1,
    "decrease": -1,
    "reduce": -1,
    "cut": -1,
    "drop": -1,
    "ease": -1,
    "increase": 1,
    "raise": 1,
    "hike": 1,
    "lift": 1,
}

_MONTHS = r"(January|February|March|April|May|June|July|August|September|October|November|December)"
_DATE_TOKEN = re.compile(
    rf"\b[0-9]{{1,2}}\s+{_MONTHS}\s+[0-9]{{4}}\b|\b[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}\b",
    re.IGNORECASE,
)
# Combined ECB enumeration: "… decreased to 2.00%, 2.25% and 1.75% respectively"
# or, for a hold, "… remain at 2.00%, 2.25% and 1.75% respectively".
_RATES_ENUMERATED = re.compile(
    r"(?:lowered|decreased|increased|raised|reduced|cut|hiked|set|kept|maintained|held|left|remain|stayed)"
    r"\s+(?:to|at)\s+"
    r"(?P<a>[0-9]+(?:\.[0-9]+)?)%\s*,\s*(?P<b>[0-9]+(?:\.[0-9]+)?)%\s+and\s+(?P<c>[0-9]+(?:\.[0-9]+)?)%"
    r"\s+respectively",
    re.IGNORECASE,
)
_INSTRUMENTS_ALT = rf"(?:{'|'.join(p for p, _ in _INSTRUMENT_SUBJECTS)})"

# Per-instrument level: "lower the deposit facility rate to 1.75 per cent".
_RATE_SINGLE = re.compile(
    rf"(?:{'|'.join(_MODIFIERS.keys())})\s+"
    rf"(?:the\s+)?(?:interest\s+rate\s+on\s+the\s+|interest\s+rates?\s+on\s+the\s+)?"
    rf"\s*{_INSTRUMENTS_ALT}\s+rate"
    rf"[^.]*?\b(?:to|at)\s+([0-9]+(?:\.[0-9]+)?)\s*(?:%|per\s+cent)",
    re.IGNORECASE,
)
# Per-instrument change: "… lower the deposit facility rate by 25 basis points".
_RATE_CHANGE_SINGLE = re.compile(
    rf"(?:{'|'.join(_MODIFIERS.keys())})\s+"
    rf"(?:the\s+)?(?:interest\s+rate\s+on\s+the\s+|interest\s+rates?\s+on\s+the\s+)?"
    rf"\s*{_INSTRUMENTS_ALT}\s+rate"
    rf"[^.]*?\bby\s+([0-9]+(?:\.[0-9]+)?)\s+basis\s+points?",
    re.IGNORECASE,
)
# Broad change: "… lower the three key ECB interest rates by 25 basis points".
_RATE_CHANGE_BROAD = re.compile(
    rf"(?:{'|'.join(_MODIFIERS.keys())})[^.]*?\bby\s+([0-9]+(?:\.[0-9]+)?)\s+basis\s+points?",
    re.IGNORECASE,
)
_EFFECTIVE_DATE = re.compile(
    r"with\s+effect\s+from\s+([0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4}|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


class EcbDecisionExtractor(DecisionExtractor):
    bank = "ecb"
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

        rates_section_index = self._rates_section_index(document)
        rates_section = document.sections[rates_section_index] if rates_section_index is not None else None
        if rates_section is None:
            result.warnings.append("no_rates_section")

        effective = self._effective_date(rates_section) if rates_section is not None else None

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

        for subject, section_index, raw, value, conf in self._rate_levels(document, rates_section_index):
            result.add(
                Fact(
                    publication_id=result.publication_id,
                    document_id=document.document_id,
                    subject=subject,
                    predicate=PREDICATE_VALUE,
                    value=percentage(value, source_text=raw),
                    effective_date=effective,
                    source_location=FactLocation(LocationKind.SECTION, section=section_index),
                    source_text=raw,
                    extraction_method=METHOD_REGEX,
                    extraction_version=EXTRACTION_VERSION,
                    confidence=conf,
                )
            )

        for subject, section_index, raw, delta in self._rate_changes(document):
            result.add(
                Fact(
                    publication_id=result.publication_id,
                    document_id=document.document_id,
                    subject=subject,
                    predicate=PREDICATE_CHANGE,
                    value=basis_points(delta, source_text=raw),
                    effective_date=effective,
                    source_location=FactLocation(LocationKind.SECTION, section=section_index),
                    source_text=raw,
                    extraction_method=METHOD_REGEX,
                    extraction_version=EXTRACTION_VERSION,
                    confidence=Confidence.HIGH,
                )
            )
        return result

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _decision_date(document) -> tuple[str, str, int] | None:
        """First parseable date in the leading (pre-H1) paragraphs, falling back
        to any date elsewhere in the document. Returns (ISO, raw, section index)."""
        for index, section in enumerate(document.sections):
            if section.heading:
                continue
            match = _DATE_TOKEN.search(section.text or "")
            if match:
                dt = parse_datetime(match.group(0))
                if dt is not None:
                    return dt.date().isoformat(), match.group(0), index
        for index, section in enumerate(document.sections):
            match = _DATE_TOKEN.search(section.text or "")
            if match:
                dt = parse_datetime(match.group(0))
                if dt is not None:
                    return dt.date().isoformat(), match.group(0), index
        return None

    @staticmethod
    def _rates_section_index(document) -> int | None:
        for index, section in enumerate(document.sections):
            if (section.heading or "").strip().lower() == "key ecb interest rates":
                return index
        return None

    @staticmethod
    def _effective_date(section) -> datetime | None:
        match = _EFFECTIVE_DATE.search(section.text or "") if section is not None else None
        if not match:
            return None
        return parse_datetime(match.group(1))

    @classmethod
    def _rate_levels(cls, document, rates_section_index: int | None):
        """Rate levels: the "rates section" enumeration is authoritative; any
        missing instrument is filled from a per-instrument sentence elsewhere."""
        found: dict[str, tuple[int, str, float, Confidence]] = {}

        if rates_section_index is not None:
            rates_text = document.sections[rates_section_index].text or ""
            enum = _RATES_ENUMERATED.search(rates_text)
            if enum:
                order = cls._instrument_order(enum.string)
                numbers = [float(v) for v in (enum.group("a"), enum.group("b"), enum.group("c"))]
                conf = Confidence.HIGH if order == _CANONICAL_ORDER else Confidence.MEDIUM
                for subject, number in zip(order, numbers):
                    raw = f"{number:.2f}%"
                    found[subject] = (rates_section_index, raw, number, conf)

        for index, section in enumerate(document.sections):
            for match in _RATE_SINGLE.finditer(section.text or ""):
                subject = cls._subject_for_text(match.group(0))
                if subject is None or subject in found:
                    continue
                value = float(match.group(1))
                found[subject] = (index, f"{value:.2f}%", value, Confidence.HIGH)

        return [
            (subject, index, raw, value, conf)
            for subject, (index, raw, value, conf) in found.items()
        ]

    @classmethod
    def _rate_changes(cls, document) -> list:
        """Explicit, directional rate changes. Sign follows the verb; magnitude
        is taken verbatim from the source. Only emitted when the source states
        a delta ("by N basis points")."""
        changes: list = []
        seen: set = set()

        for index, section in enumerate(document.sections):
            text = section.text or ""
            for match in _RATE_CHANGE_SINGLE.finditer(text):
                subject = cls._subject_for_text(match.group(0))
                if subject is None:
                    continue
                delta = cls._delta(match.group(0), float(match.group(1)))
                key = (subject, delta)
                if key in seen:
                    continue
                seen.add(key)
                changes.append((subject, index, match.group(0), delta))

        for index, section in enumerate(document.sections):
            text = section.text or ""
            for match in _RATE_CHANGE_BROAD.finditer(text):
                span = match.group(0)
                subjects = cls._subjects_in_span(span)
                if not subjects:
                    continue
                delta = cls._delta(span, float(match.group(1)))
                for subject in subjects:
                    key = (subject, delta)
                    if key in seen:
                        continue
                    seen.add(key)
                    changes.append((subject, index, span, delta))

        return changes

    @staticmethod
    def _subject_for_text(text: str) -> str | None:
        lower = text.lower()
        for phrase, subject in _INSTRUMENT_SUBJECTS:
            if phrase in lower:
                return subject
        return None

    @staticmethod
    def _subjects_in_span(span: str) -> list[str]:
        lower = span.lower()
        if "deposit facility" in lower:
            return [SUBJECT_DEPOSIT_FACILITY]
        if "marginal lending facility" in lower:
            return [SUBJECT_MARGINAL_LENDING]
        if "main refinancing operations" in lower:
            return [SUBJECT_MAIN_REFINANCING]
        if re.search(r"interest rates?\s+by", lower):
            return list(_CANONICAL_ORDER)
        return []

    @classmethod
    def _delta(cls, text: str, amount: float) -> float:
        verb = next((v for v in _MODIFIERS if re.search(rf"\b{v}\w*", text, re.IGNORECASE)), None)
        sign = _MODIFIERS.get(verb, 1) if verb else 1
        return round(sign * amount, 2)

    @staticmethod
    def _instrument_order(text: str) -> list[str]:
        """Order instruments as first named within ``text`` (before
        "respectively"); falls back to the canonical ECB order."""
        lower = text.lower()
        cutoff = lower.rfind("respectively")
        window = lower[:cutoff] if cutoff != -1 else lower
        positions = [(window.find(phrase), subject) for phrase, subject in _INSTRUMENT_SUBJECTS if window.find(phrase) != -1]
        positions.sort(key=lambda item: item[0])
        order = [subject for _, subject in positions]
        return order or list(_CANONICAL_ORDER)