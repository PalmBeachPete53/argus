"""Phase 14 — the Monetary Policy State model.

A ``MonetaryPolicyState`` is a **derived, dated observation of ONE policy
dimension of ONE central bank**, established by ONE Phase 12 ``FactChange``.
The current side of a policy ``FactChange`` is precisely "the newest known
value of this dimension, known at ``observed_at``"; the state historises those
observations so that "what is the observable state of a central bank's
monetary policy at instant T?" is always answerable without look-ahead.

Epistemic boundary (see ``docs/MONETARY_POLICY_STATE.md``):

- ``Fact`` / ``FactChange`` are **observed** (Phases 4 / 12).
- A ``PolicyReaction`` (Phase 13) is **inferred**.
- A ``MonetaryPolicyState`` is **synthesized**: ``synthesized`` is always
  ``True``, its ``formulation`` is purely descriptive, and it is never
  presented as a new fact, as a stance, as a forecast, or as a
  trading/forex signal.

The state consumes Phase 12 output (``FactChange`` relations) and reuses Phase
13's reaction-side vocabulary as its dimensions. Phase 13's *inferred*
``PolicyReaction`` values are deliberately **not** state inputs.

This layer never mutates the ``FactChange`` / ``Fact`` inputs, never reads
source documents, and never uses LLM / network / fuzzy / semantic logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..facts.base import FactPeriod, FactValue
from ..normalize import from_iso, iso, now_utc
from ..reactions.base import REACTION_SUBJECTS

# ---------------------------------------------------------------------------
# State dimension vocabulary — exactly Phase 13's reaction-side subjects
# (the observable monetary-policy dimensions). Shared with the reaction layer
# so the two analyses never drift.
# ---------------------------------------------------------------------------
STATE_SUBJECTS = REACTION_SUBJECTS

# Forecast lineages describe expected future values, not the current policy
# configuration. They are out of scope for the state (never asserted as the
# current level), matching the literal used by the projections extractor.
STATE_EXCLUDED_PREDICATES = frozenset({"projection"})


def _observed_label(dt: datetime | None) -> str:
    return dt.date().isoformat() if dt is not None else "unknown"


def _value_label(value: FactValue | None) -> str:
    if value is None:
        return "unknown"
    if value.value is not None:
        return str(value.value)
    return "unknown"


@dataclass
class MonetaryPolicyState:
    """A derived, dated observation of one policy dimension of one bank.

    ``synthesized`` is a constant (always ``True``): this object is never an
    observed fact. ``value`` is the newest known level of the dimension (the
    current side of the source change, copied verbatim — never invented,
    never converted). All provenance fields are denormalized copies of the
    current side so an entry is self-describing and traceable to its change /
    fact / publication / document.
    """

    state_id: str | None = None
    central_bank: str | None = None
    synthesized: bool = True
    source_change_id: str = ""
    dimension_key: str = ""
    # dimension components
    subject: str = ""
    predicate: str = ""
    value_kind: str | None = None
    qualifier: str = ""
    period: FactPeriod | None = None
    publication_type: str = ""
    # observed level
    value: FactValue | None = None
    previous_value: FactValue | None = None
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
        """Return ``state_id``, computing and caching it if missing."""
        from .identity import state_id_of

        if self.state_id is None:
            self.state_id = state_id_of(
                central_bank=self.central_bank,
                source_change_id=self.source_change_id,
            )
        return self.state_id

    # ------------------------------------------------------------------
    # serialization
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "state_id": self.state_id or self.resolve_id(),
            "central_bank": self.central_bank,
            "synthesized": self.synthesized,
            "source_change_id": self.source_change_id,
            "dimension_key": self.dimension_key,
            "subject": self.subject,
            "predicate": self.predicate,
            "value_kind": self.value_kind,
            "qualifier": self.qualifier,
            "period": self.period.to_dict() if self.period else None,
            "publication_type": self.publication_type,
            "value": self.value.to_dict() if self.value else None,
            "previous_value": self.previous_value.to_dict() if self.previous_value else None,
            "observed_at": iso(self.observed_at),
            "publication_id": self.publication_id,
            "document_id": self.document_id,
            "effective_date": iso(self.effective_date),
            "source_text": self.source_text,
            "analysis_version": self.analysis_version,
            "analyzed_at": iso(self.analyzed_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MonetaryPolicyState:
        return cls(
            state_id=data.get("state_id"),
            central_bank=data.get("central_bank"),
            synthesized=data.get("synthesized", True),
            source_change_id=data.get("source_change_id") or "",
            dimension_key=data.get("dimension_key") or "",
            subject=data.get("subject") or "",
            predicate=data.get("predicate") or "",
            value_kind=data.get("value_kind"),
            qualifier=data.get("qualifier") or "",
            period=FactPeriod.from_dict(data.get("period")),
            publication_type=data.get("publication_type") or "",
            value=FactValue.from_dict(data.get("value")),
            previous_value=FactValue.from_dict(data.get("previous_value")),
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
            f"monetary policy state of {self.central_bank} on {label}: "
            f"{self.subject}/{self.predicate} = {value_label} "
            f"(derived from change {self.source_change_id})"
        )


@dataclass
class MonetaryPolicyStateResult:
    """Result of a state analysis: the derived states plus observability
    warnings (changes skipped because their current-side publication is missing,
    undated, unclassified or valueless, or the change has no central bank, or
    its lineage is out of scope for the state)."""

    states: list[MonetaryPolicyState] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)