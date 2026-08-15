"""Phase 14 — monetary policy state analysis.

The analyzer is **pure** (``MonetaryPolicyStateAnalyzer.analyze`` works on
in-memory ``FactChange`` objects + a publications mapping + an optional
authoritative classifications mapping and never touches a store) and strictly
deterministic.

State rules (documented in ``docs/MONETARY_POLICY_STATE.md``):

1. A ``FactChange`` is a state observation when its ``subject`` is in
   ``STATE_SUBJECTS`` (Phase 13's reaction-side vocabulary). A change with no
   such subject is irrelevant and silently skipped.
2. The observed value is the change's **current side** (the newest known level
   of the dimension), copied verbatim — never invented, never converted.
3. The observation time is the temporal reference of the **current-side**
   publication — ``meeting_date`` when set, else ``publication_date``.
   ``effective_date`` and ``period`` are never observation times.
4. Forecast lineages (``predicate`` in ``STATE_EXCLUDED_PREDICATES``) are out
   of scope (they describe expected future values, not the current policy
   configuration) and are skipped with an ``out_of_scope_change`` warning.
5. **Bank isolation**: the bank is a property of the ``FactChange`` and is
   **never** resolved from the publication: a change without a ``central_bank``
   is skipped with an ``unplaced_change:<change_id>`` warning (never invented).
6. Each eligible change produces exactly one ``MonetaryPolicyState``.

No economic interpretation is ever attached to a state: no hawkish/dovish, no
stance, no forecast, no cross-bank comparison, no trading/forex logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from ..changes.base import FactChange
from ..models import Publication
from .base import (
    STATE_EXCLUDED_PREDICATES,
    STATE_SUBJECTS,
    MonetaryPolicyState,
    MonetaryPolicyStateResult,
)

_UNCOMPARABLE_TYPES = frozenset({"unknown", "other"})


@dataclass
class _Entry:
    """A change joined with the temporal reference and the publication type of
    its current-side publication and its resolved central bank."""

    change: FactChange
    observed_at: datetime
    central_bank: str
    pub_type: str


class MonetaryPolicyStateAnalyzer:
    """Pure monetary policy state analyzer."""

    analysis_version = "14.0.0"

    def analyze(
        self,
        changes: list[FactChange],
        *,
        publications: Mapping[str, Publication] | None = None,
        classifications: Mapping[str, str] | None = None,
    ) -> MonetaryPolicyStateResult:
        """Synthesize the monetary policy state observations of the given
        changes.

        ``changes`` are the ``FactChange`` relations produced by Phase 12;
        ``publications`` maps ``publication_id → Publication`` and supplies the
        temporal reference (``meeting_date`` else ``publication_date``) of each
        change's current-side publication.

        ``classifications`` maps ``publication_id → publication_type`` and is
        the **authoritative** source of the publication type (the lineage
        discriminator, exactly as in Phase 12). When provided, a publication
        absent from the mapping has no canonical classification and is skipped
        (``missing_classification`` warning). When ``classifications`` is
        ``None`` (standalone in-memory use), the analyzer falls back to the
        denormalized ``Publication.publication_type`` — documented convenience,
        never used by the production entry point ``analyze_policy_state``.
        """
        pubs: Mapping[str, Publication] = publications or {}
        result = MonetaryPolicyStateResult()

        states: list[MonetaryPolicyState] = []
        for change in changes:
            entry = self._prepare(change, pubs, classifications, result.warnings)
            if entry is None:
                continue
            state = self._build(entry)
            if state is not None:
                states.append(state)

        states.sort(key=lambda s: s.resolve_id())
        result.states = states
        return result

    # ------------------------------------------------------------------
    # preparation
    # ------------------------------------------------------------------
    def _prepare(
        self,
        change: FactChange,
        pubs: Mapping[str, Publication],
        classifications: Mapping[str, str] | None,
        warnings: list[str],
    ) -> _Entry | None:
        if not change.current_publication_id:
            warnings.append(f"missing_publication:{change.change_id or change.resolve_id()}")
            return None
        pub = pubs.get(change.current_publication_id)
        if pub is None:
            warnings.append(f"missing_publication:{change.current_publication_id}")
            return None
        observed_at = pub.meeting_date or pub.publication_date
        if observed_at is None:
            warnings.append(f"undated_publication:{pub.id}")
            return None
        bank = change.central_bank
        if not bank:
            warnings.append(f"unplaced_change:{change.change_id or change.resolve_id()}")
            return None
        if change.subject not in STATE_SUBJECTS:
            return None
        pub_type = self._resolve_type(change, pub, classifications, warnings)
        if pub_type is None:
            return None
        if change.predicate in STATE_EXCLUDED_PREDICATES:
            warnings.append(f"out_of_scope_change:{change.change_id or change.resolve_id()}")
            return None
        if change.current_value is None or change.current_value.kind is None:
            warnings.append(f"valueless_change:{change.change_id or change.resolve_id()}")
            return None
        return _Entry(
            change=change,
            observed_at=observed_at,
            central_bank=bank,
            pub_type=pub_type,
        )

    def _resolve_type(
        self,
        change: FactChange,
        pub: Publication,
        classifications: Mapping[str, str] | None,
        warnings: list[str],
    ) -> str | None:
        if classifications is not None:
            if pub.id not in classifications:
                warnings.append(f"missing_classification:{pub.id}")
                return None
            pub_type = classifications[pub.id]
        else:
            pub_type = pub.publication_type
        if not pub_type or pub_type in _UNCOMPARABLE_TYPES:
            warnings.append(f"unclassified_publication:{pub.id}")
            return None
        return pub_type

    # ------------------------------------------------------------------
    # build
    # ------------------------------------------------------------------
    def _build(self, entry: _Entry) -> MonetaryPolicyState | None:
        change = entry.change
        period = change.current_period
        dimension_key = "\x1f".join(
            (
                entry.central_bank,
                change.subject,
                change.predicate,
                change.value_kind or "",
                period.canonical() if period else "",
                change.identity_qualifier or "",
                entry.pub_type,
            )
        )
        state = MonetaryPolicyState(
            central_bank=entry.central_bank,
            synthesized=True,
            source_change_id=change.resolve_id(),
            dimension_key=dimension_key,
            subject=change.subject,
            predicate=change.predicate,
            value_kind=change.value_kind,
            qualifier=change.identity_qualifier or "",
            period=period,
            publication_type=entry.pub_type,
            value=change.current_value,
            previous_value=change.previous_value,
            observed_at=entry.observed_at,
            publication_id=change.current_publication_id,
            document_id=change.current_document_id,
            effective_date=change.current_effective_date,
            source_text=change.current_source_text,
            analysis_version=self.analysis_version,
        )
        state.resolve_id()
        return state


def analyze_policy_state(
    store,
    *,
    bank: str | None = None,
    persist: bool = True,
) -> MonetaryPolicyStateResult:
    """Recompute the monetary policy state of a bank (or the whole store) from
    the current ``fact_changes`` table (Phase 12 output), persist it
    idempotently, and return the result (states + observability warnings).

    Phase 14 consumes Phase 12 output: the changes are read from the persisted
    ``fact_changes`` table; Phase 12 must be run first. The publication type is
    taken from the authoritative ``classifications`` table (never from the
    denormalized ``publications.publication_type`` cache), matching Phase 12.

    The ``monetary_policy_states`` table is derived data:
    ``analyze_policy_state`` recomputes the full bank scope and *replaces* it
    (``rebuild_policy_states``), so repeated runs are idempotent, empty results
    clear the scope, and a state can never survive the disappearance of the
    change it summarizes. Source ``facts``, ``fact_changes`` and
    ``policy_reactions`` are never modified.
    """
    publications = store.list_publications(bank=bank)
    pubs: dict[str, object] = {p.id: p for p in publications if p.id}
    classifications: dict[str, str] = {
        c["publication_id"]: c["publication_type"]
        for c in store.list_classifications(bank=bank)
    }
    changes = store.get_changes(bank=bank)
    result = MonetaryPolicyStateAnalyzer().analyze(
        changes, publications=pubs, classifications=classifications
    )
    if persist:
        store.rebuild_policy_states(result.states, bank=bank)
    return result