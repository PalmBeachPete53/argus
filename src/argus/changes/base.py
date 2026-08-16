"""Phase 5 — the Fact Changes model.

A ``FactChange`` is an **analytic relation between two existing Facts**
(previous → current), produced by the temporal / cross-publication analysis
layer. It is never a new Fact: the source Facts are left completely untouched,
and every change keeps the identities of BOTH source facts plus their
publication/document provenance, so "why did Argus say this value changed?" is
always answerable by "Fact A in document A had X, Fact B in document B has Y".

A change is **strictly descriptive**: it records that an observation changed,
never what the change means economically (no hawkish/dovish, no tightening/
easing, no market reading). That interpretation belongs to later phases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from ..facts.base import FactPeriod, FactValue
from ..normalize import from_iso, iso, now_utc


class ChangeType(str, Enum):
    """The observable kind of change between two Facts.

    - ``NUMERIC`` — a numeric value changed (delta preserved, units kept).
    - ``QUALITATIVE`` — a categorical/other value changed (exact value
      comparison only, no interpretation).
    - ``TEXT`` — a verbatim wording changed (both texts preserved).
    """

    NUMERIC = "numeric_changed"
    QUALITATIVE = "qualitative_changed"
    TEXT = "text_changed"


CHANGE_TYPES = tuple(t.value for t in ChangeType)


@dataclass
class FactChange:
    """An analytic relation ``previous_fact → current_fact``.

    The reference to the two source Facts (``previous_fact_id`` /
    ``current_fact_id``) is the core of the relation. All other fields are
    denormalized copies of provenance so a change is self-describing and each
    of the two sides stays traceable to its official publication/document.

    ``delta`` is set only for ``numeric_changed`` (same kind and unit as the
    compared values, rounded to 10 decimal places to strip binary noise). It is
    the observable difference ``current − previous``; it is never an economic
    interpretation.
    """

    change_id: str | None = None
    previous_fact_id: str = ""
    current_fact_id: str = ""
    change_type: ChangeType = ChangeType.QUALITATIVE
    central_bank: str | None = None
    subject: str = ""
    predicate: str = ""
    value_kind: str | None = None
    previous_value: FactValue | None = None
    current_value: FactValue | None = None
    delta: FactValue | None = None
    identity_qualifier: str = ""
    previous_period: FactPeriod | None = None
    current_period: FactPeriod | None = None
    previous_publication_id: str = ""
    current_publication_id: str = ""
    previous_document_id: str = ""
    current_document_id: str = ""
    previous_effective_date: datetime | None = None
    current_effective_date: datetime | None = None
    previous_source_text: str | None = None
    current_source_text: str | None = None
    analysis_version: str | None = None
    analyzed_at: datetime | None = None

    # ------------------------------------------------------------------
    # identity
    # ------------------------------------------------------------------
    def resolve_id(self) -> str:
        """Return ``change_id``, computing and caching it if missing."""
        from .identity import change_id_of

        if self.change_id is None:
            self.change_id = change_id_of(
                previous_fact_id=self.previous_fact_id,
                current_fact_id=self.current_fact_id,
                change_type=self.change_type,
            )
        return self.change_id

    # ------------------------------------------------------------------
    # serialization
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "change_id": self.change_id or self.resolve_id(),
            "previous_fact_id": self.previous_fact_id,
            "current_fact_id": self.current_fact_id,
            "change_type": self.change_type.value,
            "central_bank": self.central_bank,
            "subject": self.subject,
            "predicate": self.predicate,
            "value_kind": self.value_kind,
            "previous_value": self.previous_value.to_dict() if self.previous_value else None,
            "current_value": self.current_value.to_dict() if self.current_value else None,
            "delta": self.delta.to_dict() if self.delta else None,
            "identity_qualifier": self.identity_qualifier,
            "previous_period": self.previous_period.to_dict() if self.previous_period else None,
            "current_period": self.current_period.to_dict() if self.current_period else None,
            "previous_publication_id": self.previous_publication_id,
            "current_publication_id": self.current_publication_id,
            "previous_document_id": self.previous_document_id,
            "current_document_id": self.current_document_id,
            "previous_effective_date": iso(self.previous_effective_date),
            "current_effective_date": iso(self.current_effective_date),
            "previous_source_text": self.previous_source_text,
            "current_source_text": self.current_source_text,
            "analysis_version": self.analysis_version,
            "analyzed_at": iso(self.analyzed_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FactChange:
        return cls(
            change_id=data.get("change_id"),
            previous_fact_id=data.get("previous_fact_id") or "",
            current_fact_id=data.get("current_fact_id") or "",
            change_type=ChangeType(data["change_type"]),
            central_bank=data.get("central_bank"),
            subject=data.get("subject") or "",
            predicate=data.get("predicate") or "",
            value_kind=data.get("value_kind"),
            previous_value=FactValue.from_dict(data.get("previous_value")),
            current_value=FactValue.from_dict(data.get("current_value")),
            delta=FactValue.from_dict(data.get("delta")),
            identity_qualifier=data.get("identity_qualifier") or "",
            previous_period=FactPeriod.from_dict(data.get("previous_period")),
            current_period=FactPeriod.from_dict(data.get("current_period")),
            previous_publication_id=data.get("previous_publication_id") or "",
            current_publication_id=data.get("current_publication_id") or "",
            previous_document_id=data.get("previous_document_id") or "",
            current_document_id=data.get("current_document_id") or "",
            previous_effective_date=from_iso(data.get("previous_effective_date")),
            current_effective_date=from_iso(data.get("current_effective_date")),
            previous_source_text=data.get("previous_source_text"),
            current_source_text=data.get("current_source_text"),
            analysis_version=data.get("analysis_version"),
            analyzed_at=from_iso(data.get("analyzed_at")),
        )


@dataclass
class FactChangeResult:
    """Result of a change analysis: the derived changes plus observability
    warnings (facts skipped because their publication is missing, unclassified,
    undated, or the fact carries no value)."""

    changes: list[FactChange] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)