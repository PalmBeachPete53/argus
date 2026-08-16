"""Phase 8 — the Forex Fundamentals model.

A ``ForexFundamental`` is a **derived, dated observation of ONE fundamental
dimension of ONE economy (currency)**, established by ONE source observation:
either a ``MonetaryPolicyState`` (Phase 7 — monetary dimensions) or a ``Fact``
(Phase 4 — macro dimensions). A ``ForexDifferential`` is a **derived, dated
arithmetic comparison of two fundamentals of two different economies on an
explicitly declared, shared dimension**.

Epistemic boundary (see ``docs/FOREX_FUNDAMENTALS.md``):

- ``Fact`` / ``FactChange`` are **observed** (Phases 4 /5).
- A ``PolicyReaction`` (Phase 6, legacy class name; concept: Temporal Relationship) is **inferred**.
- A ``MonetaryPolicyState`` (Phase 7) is **synthesized**.
- A ``ForexFundamental`` / ``ForexDifferential`` (Phase 8) is **synthesized**:
  ``synthesized`` is always ``True``, the ``formulation`` is purely
  descriptive, and neither is ever presented as a new fact, as a stance, as a
  forecast, as a fair value, or as a trading/forex signal.

The layer consumes Phase 7 ``MonetaryPolicyState`` entries and Phase 4 ``Fact``
objects only. It never reads source documents, never reconstructs rates, never
mutates its inputs, and never uses LLM / network / fuzzy / semantic logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..facts.base import FactPeriod, FactValue
from ..normalize import from_iso, iso
from ..temporal_relationships.base import EARLIER_SUBJECTS as CONDITION_SUBJECTS
from ..states.base import STATE_SUBJECTS

# ---------------------------------------------------------------------------
# Dimension vocabulary. Monetary dimensions are Phase 7's later-side (legacy "reaction-side")
# subjects (the observable policy dimensions); macro dimensions are Phase 6's
# condition-side subjects (the observed economic conditions). Reused, never
# re-declared, so the layers never drift.
# ---------------------------------------------------------------------------
MONETARY_SUBJECTS = STATE_SUBJECTS
MACRO_SUBJECTS = CONDITION_SUBJECTS
FUNDAMENTAL_SUBJECTS = frozenset(MONETARY_SUBJECTS) | frozenset(MACRO_SUBJECTS)

# Observation kinds excluded from the fundamental layer (macro facts): a
# ``projection`` is an expectation of a future value (same rationale as Phase
# 14's ``STATE_EXCLUDED_PREDICATES``), a ``change`` is a delta, not an absolute
# level, and a ``date`` is a meta observation. All three describe something
# other than the current observed level.
FUNDAMENTAL_EXCLUDED_PREDICATES = frozenset({"projection", "change", "date"})

# Source kinds of a fundamental.
SOURCE_MONETARY_STATE = "monetary_state"
SOURCE_FACT = "fact"

# Value kinds that support an arithmetic difference (the differential is
# ``base_value - quote_value``). Text / qualitative / date / boolean / range /
# null dimensions are observed as fundamentals but are by nature **not**
# differentiable (documented property, never a warning).
_NUMERIC_KINDS = frozenset(
    {"number", "percentage", "basis_points", "currency"}
)


def _observed_label(dt: datetime | None) -> str:
    return dt.date().isoformat() if dt is not None else "unknown"


def _value_label(value: FactValue | None) -> str:
    if value is None or value.value is None:
        return "unknown"
    return str(value.value)


@dataclass
class ForexFundamental:
    """A derived, dated observation of one fundamental dimension of one
    economy (currency).

    ``synthesized`` is a constant (always ``True``): this object is never an
    observed fact. ``value`` is the observed level copied verbatim from the
    source (never invented, never converted). ``source_kind`` /
    ``source_id`` identify the source observation (a ``MonetaryPolicyState``
    entry or a ``Fact``); all provenance fields are denormalized from that
    source so an entry is self-describing and traceable to its publication /
    document.

    ``dimension_key`` is the currency-scoped dimension (subject, predicate,
    value kind, canonical period, qualifier, publication type);
    ``lineage_key`` is the same dimension **without the currency**, the
    currency-independent lineage used to match two economies.
    """

    fundamental_id: str | None = None
    currency: str | None = None
    synthesized: bool = True
    source_kind: str = ""
    source_id: str = ""
    central_bank: str | None = None
    dimension_key: str = ""
    lineage_key: str = ""
    # dimension components
    subject: str = ""
    predicate: str = ""
    value_kind: str | None = None
    qualifier: str = ""
    period: FactPeriod | None = None
    publication_type: str = ""
    # observed level
    value: FactValue | None = None
    # temporal + provenance
    observed_at: datetime | None = None
    publication_id: str = ""
    document_id: str = ""
    effective_date: datetime | None = None
    source_text: str | None = None
    analysis_version: str | None = None
    analyzed_at: datetime | None = None

    # ------------------------------------------------------------------
    # identity
    # ------------------------------------------------------------------
    def resolve_id(self) -> str:
        """Return ``fundamental_id``, computing and caching it if missing."""
        from .identity import fundamental_id_of

        if self.fundamental_id is None:
            self.fundamental_id = fundamental_id_of(
                currency=self.currency,
                source_kind=self.source_kind,
                source_id=self.source_id,
            )
        return self.fundamental_id

    # ------------------------------------------------------------------
    # serialization
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "fundamental_id": self.fundamental_id or self.resolve_id(),
            "currency": self.currency,
            "synthesized": self.synthesized,
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "central_bank": self.central_bank,
            "dimension_key": self.dimension_key,
            "lineage_key": self.lineage_key,
            "subject": self.subject,
            "predicate": self.predicate,
            "value_kind": self.value_kind,
            "qualifier": self.qualifier,
            "period": self.period.to_dict() if self.period else None,
            "publication_type": self.publication_type,
            "value": self.value.to_dict() if self.value else None,
            "observed_at": iso(self.observed_at),
            "publication_id": self.publication_id,
            "document_id": self.document_id,
            "effective_date": iso(self.effective_date),
            "source_text": self.source_text,
            "analysis_version": self.analysis_version,
            "analyzed_at": iso(self.analyzed_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ForexFundamental:
        return cls(
            fundamental_id=data.get("fundamental_id"),
            currency=data.get("currency"),
            synthesized=data.get("synthesized", True),
            source_kind=data.get("source_kind") or "",
            source_id=data.get("source_id") or "",
            central_bank=data.get("central_bank"),
            dimension_key=data.get("dimension_key") or "",
            lineage_key=data.get("lineage_key") or "",
            subject=data.get("subject") or "",
            predicate=data.get("predicate") or "",
            value_kind=data.get("value_kind"),
            qualifier=data.get("qualifier") or "",
            period=FactPeriod.from_dict(data.get("period")),
            publication_type=data.get("publication_type") or "",
            value=FactValue.from_dict(data.get("value")),
            observed_at=from_iso(data.get("observed_at")),
            publication_id=data.get("publication_id") or "",
            document_id=data.get("document_id") or "",
            effective_date=from_iso(data.get("effective_date")),
            source_text=data.get("source_text"),
            analysis_version=data.get("analysis_version"),
            analyzed_at=from_iso(data.get("analyzed_at")),
        )

    def describe(self) -> str:
        """Deterministic, purely descriptive formulation (no stance, no
        forecast, no interpretation)."""
        label = _observed_label(self.observed_at)
        value_label = _value_label(self.value)
        return (
            f"forex fundamental of {self.currency} on {label}: "
            f"{self.subject}/{self.predicate} = {value_label} "
            f"(derived from {self.source_kind} {self.source_id})"
        )


@dataclass
class ForexDifferential:
    """A derived, dated arithmetic comparison of two fundamentals of two
    different economies, on an explicitly declared shared dimension.

    ``synthesized`` is a constant (always ``True``): this object is never an
    observed fact. The pair is **ordered** (``base_currency`` /
    ``quote_currency``); the convention is stable and never silently inverted
    (``EUR/USD`` ⇒ base EUR, quote USD). Both sides declare their dimension
    and carry full denormalized provenance. ``value`` is the arithmetic
    difference ``base_value - quote_value`` in the same unit/kind (no
    conversion, no interpretation).

    ``base_observed_at`` is the temporal anchor: the quote observation is the
    latest with ``observed_at <= base_observed_at`` (no look-ahead).
    """

    differential_id: str | None = None
    base_currency: str = ""
    quote_currency: str = ""
    synthesized: bool = True
    # shared dimension (currency-independent lineage)
    dimension_key: str = ""
    subject: str = ""
    predicate: str = ""
    value_kind: str | None = None
    qualifier: str = ""
    period: FactPeriod | None = None
    publication_type: str = ""
    # left side — base
    base_fundamental_id: str = ""
    base_source_kind: str = ""
    base_source_id: str = ""
    base_central_bank: str | None = None
    base_value: FactValue | None = None
    base_observed_at: datetime | None = None
    base_publication_id: str = ""
    base_document_id: str = ""
    base_effective_date: datetime | None = None
    base_source_text: str | None = None
    # right side — quote
    quote_fundamental_id: str = ""
    quote_source_kind: str = ""
    quote_source_id: str = ""
    quote_central_bank: str | None = None
    quote_value: FactValue | None = None
    quote_observed_at: datetime | None = None
    quote_publication_id: str = ""
    quote_document_id: str = ""
    quote_effective_date: datetime | None = None
    quote_source_text: str | None = None
    # result
    value: FactValue | None = None
    formulation: str | None = None
    analysis_version: str | None = None
    analyzed_at: datetime | None = None

    # ------------------------------------------------------------------
    # identity
    # ------------------------------------------------------------------
    def resolve_id(self) -> str:
        """Return ``differential_id``, computing and caching it if missing."""
        from .identity import differential_id_of

        if self.differential_id is None:
            self.differential_id = differential_id_of(
                base_currency=self.base_currency,
                quote_currency=self.quote_currency,
                subject=self.subject,
                predicate=self.predicate,
                base_source_id=self.base_source_id,
                quote_source_id=self.quote_source_id,
            )
        return self.differential_id

    # ------------------------------------------------------------------
    # serialization
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "differential_id": self.differential_id or self.resolve_id(),
            "base_currency": self.base_currency,
            "quote_currency": self.quote_currency,
            "synthesized": self.synthesized,
            "dimension_key": self.dimension_key,
            "subject": self.subject,
            "predicate": self.predicate,
            "value_kind": self.value_kind,
            "qualifier": self.qualifier,
            "period": self.period.to_dict() if self.period else None,
            "publication_type": self.publication_type,
            "base_fundamental_id": self.base_fundamental_id,
            "base_source_kind": self.base_source_kind,
            "base_source_id": self.base_source_id,
            "base_central_bank": self.base_central_bank,
            "base_value": self.base_value.to_dict() if self.base_value else None,
            "base_observed_at": iso(self.base_observed_at),
            "base_publication_id": self.base_publication_id,
            "base_document_id": self.base_document_id,
            "base_effective_date": iso(self.base_effective_date),
            "base_source_text": self.base_source_text,
            "quote_fundamental_id": self.quote_fundamental_id,
            "quote_source_kind": self.quote_source_kind,
            "quote_source_id": self.quote_source_id,
            "quote_central_bank": self.quote_central_bank,
            "quote_value": self.quote_value.to_dict() if self.quote_value else None,
            "quote_observed_at": iso(self.quote_observed_at),
            "quote_publication_id": self.quote_publication_id,
            "quote_document_id": self.quote_document_id,
            "quote_effective_date": iso(self.quote_effective_date),
            "quote_source_text": self.quote_source_text,
            "value": self.value.to_dict() if self.value else None,
            "formulation": self.formulation,
            "analysis_version": self.analysis_version,
            "analyzed_at": iso(self.analyzed_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ForexDifferential:
        return cls(
            differential_id=data.get("differential_id"),
            base_currency=data.get("base_currency") or "",
            quote_currency=data.get("quote_currency") or "",
            synthesized=data.get("synthesized", True),
            dimension_key=data.get("dimension_key") or "",
            subject=data.get("subject") or "",
            predicate=data.get("predicate") or "",
            value_kind=data.get("value_kind"),
            qualifier=data.get("qualifier") or "",
            period=FactPeriod.from_dict(data.get("period")),
            publication_type=data.get("publication_type") or "",
            base_fundamental_id=data.get("base_fundamental_id") or "",
            base_source_kind=data.get("base_source_kind") or "",
            base_source_id=data.get("base_source_id") or "",
            base_central_bank=data.get("base_central_bank"),
            base_value=FactValue.from_dict(data.get("base_value")),
            base_observed_at=from_iso(data.get("base_observed_at")),
            base_publication_id=data.get("base_publication_id") or "",
            base_document_id=data.get("base_document_id") or "",
            base_effective_date=from_iso(data.get("base_effective_date")),
            base_source_text=data.get("base_source_text"),
            quote_fundamental_id=data.get("quote_fundamental_id") or "",
            quote_source_kind=data.get("quote_source_kind") or "",
            quote_source_id=data.get("quote_source_id") or "",
            quote_central_bank=data.get("quote_central_bank"),
            quote_value=FactValue.from_dict(data.get("quote_value")),
            quote_observed_at=from_iso(data.get("quote_observed_at")),
            quote_publication_id=data.get("quote_publication_id") or "",
            quote_document_id=data.get("quote_document_id") or "",
            quote_effective_date=from_iso(data.get("quote_effective_date")),
            quote_source_text=data.get("quote_source_text"),
            value=FactValue.from_dict(data.get("value")),
            formulation=data.get("formulation"),
            analysis_version=data.get("analysis_version"),
            analyzed_at=from_iso(data.get("analyzed_at")),
        )

    def describe(self) -> str:
        """Deterministic, purely descriptive formulation (no stance, no
        forecast, no interpretation)."""
        label = _observed_label(self.base_observed_at)
        base_label = _value_label(self.base_value)
        quote_label = _value_label(self.quote_value)
        diff_label = _value_label(self.value)
        return (
            f"forex differential {self.base_currency}/{self.quote_currency} "
            f"on {label}: {self.subject}/{self.predicate} = "
            f"{base_label} - {quote_label} = {diff_label}"
        )


@dataclass
class ForexFundamentalResult:
    """Result of a forex analysis: the derived fundamentals, the derived
    differentials, and the observability warnings (source observations skipped
    because their bank/currency is unknown, their publication is missing,
    undated or unclassified, they are valueless or out of scope, a quote
    observation is missing at the anchor, or a dimension is incomparable)."""

    fundamentals: list[ForexFundamental] = field(default_factory=list)
    differentials: list[ForexDifferential] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)