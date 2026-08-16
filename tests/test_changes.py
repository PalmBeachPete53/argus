"""Phase 5 — temporal / cross-publication change analysis (deep hardening).

Tests the pure ``FactChangeAnalyzer`` and the store integration
(``analyze_changes`` + ``fact_changes`` persistence).

Deep-hardening coverage beyond the original suite:

- **Classification source of truth**: the publication type used for matching
  comes from the authoritative ``classifications`` mapping when provided (the
  denormalized ``Publication.publication_type`` cache is never trusted when an
  authoritative classification is available); stale cache, missing
  classification and unknown classification are handled conservatively.
- **Central bank fallback**: ``FactChange.central_bank`` = ``Fact.central_bank``
  else ``Publication.central_bank``, never invented when both are absent.
- **Provenance**: every field on both sides is checked with concrete values
  for each change type (no tautological assertions), plus traceability from a
  persisted change back to its source Facts and publications.
- **Source text verbatim**: preserved byte-for-byte, no normalisation.
- **Incompatible observations**: an incomparable adjacent pair is never jumped
  over to bridge to a later observation; incompatible units never produce a
  numeric delta.
- **Effective date**: preserved, distinct from period and ordering dates, and
  never blocks matching.
- **Publication type boundary**: decision→decision and speech→speech are
  comparable; decision→speech and minutes→decision are not.
- **Identity qualifier**: ``None`` and ``""`` are the same (no qualifier);
  distinct non-empty qualifiers never merge.
- **Period**: 2027→2027 comparable; 2027→2028, month/year mismatch and
  None→2027 are distinct lineages (no change).
- **No-change**: numeric / qualitative / text identical values → zero changes.
- **Deterministic chaining** F1→F2→F3→F4 with deterministic tie-break.
- **Directional, deterministic ``change_id``**.
- **Immutability**: snapshot copy of the source Facts is unchanged after
  analysis.
- **Persistence**: idempotence, rebuild, empty-result rebuild, bank isolation.
- **Empty/missing data**: every degraded input is warned about and never
  produces a false change.
"""

from __future__ import annotations

import copy
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
    basis_points,
    categorical,
    currency,
    date_value,
    fact_id_of,
    number,
    percentage,
    range_value,
    text_value,
)
from argus.facts.base import Confidence, Fact, FactPeriod, PeriodKind
from argus.models import Publication, PublicationStatus
from argus.store import Store

BANK = "ecb"

PERCENTAGE = "percentage"

DECISION = "monetary_policy_decision"


def mk_pub(
    pub_id: str,
    date: datetime | None = None,
    *,
    meeting_date: datetime | None = None,
    ptype: str = DECISION,
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
    qualifier: str | None = "",
    predicate: str = "value",
    doc: str | None = None,
    bank: str | None = BANK,
    source_text: str | None = None,
) -> Fact:
    doc_id = doc if doc is not None else pub_id
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
            qualifier=qualifier or "",
        ),
    )


def run(
    facts: list[Fact], pubs: dict[str, Publication]
) -> FactChangeResult:
    return FactChangeAnalyzer().analyze(facts, publications=pubs)


def run_c(
    facts: list[Fact],
    pubs: dict[str, Publication],
    classifications: dict[str, str],
) -> FactChangeResult:
    return FactChangeAnalyzer().analyze(
        facts, publications=pubs, classifications=classifications
    )


def classify(store: Store, pub_id: str, ptype: str = DECISION, bank: str = BANK) -> None:
    store.set_classification(
        pub_id,
        central_bank=bank,
        publication_type=ptype,
        confidence="high",
        method="source_type_hint",
        evidence=["test"],
    )


def year(v: str) -> FactPeriod:
    return FactPeriod(PeriodKind.YEAR, value=v, label=v)


P1 = mk_pub("P1", datetime(2026, 1, 15), meeting_date=datetime(2026, 1, 15))
P2 = mk_pub("P2", datetime(2026, 3, 15), meeting_date=datetime(2026, 3, 15))
P3 = mk_pub("P3", datetime(2026, 5, 15), meeting_date=datetime(2026, 5, 15))
P4 = mk_pub("P4", datetime(2026, 7, 15), meeting_date=datetime(2026, 7, 15))


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

    def test_range_change(self):
        f1 = mk_fact("P1", "range", range_value(2.0, 3.0))
        f2 = mk_fact("P2", "range", range_value(2.0, 3.5))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert len(res.changes) == 1
        assert res.changes[0].change_type is ChangeType.QUALITATIVE
        assert res.changes[0].previous_value.max == 3.0
        assert res.changes[0].current_value.max == 3.5

    def test_boolean_change(self):
        from argus.facts import boolean_value

        f1 = mk_fact("P1", "flag", boolean_value(False))
        f2 = mk_fact("P2", "flag", boolean_value(True))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert len(res.changes) == 1
        assert res.changes[0].change_type is ChangeType.QUALITATIVE


# ---------------------------------------------------------------------------
# currency + date kinds (D12-1 API surface)
# ---------------------------------------------------------------------------
class TestCurrencyAndDateKinds:
    def test_currency_numeric_delta_keeps_unit(self):
        f1 = mk_fact("P1", "fx", currency(1.10, unit="usd"))
        f2 = mk_fact("P2", "fx", currency(1.15, unit="usd"))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert len(res.changes) == 1
        c = res.changes[0]
        assert c.change_type is ChangeType.NUMERIC
        assert c.value_kind == "currency"
        assert c.delta.kind.value == "currency"
        assert c.delta.value == pytest.approx(0.05)
        assert c.delta.unit == "usd"

    def test_currency_identical_value_no_change(self):
        f1 = mk_fact("P1", "fx", currency(1.10, unit="usd"))
        f2 = mk_fact("P2", "fx", currency(1.10, unit="usd"))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert res.changes == []

    def test_currency_unit_mismatch_never_deltas(self):
        f1 = mk_fact("P1", "fx", currency(1.10, unit="usd"))
        f2 = mk_fact("P2", "fx", currency(1.15, unit="eur"))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert res.changes == []

    def test_date_exact_comparison_is_qualitative(self):
        f1 = mk_fact("P1", "effective_date", date_value("2026-01-15"))
        f2 = mk_fact("P2", "effective_date", date_value("2026-03-15"))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert len(res.changes) == 1
        c = res.changes[0]
        assert c.change_type is ChangeType.QUALITATIVE
        assert c.delta is None  # exact equality only, never a conversion
        assert c.previous_value.value == "2026-01-15"
        assert c.current_value.value == "2026-03-15"

    def test_date_identical_value_no_change(self):
        f1 = mk_fact("P1", "effective_date", date_value("2026-01-15"))
        f2 = mk_fact("P2", "effective_date", date_value("2026-01-15"))
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
        assert c.delta is None

    def test_identical_text_no_change(self):
        f1 = mk_fact("P1", "guidance", text_value("We stand ready."))
        f2 = mk_fact("P2", "guidance", text_value("We stand ready."))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert res.changes == []

    def test_source_text_verbatim_preserved(self):
        f1 = mk_fact(
            "P1", "inflation", percentage(2.1),
            source_text="Inflation is expected at 2.1%.",
        )
        f2 = mk_fact(
            "P2", "inflation", percentage(2.4),
            source_text="Inflation is expected at 2.4%.",
        )
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert len(res.changes) == 1
        c = res.changes[0]
        assert c.previous_source_text == "Inflation is expected at 2.1%."
        assert c.current_source_text == "Inflation is expected at 2.4%."


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
# classification source of truth
# ---------------------------------------------------------------------------
class TestClassification:
    def test_normal_classification_used(self):
        pubs = {"P1": P1, "P2": P2}
        cl = {"P1": DECISION, "P2": DECISION}
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("P2", "policy_rate", percentage(4.25))
        res = run_c([f1, f2], pubs, cl)
        assert len(res.changes) == 1

    def test_stale_cache_does_not_win(self):
        # authoritative classification says decision, denormalized cache says
        # speech — the classification must win (the change is produced).
        stale_p1 = mk_pub("P1", datetime(2026, 1, 15), meeting_date=datetime(2026, 1, 15), ptype="speech")
        stale_p2 = mk_pub("P2", datetime(2026, 3, 15), meeting_date=datetime(2026, 3, 15), ptype="speech")
        cl = {"P1": DECISION, "P2": DECISION}
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("P2", "policy_rate", percentage(4.25))
        res = run_c([f1, f2], {"P1": stale_p1, "P2": stale_p2}, cl)
        assert len(res.changes) == 1
        assert res.changes[0].change_type is ChangeType.NUMERIC

    def test_missing_classification_skips_with_warning(self):
        cl = {"P1": DECISION}
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("P2", "policy_rate", percentage(4.25))
        res = run_c([f1, f2], {"P1": P1, "P2": P2}, cl)
        assert res.changes == []
        assert any(w.startswith("missing_classification:P2") for w in res.warnings)

    def test_unknown_classification_skips(self):
        cl = {"P1": DECISION, "U": "unknown"}
        unknown = mk_pub("U", datetime(2026, 2, 1))
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("U", "policy_rate", percentage(4.25))
        res = run_c([f1, f2], {"P1": P1, "U": unknown}, cl)
        assert res.changes == []
        assert any(w.startswith("unclassified_publication:U") for w in res.warnings)

    def test_other_classification_skips(self):
        cl = {"P1": DECISION, "O": "other"}
        other = mk_pub("O", datetime(2026, 2, 1))
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("O", "policy_rate", percentage(4.25))
        res = run_c([f1, f2], {"P1": P1, "O": other}, cl)
        assert res.changes == []
        assert any(w.startswith("unclassified_publication:O") for w in res.warnings)

    def test_classification_isolates_types(self):
        # authoritative classification still isolates decision vs speech.
        speech = mk_pub("S", datetime(2026, 2, 1), ptype="speech")
        cl = {"P1": DECISION, "P2": DECISION, "S": "speech"}
        f1 = mk_fact("P1", "inflation", percentage(2.1))
        f2 = mk_fact("P2", "inflation", percentage(2.4))
        fs = mk_fact("S", "inflation", percentage(2.6))
        res = run_c([f1, f2, fs], {"P1": P1, "P2": P2, "S": speech}, cl)
        # decision → decision change, speech stays isolated
        assert len(res.changes) == 1
        assert res.changes[0].previous_publication_id == "P1"
        assert res.changes[0].current_publication_id == "P2"


# ---------------------------------------------------------------------------
# central bank fallback
# ---------------------------------------------------------------------------
class TestCentralBank:
    def test_fact_bank_used(self):
        f1 = mk_fact("P1", "policy_rate", percentage(4.00), bank="ecb")
        f2 = mk_fact("P2", "policy_rate", percentage(4.25), bank="ecb")
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert res.changes[0].central_bank == "ecb"

    def test_publication_bank_fallback(self):
        f1 = mk_fact("P1", "policy_rate", percentage(4.00), bank=None)
        f2 = mk_fact("P2", "policy_rate", percentage(4.25), bank=None)
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert len(res.changes) == 1
        assert res.changes[0].central_bank == "ecb"  # from Publication.central_bank

    def test_no_bank_is_not_invented(self):
        nobank = mk_pub("N1", datetime(2026, 1, 15), meeting_date=datetime(2026, 1, 15), bank=None)
        nobank2 = mk_pub("N2", datetime(2026, 3, 15), meeting_date=datetime(2026, 3, 15), bank=None)
        f1 = mk_fact("N1", "policy_rate", percentage(4.00), bank=None)
        f2 = mk_fact("N2", "policy_rate", percentage(4.25), bank=None)
        res = run([f1, f2], {"N1": nobank, "N2": nobank2})
        assert len(res.changes) == 1
        assert res.changes[0].central_bank is None

    def test_fact_bank_used_in_store(self):
        import tempfile

        store = Store(tempfile.mkdtemp() + "/test.db")
        for p in (P1, P2):
            store.upsert_publication(p)
            classify(store, p.id)
        store.save_fact(mk_fact("P1", "policy_rate", percentage(4.00)))
        store.save_fact(mk_fact("P2", "policy_rate", percentage(4.25)))
        res = analyze_changes(store, bank=BANK)
        assert res.changes[0].central_bank == "ecb"


# ---------------------------------------------------------------------------
# provenance (per change type, no tautologies)
# ---------------------------------------------------------------------------
class TestProvenance:
    def _assert_full_provenance(self, c: FactChange, ctype, *, previous, current):
        assert c.change_type is ctype
        # fact ids
        assert c.previous_fact_id == previous["fact_id"]
        assert c.current_fact_id == current["fact_id"]
        # documents
        assert c.previous_document_id == previous["doc"]
        assert c.current_document_id == current["doc"]
        # publications
        assert c.previous_publication_id == previous["pub"]
        assert c.current_publication_id == current["pub"]
        # periods
        assert (c.previous_period.canonical() if c.previous_period else None) == previous["period"]
        assert (c.current_period.canonical() if c.current_period else None) == current["period"]
        # effective dates
        assert c.previous_effective_date == previous["effective"]
        assert c.current_effective_date == current["effective"]
        # source texts
        assert c.previous_source_text == previous["source_text"]
        assert c.current_source_text == current["source_text"]
        # values
        assert c.previous_value.value == previous["value"]
        assert c.current_value.value == current["value"]
        assert c.subject == "policy_rate"
        assert c.predicate == "value"

    def test_numeric_provenance(self):
        f1 = mk_fact("P1", "policy_rate", percentage(4.00),
                     period=year("2026"), effective=datetime(2026, 1, 16),
                     source_text="rate at 4.00 percent")
        f2 = mk_fact("P2", "policy_rate", percentage(4.25),
                     period=year("2026"), effective=datetime(2026, 3, 16),
                     source_text="rate at 4.25 percent")
        c = run([f1, f2], {"P1": P1, "P2": P2}).changes[0]
        self._assert_full_provenance(
            c, ChangeType.NUMERIC,
            previous={"fact_id": f1.fact_id, "doc": "P1", "pub": "P1",
                      "period": "year:2026", "effective": datetime(2026, 1, 16),
                      "source_text": "rate at 4.00 percent", "value": 4.00},
            current={"fact_id": f2.fact_id, "doc": "P2", "pub": "P2",
                     "period": "year:2026", "effective": datetime(2026, 3, 16),
                     "source_text": "rate at 4.25 percent", "value": 4.25},
        )
        assert c.delta is not None
        assert c.delta.value == pytest.approx(0.25)
        assert c.previous_value.unit is None
        assert c.current_value.unit is None

    def test_qualitative_provenance(self):
        f1 = mk_fact("P1", "policy_rate", categorical("balanced"),
                     effective=datetime(2026, 1, 16),
                     source_text="risks are balanced")
        f2 = mk_fact("P2", "policy_rate", categorical("upside"),
                     effective=datetime(2026, 3, 16),
                     source_text="risks are tilted to the upside")
        c = run([f1, f2], {"P1": P1, "P2": P2}).changes[0]
        self._assert_full_provenance(
            c, ChangeType.QUALITATIVE,
            previous={"fact_id": f1.fact_id, "doc": "P1", "pub": "P1",
                      "period": None, "effective": datetime(2026, 1, 16),
                      "source_text": "risks are balanced", "value": "balanced"},
            current={"fact_id": f2.fact_id, "doc": "P2", "pub": "P2",
                     "period": None, "effective": datetime(2026, 3, 16),
                     "source_text": "risks are tilted to the upside", "value": "upside"},
        )
        assert c.delta is None

    def test_text_provenance(self):
        f1 = mk_fact("P1", "policy_rate", text_value("We stand ready."),
                     effective=datetime(2026, 1, 16),
                     source_text="We stand ready.")
        f2 = mk_fact("P2", "policy_rate", text_value("We stand ready to act."),
                     effective=datetime(2026, 3, 16),
                     source_text="We stand ready to act.")
        c = run([f1, f2], {"P1": P1, "P2": P2}).changes[0]
        self._assert_full_provenance(
            c, ChangeType.TEXT,
            previous={"fact_id": f1.fact_id, "doc": "P1", "pub": "P1",
                      "period": None, "effective": datetime(2026, 1, 16),
                      "source_text": "We stand ready.", "value": "We stand ready."},
            current={"fact_id": f2.fact_id, "doc": "P2", "pub": "P2",
                     "period": None, "effective": datetime(2026, 3, 16),
                     "source_text": "We stand ready to act.", "value": "We stand ready to act."},
        )
        assert c.delta is None


# ---------------------------------------------------------------------------
# incompatible units
# ---------------------------------------------------------------------------
class TestUnits:
    def test_percentage_vs_basis_points_kinds_never_meet(self):
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("P2", "policy_rate", basis_points(400))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert res.changes == []

    def test_same_kind_incompatible_units_no_delta(self):
        f1 = mk_fact("P1", "spread", number(4.00, unit="pp"))
        f2 = mk_fact("P2", "spread", number(4.25, unit="%"))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert res.changes == []

    def test_no_bridge_over_incompatible_unit(self):
        # F1→F2 comparable (same unit "pp"); F2→F3 unit-mismatched; F1→F3 must
        # NOT be bridged even though F1 and F3 share the lineage key.
        f1 = mk_fact("P1", "spread", number(4.00, unit="pp"))
        f2 = mk_fact("P2", "spread", number(4.25, unit="pp"))
        f3 = mk_fact("P3", "spread", number(4.50, unit="%"))
        res = run([f1, f2, f3], {"P1": P1, "P2": P2, "P3": P3})
        assert len(res.changes) == 1
        assert res.changes[0].previous_publication_id == "P1"
        assert res.changes[0].current_publication_id == "P2"


# ---------------------------------------------------------------------------
# incompatible observation in the middle of a sequence
# ---------------------------------------------------------------------------
class TestIncomparableMiddle:
    def test_equal_middle_never_bridges(self):
        # F1→F2 equal (no change); F2→F3 changed; F1→F3 must not be produced.
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("P2", "policy_rate", percentage(4.00))
        f3 = mk_fact("P3", "policy_rate", percentage(4.50))
        res = run([f1, f2, f3], {"P1": P1, "P2": P2, "P3": P3})
        assert len(res.changes) == 1
        assert res.changes[0].previous_publication_id == "P2"
        assert res.changes[0].current_publication_id == "P3"

    def test_qualifier_split_keeps_lineages_separate(self):
        # F2 has a different qualifier → it is a separate lineage, so F1→F3 are
        # the consecutive observations of the no-qualifier lineage.
        f1 = mk_fact("P1", "inflation", percentage(2.1), qualifier="")
        f2 = mk_fact("P2", "inflation", percentage(2.4), qualifier="answer:1:0")
        f3 = mk_fact("P3", "inflation", percentage(2.7), qualifier="")
        res = run([f1, f2, f3], {"P1": P1, "P2": P2, "P3": P3})
        assert len(res.changes) == 1
        assert res.changes[0].previous_publication_id == "P1"
        assert res.changes[0].current_publication_id == "P3"


# ---------------------------------------------------------------------------
# effective date
# ---------------------------------------------------------------------------
class TestEffectiveDate:
    def test_different_effective_dates_do_not_break_matching(self):
        f1 = mk_fact("P1", "policy_rate", percentage(4.00), effective=datetime(2026, 1, 16))
        f2 = mk_fact("P2", "policy_rate", percentage(4.25), effective=datetime(2026, 3, 16))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert len(res.changes) == 1
        assert res.changes[0].previous_effective_date == datetime(2026, 1, 16)
        assert res.changes[0].current_effective_date == datetime(2026, 3, 16)

    def test_effective_date_not_used_for_ordering(self):
        # publication dates say P1 < P2; effective dates are reversed — ordering
        # must follow the publication dates, not effective dates.
        p1 = mk_pub("P1", datetime(2026, 1, 15), meeting_date=datetime(2026, 1, 15))
        p2 = mk_pub("P2", datetime(2026, 3, 15), meeting_date=datetime(2026, 3, 15))
        f1 = mk_fact("P1", "policy_rate", percentage(4.00), effective=datetime(2026, 3, 20))
        f2 = mk_fact("P2", "policy_rate", percentage(4.25), effective=datetime(2026, 1, 10))
        res = run([f1, f2], {"P1": p1, "P2": p2})
        assert len(res.changes) == 1
        assert res.changes[0].previous_publication_id == "P1"
        assert res.changes[0].current_publication_id == "P2"
        assert res.changes[0].previous_effective_date == datetime(2026, 3, 20)
        assert res.changes[0].current_effective_date == datetime(2026, 1, 10)

    def test_effective_date_distinct_from_period(self):
        f1 = mk_fact("P1", "inflation", percentage(2.1), period=year("2027"),
                     effective=datetime(2026, 9, 16))
        f2 = mk_fact("P2", "inflation", percentage(2.4), period=year("2027"),
                     effective=datetime(2026, 12, 16))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert len(res.changes) == 1
        assert res.changes[0].previous_period.canonical() == "year:2027"
        assert res.changes[0].current_period.canonical() == "year:2027"
        assert res.changes[0].previous_effective_date == datetime(2026, 9, 16)
        assert res.changes[0].current_effective_date == datetime(2026, 12, 16)


# ---------------------------------------------------------------------------
# publication type boundary
# ---------------------------------------------------------------------------
class TestPublicationTypeBoundary:
    def test_decision_to_decision_comparable(self):
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("P2", "policy_rate", percentage(4.25))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert len(res.changes) == 1

    def test_speech_to_speech_comparable(self):
        s1 = mk_pub("S1", datetime(2026, 1, 15), meeting_date=datetime(2026, 1, 15), ptype="speech")
        s2 = mk_pub("S2", datetime(2026, 3, 15), meeting_date=datetime(2026, 3, 15), ptype="speech")
        f1 = mk_fact("S1", "inflation", percentage(2.1))
        f2 = mk_fact("S2", "inflation", percentage(2.4))
        res = run([f1, f2], {"S1": s1, "S2": s2})
        assert len(res.changes) == 1

    def test_decision_to_speech_not_comparable(self):
        speech = mk_pub("S", datetime(2026, 2, 1), ptype="speech")
        f1 = mk_fact("P1", "inflation", percentage(2.1))
        f2 = mk_fact("S", "inflation", percentage(2.4))
        res = run([f1, f2], {"P1": P1, "S": speech})
        assert res.changes == []

    def test_minutes_to_minutes_comparable(self):
        m1 = mk_pub("M1", datetime(2026, 1, 15), meeting_date=datetime(2026, 1, 15), ptype="minutes")
        m2 = mk_pub("M2", datetime(2026, 3, 15), meeting_date=datetime(2026, 3, 15), ptype="minutes")
        f1 = mk_fact("M1", "risk", categorical("balanced"))
        f2 = mk_fact("M2", "risk", categorical("upside"))
        res = run([f1, f2], {"M1": m1, "M2": m2})
        assert len(res.changes) == 1

    def test_minutes_to_decision_not_comparable(self):
        m = mk_pub("M", datetime(2026, 2, 1), ptype="minutes")
        f1 = mk_fact("P1", "risk", categorical("balanced"))
        f2 = mk_fact("M", "risk", categorical("upside"))
        res = run([f1, f2], {"P1": P1, "M": m})
        assert res.changes == []


# ---------------------------------------------------------------------------
# identity qualifier
# ---------------------------------------------------------------------------
class TestIdentityQualifier:
    def test_none_equals_empty(self):
        f1 = mk_fact("P1", "policy_rate", percentage(4.00), qualifier=None)
        f2 = mk_fact("P2", "policy_rate", percentage(4.25), qualifier="")
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert len(res.changes) == 1
        assert res.changes[0].identity_qualifier == ""

    def test_non_empty_qualifiers_never_merge(self):
        f1 = mk_fact("P1", "inflation", percentage(2.1), qualifier="answer:1:0")
        f2 = mk_fact("P2", "inflation", percentage(2.4), qualifier="answer:2:0")
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert res.changes == []


# ---------------------------------------------------------------------------
# period
# ---------------------------------------------------------------------------
class TestPeriod:
    def test_same_period_comparable(self):
        f1 = mk_fact("P1", "inflation", percentage(2.1), period=year("2027"))
        f2 = mk_fact("P2", "inflation", percentage(2.4), period=year("2027"))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert len(res.changes) == 1

    def test_2027_vs_2028_no_change(self):
        f1 = mk_fact("P1", "inflation", percentage(2.1), period=year("2027"))
        f2 = mk_fact("P2", "inflation", percentage(2.4), period=year("2028"))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert res.changes == []

    def test_year_vs_month_no_change(self):
        f1 = mk_fact("P1", "inflation", percentage(2.1), period=year("2027"))
        f2 = mk_fact("P2", "inflation", percentage(2.4),
                     period=FactPeriod(PeriodKind.MONTH, value="2027-08"))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert res.changes == []

    def test_none_vs_year_no_change(self):
        f1 = mk_fact("P1", "inflation", percentage(2.1))
        f2 = mk_fact("P2", "inflation", percentage(2.4), period=year("2027"))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert res.changes == []

    def test_both_no_period_comparable(self):
        f1 = mk_fact("P1", "inflation", percentage(2.1))
        f2 = mk_fact("P2", "inflation", percentage(2.4))
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert len(res.changes) == 1


# ---------------------------------------------------------------------------
# no-change (triple)
# ---------------------------------------------------------------------------
class TestNoChange:
    def test_all_types_identical_no_change(self):
        num = run(
            [
                mk_fact("P1", "policy_rate", percentage(4.00)),
                mk_fact("P2", "policy_rate", percentage(4.00)),
            ],
            {"P1": P1, "P2": P2},
        )
        qual = run(
            [
                mk_fact("P1", "risk", categorical("balanced")),
                mk_fact("P2", "risk", categorical("balanced")),
            ],
            {"P1": P1, "P2": P2},
        )
        text = run(
            [
                mk_fact("P1", "guidance", text_value("same wording")),
                mk_fact("P2", "guidance", text_value("same wording")),
            ],
            {"P1": P1, "P2": P2},
        )
        assert num.changes == []
        assert qual.changes == []
        assert text.changes == []


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

    def test_publication_date_fallback_when_no_meeting_date(self):
        no_meeting = mk_pub("N1", datetime(2026, 1, 15), meeting_date=None)
        no_meeting2 = mk_pub("N2", datetime(2026, 3, 15), meeting_date=None)
        f1 = mk_fact("N1", "policy_rate", percentage(4.00))
        f2 = mk_fact("N2", "policy_rate", percentage(4.25))
        res = run([f1, f2], {"N1": no_meeting, "N2": no_meeting2})
        assert len(res.changes) == 1
        assert res.changes[0].previous_publication_id == "N1"
        assert res.changes[0].current_publication_id == "N2"

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

    def test_chaining_f1_f2_f3_f4(self):
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("P2", "policy_rate", percentage(4.25))
        f3 = mk_fact("P3", "policy_rate", percentage(4.50))
        f4 = mk_fact("P4", "policy_rate", percentage(4.75))
        res = run([f1, f2, f3, f4], {"P1": P1, "P2": P2, "P3": P3, "P4": P4})
        assert len(res.changes) == 3
        by_prev = {c.previous_publication_id: c for c in res.changes}
        assert set(by_prev) == {"P1", "P2", "P3"}
        assert by_prev["P1"].current_publication_id == "P2"
        assert by_prev["P2"].current_publication_id == "P3"
        assert by_prev["P3"].current_publication_id == "P4"
        # no cross-links F1→F3, F1→F4, F2→F4
        assert not any(c.previous_publication_id == "P1" and c.current_publication_id == "P3"
                       for c in res.changes)
        assert not any(c.previous_publication_id == "P1" and c.current_publication_id == "P4"
                       for c in res.changes)
        assert not any(c.previous_publication_id == "P2" and c.current_publication_id == "P4"
                       for c in res.changes)

    def test_interleaved_unrelated_fact_does_not_break_chain(self):
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("P2", "policy_rate", percentage(4.25))
        f3 = mk_fact("P3", "policy_rate", percentage(4.50))
        other = mk_fact("P2", "inflation", percentage(2.1))
        res = run([f1, f2, f3, other], {"P1": P1, "P2": P2, "P3": P3})
        assert len(res.changes) == 2
        assert all(c.subject == "policy_rate" for c in res.changes)


# ---------------------------------------------------------------------------
# final chaining hardening (Phase 5): documented adjacency semantics
# ---------------------------------------------------------------------------
class TestChainingHardening:
    """Formalizes the exact chaining semantics documented in docs/CHANGES.md.

    Each adjacent pair of an ordered lineage is evaluated **independently**: a
    pair that yields no change, or cannot be compared, produces no change for
    that pair and is **never bridged over** to a later observation; the pair
    immediately *following* an incomparable pair is still evaluated. Facts of
    different lineages (e.g. a different value kind) never interact, so the
    consecutive pair of a lineage is the next observation of *that* lineage.
    """

    @staticmethod
    def _pairs(res):
        return {(c.previous_publication_id, c.current_publication_id) for c in res.changes}

    def test_scenario_a_all_adjacent_comparable(self):
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("P2", "policy_rate", percentage(4.25))
        f3 = mk_fact("P3", "policy_rate", percentage(4.50))
        f4 = mk_fact("P4", "policy_rate", percentage(4.75))
        res = run([f1, f2, f3, f4], {"P1": P1, "P2": P2, "P3": P3, "P4": P4})
        assert self._pairs(res) == {("P1", "P2"), ("P2", "P3"), ("P3", "P4")}
        by_prev = {c.previous_publication_id: c for c in res.changes}
        assert by_prev["P1"].previous_fact_id == f1.fact_id
        assert by_prev["P1"].current_fact_id == f2.fact_id
        assert by_prev["P2"].previous_fact_id == f2.fact_id
        assert by_prev["P2"].current_fact_id == f3.fact_id
        assert by_prev["P3"].previous_fact_id == f3.fact_id
        assert by_prev["P3"].current_fact_id == f4.fact_id

    def test_scenario_b_incomparable_middle_is_not_bridged(self):
        # F1→F2 comparable (4.00→4.25); F2→F3 no change (4.25→4.25);
        # F3→F4 comparable (4.25→4.50).
        # Expected: F1→F2 and F3→F4; NEVER F1→F3 or F2→F4.
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("P2", "policy_rate", percentage(4.25))
        f3 = mk_fact("P3", "policy_rate", percentage(4.25))
        f4 = mk_fact("P4", "policy_rate", percentage(4.50))
        res = run([f1, f2, f3, f4], {"P1": P1, "P2": P2, "P3": P3, "P4": P4})
        assert self._pairs(res) == {("P1", "P2"), ("P3", "P4")}
        assert not any((c.previous_publication_id, c.current_publication_id) == ("P1", "P3")
                       for c in res.changes)
        assert not any((c.previous_publication_id, c.current_publication_id) == ("P2", "P4")
                       for c in res.changes)
        first = next(c for c in res.changes if c.previous_publication_id == "P1")
        second = next(c for c in res.changes if c.previous_publication_id == "P3")
        assert first.previous_fact_id == f1.fact_id
        assert first.current_fact_id == f2.fact_id
        assert second.previous_fact_id == f3.fact_id
        assert second.current_fact_id == f4.fact_id

    def test_scenario_c_incomparable_first_pair(self):
        # F1→F2 no change (4.00→4.00); F2→F3 comparable (4.00→4.50).
        # Expected: no bridge; F2→F3 is produced.
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("P2", "policy_rate", percentage(4.00))
        f3 = mk_fact("P3", "policy_rate", percentage(4.50))
        res = run([f1, f2, f3], {"P1": P1, "P2": P2, "P3": P3})
        assert self._pairs(res) == {("P2", "P3")}
        c = res.changes[0]
        assert c.previous_fact_id == f2.fact_id
        assert c.current_fact_id == f3.fact_id

    def test_scenario_c_incompatible_units_first_pair(self):
        # F1→F2 same kind but unit-mismatched → no change; F2→F3 comparable.
        # Expected: F2→F3 only, never F1→F3.
        f1 = mk_fact("P1", "spread", number(4.00, unit="pp"))
        f2 = mk_fact("P2", "spread", number(4.25, unit="%"))
        f3 = mk_fact("P3", "spread", number(4.50, unit="%"))
        res = run([f1, f2, f3], {"P1": P1, "P2": P2, "P3": P3})
        assert self._pairs(res) == {("P2", "P3")}

    def test_scenario_d_inter_unit_never_delta(self):
        # F1=4.00%, F2=4.25%, F3=425 bps, F4=4.50%.
        # bps is a *different lineage* (different value.kind): no inter-unit
        # delta is ever produced; the percentage lineage still links its own
        # consecutive observations F1→F2 and F2→F4; F1→F3 / F1→F4 are never
        # invented.
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("P2", "policy_rate", percentage(4.25))
        f3 = mk_fact("P3", "policy_rate", basis_points(425))
        f4 = mk_fact("P4", "policy_rate", percentage(4.50))
        res = run([f1, f2, f3, f4], {"P1": P1, "P2": P2, "P3": P3, "P4": P4})
        pairs = self._pairs(res)
        assert ("P1", "P2") in pairs
        assert ("P2", "P4") in pairs
        # no inter-unit delta: the bps fact never meets a percentage fact
        assert ("P2", "P3") not in pairs
        assert ("P3", "P4") not in pairs
        # F1→F3 and F1→F4 are never invented
        assert ("P1", "P3") not in pairs
        assert ("P1", "P4") not in pairs
        # every produced change is strictly intra-lineage
        for c in res.changes:
            assert c.previous_value.kind is c.current_value.kind
            assert c.delta is not None
            assert c.delta.unit == c.previous_value.unit
        c12 = next(c for c in res.changes if c.previous_publication_id == "P1")
        c24 = next(c for c in res.changes if c.previous_publication_id == "P2")
        assert c12.previous_fact_id == f1.fact_id
        assert c12.current_fact_id == f2.fact_id
        assert c24.previous_fact_id == f2.fact_id
        assert c24.current_fact_id == f4.fact_id

    def test_units_percentage_and_basis_points(self):
        # 4.00 % → 4.25 % : numeric change
        res = run(
            [
                mk_fact("P1", "policy_rate", percentage(4.00)),
                mk_fact("P2", "policy_rate", percentage(4.25)),
            ],
            {"P1": P1, "P2": P2},
        )
        assert len(res.changes) == 1
        assert res.changes[0].delta.value == pytest.approx(0.25)
        # 4.25 % → 425 bps : no change (incompatible value kinds)
        res = run(
            [
                mk_fact("P1", "policy_rate", percentage(4.25)),
                mk_fact("P2", "policy_rate", basis_points(425)),
            ],
            {"P1": P1, "P2": P2},
        )
        assert res.changes == []
        # 425 bps → 450 bps : numeric change
        res = run(
            [
                mk_fact("P1", "policy_rate", basis_points(425)),
                mk_fact("P2", "policy_rate", basis_points(450)),
            ],
            {"P1": P1, "P2": P2},
        )
        assert len(res.changes) == 1
        assert res.changes[0].delta.value == pytest.approx(25.0)


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

    def test_undocumented_fact_warns(self):
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("P2", "policy_rate", percentage(4.25), doc="")
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert res.changes == []
        assert any(w.startswith("undocumented_fact") for w in res.warnings)

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

    def test_unclassified_publication_warns(self):
        unknown = mk_pub("U", datetime(2026, 2, 1), ptype="unknown")
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("U", "policy_rate", percentage(4.25))
        res = run([f1, f2], {"P1": P1, "U": unknown})
        assert res.changes == []
        assert any(w.startswith("unclassified_publication:U") for w in res.warnings)


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

    def test_change_id_directional(self):
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("P2", "policy_rate", percentage(4.25))
        # the analyzer always orders chronologically (P1 < P2) whatever the
        # input order, so the direction of the change is fully determined.
        res = run([f2, f1], {"P1": P1, "P2": P2})
        assert len(res.changes) == 1
        c = res.changes[0]
        assert c.previous_fact_id == f1.fact_id
        assert c.current_fact_id == f2.fact_id
        # the identity is directional: swapping the two sides changes the id.
        forward = change_id_of(
            previous_fact_id=f1.fact_id, current_fact_id=f2.fact_id,
            change_type=ChangeType.NUMERIC,
        )
        reverse = change_id_of(
            previous_fact_id=f2.fact_id, current_fact_id=f1.fact_id,
            change_type=ChangeType.NUMERIC,
        )
        assert c.change_id == forward
        assert forward != reverse

    def test_change_id_distinct_across_kinds(self):
        f1 = mk_fact("P1", "policy_rate", percentage(4.00))
        f2 = mk_fact("P2", "policy_rate", percentage(4.25))
        num = run([f1, f2], {"P1": P1, "P2": P2}).changes[0]
        other = change_id_of(
            previous_fact_id=f1.fact_id,
            current_fact_id=f2.fact_id,
            change_type=ChangeType.QUALITATIVE,
        )
        assert num.change_id != other
        # the three kinds are pairwise distinct for the same fact pair
        ids = {
            change_id_of(previous_fact_id=f1.fact_id, current_fact_id=f2.fact_id, change_type=t)
            for t in ChangeType
        }
        assert len(ids) == 3

    def test_change_id_of_function(self):
        a = change_id_of(previous_fact_id="x", current_fact_id="y", change_type=ChangeType.NUMERIC)
        b = change_id_of(previous_fact_id="x", current_fact_id="y", change_type=ChangeType.NUMERIC)
        c = change_id_of(previous_fact_id="x", current_fact_id="y", change_type=ChangeType.TEXT)
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

    def test_source_facts_immutable_snapshot(self):
        f1 = mk_fact(
            "P1", "policy_rate", percentage(4.00),
            period=year("2026"), effective=datetime(2026, 1, 16),
            source_text="rate at 4.00 percent",
        )
        f2 = mk_fact(
            "P2", "policy_rate", percentage(4.25),
            period=year("2026"), effective=datetime(2026, 3, 16),
            source_text="rate at 4.25 percent",
        )
        snap1, snap2 = copy.deepcopy(f1), copy.deepcopy(f2)
        res = run([f1, f2], {"P1": P1, "P2": P2})
        assert res.changes
        # full dataclass equality
        assert f1 == snap1
        assert f2 == snap2
        # identity fields individually untouched
        assert f1.fact_id == snap1.fact_id
        assert f1.value == snap1.value
        assert f1.period == snap1.period
        assert f1.source_text == snap1.source_text
        assert f1.source_location == snap1.source_location
        assert f1.identity_qualifier == snap1.identity_qualifier
        assert f1.effective_date == snap1.effective_date
        assert f1.speaker == snap1.speaker
        assert f2.fact_id == snap2.fact_id
        assert f2.value == snap2.value
        assert f2.period == snap2.period
        assert f2.source_text == snap2.source_text
        assert f2.source_location == snap2.source_location
        assert f2.identity_qualifier == snap2.identity_qualifier
        assert f2.effective_date == snap2.effective_date
        assert f2.speaker == snap2.speaker


# ---------------------------------------------------------------------------
# store integration
# ---------------------------------------------------------------------------
class TestStore:
    def _store(self) -> Store:
        import tempfile

        d = tempfile.mkdtemp()
        return Store(str(d) + "/test.db")

    def _seed(self, store: Store, pubs=(P1, P2, P3)) -> None:
        for p in pubs:
            store.upsert_publication(p)
            classify(store, p.id)
        store.save_fact(mk_fact("P1", "policy_rate", percentage(4.00)))
        store.save_fact(mk_fact("P2", "policy_rate", percentage(4.25)))
        store.save_fact(mk_fact("P3", "policy_rate", percentage(4.50)))

    def test_analyze_changes_persists(self):
        store = self._store()
        self._seed(store)
        result = analyze_changes(store, bank=BANK)
        assert len(result.changes) == 2
        assert len(store.get_changes()) == 2

    def test_idempotent_rebuild(self):
        store = self._store()
        self._seed(store)
        first = analyze_changes(store, bank=BANK)
        ids1 = [c.change_id for c in first.changes]
        second = analyze_changes(store, bank=BANK)
        assert len(store.get_changes()) == 2
        assert [c.change_id for c in second.changes] == ids1

    def test_first_and_second_run_no_duplicates(self):
        store = self._store()
        self._seed(store)
        analyze_changes(store, bank=BANK)
        analyze_changes(store, bank=BANK)
        rows = store.get_changes()
        ids = [c.change_id for c in rows]
        assert len(ids) == len(set(ids)) == 2

    def test_rebuild_twice_same_state(self):
        store = self._store()
        self._seed(store)
        first = analyze_changes(store, bank=BANK).changes
        store.rebuild_changes(first, bank=BANK)
        after = store.get_changes()
        assert {c.change_id for c in after} == {c.change_id for c in first}
        assert len(after) == len(first) == 2

    def test_empty_result_clears_scope(self):
        store = self._store()
        self._seed(store)
        analyze_changes(store, bank=BANK)
        assert len(store.get_changes()) == 2
        store.delete_facts_for_publication("P2")
        store.delete_facts_for_publication("P3")
        analyze_changes(store, bank=BANK)
        assert store.get_changes() == []

    def test_rebuild_with_zero_changes_clears(self):
        store = self._store()
        self._seed(store)
        analyze_changes(store, bank=BANK)
        assert len(store.get_changes()) == 2
        store.rebuild_changes([], bank=BANK)
        assert store.get_changes() == []

    def test_bank_isolation(self):
        # both banks have real changes; rebuilding A must not touch B.
        store = self._store()
        self._seed(store)
        fed_pub1 = mk_pub("F1", datetime(2026, 1, 15), meeting_date=datetime(2026, 1, 15), bank="fed")
        fed_pub2 = mk_pub("F2", datetime(2026, 3, 15), meeting_date=datetime(2026, 3, 15), bank="fed")
        for p in (fed_pub1, fed_pub2):
            store.upsert_publication(p)
            classify(store, p.id, bank="fed")
        store.save_fact(mk_fact("F1", "policy_rate", percentage(5.00), bank="fed"))
        store.save_fact(mk_fact("F2", "policy_rate", percentage(5.25), bank="fed"))
        analyze_changes(store, bank=BANK)
        analyze_changes(store, bank="fed")
        fed_before = {c.change_id for c in store.get_changes(bank="fed")}
        assert len(fed_before) == 1
        # rebuild bank A only; B stays intact
        analyze_changes(store, bank=BANK)
        assert {c.change_id for c in store.get_changes(bank="fed")} == fed_before
        assert len(store.get_changes(bank=BANK)) == 2

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

    def test_analyze_changes_persist_false(self):
        # analyze without persisting: the result is returned, the store stays empty.
        store = self._store()
        self._seed(store)
        result = analyze_changes(store, bank=BANK, persist=False)
        assert len(result.changes) == 2
        assert store.get_changes() == []

    def test_analyze_changes_empty_store(self):
        # empty input → no changes, no warnings, no persisted rows.
        store = self._store()
        result = analyze_changes(store, bank=BANK)
        assert result.changes == []
        assert result.warnings == []
        assert store.get_changes() == []

    def test_get_changes_limit(self):
        store = self._store()
        self._seed(store)
        analyze_changes(store, bank=BANK)
        assert len(store.get_changes()) == 2
        assert len(store.get_changes(limit=1)) == 1
        assert len(store.get_changes(limit=0)) == 0

    def test_delete_changes_for_document(self):
        # document_id == publication_id for these facts; deleting the middle
        # document removes both adjacent changes.
        store = self._store()
        self._seed(store)
        analyze_changes(store, bank=BANK)
        assert store.delete_changes_for_document("P2") == 2
        assert store.get_changes() == []

    def test_delete_changes_for_publication(self):
        # P1 participates only as the previous side of P1→P2 → one row removed,
        # the P2→P3 change survives.
        store = self._store()
        self._seed(store)
        analyze_changes(store, bank=BANK)
        assert store.delete_changes_for_publication("P1") == 1
        remaining = store.get_changes()
        assert len(remaining) == 1
        assert remaining[0].previous_publication_id == "P2"
        assert remaining[0].current_publication_id == "P3"

    def test_save_change_preserves_created_at(self):
        store = self._store()
        self._seed(store)
        result = analyze_changes(store, bank=BANK, persist=False)
        change = result.changes[0]
        change.analyzed_at = datetime(2026, 1, 1)
        store.save_change(change)
        change_id = change.change_id
        row = store._conn.execute(
            "SELECT created_at FROM fact_changes WHERE change_id = ?", (change_id,)
        ).fetchone()
        first_created = row["created_at"]
        change.analyzed_at = datetime(2026, 2, 1)
        store.save_change(change)
        row = store._conn.execute(
            "SELECT created_at, analyzed_at FROM fact_changes WHERE change_id = ?", (change_id,)
        ).fetchone()
        assert row["created_at"] == first_created  # upsert preserves created_at
        assert row["analyzed_at"] is not None

    def test_store_stale_cache_ignored(self):
        # authoritative classification = decision; denormalized cache corrupted
        # to speech. analyze_changes must still use the classification.
        store = self._store()
        self._seed(store)
        # corrupt the denormalized cache rows (the classifications table wins)
        for pid in ("P1", "P2"):
            store._conn.execute(
                "UPDATE publications SET publication_type='speech' WHERE id=?", (pid,)
            )
        store._conn.commit()
        result = analyze_changes(store, bank=BANK)
        assert len(result.changes) == 2  # decision→decision, classification wins

    def test_store_missing_classification_skips(self):
        # a publication with facts but no classification row is skipped.
        store = self._store()
        self._seed(store, pubs=(P1, P2))
        # P3 exists with facts but no classification → must be skipped
        store.upsert_publication(P3)
        store.save_fact(mk_fact("P3", "policy_rate", percentage(4.50)))
        result = analyze_changes(store, bank=BANK)
        # P1→P2 change remains; P2→P3 is skipped (P3 unclassified)
        assert len(result.changes) == 1
        assert result.changes[0].previous_publication_id == "P1"
        assert result.changes[0].current_publication_id == "P2"
        assert any(w.startswith("missing_classification:P3") for w in result.warnings)

    def test_change_traces_to_source_facts_and_publications(self):
        store = self._store()
        self._seed(store)
        result = analyze_changes(store, bank=BANK)
        change = next(c for c in result.changes if c.previous_publication_id == "P1")
        prev_fact = store.get_fact(change.previous_fact_id)
        cur_fact = store.get_fact(change.current_fact_id)
        assert prev_fact is not None and cur_fact is not None
        # Change → previous Fact → previous publication/document
        assert prev_fact.publication_id == change.previous_publication_id == "P1"
        assert prev_fact.document_id == change.previous_document_id
        prev_pub = store.get_publication(change.previous_publication_id)
        assert prev_pub.central_bank == "ecb"
        assert prev_pub.publication_type == DECISION
        # Change → current Fact → current publication/document
        assert cur_fact.publication_id == change.current_publication_id == "P2"
        assert cur_fact.document_id == change.current_document_id
        cur_pub = store.get_publication(change.current_publication_id)
        assert cur_pub.central_bank == "ecb"
        assert cur_pub.publication_type == DECISION
        # the change is strictly derived: both sides exist and carry provenance
        assert change.previous_document_id and change.current_document_id

    def test_phase5_11_coexistence(self):
        store = self._store()
        self._seed(store)
        # a speech fact lives in the same store but never becomes a change
        speech = mk_pub("S", datetime(2026, 2, 1), ptype="speech")
        store.upsert_publication(speech)
        classify(store, "S", ptype="speech")
        store.save_fact(mk_fact("S", "inflation", percentage(2.1)))
        store.save_fact(mk_fact("P2", "inflation", percentage(2.4)))
        result = analyze_changes(store, bank=BANK)
        # decision policy-rate chain (2) + inflation across decision vs speech (0)
        assert len(result.changes) == 2
        assert len(store.get_facts()) == 5
        assert len(store.get_changes()) == 2