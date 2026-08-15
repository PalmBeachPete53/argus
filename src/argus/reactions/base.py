"""Phase 13 — the Policy Reaction Function model.

A ``PolicyReaction`` is a **derived, inferred relation between two existing
``FactChange`` objects**: a condition-side change (an observed change in an
economic condition) temporally followed, within a documented window, by a
policy-side change (an observable monetary-policy response).

Epistemic boundary (see ``docs/REACTIONS.md``):

- ``Fact`` / ``FactChange`` are **observed** (Phases 4 / 12).
- A ``PolicyReaction`` is **inferred**: ``inferred`` is always ``True``, its
  ``formulation`` is explicitly non-causal, and it is never presented as a
  fact, as a "true" structural reaction function, or as causality.

This layer never mutates the ``FactChange`` / ``Fact`` inputs, never reads
source documents, and never uses LLM / network / fuzzy / semantic logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..facts.base import FactPeriod, FactValue
from ..normalize import from_iso, iso, now_utc

# ---------------------------------------------------------------------------
# Condition-side vocabulary — observed economic conditions (Phases 5–11 subjects).
# A FactChange on one of these subjects is a candidate *antecedent*.
# ---------------------------------------------------------------------------
CONDITION_SUBJECTS = frozenset(
    {
        "inflation",
        "core_inflation",
        "inflation_expectations",
        "gdp",
        "growth",
        "unemployment",
        "wages",
        "labour_market",
        "financial_conditions",
        "fiscal_policy",
    }
)

# ---------------------------------------------------------------------------
# Reaction-side vocabulary — observable monetary-policy responses (Phases 5–11
# subjects). Risk assessments are assigned the reaction role (documented choice,
# see docs/REACTIONS.md) — they are never used as a condition in this phase.
# ---------------------------------------------------------------------------
REACTION_SUBJECTS = frozenset(
    {
        "policy_rate",
        "main_refinancing_rate",
        "deposit_facility_rate",
        "marginal_lending_rate",
        "policy_guidance",
        "asset_purchase",
        "risk",
        "inflation_risk",
        "growth_risk",
    }
)

# Documented, deterministic default reaction window (days). Explicit parameter
# of the analyzer — never fitted to historical data.
DEFAULT_MAX_LAG_DAYS = 180


def _observed_label(dt: datetime | None) -> str:
    return dt.date().isoformat() if dt is not None else "unknown"


@dataclass
class PolicyReaction:
    """A derived, inferred temporal association ``condition change → policy
    change``.

    ``inferred`` is a constant (always ``True``): this object is never an
    observed fact. All other fields are denormalized provenance copied from the
    two source ``FactChange`` objects so a reaction is self-describing and each
    side stays traceable to its official publication/document.
    """

    reaction_id: str | None = None
    central_bank: str | None = None
    inferred: bool = True
    # condition side
    condition_change_id: str = ""
    condition_subject: str = ""
    condition_predicate: str = ""
    condition_value_kind: str | None = None
    condition_previous_value: FactValue | None = None
    condition_current_value: FactValue | None = None
    condition_period: FactPeriod | None = None
    condition_publication_id: str = ""
    condition_document_id: str = ""
    condition_effective_date: datetime | None = None
    condition_source_text: str | None = None
    condition_observed_at: datetime | None = None
    # policy side
    policy_change_id: str = ""
    policy_subject: str = ""
    policy_predicate: str = ""
    policy_value_kind: str | None = None
    policy_previous_value: FactValue | None = None
    policy_current_value: FactValue | None = None
    policy_period: FactPeriod | None = None
    policy_publication_id: str = ""
    policy_document_id: str = ""
    policy_effective_date: datetime | None = None
    policy_source_text: str | None = None
    policy_observed_at: datetime | None = None
    # relationship
    lag_days: int | None = None
    max_lag_days: int | None = None
    formulation: str | None = None
    analysis_version: str | None = None
    analyzed_at: datetime | None = None

    # ------------------------------------------------------------------
    # identity
    # ------------------------------------------------------------------
    def resolve_id(self) -> str:
        """Return ``reaction_id``, computing and caching it if missing."""
        from .identity import reaction_id_of

        if self.reaction_id is None:
            self.reaction_id = reaction_id_of(
                central_bank=self.central_bank,
                condition_change_id=self.condition_change_id,
                policy_change_id=self.policy_change_id,
            )
        return self.reaction_id

    # ------------------------------------------------------------------
    # serialization
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "reaction_id": self.reaction_id or self.resolve_id(),
            "central_bank": self.central_bank,
            "inferred": self.inferred,
            "condition_change_id": self.condition_change_id,
            "condition_subject": self.condition_subject,
            "condition_predicate": self.condition_predicate,
            "condition_value_kind": self.condition_value_kind,
            "condition_previous_value": self.condition_previous_value.to_dict() if self.condition_previous_value else None,
            "condition_current_value": self.condition_current_value.to_dict() if self.condition_current_value else None,
            "condition_period": self.condition_period.to_dict() if self.condition_period else None,
            "condition_publication_id": self.condition_publication_id,
            "condition_document_id": self.condition_document_id,
            "condition_effective_date": iso(self.condition_effective_date),
            "condition_source_text": self.condition_source_text,
            "condition_observed_at": iso(self.condition_observed_at),
            "policy_change_id": self.policy_change_id,
            "policy_subject": self.policy_subject,
            "policy_predicate": self.policy_predicate,
            "policy_value_kind": self.policy_value_kind,
            "policy_previous_value": self.policy_previous_value.to_dict() if self.policy_previous_value else None,
            "policy_current_value": self.policy_current_value.to_dict() if self.policy_current_value else None,
            "policy_period": self.policy_period.to_dict() if self.policy_period else None,
            "policy_publication_id": self.policy_publication_id,
            "policy_document_id": self.policy_document_id,
            "policy_effective_date": iso(self.policy_effective_date),
            "policy_source_text": self.policy_source_text,
            "policy_observed_at": iso(self.policy_observed_at),
            "lag_days": self.lag_days,
            "max_lag_days": self.max_lag_days,
            "formulation": self.formulation,
            "analysis_version": self.analysis_version,
            "analyzed_at": iso(self.analyzed_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PolicyReaction:
        return cls(
            reaction_id=data.get("reaction_id"),
            central_bank=data.get("central_bank"),
            inferred=data.get("inferred", True),
            condition_change_id=data.get("condition_change_id") or "",
            condition_subject=data.get("condition_subject") or "",
            condition_predicate=data.get("condition_predicate") or "",
            condition_value_kind=data.get("condition_value_kind"),
            condition_previous_value=FactValue.from_dict(data.get("condition_previous_value")),
            condition_current_value=FactValue.from_dict(data.get("condition_current_value")),
            condition_period=FactPeriod.from_dict(data.get("condition_period")),
            condition_publication_id=data.get("condition_publication_id") or "",
            condition_document_id=data.get("condition_document_id") or "",
            condition_effective_date=from_iso(data.get("condition_effective_date")),
            condition_source_text=data.get("condition_source_text"),
            condition_observed_at=from_iso(data.get("condition_observed_at")),
            policy_change_id=data.get("policy_change_id") or "",
            policy_subject=data.get("policy_subject") or "",
            policy_predicate=data.get("policy_predicate") or "",
            policy_value_kind=data.get("policy_value_kind"),
            policy_previous_value=FactValue.from_dict(data.get("policy_previous_value")),
            policy_current_value=FactValue.from_dict(data.get("policy_current_value")),
            policy_period=FactPeriod.from_dict(data.get("policy_period")),
            policy_publication_id=data.get("policy_publication_id") or "",
            policy_document_id=data.get("policy_document_id") or "",
            policy_effective_date=from_iso(data.get("policy_effective_date")),
            policy_source_text=data.get("policy_source_text"),
            policy_observed_at=from_iso(data.get("policy_observed_at")),
            lag_days=data.get("lag_days"),
            max_lag_days=data.get("max_lag_days"),
            formulation=data.get("formulation"),
            analysis_version=data.get("analysis_version"),
            analyzed_at=from_iso(data.get("analyzed_at")),
        )

    def describe(self) -> str:
        """Deterministic, non-causal human-readable formulation."""
        cond_label = _observed_label(self.condition_observed_at)
        pol_label = _observed_label(self.policy_observed_at)
        lag = f"within {self.lag_days} days" if self.lag_days is not None else "within the window"
        return (
            f"policy change {self.policy_subject} observed on {pol_label} "
            f"followed a condition change {self.condition_subject} observed on "
            f"{cond_label} {lag} (empirical temporal association, not causal)"
        )


@dataclass
class PolicyReactionResult:
    """Result of a reaction analysis: the derived reactions plus observability
    warnings (changes skipped because their current-side publication is missing
    or undated, or the change has no central bank)."""

    reactions: list[PolicyReaction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)