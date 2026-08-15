"""Phase 14 — monetary policy state analysis.

Tests the pure ``MonetaryPolicyStateAnalyzer`` and the store integration
(``analyze_policy_state`` + ``monetary_policy_states`` persistence).

Coverage:

- **Vocabulary**: state dimensions == Phase 13 reaction-side subjects;
  excluded (forecast) predicates documented; dimensions are disjoint from the
  excluded set.
- **Model**: ``synthesized`` is always ``True``; formulation is purely
  descriptive (never stance/forecast/forex); deterministic; serialization
  round-trip.
- **Identity**: deterministic, change-specific, bank-specific ``state_id``.
- **Temporal**: ``meeting_date`` priority, ``publication_date`` fallback,
  ``effective_date`` is never an observation time, no-look-ahead
  (``get_policy_state_as_of``), state persists between decisions, initial state
  (before the first change) is empty.
- **Evolution**: state advances on a new change; several policy rates are
  separate dimensions; succession of changes; guidance text; risk assessment.
- **Scope**: projection lineages excluded with an ``out_of_scope_change``
  warning; irrelevant subjects ignored silently; valueless changes skipped;
  no invented/converted rate.
- **Warnings**: ``missing_publication`` (change id / publication id),
  ``undated_publication``, ``unplaced_change``, ``missing_classification``
  (authoritative mode).
- **Provenance**: verbatim current side, ``observed_at``, dimension key
  includes the publication type.
- **Determinism**: identical input → identical states, order-independent.
- **Persistence**: idempotent rebuild, empty clears scope, bank isolation,
  filters, deletes, ``created_at`` preservation, ``persist=False``, as-of
  queries.
- **Negative**: source FactChanges never mutated; no hawkish/dovish, no
  forecast, no stance, no cross-bank comparison, no forex.
"""

from __future__ import annotations

import copy
from datetime import datetime

import pytest

from argus.changes import ChangeType, FactChange
from argus.facts import categorical, percentage, text_value
from argus.facts.base import FactPeriod, PeriodKind
from argus.models import Publication, PublicationStatus
from argus.reactions import REACTION_SUBJECTS
from argus.states import (
    STATE_EXCLUDED_PREDICATES,
    STATE_SUBJECTS,
    MonetaryPolicyState,
    MonetaryPolicyStateAnalyzer,
    MonetaryPolicyStateResult,
    analyze_policy_state,
    state_id_of,
)
from argus.store import Store

BANK = "ecb"

DECISION = "monetary_policy_decision"

FORBIDDEN_WORDS = ("hawkish", "dovish", "forecast", "expected", "buy", "sell",
                   "stance", "directional", "fx", "forex", "carry")


def mk_pub(
    pub_id: str,
    date: datetime | None,
    *,
    meeting_date: datetime | None = None,
    bank: str = BANK,
    pub_type: str = DECISION,
) -> Publication:
    return Publication(
        central_bank=bank,
        title="title",
        url=f"https://example.org/{pub_id}",
        source_id="src",
        source_url="https://example.org",
        id=pub_id,
        publication_type=pub_type,
        publication_date=date,
        meeting_date=meeting_date,
        status=PublicationStatus.FETCHED,
    )


def mk_change(
    change_id: str,
    subject: str,
    *,
    cur_pub: str,
    prev_fact: str = "F0",
    cur_fact: str = "F1",
    prev_value=None,
    cur_value=None,
    ctype: ChangeType = ChangeType.NUMERIC,
    bank: str = BANK,
    period: FactPeriod | None = None,
    effective: datetime | None = None,
    source_text: str | None = None,
    predicate: str = "value",
    qualifier: str = "",
    value_kind: str | None = None,
) -> FactChange:
    if value_kind is None:
        value_kind = cur_value.kind.value if cur_value is not None and cur_value.kind else None
    return FactChange(
        change_id=change_id,
        previous_fact_id=prev_fact,
        current_fact_id=cur_fact,
        change_type=ctype,
        central_bank=bank,
        subject=subject,
        predicate=predicate,
        value_kind=value_kind,
        previous_value=prev_value,
        current_value=cur_value,
        identity_qualifier=qualifier,
        current_period=period,
        current_publication_id=cur_pub,
        current_document_id=cur_pub,
        current_effective_date=effective,
        current_source_text=source_text,
    )


def run(
    changes: list[FactChange],
    pubs: dict[str, Publication],
    *,
    classifications: dict[str, str] | None = None,
) -> MonetaryPolicyStateResult:
    return MonetaryPolicyStateAnalyzer().analyze(
        changes, publications=pubs, classifications=classifications
    )


def rate_changes() -> tuple[list[FactChange], dict[str, Publication]]:
    pubs = {
        "P1": mk_pub("P1", datetime(2026, 1, 15), meeting_date=datetime(2026, 1, 15)),
        "P2": mk_pub("P2", datetime(2026, 3, 15), meeting_date=datetime(2026, 3, 15)),
        "P3": mk_pub("P3", datetime(2026, 5, 15), meeting_date=datetime(2026, 5, 15)),
    }
    changes = [
        mk_change(
            "r1", "policy_rate", cur_pub="P2",
            prev_value=percentage(4.00), cur_value=percentage(4.25),
            effective=datetime(2026, 3, 18), source_text="4.25 percent",
        ),
        mk_change(
            "r2", "policy_rate", cur_pub="P3",
            prev_value=percentage(4.25), cur_value=percentage(4.50),
            effective=datetime(2026, 5, 20), source_text="4.50 percent",
        ),
    ]
    return changes, pubs


# ---------------------------------------------------------------------------
# vocabulary
# ---------------------------------------------------------------------------
class TestVocabulary:
    def test_state_subjects_are_phase13_reaction_subjects(self):
        assert STATE_SUBJECTS == REACTION_SUBJECTS

    def test_policy_rate_in_state_subjects(self):
        assert "policy_rate" in STATE_SUBJECTS
        assert "main_refinancing_rate" in STATE_SUBJECTS
        assert "deposit_facility_rate" in STATE_SUBJECTS
        assert "marginal_lending_rate" in STATE_SUBJECTS

    def test_guidance_asset_purchase_risk_in_state_subjects(self):
        assert {"policy_guidance", "asset_purchase", "risk", "inflation_risk", "growth_risk"} <= STATE_SUBJECTS

    def test_conditions_are_not_state_subjects(self):
        assert "inflation" not in STATE_SUBJECTS
        assert "gdp" not in STATE_SUBJECTS
        assert "unemployment" not in STATE_SUBJECTS

    def test_excluded_predicates_documented(self):
        assert STATE_EXCLUDED_PREDICATES == {"projection"}

    def test_excluded_predicates_not_subjects(self):
        assert STATE_SUBJECTS.isdisjoint(STATE_EXCLUDED_PREDICATES)


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------
class TestModel:
    def test_synthesized_is_always_true(self):
        assert MonetaryPolicyState().synthesized is True

    def test_synthesized_constant_even_after_analysis(self):
        changes, pubs = rate_changes()
        res = run(changes, pubs)
        assert res.states
        for state in res.states:
            assert state.synthesized is True

    def test_describe_is_descriptive_only(self):
        changes, pubs = rate_changes()
        for state in run(changes, pubs).states:
            text = state.describe()
            assert "derived from change" in text
            assert state.central_bank in text
            assert state.subject in text
            assert "= " in text
            assert "monetary policy state" in text

    def test_describe_never_stance_forecast_forex(self):
        changes, pubs = rate_changes()
        for state in run(changes, pubs).states:
            text = state.describe().lower()
            for word in FORBIDDEN_WORDS:
                assert word not in text

    def test_describe_is_deterministic(self):
        changes, pubs = rate_changes()
        a = run(changes, pubs).states[0].describe()
        b = run(changes, pubs).states[0].describe()
        assert a == b

    def test_serialization_round_trip(self):
        changes, pubs = rate_changes()
        state = run(changes, pubs).states[0]
        restored = MonetaryPolicyState.from_dict(state.to_dict())
        assert restored.state_id == state.state_id
        assert restored.synthesized is True
        assert restored.subject == "policy_rate"
        assert restored.value.value == 4.25
        assert restored.previous_value.value == 4.00
        from argus.normalize import iso

        assert iso(restored.observed_at) == iso(state.observed_at)
        assert iso(restored.effective_date) == iso(state.effective_date)
        assert restored.source_text == "4.25 percent"
        assert restored.source_change_id == "r1"
        assert restored.dimension_key == state.dimension_key
        assert restored.analysis_version == state.analysis_version


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------
class TestIdentity:
    def test_state_id_is_deterministic(self):
        a = state_id_of(central_bank=BANK, source_change_id="r1")
        b = state_id_of(central_bank=BANK, source_change_id="r1")
        assert a == b
        assert len(a) == 64  # sha256 hex

    def test_state_id_change_specific(self):
        a = state_id_of(central_bank=BANK, source_change_id="r1")
        b = state_id_of(central_bank=BANK, source_change_id="r2")
        assert a != b

    def test_state_id_bank_specific(self):
        a = state_id_of(central_bank="ecb", source_change_id="r1")
        b = state_id_of(central_bank="fed", source_change_id="r1")
        assert a != b

    def test_state_id_resolve_matches_identity(self):
        changes, pubs = rate_changes()
        state = run(changes, pubs).states[0]
        assert state.state_id == state_id_of(central_bank=BANK, source_change_id="r1")


# ---------------------------------------------------------------------------
# temporal
# ---------------------------------------------------------------------------
class TestTemporal:
    def test_meeting_date_is_observation_time(self):
        changes, pubs = rate_changes()
        state = run(changes, pubs).states[0]
        assert state.observed_at == datetime(2026, 3, 15)
        assert state.observed_at != state.effective_date

    def test_publication_date_fallback_when_no_meeting_date(self):
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 15), meeting_date=None),
        }
        changes = [mk_change("r1", "policy_rate", cur_pub="P2",
                             prev_value=percentage(4.00), cur_value=percentage(4.25))]
        state = run(changes, pubs).states[0]
        assert state.observed_at == datetime(2026, 3, 15)

    def test_meeting_date_wins_over_publication_date(self):
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 17), meeting_date=datetime(2026, 3, 15)),
        }
        changes = [mk_change("r1", "policy_rate", cur_pub="P2",
                             prev_value=percentage(4.00), cur_value=percentage(4.25))]
        state = run(changes, pubs).states[0]
        assert state.observed_at == datetime(2026, 3, 15)

    def test_effective_date_never_used_as_observation_time(self):
        changes, pubs = rate_changes()
        for state in run(changes, pubs).states:
            assert state.observed_at.date() < state.effective_date.date()

    def test_identical_dates_distinct_dimensions(self):
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 15), meeting_date=datetime(2026, 3, 15)),
        }
        changes = [
            mk_change("r1", "policy_rate", cur_pub="P2",
                      prev_value=percentage(4.00), cur_value=percentage(4.25)),
            mk_change("d1", "deposit_facility_rate", cur_pub="P2",
                      prev_value=percentage(3.50), cur_value=percentage(3.75)),
        ]
        states = run(changes, pubs).states
        assert {s.subject for s in states} == {"policy_rate", "deposit_facility_rate"}
        assert len({s.observed_at for s in states}) == 1

    def test_no_look_ahead_in_as_of_query(self):
        store = self._seed_store()
        st = store.get_policy_state_as_of(BANK, datetime(2026, 1, 15))
        assert st == []
        st = store.get_policy_state_as_of(BANK, datetime(2026, 3, 15))
        assert [(s.value.value) for s in st] == [4.25]
        st = store.get_policy_state_as_of(BANK, datetime(2026, 5, 15))
        assert [(s.value.value) for s in st] == [4.5]

    def test_state_persists_between_decisions(self):
        store = self._seed_store()
        st = store.get_policy_state_as_of(BANK, datetime(2026, 4, 15))
        assert [(s.value.value) for s in st] == [4.25]

    def test_initial_state_before_first_change_is_empty(self):
        changes, pubs = rate_changes()
        # nothing observed yet — a store without changes has no state
        store = Store(":memory:")
        res = analyze_policy_state(store, bank=BANK)
        assert res.states == []
        assert res.warnings == []

    def test_period_is_never_an_observation_time(self):
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 15), meeting_date=datetime(2026, 3, 15)),
        }
        period = FactPeriod(PeriodKind.YEAR, "2027", "2027")
        changes = [mk_change("p1", "policy_rate", cur_pub="P2", period=period,
                             prev_value=percentage(4.00), cur_value=percentage(4.25),
                             predicate="projection")]
        res = run(changes, pubs)
        assert res.states == []
        assert any("out_of_scope_change" in w for w in res.warnings)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _seed_store(self) -> Store:
        store = Store(":memory:")
        for pid, date, meeting, rate in (
            ("P1", datetime(2026, 1, 15), datetime(2026, 1, 15), 4.00),
            ("P2", datetime(2026, 3, 15), datetime(2026, 3, 15), 4.25),
            ("P3", datetime(2026, 5, 15), datetime(2026, 5, 15), 4.50),
        ):
            store.upsert_publication(mk_pub(pid, date, meeting_date=meeting))
            store.set_classification(
                pid, central_bank=BANK, publication_type=DECISION,
                confidence="high", method="source_type_hint", evidence=["test"],
            )
            store.save_fact(_mk_fact(pid, "policy_rate", percentage(rate)))
        from argus.changes import analyze_changes

        analyze_changes(store, bank=BANK)
        analyze_policy_state(store, bank=BANK)
        return store


# ---------------------------------------------------------------------------
# evolution
# ---------------------------------------------------------------------------
class TestEvolution:
    def test_state_advances_on_new_change(self):
        changes, pubs = rate_changes()
        states = run(changes, pubs).states
        by_change = {s.source_change_id: s for s in states}
        assert by_change["r1"].value.value == 4.25
        assert by_change["r2"].value.value == 4.50

    def test_multiple_policy_rates_are_separate_dimensions(self):
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 15), meeting_date=datetime(2026, 3, 15)),
        }
        changes = [
            mk_change("r1", "policy_rate", cur_pub="P2",
                      prev_value=percentage(4.00), cur_value=percentage(4.25)),
            mk_change("m1", "main_refinancing_rate", cur_pub="P2",
                      prev_value=percentage(4.00), cur_value=percentage(4.25)),
            mk_change("d1", "deposit_facility_rate", cur_pub="P2",
                      prev_value=percentage(3.50), cur_value=percentage(3.75)),
            mk_change("l1", "marginal_lending_rate", cur_pub="P2",
                      prev_value=percentage(4.75), cur_value=percentage(5.00)),
        ]
        states = run(changes, pubs).states
        assert {s.subject for s in states} == {
            "policy_rate", "main_refinancing_rate",
            "deposit_facility_rate", "marginal_lending_rate",
        }
        assert len(states) == 4

    def test_succession_of_changes_same_dimension(self):
        changes, pubs = rate_changes()
        states = run(changes, pubs).states
        assert len(states) == 2
        assert {s.source_change_id for s in states} == {"r1", "r2"}
        assert {s.dimension_key for s in states} == {states[0].dimension_key}

    def test_guidance_text_kept_verbatim(self):
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 15), meeting_date=datetime(2026, 3, 15)),
        }
        changes = [
            mk_change("g1", "policy_guidance", cur_pub="P2", ctype=ChangeType.TEXT,
                      prev_value=text_value("The Governing Council expects rates to stay at current levels."),
                      cur_value=text_value("The Governing Council is attentive to the risks to price stability."),
                      predicate="statement", source_text="The Governing Council is attentive to the risks to price stability."),
        ]
        state = run(changes, pubs).states[0]
        assert state.subject == "policy_guidance"
        assert state.value.value == "The Governing Council is attentive to the risks to price stability."
        assert state.predicate == "statement"

    def test_risk_assessment_kept_as_observed(self):
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 15), meeting_date=datetime(2026, 3, 15)),
        }
        changes = [
            mk_change("x1", "inflation_risk", cur_pub="P2", ctype=ChangeType.QUALITATIVE,
                      prev_value=categorical("balanced"), cur_value=categorical("to_the_upside"),
                      predicate="assessment"),
        ]
        state = run(changes, pubs).states[0]
        assert state.subject == "inflation_risk"
        assert state.value.value == "to_the_upside"

    def test_asset_purchase_kept_as_observed(self):
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 15), meeting_date=datetime(2026, 3, 15)),
        }
        changes = [
            mk_change("a1", "asset_purchase", cur_pub="P2",
                      prev_value=percentage(2.0), cur_value=percentage(1.5),
                      predicate="value"),
        ]
        state = run(changes, pubs).states[0]
        assert state.subject == "asset_purchase"
        assert state.value.value == 1.5


# ---------------------------------------------------------------------------
# scope
# ---------------------------------------------------------------------------
class TestScope:
    def test_projection_lineage_excluded_with_warning(self):
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 15), meeting_date=datetime(2026, 3, 15)),
            "P3": mk_pub("P3", datetime(2026, 5, 15), meeting_date=datetime(2026, 5, 15)),
        }
        changes = [
            mk_change("r1", "policy_rate", cur_pub="P2",
                      prev_value=percentage(4.00), cur_value=percentage(4.25)),
            mk_change("pr1", "policy_rate", cur_pub="P3", predicate="projection",
                      prev_value=percentage(4.25), cur_value=percentage(3.50)),
        ]
        res = run(changes, pubs)
        assert {s.source_change_id for s in res.states} == {"r1"}
        assert any("out_of_scope_change:pr1" in w for w in res.warnings)

    def test_irrelevant_subjects_ignored_silently(self):
        changes, pubs = rate_changes()
        changes = changes + [
            mk_change("i1", "inflation", cur_pub="P2",
                      prev_value=percentage(2.1), cur_value=percentage(2.4)),
            mk_change("g1", "gdp", cur_pub="P2",
                      prev_value=percentage(2.0), cur_value=percentage(2.5)),
        ]
        res = run(changes, pubs)
        assert {s.subject for s in res.states} == {"policy_rate"}
        assert res.warnings == []

    def test_valueless_change_skipped_with_warning(self):
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 15), meeting_date=datetime(2026, 3, 15)),
        }
        changes = [
            mk_change("v1", "policy_rate", cur_pub="P2",
                      prev_value=percentage(4.00), cur_value=None),
        ]
        res = run(changes, pubs)
        assert res.states == []
        assert any("valueless_change:v1" in w for w in res.warnings)

    def test_no_invented_rate_when_absent(self):
        changes, pubs = rate_changes()
        states = run(changes, pubs).states
        assert "deposit_facility_rate" not in {s.subject for s in states}
        assert all(s.value.value in (4.25, 4.50) for s in states)

    def test_no_conversion_between_rates(self):
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 15), meeting_date=datetime(2026, 3, 15)),
        }
        changes = [
            mk_change("m1", "main_refinancing_rate", cur_pub="P2",
                      prev_value=percentage(4.00), cur_value=percentage(4.25)),
        ]
        states = run(changes, pubs).states
        assert len(states) == 1
        assert states[0].subject == "main_refinancing_rate"
        assert states[0].value.value == 4.25


# ---------------------------------------------------------------------------
# warnings / observability
# ---------------------------------------------------------------------------
class TestWarnings:
    def test_missing_publication_warns(self):
        pubs = {"P1": mk_pub("P1", datetime(2026, 1, 15))}
        changes = [mk_change("r1", "policy_rate", cur_pub="PX",
                             prev_value=percentage(4.00), cur_value=percentage(4.25))]
        res = run(changes, pubs)
        assert res.states == []
        assert any("missing_publication:PX" in w for w in res.warnings)

    def test_no_current_publication_warns_with_change_id(self):
        pubs = {}
        changes = [mk_change("r1", "policy_rate", cur_pub="",
                             prev_value=percentage(4.00), cur_value=percentage(4.25))]
        res = run(changes, pubs)
        assert res.states == []
        assert any("missing_publication:r1" in w for w in res.warnings)

    def test_undated_publication_warns(self):
        pubs = {"P2": mk_pub("P2", None)}
        changes = [mk_change("r1", "policy_rate", cur_pub="P2",
                             prev_value=percentage(4.00), cur_value=percentage(4.25))]
        res = run(changes, pubs)
        assert res.states == []
        assert any("undated_publication:P2" in w for w in res.warnings)

    def test_unplaced_change_warns(self):
        pubs = {"P2": mk_pub("P2", datetime(2026, 3, 15), meeting_date=datetime(2026, 3, 15))}
        changes = [mk_change("r1", "policy_rate", cur_pub="P2",
                             prev_value=percentage(4.00), cur_value=percentage(4.25),
                             bank=None)]
        res = run(changes, pubs)
        assert res.states == []
        assert any("unplaced_change:r1" in w for w in res.warnings)

    def test_unplaced_change_warns_even_when_publication_has_bank(self):
        pubs = {"P2": mk_pub("P2", datetime(2026, 3, 15), meeting_date=datetime(2026, 3, 15))}
        changes = [mk_change("r1", "policy_rate", cur_pub="P2", bank=None,
                             prev_value=percentage(4.00), cur_value=percentage(4.25))]
        res = run(changes, pubs)
        assert res.states == []
        assert any("unplaced_change:r1" in w for w in res.warnings)

    def test_missing_classification_warns_in_authoritative_mode(self):
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 15), meeting_date=datetime(2026, 3, 15)),
        }
        changes = [mk_change("r1", "policy_rate", cur_pub="P2",
                             prev_value=percentage(4.00), cur_value=percentage(4.25))]
        res = run(changes, pubs, classifications={"P1": DECISION})
        assert res.states == []
        assert any("missing_classification:P2" in w for w in res.warnings)

    def test_authoritative_classification_used_when_provided(self):
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 15), meeting_date=datetime(2026, 3, 15),
                         pub_type="press_conference"),
        }
        changes = [mk_change("r1", "policy_rate", cur_pub="P2",
                             prev_value=percentage(4.00), cur_value=percentage(4.25))]
        # the denormalized cache says press_conference, but the authoritative
        # classification says decision — the authoritative one must win.
        res = run(changes, pubs, classifications={"P1": DECISION, "P2": DECISION})
        assert len(res.states) == 1
        assert res.states[0].publication_type == DECISION


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------
class TestDeterminism:
    def test_same_input_same_states(self):
        changes, pubs = rate_changes()
        a = [s.state_id for s in run(changes, pubs).states]
        b = [s.state_id for s in run(changes, pubs).states]
        assert a == b

    def test_input_order_independent(self):
        changes, pubs = rate_changes()
        shuffled = list(reversed(changes))
        a = [s.state_id for s in run(changes, pubs).states]
        b = [s.state_id for s in run(shuffled, pubs).states]
        assert a == b

    def test_empty_input(self):
        res = run([], {})
        assert res.states == []
        assert res.warnings == []


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------
class TestProvenance:
    def test_current_side_verbatim(self):
        changes, pubs = rate_changes()
        state = run(changes, pubs).states[0]
        assert state.publication_id == "P2"
        assert state.document_id == "P2"
        assert state.source_text == "4.25 percent"
        assert state.effective_date == datetime(2026, 3, 18)
        assert state.previous_value.value == 4.00

    def test_dimension_key_includes_publication_type(self):
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2a": mk_pub("P2a", datetime(2026, 3, 15), meeting_date=datetime(2026, 3, 15)),
            "P2b": mk_pub("P2b", datetime(2026, 3, 16), meeting_date=datetime(2026, 3, 16),
                          pub_type="press_conference"),
        }
        changes = [
            mk_change("d1", "policy_rate", cur_pub="P2a",
                      prev_value=percentage(4.00), cur_value=percentage(4.25)),
            mk_change("s1", "policy_rate", cur_pub="P2b",
                      prev_value=percentage(4.25), cur_value=percentage(4.25)),
        ]
        states = run(changes, pubs).states
        # two different lineages (decision vs press_conference) → two
        # dimensions, even though subject and value are the same.
        assert len(states) == 2
        assert len({s.dimension_key for s in states}) == 2

    def test_analysis_version_set(self):
        changes, pubs = rate_changes()
        for state in run(changes, pubs).states:
            assert state.analysis_version == "14.0.0"

    def test_provenance_traceable_to_change(self):
        changes, pubs = rate_changes()
        state = run(changes, pubs).states[0]
        assert state.source_change_id == "r1"
        assert state.dimension_key.startswith("ecb")


# ---------------------------------------------------------------------------
# store persistence
# ---------------------------------------------------------------------------
class TestStore:
    def _store(self) -> Store:
        import tempfile

        d = tempfile.mkdtemp()
        return Store(str(d) + "/test.db")

    def _classify(self, store: Store, pub_id: str, bank: str = BANK) -> None:
        store.set_classification(
            pub_id,
            central_bank=bank,
            publication_type=DECISION,
            confidence="high",
            method="source_type_hint",
            evidence=["test"],
        )

    def _seed_rate(self, store: Store) -> None:
        for pid, date, meeting, rate in (
            ("P1", datetime(2026, 1, 15), datetime(2026, 1, 15), 4.00),
            ("P2", datetime(2026, 3, 15), datetime(2026, 3, 15), 4.25),
            ("P3", datetime(2026, 5, 15), datetime(2026, 5, 15), 4.50),
        ):
            store.upsert_publication(mk_pub(pid, date, meeting_date=meeting))
            self._classify(store, pid)
            store.save_fact(_mk_fact(pid, "policy_rate", percentage(rate)))
        from argus.changes import analyze_changes

        analyze_changes(store, bank=BANK)

    def test_analyze_policy_state_persists(self):
        store = self._store()
        self._seed_rate(store)
        result = analyze_policy_state(store, bank=BANK)
        assert len(result.states) == 2
        assert len(store.get_policy_states(bank=BANK)) == 2

    def test_idempotent_rebuild(self):
        store = self._store()
        self._seed_rate(store)
        first = analyze_policy_state(store, bank=BANK)
        ids1 = [s.state_id for s in first.states]
        second = analyze_policy_state(store, bank=BANK)
        assert len(store.get_policy_states(bank=BANK)) == 2
        assert [s.state_id for s in second.states] == ids1

    def test_first_and_second_run_no_duplicates(self):
        store = self._store()
        self._seed_rate(store)
        analyze_policy_state(store, bank=BANK)
        analyze_policy_state(store, bank=BANK)
        rows = store.get_policy_states(bank=BANK)
        ids = [s.state_id for s in rows]
        assert len(ids) == len(set(ids)) == 2

    def test_rebuild_twice_same_state(self):
        store = self._store()
        self._seed_rate(store)
        first = analyze_policy_state(store, bank=BANK, persist=False).states
        store.rebuild_policy_states(first, bank=BANK)
        after = store.get_policy_states(bank=BANK)
        assert {s.state_id for s in after} == {s.state_id for s in first}
        assert len(after) == len(first) == 2

    def test_empty_result_clears_scope(self):
        store = self._store()
        self._seed_rate(store)
        analyze_policy_state(store, bank=BANK)
        assert len(store.get_policy_states(bank=BANK)) == 2
        store.rebuild_policy_states([], bank=BANK)
        assert store.get_policy_states(bank=BANK) == []

    def test_analyze_policy_state_empty_store(self):
        store = self._store()
        result = analyze_policy_state(store, bank=BANK)
        assert result.states == []
        assert result.warnings == []
        assert store.get_policy_states(bank=BANK) == []

    def test_bank_isolation(self):
        store = self._store()
        self._seed_rate(store)
        # seed a fed bank with its own policy_rate series
        for pid, date, meeting, rate in (
            ("F1", datetime(2026, 1, 15), datetime(2026, 1, 15), 5.00),
            ("F2", datetime(2026, 3, 15), datetime(2026, 3, 15), 5.25),
        ):
            store.upsert_publication(mk_pub(pid, date, meeting_date=meeting, bank="fed"))
            self._classify(store, pid, bank="fed")
            store.save_fact(_mk_fact(pid, "policy_rate", percentage(rate), bank="fed"))
        from argus.changes import analyze_changes

        analyze_changes(store, bank="fed")
        analyze_policy_state(store, bank="fed")
        fed_before = {s.state_id for s in store.get_policy_states(bank="fed")}
        assert len(fed_before) == 1
        # rebuild ecb only; fed untouched
        analyze_policy_state(store, bank=BANK)
        fed_after = {s.state_id for s in store.get_policy_states(bank="fed")}
        assert fed_after == fed_before
        assert len(store.get_policy_states(bank=BANK)) == 2

    def test_persist_false_does_not_write(self):
        store = self._store()
        self._seed_rate(store)
        result = analyze_policy_state(store, bank=BANK, persist=False)
        assert len(result.states) == 2
        assert store.get_policy_states(bank=BANK) == []

    def test_get_policy_state_single(self):
        store = self._store()
        self._seed_rate(store)
        analyze_policy_state(store, bank=BANK)
        state = store.get_policy_states(bank=BANK)[0]
        fetched = store.get_policy_state(state.state_id)
        assert fetched is not None
        assert fetched.state_id == state.state_id
        assert fetched.subject == "policy_rate"

    def test_get_policy_states_filters(self):
        store = self._store()
        self._seed_rate(store)
        analyze_policy_state(store, bank=BANK)
        assert len(store.get_policy_states(bank=BANK, subject="policy_rate")) == 2
        assert len(store.get_policy_states(bank=BANK, subject="inflation")) == 0
        assert len(store.get_policy_states(bank=BANK, predicate="value")) == 2
        assert len(store.get_policy_states(bank=BANK, predicate="projection")) == 0

    def test_get_policy_state_as_of_no_look_ahead(self):
        store = self._store()
        self._seed_rate(store)
        analyze_policy_state(store, bank=BANK)
        early = store.get_policy_state_as_of(BANK, datetime(2026, 3, 15))
        late = store.get_policy_state_as_of(BANK, datetime(2026, 5, 15))
        assert [s.value.value for s in early] == [4.25]
        assert [s.value.value for s in late] == [4.5]

    def test_get_policy_state_as_of_none_is_latest(self):
        store = self._store()
        self._seed_rate(store)
        analyze_policy_state(store, bank=BANK)
        latest = store.get_policy_state_as_of(BANK)
        assert [s.value.value for s in latest] == [4.5]

    def test_get_policy_state_as_of_before_first_change_empty(self):
        store = self._store()
        self._seed_rate(store)
        analyze_policy_state(store, bank=BANK)
        assert store.get_policy_state_as_of(BANK, datetime(2026, 1, 15)) == []

    def test_delete_policy_states_by_bank(self):
        store = self._store()
        self._seed_rate(store)
        analyze_policy_state(store, bank=BANK)
        assert len(store.get_policy_states(bank=BANK)) == 2
        assert store.delete_policy_states(bank=BANK) == 2
        assert store.get_policy_states(bank=BANK) == []

    def test_delete_policy_states_for_publication(self):
        store = self._store()
        self._seed_rate(store)
        analyze_policy_state(store, bank=BANK)
        assert store.delete_policy_states_for_publication("P2") == 1
        assert len(store.get_policy_states(bank=BANK)) == 1

    def test_delete_policy_states_for_document(self):
        store = self._store()
        self._seed_rate(store)
        analyze_policy_state(store, bank=BANK)
        assert store.delete_policy_states_for_document("P3") == 1
        assert len(store.get_policy_states(bank=BANK)) == 1

    def test_created_at_preserved_on_upsert(self):
        store = self._store()
        self._seed_rate(store)
        analyze_policy_state(store, bank=BANK)
        state = store.get_policy_states(bank=BANK)[0]
        created_before = store._conn.execute(
            "SELECT created_at FROM monetary_policy_states WHERE state_id = ?",
            (state.state_id,),
        ).fetchone()["created_at"]
        store.save_policy_state(state)
        created_after = store._conn.execute(
            "SELECT created_at FROM monetary_policy_states WHERE state_id = ?",
            (state.state_id,),
        ).fetchone()["created_at"]
        assert created_before == created_after

    def test_analyze_uses_authoritative_classifications(self):
        store = self._store()
        self._seed_rate(store)
        analyze_policy_state(store, bank=BANK)
        assert len(store.get_policy_states(bank=BANK)) == 2
        # drop P3's authoritative classification, then re-analyze: the change
        # whose current publication is no longer canonical must be skipped
        # (missing_classification), never falling back to the denormalized
        # publications.publication_type cache.
        store._conn.execute("DELETE FROM classifications WHERE publication_id = 'P3'")
        store._conn.commit()
        result = analyze_policy_state(store, bank=BANK)
        assert any("missing_classification:P3" in w for w in result.warnings)
        remaining = store.get_policy_states(bank=BANK)
        assert len(remaining) == 1
        assert [s.value.value for s in remaining] == [4.25]


# ---------------------------------------------------------------------------
# negative / boundaries
# ---------------------------------------------------------------------------
class TestNegative:
    def test_source_changes_never_mutated(self):
        changes, pubs = rate_changes()
        snapshot = [copy.deepcopy(c) for c in changes]
        run(changes, pubs)
        for original, after in zip(snapshot, changes):
            assert after.to_dict() == original.to_dict()

    def test_no_hawkish_dovish_vocabulary(self):
        changes, pubs = rate_changes()
        for state in run(changes, pubs).states:
            text = state.describe().lower()
            assert "hawkish" not in text
            assert "dovish" not in text

    def test_no_stance_score_field(self):
        state = MonetaryPolicyState()
        assert not hasattr(state, "stance")
        assert not hasattr(state, "direction")
        assert not hasattr(state, "score")

    def test_no_forecast_field(self):
        state = MonetaryPolicyState()
        assert not hasattr(state, "forecast")
        assert not hasattr(state, "expectation")
        assert not hasattr(state, "rate_expectation")

    def test_no_cross_bank_comparison_in_analyzer(self):
        # two banks in the same input produce independent states, never a
        # comparison, never a differential.
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 15), meeting_date=datetime(2026, 3, 15)),
            "F1": mk_pub("F1", datetime(2026, 1, 15), bank="fed"),
            "F2": mk_pub("F2", datetime(2026, 3, 15), meeting_date=datetime(2026, 3, 15), bank="fed"),
        }
        changes = [
            mk_change("r1", "policy_rate", cur_pub="P2",
                      prev_value=percentage(4.00), cur_value=percentage(4.25)),
            mk_change("f1", "policy_rate", cur_pub="F2", bank="fed",
                      prev_value=percentage(5.00), cur_value=percentage(5.25)),
        ]
        states = run(changes, pubs).states
        assert {s.central_bank for s in states} == {"ecb", "fed"}
        assert len(states) == 2

    def test_no_network_or_semantic_dependency(self):
        import inspect

        from argus.states import analyzer

        source = inspect.getsource(analyzer)
        assert "requests" not in source
        assert "openai" not in source
        assert "fuzzy" not in source
        assert "llm" not in source.lower()


def _mk_fact(pub_id: str, subject: str, value, *, bank: str = BANK):
    from argus.facts import fact_id_of
    from argus.facts.base import Confidence, Fact

    doc_id = pub_id
    return Fact(
        publication_id=pub_id,
        document_id=doc_id,
        subject=subject,
        predicate="value",
        value=value,
        central_bank=bank,
        confidence=Confidence.HIGH,
        fact_id=fact_id_of(
            publication_id=pub_id,
            document_id=doc_id,
            subject=subject,
            predicate="value",
            period=None,
            effective_date=None,
            qualifier="",
        ),
    )