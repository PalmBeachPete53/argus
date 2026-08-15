"""Phase 13 — empirical policy reaction analysis.

The analyzer is **pure** (``PolicyReactionAnalyzer.analyze`` works on in-memory
``FactChange`` objects + a publications mapping and never touches a store) and
strictly deterministic.

Relationship rules (documented in ``docs/REACTIONS.md``):

1. A ``FactChange`` is **condition-side** when its ``subject`` is in
   ``CONDITION_SUBJECTS``; it is **reaction-side** when its ``subject`` is in
   ``REACTION_SUBJECTS``. A change with neither role is ignored (irrelevant,
   not an error).
2. The observation time of a change is the temporal reference of its
   **current-side** publication — ``meeting_date`` when set, else
   ``publication_date`` (same reference Phase 12 uses to order observations).
3. **No look-ahead**: a condition may relate to a policy response only when
   ``condition_observed_at <= policy_observed_at``.
4. **Window**: the lag ``policy_observed_at - condition_observed_at`` must be
   ``0 <= lag_days <= max_lag_days`` (documented default 180 days).
5. **Bank isolation**: pairing is per ``central_bank``; a change without a
   central bank is skipped with a warning.
6. Each eligible ``(condition change, policy change)`` pair produces exactly
   one ``PolicyReaction``.

No economic interpretation is ever attached to a reaction: no hawkish/dovish,
no stance score, no causality, no trading/forex logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from ..changes.base import FactChange
from ..models import Publication
from ..normalize import iso
from .base import (
    CONDITION_SUBJECTS,
    DEFAULT_MAX_LAG_DAYS,
    REACTION_SUBJECTS,
    PolicyReaction,
    PolicyReactionResult,
)


@dataclass
class _Entry:
    """A change joined with the temporal reference of its current-side
    publication and its resolved central bank."""

    change: FactChange
    observed_at: datetime
    central_bank: str


class PolicyReactionAnalyzer:
    """Pure empirical policy reaction analyzer."""

    analysis_version = "13.0.0"

    def analyze(
        self,
        changes: list[FactChange],
        *,
        publications: Mapping[str, Publication] | None = None,
        max_lag_days: int = DEFAULT_MAX_LAG_DAYS,
    ) -> PolicyReactionResult:
        """Derive the empirical reactions between condition-side and
        reaction-side changes.

        ``changes`` are the ``FactChange`` relations produced by Phase 12;
        ``publications`` maps ``publication_id → Publication`` and supplies the
        temporal reference (``meeting_date`` else ``publication_date``) of each
        change's current-side publication.
        """
        pubs: Mapping[str, Publication] = publications or {}
        result = PolicyReactionResult()
        if max_lag_days < 0:
            raise ValueError(f"max_lag_days must be >= 0, got {max_lag_days}")

        conditions: dict[str, list[_Entry]] = {}
        reactions: dict[str, list[_Entry]] = {}
        for change in changes:
            entry = self._prepare(change, pubs, result.warnings)
            if entry is None:
                continue
            subject = change.subject
            if subject in REACTION_SUBJECTS:
                reactions.setdefault(entry.central_bank, []).append(entry)
            elif subject in CONDITION_SUBJECTS:
                conditions.setdefault(entry.central_bank, []).append(entry)
            # neither role → irrelevant, silently skipped.

        derived: list[PolicyReaction] = []
        for bank, reaction_entries in reactions.items():
            condition_entries = conditions.get(bank, ())
            for r in reaction_entries:
                for c in condition_entries:
                    lag = int((r.observed_at - c.observed_at).total_seconds())
                    if lag < 0 or lag > max_lag_days * 86400:
                        continue
                    derived.append(
                        self._build(c, r, lag_days=lag // 86400, max_lag_days=max_lag_days)
                    )

        derived.sort(key=lambda rr: rr.resolve_id())
        result.reactions = derived
        return result

    # ------------------------------------------------------------------
    # preparation
    # ------------------------------------------------------------------
    def _prepare(
        self,
        change: FactChange,
        pubs: Mapping[str, Publication],
        warnings: list[str],
    ) -> _Entry | None:
        if not change.current_publication_id:
            warnings.append(f"missing_publication:{change.current_publication_id}")
            return None
        pub = pubs.get(change.current_publication_id)
        if pub is None:
            warnings.append(f"missing_publication:{change.current_publication_id}")
            return None
        observed_at = pub.meeting_date or pub.publication_date
        if observed_at is None:
            warnings.append(f"undated_publication:{pub.id}")
            return None
        bank = change.central_bank or pub.central_bank
        if not bank:
            warnings.append(f"unplaced_change:{change.change_id or change.resolve_id()}")
            return None
        return _Entry(change=change, observed_at=observed_at, central_bank=bank)

    # ------------------------------------------------------------------
    # build
    # ------------------------------------------------------------------
    def _build(
        self,
        condition: _Entry,
        policy: _Entry,
        *,
        lag_days: int,
        max_lag_days: int,
    ) -> PolicyReaction:
        c, p = condition.change, policy.change
        reaction = PolicyReaction(
            central_bank=condition.central_bank,
            inferred=True,
            # condition side
            condition_change_id=c.resolve_id(),
            condition_subject=c.subject,
            condition_predicate=c.predicate,
            condition_value_kind=c.value_kind,
            condition_previous_value=c.previous_value,
            condition_current_value=c.current_value,
            condition_period=c.current_period,
            condition_publication_id=c.current_publication_id,
            condition_document_id=c.current_document_id,
            condition_effective_date=c.current_effective_date,
            condition_source_text=c.current_source_text,
            condition_observed_at=condition.observed_at,
            # policy side
            policy_change_id=p.resolve_id(),
            policy_subject=p.subject,
            policy_predicate=p.predicate,
            policy_value_kind=p.value_kind,
            policy_previous_value=p.previous_value,
            policy_current_value=p.current_value,
            policy_period=p.current_period,
            policy_publication_id=p.current_publication_id,
            policy_document_id=p.current_document_id,
            policy_effective_date=p.current_effective_date,
            policy_source_text=p.current_source_text,
            policy_observed_at=policy.observed_at,
            # relationship
            lag_days=lag_days,
            max_lag_days=max_lag_days,
            analysis_version=self.analysis_version,
        )
        reaction.formulation = reaction.describe()
        return reaction


def analyze_reactions(
    store,
    *,
    bank: str | None = None,
    max_lag_days: int = DEFAULT_MAX_LAG_DAYS,
    persist: bool = True,
) -> PolicyReactionResult:
    """Recompute the reactions of a bank (or the whole store) from the current
    ``fact_changes`` table (Phase 12 output), persist them idempotently, and
    return the result (reactions + observability warnings).

    Phase 13 consumes Phase 12 output: the changes are read from the persisted
    ``fact_changes`` table; Phase 12 must be run first. The ``policy_reactions``
    table is derived data: ``analyze_reactions`` recomputes the full bank scope
    and *replaces* it (``rebuild_reactions``), so repeated runs are idempotent,
    empty results clear the scope, and a reaction can never survive the
    disappearance of the changes it relates. Source ``facts`` and
    ``fact_changes`` are never modified.
    """
    publications = store.list_publications(bank=bank)
    pubs: dict[str, object] = {p.id: p for p in publications if p.id}
    changes = store.get_changes(bank=bank)
    result = PolicyReactionAnalyzer().analyze(
        changes, publications=pubs, max_lag_days=max_lag_days
    )
    if persist:
        store.rebuild_reactions(result.reactions, bank=bank)
    return result