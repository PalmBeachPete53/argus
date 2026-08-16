"""Phase 4 — the foundational Fact model.

A ``Fact`` is a **structured representation of information explicitly present in
a source document**. It is not an interpretation, not a prediction, not an
economic judgement. The full design is documented in ``docs/DATA_MODEL.md``;
everything here must stay consistent with that document.

Layering: a Fact belongs to a ``NormalizedDocument`` (``document_id``) which
belongs to a ``Publication`` (``publication_id``). The chain to ``Source`` /
``CentralBank`` lives in the existing tables and is *reused*, never duplicated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from ..classification.base import Confidence
from ..normalize import from_iso, iso, now_utc

# ---------------------------------------------------------------------------
# Extraction method vocabulary — how a Fact was produced. Used for auditability:
# downstream code can distinguish facts produced by different mechanisms/versions.
# ---------------------------------------------------------------------------
METHOD_RULE = "rule"
METHOD_PARSER = "parser"
METHOD_TABLE = "table_extraction"
METHOD_REGEX = "regex"
METHOD_METADATA = "structured_metadata"
METHOD_MANUAL = "manual"
METHOD_LLM = "llm"  # reserved — NOT used in this phase
METHOD_UNKNOWN = "unknown"

EXTRACTION_METHODS = (
    METHOD_RULE,
    METHOD_PARSER,
    METHOD_TABLE,
    METHOD_REGEX,
    METHOD_METADATA,
    METHOD_MANUAL,
    METHOD_LLM,
    METHOD_UNKNOWN,
)


class ValueKind(str, Enum):
    """Machine-readable value kinds. Values are never stored as opaque strings.

    ``source_text`` (when present) preserves the verbatim source wording next to
    the canonical value, so the raw evidence is never lost.
    """

    NUMBER = "number"  # e.g. 1.4 (no unit or with explicit ``unit``)
    PERCENTAGE = "percentage"  # e.g. 4.25 → 4.25%
    BASIS_POINTS = "basis_points"  # e.g. +25 (sign preserved)
    CURRENCY = "currency"  # float value + ``unit`` (e.g. "usd", "billion")
    DATE = "date"  # value as ISO-8601 string ("2026-08-14")
    BOOLEAN = "boolean"
    CATEGORICAL = "categorical"  # canonical category, e.g. "upside"
    TEXT = "text"  # verbatim-ish quoted passage
    RANGE = "range"  # numeric interval via ``min`` / ``max``
    NULL = "null"  # explicitly unavailable / not disclosed


class PeriodKind(str, Enum):
    """Reference / forecast periods. ``value`` uses canonical, sortable forms.

    Conventions (zero-padded so lexicographic order == chronological order):
    year "2027", quarter "2027-Q4", month "2027-08", range "2027-2028",
    semester "2027-H1".
    """

    YEAR = "year"
    QUARTER = "quarter"
    MONTH = "month"
    SEMESTER = "semester"
    RANGE = "range"
    UNKNOWN = "unknown"


class LocationKind(str, Enum):
    """Where inside a document a Fact was found. Format-independent: the same
    model works for HTML, PDF, DOCX, XLSX, CSV and TXT."""

    SECTION = "section"  # a heading + its following text (sections[position])
    TABLE = "table"  # a table (tables[position]); optional row/column indexes
    PAGE = "page"  # PDF/DOCX page number only
    OFFSET = "offset"  # character range in the normalized text


@dataclass(frozen=True, slots=True)
class FactValue:
    """A structured value with an explicit kind, optional unit and verbatim
    ``source_text``."""

    kind: ValueKind
    value: float | str | bool | None = None
    unit: str | None = None
    source_text: str | None = None
    min: float | None = None
    max: float | None = None

    def __post_init__(self) -> None:
        if self.kind in (ValueKind.NUMBER, ValueKind.PERCENTAGE, ValueKind.BASIS_POINTS, ValueKind.CURRENCY):
            if self.value is not None and not isinstance(self.value, (int, float)):
                raise TypeError(f"{self.kind.value} value must be numeric, got {self.value!r}")
            if self.kind is ValueKind.CURRENCY and self.unit is not None and not isinstance(self.unit, str):
                raise TypeError(f"currency unit must be a string, got {self.unit!r}")
        elif self.kind is ValueKind.BOOLEAN:
            if self.value is not None and not isinstance(self.value, bool):
                raise TypeError(f"boolean value must be bool, got {self.value!r}")
        elif self.kind is ValueKind.RANGE:
            if self.min is not None and not isinstance(self.min, (int, float)):
                raise TypeError(f"range min must be numeric, got {self.min!r}")
            if self.max is not None and not isinstance(self.max, (int, float)):
                raise TypeError(f"range max must be numeric, got {self.max!r}")
        elif self.kind in (ValueKind.TEXT, ValueKind.CATEGORICAL, ValueKind.DATE):
            if self.value is not None and not isinstance(self.value, str):
                raise TypeError(f"{self.kind.value} value must be str, got {self.value!r}")
        elif self.kind is ValueKind.NULL:
            if self.value is not None:
                raise TypeError("null value must have value=None")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "value": self.value,
            "unit": self.unit,
            "source_text": self.source_text,
            "min": self.min,
            "max": self.max,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> FactValue | None:
        if not data:
            return None
        return cls(
            kind=ValueKind(data["kind"]),
            value=data.get("value"),
            unit=data.get("unit"),
            source_text=data.get("source_text"),
            min=data.get("min"),
            max=data.get("max"),
        )


def number(value: float, *, unit: str | None = None, source_text: str | None = None) -> FactValue:
    return FactValue(ValueKind.NUMBER, value=value, unit=unit, source_text=source_text)


def percentage(value: float, *, source_text: str | None = None) -> FactValue:
    return FactValue(ValueKind.PERCENTAGE, value=value, source_text=source_text)


def basis_points(value: float, *, source_text: str | None = None) -> FactValue:
    return FactValue(ValueKind.BASIS_POINTS, value=value, source_text=source_text)


def currency(value: float, *, unit: str, source_text: str | None = None) -> FactValue:
    return FactValue(ValueKind.CURRENCY, value=value, unit=unit, source_text=source_text)


def date_value(value: str, *, source_text: str | None = None) -> FactValue:
    return FactValue(ValueKind.DATE, value=value, source_text=source_text)


def boolean_value(value: bool, *, source_text: str | None = None) -> FactValue:
    return FactValue(ValueKind.BOOLEAN, value=value, source_text=source_text)


def categorical(value: str, *, source_text: str | None = None) -> FactValue:
    return FactValue(ValueKind.CATEGORICAL, value=value, source_text=source_text)


def text_value(value: str) -> FactValue:
    return FactValue(ValueKind.TEXT, value=value)


def range_value(min: float | None, max: float | None, *, unit: str | None = None, source_text: str | None = None) -> FactValue:
    return FactValue(ValueKind.RANGE, unit=unit, source_text=source_text, min=min, max=max)


def null_value(*, source_text: str | None = None) -> FactValue:
    return FactValue(ValueKind.NULL, source_text=source_text)


@dataclass(frozen=True, slots=True)
class FactPeriod:
    """A reference/forecast period, distinct from dates (publication, effective,
    meeting). Canonical ``value`` follows ``PeriodKind`` conventions; ``label``
    preserves the verbatim source wording."""

    kind: PeriodKind
    value: str
    label: str | None = None

    def canonical(self) -> str:
        return f"{self.kind.value}:{self.value}"

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "value": self.value, "label": self.label}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> FactPeriod | None:
        if not data:
            return None
        return cls(kind=PeriodKind(data["kind"]), value=data["value"], label=data.get("label"))


def year(value: str | int, *, label: str | None = None) -> FactPeriod:
    return FactPeriod(PeriodKind.YEAR, str(value), label=label)


def quarter(value: str, *, label: str | None = None) -> FactPeriod:
    return FactPeriod(PeriodKind.QUARTER, value, label=label)


@dataclass(frozen=True, slots=True)
class FactLocation:
    """Exact location of a Fact inside its document. Format-independent."""

    kind: LocationKind
    section: int | None = None
    table: int | None = None
    row: int | None = None
    column: int | None = None
    page: int | None = None
    char_start: int | None = None
    char_end: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "section": self.section,
            "table": self.table,
            "row": self.row,
            "column": self.column,
            "page": self.page,
            "char_start": self.char_start,
            "char_end": self.char_end,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> FactLocation | None:
        if not data:
            return None
        return cls(
            kind=LocationKind(data["kind"]),
            section=data.get("section"),
            table=data.get("table"),
            row=data.get("row"),
            column=data.get("column"),
            page=data.get("page"),
            char_start=data.get("char_start"),
            char_end=data.get("char_end"),
        )


@dataclass
class Fact:
    """A structured, provenance-carrying assertion extracted from a source.

    ``fact_id`` is a deterministic SHA-256 identity derived from stable semantic
    + provenance fields (subject, predicate, period, effective_date — NOT the
    extracted value), so re-running an extractor or correcting a value updates
    the same row instead of duplicating it. ``identity_qualifier`` is an
    optional extractor-provided discriminator for the rare case where two facts
    would otherwise share the same key. ``effective_date`` is part of the
    identity because two facts differing only by their effective date are
    distinct facts (see ``facts/identity.py``).

    ``speaker`` (Phase 4.3, optional) preserves the verbatim official speaker of a
    statement when the source structure exposes one (e.g. a Q&A answer labelled
    "President Christine Lagarde"). It is a provenance attribute, never
    inferred: an unlabelled/collective statement carries ``speaker=None``. It is
    deliberately excluded from ``fact_id`` (the identity discriminator is
    ``identity_qualifier``).
    """

    publication_id: str
    document_id: str
    subject: str
    predicate: str
    value: FactValue | None = None
    previous_value: FactValue | None = None
    change: FactValue | None = None
    period: FactPeriod | None = None
    effective_date: datetime | None = None
    source_location: FactLocation | None = None
    source_text: str | None = None
    extraction_method: str = METHOD_UNKNOWN
    extraction_version: str | None = None
    confidence: Confidence = Confidence.MEDIUM
    central_bank: str | None = None
    speaker: str | None = None
    identity_qualifier: str = ""
    fact_id: str | None = None
    extracted_at: datetime | None = None

    # ------------------------------------------------------------------
    # identity
    # ------------------------------------------------------------------
    def compute_fact_id(self) -> str:
        from .identity import fact_id_of

        return fact_id_of(
            publication_id=self.publication_id,
            document_id=self.document_id,
            subject=self.subject,
            predicate=self.predicate,
            period=self.period,
            effective_date=self.effective_date,
            qualifier=self.identity_qualifier,
        )

    def resolve_id(self) -> str:
        """Return ``fact_id``, computing and caching it if missing."""
        if self.fact_id is None:
            self.fact_id = self.compute_fact_id()
        return self.fact_id

    # ------------------------------------------------------------------
    # serialization
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id or self.compute_fact_id(),
            "publication_id": self.publication_id,
            "document_id": self.document_id,
            "central_bank": self.central_bank,
            "subject": self.subject,
            "predicate": self.predicate,
            "value": self.value.to_dict() if self.value else None,
            "previous_value": self.previous_value.to_dict() if self.previous_value else None,
            "change": self.change.to_dict() if self.change else None,
            "period": self.period.to_dict() if self.period else None,
            "effective_date": iso(self.effective_date),
            "source_location": self.source_location.to_dict() if self.source_location else None,
            "source_text": self.source_text,
            "extraction_method": self.extraction_method,
            "extraction_version": self.extraction_version,
            "confidence": self.confidence.value if self.confidence else None,
            "speaker": self.speaker,
            "identity_qualifier": self.identity_qualifier,
            "extracted_at": iso(self.extracted_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Fact:
        return cls(
            fact_id=data.get("fact_id"),
            publication_id=data["publication_id"],
            document_id=data["document_id"],
            central_bank=data.get("central_bank"),
            subject=data["subject"],
            predicate=data["predicate"],
            value=FactValue.from_dict(data.get("value")),
            previous_value=FactValue.from_dict(data.get("previous_value")),
            change=FactValue.from_dict(data.get("change")),
            period=FactPeriod.from_dict(data.get("period")),
            effective_date=from_iso(data.get("effective_date")),
            source_location=FactLocation.from_dict(data.get("source_location")),
            source_text=data.get("source_text"),
            extraction_method=data.get("extraction_method") or METHOD_UNKNOWN,
            extraction_version=data.get("extraction_version"),
            confidence=Confidence(data["confidence"]) if data.get("confidence") else None,
            speaker=data.get("speaker"),
            identity_qualifier=data.get("identity_qualifier") or "",
            extracted_at=from_iso(data.get("extracted_at")),
        )


@dataclass
class ExtractionResult:
    """Contract that future type-specific extractors must return.

    An extractor produces ``facts`` for exactly one ``(publication, document)``
    pair and may attach ``warnings`` describing skipped or degraded extractions.
    This container is deliberately free of business logic.
    """

    publication_id: str
    document_id: str
    facts: list[Fact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add(self, fact: Fact) -> None:
        self.facts.append(fact)
