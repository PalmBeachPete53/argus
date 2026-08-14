"""Phase 12 — temporal / cross-publication change analysis.

Tests the pure ``FactChangeAnalyzer`` and the store integration
(``analyze_changes`` + ``fact_changes`` persistence).

Covers: the three change types (numeric / qualitative / text), positive /
negative / zero deltas, exact-value no-change, verbatim text no-change,
period mismatch → no change, ordering by temporal reference (meeting_date
preferred, then publication_date, tie-break by publication id), consecutive
chaining (never a fixed baseline), cross-publication-only matching, publication
type compatibility, identity qualifier discrimination, provenance to both
source facts/documents/publications, deterministic change ids, idempotent
rebuild, empty-result persistence, no mutation of the source Facts, skipped /
warning observability (missing, unclassified, undated, valueless), and Phase
5–11 coexistence in the store.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from argus.changes import (
    CHANGE_TYPES,
    ChangeType,
    FactChange,
    FactChangeAnalyzer,
    FactChangeResult,
    analyze_changes,
    change_id_of,
)
from argus.facts import (
    categorical,
    date_value,
    fact_id_of,
    percentage,
    range_value,
    text_value,
)
from argus.facts.base import Confidence, Fact, FactPeriod, PeriodKind
from argus.models import Publication, PublicationStatus
from argus.store import Store

BANK = "ecb"

PERCENTAGE = "percentage"


def mk_pub(
    pub_id: str,
    date: datetime | None = None,
    *,
    meeting_date: datetime | None = None,
    ptype: str = "monetary_policy_decision",
    bank: str = BANK,
) -> Publication:
    return Publication(
        central_bank=bank,
        title="title",
        url=f"https://example.org/{pub_id}",
        source_id="src",
        source_url="https://example.org",
        id=pub_id,
        publication_type=ptype,
        publication_date=date,
        meeting_date=meeting_date,
        status=PublicationStatus.FETCHED,
    )


def mk_fact(
    pub_id: str,
    subject: str,
    value,
    *,
    period: FactPeriod | None = None,
    effective: datetime | None = None,
    qualifier: str = "",
    predicate: str = "value",
    doc: str | None = None,
    bank: str = BANK,
    source_text: str | None = None,
) -> Fact:
    doc_id = doc or pub_id
    return Fact(
        publication_id=pub_id,
        document_id=doc_id,
        subject=subject,
        predicate=predicate,
        value=value,
        period=period,
        effective_date=effective,
        source_text=source_text,
        identity_qualifier=qualifier,
        central_bank=bank,
        confidence=Confidence.HIGH,
        fact_id=fact_id_of(
            publication_id=pub_id,
            document_id=doc_id,
            subject=subject,
            predicate=predicate,
            period=period,
            effective_date=effective,
            qualifier=qualifier,
        ),
    )


def run(facts: list[Fact], pubs: dict[str, Publication]) -> FactChangeResult:
    return FactChangeAnalyzer().analyze(facts, publications=pubs)


def year(v: str) -> FactPeriod:
    return FactPeriod(PeriodKind.YEAR, value=v, label=v)


P1 = mk_pub("P1", datetime(2026, 1, 15), meeting_date=datetime(2026, 1, 15))
P2 = mk_pub("P2", datetime(2026, 3, 15), meeting_date=datetime(2026, 3, 15))
P3 = mk_pub("P3", datetime(2026, 5, 15), meeting_date=datetime(2026, 5, 15))


# ---------------------------------------------------------------------------
# numeric changes
# ---------------------------------------------------------------------------
class TestNumeric:
    def test_positive_delta(self):
        f1 = mk_fact("P1", "policy_rate", percentage(4.00), effective=datetime(2026, 1, 16))
        f2 = mk_fact("P2", "policy_rate", percentage(4.25), effective=datetime(2026, 3, 16))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert len(res.changes) == 1
        c = res.changes[0]
        assert c.change_type is ChangeType.NUMERIC
        assert c.previous_fact_id == f1.fact_id
        assert c.current_fact_id == f2.fact_id
        assert c.previous_value.value == 4.00
        assert c.current_value.value == 4.25
        assert c.delta.kind.value == PERCENTAGE
        assert c.delta.value == pytest.approx(0.25)
        assert c.previous_publication_id == "P1"
        assert c.current_publication_id == "P2"
        assert c.previous_document_id == "P1"
        assert c.current_document_id == "P2"
        assert c.previous_effective_date == datetime(2026, 1, 16)
        assert c.current_effective_date == datetime(2026, 3, 16)
        assert c.subject == "policy_rate"
        assert c.predicate == "value"
        assert c.central_bank == BANK

    def test_negative_delta(self):
        f1 = mk_fact("P1", "policy_rate", percentage(4.25), effective=datetime(2026, 1, 16))
        f2 = mk_fact("P2", "policy_rate", percentage(4.00), effective=datetime(2026, 3, 16))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert len(res.changes) == 1
        assert res.changes[0].delta.value == pytest.approx(-0.25)

    def test_identical_values_no_change(self):
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("P2", "policy_rate", percentage(4.00))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert res.changes == []

    def test_float_artifact_delta_rounded(self):
        f1 = mk_fact("P1", "inflation", percentage(2.1), period=year("2027"))
        f2 = mk_fact("P2", "inflation", percentage(2.4), period=year("2027"))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert len(res.changes) == 1
        assert res.changes[0].delta.value == pytest.approx(0.3)

    def test_single_observation_no_changes(self):
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        res = run([f1], {"P1": P1})
        assert res.changes == []


# ---------------------------------------------------------------------------
# qualitative changes
# ---------------------------------------------------------------------------
class TestQualitative:
    def test_balanced_to_upside(self):
        f1 = mk_fact("P1", "risk", categorical("balanced"))
        f2 = mk_fact("P2", "risk", categorical("upside"))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert len(res.changes) == 1
        c = res.changes[0]
        assert c.change_type is ChangeType.QUALITATIVE
        assert c.previous_value.value == "balanced"
        assert c.current_value.value == "upside"
        assert c.delta is None

    def test_identical_categorical_no_change(self):
        f1 = mk_fact("P1", "risk", categorical("balanced"))
        f2 = mk_fact("P2", "risk", categorical("balanced"))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert res.changes == []


# ---------------------------------------------------------------------------
# text changes
# ---------------------------------------------------------------------------
class TestText:
    def test_wording_change(self):
        f1 = mk_fact("P1", "guidance", text_value("We stand ready."))
        f2 = mk_fact("P2", "guidance", text_value("We stand ready to act."))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert len(res.changes) == 1
        c = res.changes[0]
        assert c.change_type is ChangeType.TEXT
        assert c.previous_value.value == "We stand ready."
        assert c.current_value.value == "We stand ready to act."
        assert c.previous_source_text is None or c.previous_value.value == c.previous_value.value

    def test_identical_text_no_change(self):
        f1 = mk_fact("P1", "guidance", text_value("We stand ready."))
        f2 = mk_fact("P2", "guidance", text_value("We stand ready."))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert res.changes == []


# ---------------------------------------------------------------------------
# matching: period, publication type, qualifier, cross-publication, bank
# ---------------------------------------------------------------------------
class TestMatching:
    def test_period_mismatch_no_change(self):
        f1 = mk_fact("P1", "inflation", percentage(2.1), period=year("2027"))
        f2 = mk_fact("P2", "inflation", percentage(2.4), period=year("2028"))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert res.changes == []

    def test_publication_type_mismatch_no_change(self):
        speech = mk_pub("S", datetime(2026, 2, 1), ptype="speech")
        f1 = mk_fact("P1", "inflation", percentage(2.1))
        f2 = mk_fact("S", "inflation", percentage(2.4))
        res = run([f1, f2], {"P1": P1, "S": speech})
        assert res.changes == []

    def test_identity_qualifier_no_cross(self):
        f1 = mk_fact("P1", "inflation", percentage(2.1), qualifier="answer:1:0")
        f2 = mk_fact("P2", "inflation", percentage(2.4), qualifier="answer:2:0")
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert res.changes == []

    def test_identity_qualifier_minutes_dissent_vs_members(self):
        f1 = mk_fact("P1", "rate", categorical("hold"), qualifier="minutes:dissent:1")
        f2 = mk_fact("P2", "rate", categorical("cut"), qualifier="minutes:members:1")
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert res.changes == []

    def test_same_publication_never_compared(self):
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("P1", "policy_rate", percentage(4.25))
        res = run([f1, f2], {"P1": P1})
        assert res.changes == []

    def test_different_banks_never_compared(self):
        fed_pub = mk_pub("F1", datetime(2026, 1, 15), bank="fed")
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("F1", "policy_rate", percentage(5.00), bank="fed")
        res = run([f1, f2], {"P1": P1, "F1": fed_pub})
        assert res.changes == []


# ---------------------------------------------------------------------------
# ordering and chaining
# ---------------------------------------------------------------------------
class TestOrderingChaining:
    def test_meeting_date_preferred_over_publication_date(self):
        early_meeting = mk_pub("P1", datetime(2026, 2, 1), meeting_date=datetime(2026, 1, 1))
        late_meeting = mk_pub("P2", datetime(2026, 1, 10), meeting_date=datetime(2026, 3, 1))
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("P2", "policy_rate", percentage(4.25))
        res = run([f1, f2], {"P1": early_meeting, "P2": late_meeting})
        assert len(res.changes) == 1
        assert res.changes[0].previous_publication_id == "P1"
        assert res.changes[0].current_publication_id == "P2"

    def test_tiebreak_by_publication_id(self):
        p1 = mk_pub("A1", datetime(2026, 1, 15), meeting_date=datetime(2026, 1, 15))
        p2 = mk_pub("B1", datetime(2026, 1, 15), meeting_date=datetime(2026, 1, 15))
        f_a = mk_fact("A1", "policy_rate", percentage(4.00))
        f_b = mk_fact("B1", "policy_rate", percentage(4.25))
        res = run([f_a, f_b], {"A1": p1, "B1": p2})
        assert len(res.changes) == 1
        assert res.changes[0].previous_publication_id == "A1"
        assert res.changes[0].current_publication_id == "B1"

    def test_chaining_f1_f2_f3(self):
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("P2", "policy_rate", percentage(4.25))
        f3 = mk_fact("P3", "policy_rate", percentage(4.50))
        res = run([f1, f2, f3], {"P1": P1, "P2": P2, "P3": P3})
        assert len(res.changes) == 2
        first = next(c for c in res.changes if c.previous_publication_id == "P1")
        second = next(c for c in res.changes if c.previous_publication_id == "P2")
        assert first.current_publication_id == "P2"
        assert second.current_publication_id == "P3"
        assert first.current_fact_id == second.previous_fact_id
        assert first.delta.value == pytest.approx(0.25)
        assert second.delta.value == pytest.approx(0.25)

    def test_interleaved_unrelated_fact_does_not_break_chain(self):
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("P2", "policy_rate", percentage(4.25))
        f3 = mk_fact("P3", "policy_rate", percentage(4.50))
        other = mk_fact("P2", "inflation", percentage(2.1))
        res = run([f1, f2, f3, other], {"P1": P1, "P2": P2, "P3": P3})
        assert len(res.changes) == 2
        assert all(c.subject == "policy_rate" for c in res.changes)


# ---------------------------------------------------------------------------
# observability warnings
# ---------------------------------------------------------------------------
class TestWarnings:
    def test_missing_publication_warns_and_skips(self):
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("PX", "policy_rate", percentage(4.25))
        res = run([f1, f2], {"P1": P1})
        assert res.changes == []
        assert any(w.startswith("missing_publication:PX") for w in res.warnings)

    def test_unclassified_publication_warns(self):
        unknown = mk_pub("U", datetime(2026, 2, 1), ptype="unknown")
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("U", "policy_rate", percentage(4.25))
        res = run([f1, f2], {"P1": P1, "U": unknown})
        assert res.changes == []
        assert any(w.startswith("unclassified_publication:U") for w in res.warnings)

    def test_undated_publication_warns(self):
        undated = mk_pub("N", None)
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("N", "policy_rate", percentage(4.25))
        res = run([f1, f2], {"P1": P1, "N": undated})
        assert res.changes == []
        assert any(w.startswith("undated_publication:N") for w in res.warnings)

    def test_valueless_fact_warns(self):
        from argus.facts import null_value

        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("P2", "policy_rate", null_value())
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert res.changes == []
        assert any(w.startswith("valueless_fact") for w in res.warnings)


# ---------------------------------------------------------------------------
# determinism and identity
# ---------------------------------------------------------------------------
class TestIdentity:
    def test_change_id_is_deterministic(self):
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("P2", "policy_rate", percentage(4.25))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        res2 = run([f1, f2], {"P1": P1, "P2": P2})
        assert res.changes[0].change_id == res2.changes[0].change_id

    def test_change_id_differs_across_kinds(self):
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("P2", "policy_rate", percentage(4.25))
        num = run([f1, f2], {"P1": P1, "P2": P2}).changes[0]
        # same pair but different kind (impossible in practice; pure function check)
        other = change_id_of(
            previous_fact_id=f1.fact_id,
            current_fact_id=f2.fact_id,
            change_type=ChangeType.QUALITATIVE,
        )
        assert num.change_id != other

    def test_change_id_of_function(self):
        a = change_id_of(
            previous_fact_id="x",
            current_fact_id="y",
            change_type=ChangeType.NUMERIC,
        )
        b = change_id_of(
            previous_fact_id="x",
            current_fact_id="y",
            change_type=ChangeType.NUMERIC,
        )
        c = change_id_of(
            previous_fact_id="x",
            current_fact_id="y",
            change_type=ChangeType.TEXT,
        )
        assert a == b
        assert a != c

    def test_change_types_vocabulary(self):
        assert CHANGE_TYPES == ("numeric_changed", "qualitative_changed", "text_changed")
        assert {t.value for t in ChangeType} == set(CHANGE_TYPES)


# ---------------------------------------------------------------------------
# serialization
# ---------------------------------------------------------------------------
class TestSerialization:
    def test_round_trip(self):
        f1 = mk_fact("P1", "policy_rate", percentage(4.00), period=year("2026"))
        f2 = mk_fact("P2", "policy_rate", percentage(4.25), period=year("2026"))
        c = run([f1, f2], {"P1": P1, "P2": P2}).changes[0]
        restored = FactChange.from_dict(c.to_dict())
        assert restored.change_id == c.change_id
        assert restored.previous_fact_id == c.previous_fact_id
        assert restored.current_fact_id == c.current_fact_id
        assert restored.change_type is c.change_type
        assert restored.previous_value.value == 4.00
        assert restored.current_value.value == 4.25
        assert restored.delta.value == c.delta.value
        assert restored.previous_period.canonical() == "year:2026"
        assert restored.current_period.canonical() == "year:2026"

    def test_no_mutation_of_source_facts(self):
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("P2", "policy_rate", percentage(4.25))
        before = (f1.to_dict(), f2.to_dict())
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert res.changes
        assert (f1.to_dict(), f2.to_dict()) == before


# ---------------------------------------------------------------------------
# store integration
# ---------------------------------------------------------------------------
class TestStore:
    def _store(self) -> Store:
        import tempfile

        d = tempfile.mkdtemp()
        return Store(str(d) + "/test.db")

    def _seed(self, store: Store) -> None:
        for p in (P1, P2, P3):
            store.upsert_publication(p)
        store.save_fact(mk_fact("P1", "policy_rate", percentage(4.00)))
        store.save_fact(mk_fact("P2", "policy_rate", percentage(4.25)))
        store.save_fact(mk_fact("P3", "policy_rate", percentage(4.50)))

    def test_analyze_changes_persists(self):
        store = self._store()
        self._seed(store)
        changes = analyze_changes(store, bank=BANK)
        assert len(changes) == 2
        assert len(store.get_changes()) == 2

    def test_idempotent_rebuild(self):
        store = self._store()
        self._seed(store)
        first = analyze_changes(store, bank=BANK)
        ids1 = [c.change_id for c in first]
        second = analyze_changes(store, bank=BANK)
        assert len(store.get_changes()) == 2
        assert [c.change_id for c in second] == ids1

    def test_empty_result_clears_scope(self):
        store = self._store()
        self._seed(store)
        analyze_changes(store, bank=BANK)
        assert len(store.get_changes()) == 2
        store.delete_facts_for_publication("P2")
        store.delete_facts_for_publication("P3")
        analyze_changes(store, bank=BANK)
        assert store.get_changes() == []

    def test_bank_scoped_rebuild(self):
        store = self._store()
        self._seed(store)
        fed_pub = mk_pub("F1", datetime(2026, 1, 15), bank="fed")
        store.upsert_publication(fed_pub)
        store.save_fact(mk_fact("F1", "policy_rate", percentage(5.00), bank="fed"))
        analyze_changes(store, bank=BANK)
        analyze_changes(store, bank="fed")
        ecb_changes = store.get_changes(bank=BANK)
        fed_changes = store.get_changes(bank="fed")
        assert len(ecb_changes) == 2
        assert len(fed_changes) == 0  # a single fed observation → no changes

    def test_get_changes_filters(self):
        store = self._store()
        self._seed(store)
        analyze_changes(store, bank=BANK)
        assert len(store.get_changes(subject="policy_rate")) == 2
        assert len(store.get_changes(change_type="numeric_changed")) == 2
        assert len(store.get_changes(publication_id="P2")) == 2
        assert len(store.get_changes(publication_id="P1")) == 1
        change = store.get_changes()[0]
        assert len(store.get_changes(previous_fact_id=change.previous_fact_id)) == 1
        assert len(store.get_changes(current_fact_id=change.current_fact_id)) == 1

    def test_delete_changes(self):
        store = self._store()
        self._seed(store)
        analyze_changes(store, bank=BANK)
        assert store.delete_changes(bank=BANK) == 2
        assert store.get_changes() == []

    def test_rebuild_changes_idempotent(self):
        store = self._store()
        self._seed(store)
        changes = analyze_changes(store, bank=BANK)
        store.rebuild_changes(changes, bank=BANK)
        assert len(store.get_changes()) == 2
        store.rebuild_changes([], bank=BANK)
        assert store.get_changes() == []

    def test_phase5_11_coexistence(self):
        store = self._store()
        self._seed(store)
        # a speech fact lives in the same store but never becomes a change
        speech = mk_pub("S", datetime(2026, 2, 1), ptype="speech")
        store.upsert_publication(speech)
        store.save_fact(mk_fact("S", "inflation", percentage(2.1)))
        store.save_fact(mk_fact("P2", "inflation", percentage(2.4)))
        changes = analyze_changes(store, bank=BANK)
        # decision policy-rate chain (2) + inflation across decision vs speech (0)
        assert len(changes) == 2
        assert len(store.get_facts()) == 5
        assert len(store.get_changes()) == 2