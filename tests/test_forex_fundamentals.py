"""Phase 8 — forex fundamentals analysis.

Tests the pure ``ForexFundamentalsAnalyzer`` and the store integration
(``analyze_forex_fundamentals`` + ``forex_fundamentals`` /
``forex_differentials`` persistence).

Coverage:

- **Vocabulary**: fundamental dimensions == Phase 6 condition subjects ∪
  Phase 7 state subjects; excluded (projection/change/date) predicates
  documented; monetary and macro vocabularies reused from the canonical layers.
- **Model**: ``synthesized`` is always ``True``; formulation is purely
  descriptive (never stance/forecast/fair value/forex signal); deterministic;
  serialization round-trip for both fundamentals and differentials.
- **Identity**: deterministic, currency+source specific ``fundamental_id``;
  deterministic, orientation-specific ``differential_id`` (EUR/USD ≠ USD/EUR).
- **Monetary fundamentals**: Phase 7 states are the monetary source (never
  reconstructed from documents); several instruments are separate dimensions;
  unknown currency / valueless skipped with warnings.
- **Macro fundamentals**: Phase 4 facts are the macro source;
  ``meeting_date`` priority, ``publication_date`` fallback; ``effective_date``
  and ``period`` are never observation times; projection/change/date excluded;
  irrelevant subjects ignored silently; latest-known-observation model.
- **Differentials**: same-dimension, cross-currency, arithmetic
  (``base - quote``); both orientations generated with distinct identities;
  base-anchored latest-known matching (no look-ahead); missing side; unit
  mismatch; non-numeric dimensions not differentiable; no cross-instrument
  merging; provenance on both sides.
- **Temporal / as-of**: ``get_fundamentals_as_of`` / ``get_differential_as_of``
  no look-ahead; revisions are point-in-time.
- **Persistence**: idempotent rebuild, empty clears scope, bank/currency
  isolation, filters, deletes, ``created_at`` preservation, ``persist=False``,
  authoritative classifications.
- **Negative**: source states/facts never mutated; no hawkish/dovish, no
  stance, no forecast, no fair value, no signal, no ranking, no causality, no
  self-pairs, no network/LLM/fuzzy/semantic dependency.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest

from argus.facts import percentage, currency, text_value
from argus.facts.base import Confidence, Fact, FactPeriod, PeriodKind
from argus.models import Publication, PublicationStatus
from argus.reactions import CONDITION_SUBJECTS
from argus.states import STATE_SUBJECTS, MonetaryPolicyState
from argus.forex import (
    FUNDAMENTAL_EXCLUDED_PREDICATES,
    FUNDAMENTAL_SUBJECTS,
    MACRO_SUBJECTS,
    MONETARY_SUBJECTS,
    SOURCE_FACT,
    SOURCE_MONETARY_STATE,
    ForexDifferential,
    ForexFundamental,
    ForexFundamentalsAnalyzer,
    analyze_forex_fundamentals,
    differential_id_of,
    fundamental_id_of,
)
from argus.store import Store

BANK = "ecb"
FED = "fed"

CURRENCIES = {"ecb": "EUR", "fed": "USD"}

DECISION = "monetary_policy_decision"
REPORT = "report"

FORBIDDEN_WORDS = ("hawkish", "dovish", "forecast", "expected", "buy", "sell",
                   "stance", "directional", "carry", "signal",
                   "conviction", "fair value", "ranking")


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


def mk_state(
    sid: str,
    subject: str,
    value,
    *,
    bank: str = BANK,
    predicate: str = "value",
    pub_type: str = DECISION,
    period: FactPeriod | None = None,
    qualifier: str = "",
    observed_at: datetime | None = None,
    pub_id: str | None = None,
    effective: datetime | None = None,
    source_text: str | None = None,
) -> MonetaryPolicyState:
    if observed_at is None:
        observed_at = datetime(2026, 1, 15)
    state = MonetaryPolicyState(
        state_id=sid,
        central_bank=bank,
        synthesized=True,
        source_change_id=f"change:{sid}",
        subject=subject,
        predicate=predicate,
        value_kind=value.kind.value if value is not None and value.kind else None,
        qualifier=qualifier,
        period=period,
        publication_type=pub_type,
        value=value,
        observed_at=observed_at,
        publication_id=pub_id or sid,
        document_id=pub_id or sid,
        effective_date=effective,
        source_text=source_text,
        analysis_version="14.0.0",
    )
    state.resolve_id()
    return state


def mk_fact(
    fid: str,
    pub_id: str,
    subject: str,
    value,
    *,
    bank: str = BANK,
    predicate: str = "value",
    period: FactPeriod | None = None,
    effective: datetime | None = None,
    qualifier: str = "",
    source_text: str | None = None,
) -> Fact:
    return Fact(
        fact_id=fid,
        publication_id=pub_id,
        document_id=pub_id,
        central_bank=bank,
        subject=subject,
        predicate=predicate,
        value=value,
        period=period,
        effective_date=effective,
        identity_qualifier=qualifier,
        source_text=source_text,
        confidence=Confidence.HIGH,
    )


def run(
    states: list[MonetaryPolicyState] | None = None,
    facts: list[Fact] | None = None,
    pubs: dict[str, Publication] | None = None,
    classifications: dict[str, str] | None = None,
    currencies: dict[str, str] | None = None,
):
    return ForexFundamentalsAnalyzer().analyze(
        states=states or [],
        facts=facts or [],
        currencies=currencies or CURRENCIES,
        publications=pubs or {},
        classifications=classifications,
    )


def policy_states() -> list[MonetaryPolicyState]:
    return [
        mk_state("s_ecb_1", "policy_rate", percentage(4.25), bank=BANK,
                 observed_at=datetime(2026, 3, 15), pub_id="P2"),
        mk_state("s_ecb_2", "policy_rate", percentage(4.50), bank=BANK,
                 observed_at=datetime(2026, 5, 15), pub_id="P3"),
        mk_state("s_fed_1", "policy_rate", percentage(5.25), bank=FED,
                 observed_at=datetime(2026, 3, 20), pub_id="F2"),
    ]


# ---------------------------------------------------------------------------
# vocabulary
# ---------------------------------------------------------------------------
class TestVocabulary:
    def test_fundamental_subjects_union(self):
        assert FUNDAMENTAL_SUBJECTS == MONETARY_SUBJECTS | MACRO_SUBJECTS

    def test_monetary_subjects_reuse_phase14(self):
        assert MONETARY_SUBJECTS == STATE_SUBJECTS

    def test_macro_subjects_reuse_phase13(self):
        assert MACRO_SUBJECTS == CONDITION_SUBJECTS

    def test_excluded_predicates_documented(self):
        assert FUNDAMENTAL_EXCLUDED_PREDICATES == {"projection", "change", "date"}

    def test_excluded_predicates_not_subjects(self):
        assert FUNDAMENTAL_SUBJECTS.isdisjoint(FUNDAMENTAL_EXCLUDED_PREDICATES)


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------
class TestModel:
    def test_synthesized_is_always_true(self):
        assert ForexFundamental().synthesized is True
        assert ForexDifferential().synthesized is True

    def test_synthesized_constant_even_after_analysis(self):
        res = run(states=policy_states())
        assert res.fundamentals
        assert res.differentials
        for f in res.fundamentals:
            assert f.synthesized is True
        for d in res.differentials:
            assert d.synthesized is True

    def test_fundamental_describe_is_descriptive_only(self):
        for f in run(states=policy_states()).fundamentals:
            text = f.describe()
            assert "forex fundamental of" in text
            assert f.currency in text
            assert f.subject in text
            assert "derived from" in text

    def test_differential_describe_is_descriptive_only(self):
        for d in run(states=policy_states()).differentials:
            text = d.describe()
            assert "forex differential" in text
            assert d.base_currency in text
            assert d.quote_currency in text
            assert "= " in text

    def test_describe_never_forbidden_words(self):
        res = run(states=policy_states())
        for f in res.fundamentals:
            text = f.describe().lower()
            for word in FORBIDDEN_WORDS:
                assert word not in text
        for d in res.differentials:
            text = d.describe().lower()
            for word in FORBIDDEN_WORDS:
                assert word not in text

    def test_describe_is_deterministic(self):
        res = run(states=policy_states())
        assert res.fundamentals[0].describe() == res.fundamentals[0].describe()
        assert res.differentials[0].describe() == res.differentials[0].describe()

    def test_fundamental_serialization_round_trip(self):
        res = run(states=policy_states())
        f = next(f for f in res.fundamentals if f.currency == "EUR")
        restored = ForexFundamental.from_dict(f.to_dict())
        assert restored.fundamental_id == f.fundamental_id
        assert restored.currency == "EUR"
        assert restored.synthesized is True
        assert restored.source_kind == SOURCE_MONETARY_STATE
        assert restored.subject == "policy_rate"
        assert restored.value.value == f.value.value
        assert restored.lineage_key == f.lineage_key
        assert restored.dimension_key == f.dimension_key
        assert restored.analysis_version == f.analysis_version

    def test_differential_serialization_round_trip(self):
        res = run(states=policy_states())
        d = res.differentials[0]
        restored = ForexDifferential.from_dict(d.to_dict())
        assert restored.differential_id == d.differential_id
        assert restored.base_currency == d.base_currency
        assert restored.quote_currency == d.quote_currency
        assert restored.value.value == d.value.value
        assert restored.base_source_id == d.base_source_id
        assert restored.quote_source_id == d.quote_source_id
        assert restored.formulation == d.formulation

    def test_analysis_version(self):
        res = run(states=policy_states())
        assert res.fundamentals[0].analysis_version == "15.0.0"
        assert res.differentials[0].analysis_version == "15.0.0"


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------
class TestIdentity:
    def test_fundamental_id_deterministic(self):
        a = fundamental_id_of(currency="EUR", source_kind=SOURCE_MONETARY_STATE, source_id="S1")
        b = fundamental_id_of(currency="EUR", source_kind=SOURCE_MONETARY_STATE, source_id="S1")
        assert a == b
        assert len(a) == 64

    def test_fundamental_id_currency_specific(self):
        a = fundamental_id_of(currency="EUR", source_kind=SOURCE_MONETARY_STATE, source_id="S1")
        b = fundamental_id_of(currency="USD", source_kind=SOURCE_MONETARY_STATE, source_id="S1")
        assert a != b

    def test_fundamental_id_source_kind_specific(self):
        a = fundamental_id_of(currency="EUR", source_kind=SOURCE_MONETARY_STATE, source_id="S1")
        b = fundamental_id_of(currency="EUR", source_kind=SOURCE_FACT, source_id="S1")
        assert a != b

    def test_fundamental_id_source_specific(self):
        a = fundamental_id_of(currency="EUR", source_kind=SOURCE_MONETARY_STATE, source_id="S1")
        b = fundamental_id_of(currency="EUR", source_kind=SOURCE_MONETARY_STATE, source_id="S2")
        assert a != b

    def test_differential_id_deterministic(self):
        kwargs = dict(base_currency="EUR", quote_currency="USD", subject="policy_rate",
                      predicate="value", base_source_id="A", quote_source_id="B")
        assert differential_id_of(**kwargs) == differential_id_of(**kwargs)

    def test_differential_id_orientation_specific(self):
        a = differential_id_of(base_currency="EUR", quote_currency="USD", subject="policy_rate",
                               predicate="value", base_source_id="A", quote_source_id="B")
        b = differential_id_of(base_currency="USD", quote_currency="EUR", subject="policy_rate",
                               predicate="value", base_source_id="B", quote_source_id="A")
        assert a != b

    def test_differential_id_subject_specific(self):
        a = differential_id_of(base_currency="EUR", quote_currency="USD", subject="policy_rate",
                               predicate="value", base_source_id="A", quote_source_id="B")
        b = differential_id_of(base_currency="EUR", quote_currency="USD", subject="inflation",
                               predicate="value", base_source_id="A", quote_source_id="B")
        assert a != b

    def test_differential_id_source_specific(self):
        a = differential_id_of(base_currency="EUR", quote_currency="USD", subject="policy_rate",
                               predicate="value", base_source_id="A", quote_source_id="B")
        b = differential_id_of(base_currency="EUR", quote_currency="USD", subject="policy_rate",
                               predicate="value", base_source_id="A", quote_source_id="C")
        assert a != b


# ---------------------------------------------------------------------------
# monetary fundamentals (Phase 7 states)
# ---------------------------------------------------------------------------
class TestMonetaryFundamentals:
    def test_state_is_one_fundamental(self):
        res = run(states=[mk_state("s1", "policy_rate", percentage(4.25))])
        assert len(res.fundamentals) == 1
        f = res.fundamentals[0]
        assert f.currency == "EUR"
        assert f.source_kind == SOURCE_MONETARY_STATE
        assert f.source_id == "s1"
        assert f.value.value == 4.25
        assert f.subject == "policy_rate"
        assert f.predicate == "value"

    def test_state_currency_from_bank(self):
        res = run(states=[mk_state("s1", "policy_rate", percentage(4.25), bank=FED)])
        assert res.fundamentals[0].currency == "USD"

    def test_several_instruments_are_separate_dimensions(self):
        res = run(states=[
            mk_state("s1", "main_refinancing_rate", percentage(4.25)),
            mk_state("s2", "deposit_facility_rate", percentage(3.75)),
            mk_state("s3", "marginal_lending_rate", percentage(4.75)),
        ])
        assert len(res.fundamentals) == 3
        assert len({f.lineage_key for f in res.fundamentals}) == 3

    def test_guidance_state_observed(self):
        res = run(states=[mk_state("s1", "policy_guidance", text_value("data dependent"),
                                   predicate="statement")])
        assert len(res.fundamentals) == 1
        assert res.fundamentals[0].value.value == "data dependent"
        assert res.differentials == []

    def test_unknown_currency_warning(self):
        res = run(states=[mk_state("s1", "policy_rate", percentage(4.25), bank="bogus")])
        assert res.fundamentals == []
        assert any(w.startswith("unknown_currency:bogus") for w in res.warnings)

    def test_valueless_state_skipped(self):
        res = run(states=[mk_state("s1", "policy_rate", None)])
        assert res.fundamentals == []
        assert any(w.startswith("valueless:") for w in res.warnings)

    def test_rates_period_none_in_lineage(self):
        res = run(states=[mk_state("s1", "policy_rate", percentage(4.25))])
        assert res.fundamentals[0].period is None
        assert res.fundamentals[0].lineage_key.split("\x1f") == [
            "policy_rate", "value", "percentage", "", "", DECISION,
        ]

    def test_provenance_verbatim(self):
        res = run(states=[mk_state("s1", "policy_rate", percentage(4.25),
                                   observed_at=datetime(2026, 3, 15), pub_id="P2",
                                   effective=datetime(2026, 3, 18),
                                   source_text="4.25 percent")])
        f = res.fundamentals[0]
        assert f.observed_at == datetime(2026, 3, 15)
        assert f.effective_date == datetime(2026, 3, 18)
        assert f.source_text == "4.25 percent"
        assert f.publication_id == "P2"
        assert f.document_id == "P2"


# ---------------------------------------------------------------------------
# macro fundamentals (Phase 4 facts)
# ---------------------------------------------------------------------------
class TestMacroFundamentals:
    def test_fact_is_one_fundamental(self):
        pubs = {"R1": mk_pub("R1", datetime(2026, 1, 15), pub_type=REPORT)}
        res = run(
            facts=[mk_fact("f1", "R1", "inflation", percentage(2.5))],
            pubs=pubs,
        )
        assert len(res.fundamentals) == 1
        f = res.fundamentals[0]
        assert f.currency == "EUR"
        assert f.source_kind == SOURCE_FACT
        assert f.source_id == "f1"
        assert f.value.value == 2.5
        assert f.subject == "inflation"
        assert f.value_kind == "percentage"

    def test_observed_at_meeting_date_priority(self):
        pubs = {"R1": mk_pub("R1", datetime(2026, 1, 15),
                             meeting_date=datetime(2026, 1, 10), pub_type=REPORT)}
        res = run(facts=[mk_fact("f1", "R1", "inflation", percentage(2.5))], pubs=pubs)
        assert res.fundamentals[0].observed_at == datetime(2026, 1, 10)

    def test_effective_date_never_observation_time(self):
        pubs = {"R1": mk_pub("R1", datetime(2026, 1, 15), pub_type=REPORT)}
        res = run(
            facts=[mk_fact("f1", "R1", "inflation", percentage(2.5),
                           effective=datetime(2025, 12, 1))],
            pubs=pubs,
        )
        f = res.fundamentals[0]
        assert f.observed_at == datetime(2026, 1, 15)
        assert f.effective_date == datetime(2025, 12, 1)

    def test_period_kept_in_lineage_not_observation_time(self):
        pubs = {"R1": mk_pub("R1", datetime(2026, 4, 15), pub_type=REPORT)}
        res = run(
            facts=[mk_fact("f1", "R1", "inflation", percentage(2.5),
                           period=FactPeriod(PeriodKind.QUARTER, "2026-Q1"))],
            pubs=pubs,
        )
        f = res.fundamentals[0]
        assert f.period is not None
        assert f.observed_at == datetime(2026, 4, 15)
        assert "quarter:2026-Q1" in f.lineage_key

    def test_projection_excluded_with_warning(self):
        pubs = {"R1": mk_pub("R1", datetime(2026, 1, 15), pub_type=REPORT)}
        res = run(
            facts=[mk_fact("f1", "R1", "inflation", percentage(2.5), predicate="projection")],
            pubs=pubs,
        )
        assert res.fundamentals == []
        assert any(w.startswith("out_of_scope_fact:f1") for w in res.warnings)

    def test_change_predicate_excluded(self):
        pubs = {"R1": mk_pub("R1", datetime(2026, 1, 15), pub_type=REPORT)}
        res = run(
            facts=[mk_fact("f1", "R1", "inflation", percentage(0.1), predicate="change")],
            pubs=pubs,
        )
        assert res.fundamentals == []
        assert any(w.startswith("out_of_scope_fact:f1") for w in res.warnings)

    def test_date_predicate_excluded(self):
        from argus.facts import date_value

        pubs = {"R1": mk_pub("R1", datetime(2026, 1, 15), pub_type=REPORT)}
        res = run(
            facts=[mk_fact("f1", "R1", "inflation", date_value("2026-01-15"), predicate="date")],
            pubs=pubs,
        )
        assert res.fundamentals == []
        assert any(w.startswith("out_of_scope_fact:f1") for w in res.warnings)

    def test_irrelevant_subject_silently_ignored(self):
        pubs = {"R1": mk_pub("R1", datetime(2026, 1, 15), pub_type=DECISION)}
        res = run(
            facts=[mk_fact("f1", "R1", "monetary_policy_decision", text_value("held"))],
            pubs=pubs,
        )
        assert res.fundamentals == []
        assert res.warnings == []

    def test_missing_publication_warning(self):
        res = run(facts=[mk_fact("f1", "R1", "inflation", percentage(2.5))])
        assert res.fundamentals == []
        assert any(w.startswith("missing_publication:R1") for w in res.warnings)

    def test_undated_publication_warning(self):
        pubs = {"R1": mk_pub("R1", None, pub_type=REPORT)}
        res = run(facts=[mk_fact("f1", "R1", "inflation", percentage(2.5))], pubs=pubs)
        assert res.fundamentals == []
        assert any(w.startswith("undated_publication:R1") for w in res.warnings)

    def test_unplaced_fact_warning(self):
        pubs = {"R1": mk_pub("R1", datetime(2026, 1, 15), bank="", pub_type=REPORT)}
        res = run(
            facts=[mk_fact("f1", "R1", "inflation", percentage(2.5), bank="")],
            pubs=pubs,
        )
        assert res.fundamentals == []
        assert any(w.startswith("unplaced_fact:f1") for w in res.warnings)

    def test_bank_falls_back_to_publication(self):
        pubs = {"R1": mk_pub("R1", datetime(2026, 1, 15), bank=FED, pub_type=REPORT)}
        res = run(
            facts=[mk_fact("f1", "R1", "inflation", percentage(3.0), bank="")],
            pubs=pubs,
        )
        assert len(res.fundamentals) == 1
        assert res.fundamentals[0].currency == "USD"

    def test_valueless_fact_warning(self):
        pubs = {"R1": mk_pub("R1", datetime(2026, 1, 15), pub_type=REPORT)}
        res = run(facts=[mk_fact("f1", "R1", "inflation", None)], pubs=pubs)
        assert res.fundamentals == []
        assert any(w.startswith("valueless:f1") for w in res.warnings)

    def test_missing_classification_authoritative(self):
        pubs = {"R1": mk_pub("R1", datetime(2026, 1, 15), pub_type=REPORT)}
        res = run(
            facts=[mk_fact("f1", "R1", "inflation", percentage(2.5))],
            pubs=pubs,
            classifications={},
        )
        assert res.fundamentals == []
        assert any(w.startswith("missing_classification:R1") for w in res.warnings)

    def test_unclassified_publication_warning(self):
        pubs = {"R1": mk_pub("R1", datetime(2026, 1, 15), pub_type="unknown")}
        res = run(
            facts=[mk_fact("f1", "R1", "inflation", percentage(2.5))],
            pubs=pubs,
            classifications={"R1": "unknown"},
        )
        assert res.fundamentals == []
        assert any(w.startswith("unclassified_publication:R1") for w in res.warnings)

    def test_latest_known_observation_model(self):
        pubs = {
            "R1": mk_pub("R1", datetime(2026, 1, 15), pub_type=REPORT),
            "R2": mk_pub("R2", datetime(2026, 2, 15), pub_type=REPORT),
        }
        res = run(
            facts=[
                mk_fact("f1", "R1", "inflation", percentage(2.5),
                        period=FactPeriod(PeriodKind.MONTH, "2026-01")),
                mk_fact("f2", "R2", "inflation", percentage(2.4),
                        period=FactPeriod(PeriodKind.MONTH, "2026-02")),
            ],
            pubs=pubs,
        )
        # two distinct lineages (two months) → two fundamentals
        assert len(res.fundamentals) == 2
        assert {f.value.value for f in res.fundamentals} == {2.5, 2.4}


# ---------------------------------------------------------------------------
# differentials
# ---------------------------------------------------------------------------
class TestDifferentials:
    def test_same_dimension_cross_currency_differential(self):
        res = run(states=policy_states())
        eur_usd = [d for d in res.differentials if d.base_currency == "EUR" and d.quote_currency == "USD"]
        assert eur_usd
        d = eur_usd[0]
        assert d.subject == "policy_rate"
        assert d.predicate == "value"
        assert d.value.value == d.base_value.value - d.quote_value.value

    def test_differential_arithmetic_difference(self):
        states = [
            mk_state("s1", "policy_rate", percentage(4.25), bank=BANK,
                     observed_at=datetime(2026, 3, 15), pub_id="P2"),
            mk_state("s2", "policy_rate", percentage(5.00), bank=FED,
                     observed_at=datetime(2026, 1, 20), pub_id="F1"),
        ]
        res = run(states=states)
        d = [x for x in res.differentials if x.base_currency == "EUR"][0]
        assert d.base_value.value == 4.25
        assert d.quote_value.value == 5.00
        assert d.value.value == -0.75
        assert d.value.kind.value == "percentage"
        assert d.value.unit is None

    def test_both_orientations_generated(self):
        res = run(states=policy_states())
        pairs = {(d.base_currency, d.quote_currency) for d in res.differentials}
        assert ("EUR", "USD") in pairs
        assert ("USD", "EUR") in pairs

    def test_orientations_have_distinct_identities(self):
        res = run(states=policy_states())
        eur_usd = {(d.differential_id, d.base_source_id, d.quote_source_id)
                   for d in res.differentials if d.base_currency == "EUR"}
        usd_eur = {(d.differential_id, d.base_source_id, d.quote_source_id)
                   for d in res.differentials if d.base_currency == "USD"}
        assert eur_usd.isdisjoint(usd_eur)

    def test_no_self_pairs(self):
        res = run(states=policy_states())
        for d in res.differentials:
            assert d.base_currency != d.quote_currency

    def test_no_look_ahead_missing_side(self):
        # the only quote observation is released after every base observation
        states = [
            mk_state("s1", "policy_rate", percentage(4.25), bank=BANK,
                     observed_at=datetime(2026, 3, 15), pub_id="P2"),
            mk_state("s2", "policy_rate", percentage(5.00), bank=FED,
                     observed_at=datetime(2026, 6, 1), pub_id="F1"),
        ]
        res = run(states=states)
        eur_usd = [d for d in res.differentials if d.base_currency == "EUR"]
        assert eur_usd == []
        assert any(w.startswith("missing_side:") and "USD" in w for w in res.warnings)

    def test_quote_latest_known_at_anchor(self):
        # USD has two observations; the latest ≤ each EUR anchor must be used
        states = [
            mk_state("e1", "policy_rate", percentage(4.25), bank=BANK,
                     observed_at=datetime(2026, 3, 15), pub_id="P2"),
            mk_state("e2", "policy_rate", percentage(4.50), bank=BANK,
                     observed_at=datetime(2026, 5, 15), pub_id="P3"),
            mk_state("f1", "policy_rate", percentage(5.00), bank=FED,
                     observed_at=datetime(2026, 1, 20), pub_id="F1"),
            mk_state("f2", "policy_rate", percentage(5.25), bank=FED,
                     observed_at=datetime(2026, 4, 20), pub_id="F2"),
        ]
        res = run(states=states)
        eur_usd = sorted(
            [d for d in res.differentials if d.base_currency == "EUR"],
            key=lambda d: d.base_observed_at,
        )
        assert len(eur_usd) == 2
        assert eur_usd[0].base_observed_at == datetime(2026, 3, 15)
        assert eur_usd[0].quote_value.value == 5.00  # F1 (Jan) — F2 not yet known
        assert eur_usd[1].base_observed_at == datetime(2026, 5, 15)
        assert eur_usd[1].quote_value.value == 5.25  # F2 (Apr) is the latest known

    def test_dimension_absent_on_quote_side_no_differential_no_warning(self):
        states = [
            mk_state("s1", "policy_rate", percentage(4.25), bank=BANK,
                     observed_at=datetime(2026, 3, 15), pub_id="P2"),
            mk_state("s2", "deposit_facility_rate", percentage(3.75), bank=FED,
                     observed_at=datetime(2026, 3, 20), pub_id="F2"),
        ]
        res = run(states=states)
        assert res.differentials == []
        assert all("missing_side" not in w for w in res.warnings)

    def test_cross_instrument_never_merged(self):
        # ECB deposit facility vs Fed policy rate are different lineages →
        # no differential, never a unique "policy rate".
        states = [
            mk_state("s1", "deposit_facility_rate", percentage(3.75), bank=BANK,
                     observed_at=datetime(2026, 3, 15), pub_id="P2"),
            mk_state("s2", "policy_rate", percentage(5.25), bank=FED,
                     observed_at=datetime(2026, 3, 20), pub_id="F2"),
        ]
        res = run(states=states)
        assert res.differentials == []
        assert all("incomparable" not in w for w in res.warnings)

    def test_unit_mismatch_incomparable(self):
        pubs = {
            "R1": mk_pub("R1", datetime(2026, 1, 15), pub_type=REPORT),
            "R2": mk_pub("R2", datetime(2026, 1, 20), pub_type=REPORT),
        }
        facts = [
            mk_fact("f1", "R1", "gdp", currency(2.5, unit="trillion_usd"), bank=BANK),
            mk_fact("f2", "R2", "gdp", currency(1.0, unit="billion_usd"), bank=FED),
        ]
        res = run(facts=facts, pubs=pubs)
        assert res.differentials == []
        assert any(w.startswith("incomparable_differential:gdp/value") for w in res.warnings)

    def test_same_unit_comparable(self):
        pubs = {
            "R1": mk_pub("R1", datetime(2026, 1, 15), pub_type=REPORT),
            "R2": mk_pub("R2", datetime(2026, 1, 20), pub_type=REPORT),
        }
        facts = [
            mk_fact("f1", "R1", "gdp", currency(2.5, unit="trillion_usd"), bank=BANK),
            mk_fact("f2", "R2", "gdp", currency(3.0, unit="trillion_usd"), bank=FED),
        ]
        res = run(facts=facts, pubs=pubs)
        d = [x for x in res.differentials if x.base_currency == "USD"][0]
        assert d.value.value == 0.5
        assert d.value.unit == "trillion_usd"

    def test_non_numeric_dimension_not_differentiable(self):
        states = [
            mk_state("s1", "policy_guidance", text_value("data dependent"), bank=BANK,
                     predicate="statement", observed_at=datetime(2026, 3, 15), pub_id="P2"),
            mk_state("s2", "policy_guidance", text_value("patient"), bank=FED,
                     predicate="statement", observed_at=datetime(2026, 3, 15), pub_id="F2"),
        ]
        res = run(states=states)
        assert len(res.fundamentals) == 2
        assert res.differentials == []
        # even with a quote available at the anchor, a text dimension is by
        # nature not differentiable — silently, never a warning
        assert res.warnings == []

    def test_macro_same_period_differential(self):
        pubs = {
            "R1": mk_pub("R1", datetime(2026, 1, 15), pub_type=REPORT),
            "R2": mk_pub("R2", datetime(2026, 1, 20), pub_type=REPORT),
        }
        facts = [
            mk_fact("f1", "R1", "inflation", percentage(2.5), bank=BANK,
                    period=FactPeriod(PeriodKind.MONTH, "2026-01")),
            mk_fact("f2", "R2", "inflation", percentage(3.0), bank=FED,
                    period=FactPeriod(PeriodKind.MONTH, "2026-01")),
        ]
        res = run(facts=facts, pubs=pubs)
        # USD anchored (EUR released Jan 15 ≤ Jan 20) → USD/EUR differential
        usd_eur = [d for d in res.differentials if d.base_currency == "USD"]
        assert len(usd_eur) == 1
        assert usd_eur[0].value.value == 0.5
        assert usd_eur[0].period.canonical() == "month:2026-01"
        # EUR anchored has no eligible quote → missing_side
        assert any(w.startswith("missing_side:") for w in res.warnings)

    def test_different_periods_are_different_dimensions(self):
        pubs = {
            "R1": mk_pub("R1", datetime(2026, 1, 15), pub_type=REPORT),
            "R2": mk_pub("R2", datetime(2026, 2, 20), pub_type=REPORT),
        }
        facts = [
            mk_fact("f1", "R1", "inflation", percentage(2.5), bank=BANK,
                    period=FactPeriod(PeriodKind.MONTH, "2026-01")),
            mk_fact("f2", "R2", "inflation", percentage(3.0), bank=FED,
                    period=FactPeriod(PeriodKind.MONTH, "2026-02")),
        ]
        res = run(facts=facts, pubs=pubs)
        assert res.differentials == []

    def test_differential_provenance_both_sides(self):
        states = [
            mk_state("s1", "policy_rate", percentage(4.25), bank=BANK,
                     observed_at=datetime(2026, 3, 15), pub_id="P2",
                     effective=datetime(2026, 3, 18), source_text="4.25 percent"),
            mk_state("s2", "policy_rate", percentage(5.00), bank=FED,
                     observed_at=datetime(2026, 1, 20), pub_id="F1",
                     effective=datetime(2026, 1, 22), source_text="5.00 percent"),
        ]
        res = run(states=states)
        d = [x for x in res.differentials if x.base_currency == "EUR"][0]
        assert d.base_source_id == "s1"
        assert d.quote_source_id == "s2"
        assert d.base_publication_id == "P2"
        assert d.quote_publication_id == "F1"
        assert d.base_effective_date == datetime(2026, 3, 18)
        assert d.base_source_text == "4.25 percent"
        assert d.quote_observed_at == datetime(2026, 1, 20)

    def test_formulation_never_forbidden(self):
        res = run(states=policy_states())
        for d in res.differentials:
            text = d.formulation.lower()
            for word in FORBIDDEN_WORDS:
                assert word not in text


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------
class TestDeterminism:
    def test_same_input_same_output(self):
        a = run(states=policy_states())
        b = run(states=policy_states())
        assert [f.fundamental_id for f in a.fundamentals] == [f.fundamental_id for f in b.fundamentals]
        assert [d.differential_id for d in a.differentials] == [d.differential_id for d in b.differentials]
        assert a.warnings == b.warnings

    def test_input_order_independent(self):
        a = run(states=policy_states())
        b = run(states=list(reversed(policy_states())))
        assert [f.fundamental_id for f in a.fundamentals] == [f.fundamental_id for f in b.fundamentals]
        assert [d.differential_id for d in a.differentials] == [d.differential_id for d in b.differentials]

    def test_empty_input(self):
        res = run()
        assert res.fundamentals == []
        assert res.differentials == []
        assert res.warnings == []


# ---------------------------------------------------------------------------
# store persistence
# ---------------------------------------------------------------------------
class TestStore:
    def _store(self) -> Store:
        import tempfile

        d = tempfile.mkdtemp()
        return Store(str(d) + "/test.db")

    def _classify(self, store: Store, pub_id: str, bank: str = BANK, pub_type: str = DECISION) -> None:
        store.set_classification(
            pub_id,
            central_bank=bank,
            publication_type=pub_type,
            confidence="high",
            method="source_type_hint",
            evidence=["test"],
        )

    def _seed(self, store: Store, *, with_macro: bool = True) -> None:
        for pid, date, meeting, rate in (
            ("P1", datetime(2026, 1, 15), datetime(2026, 1, 15), 4.00),
            ("P2", datetime(2026, 3, 15), datetime(2026, 3, 15), 4.25),
            ("P3", datetime(2026, 5, 15), datetime(2026, 5, 15), 4.50),
        ):
            store.upsert_publication(mk_pub(pid, date, meeting_date=meeting))
            self._classify(store, pid)
            store.save_fact(_mk_fact(pid, "policy_rate", percentage(rate)))
        for pid, date, meeting, rate in (
            ("F1", datetime(2026, 1, 20), datetime(2026, 1, 20), 5.00),
            ("F2", datetime(2026, 3, 20), datetime(2026, 3, 20), 5.25),
        ):
            store.upsert_publication(mk_pub(pid, date, meeting_date=meeting, bank=FED))
            self._classify(store, pid, bank=FED)
            store.save_fact(_mk_fact(pid, "policy_rate", percentage(rate), bank=FED))
        from argus.changes import analyze_changes

        analyze_changes(store, bank=BANK)
        analyze_changes(store, bank=FED)
        from argus.states import analyze_policy_state

        analyze_policy_state(store, bank=BANK)
        analyze_policy_state(store, bank=FED)
        if with_macro:
            for pid, date, bank, value in (
                ("M1", datetime(2026, 1, 15), BANK, 2.5),
                ("M2", datetime(2026, 1, 20), FED, 3.0),
            ):
                store.upsert_publication(mk_pub(pid, date, pub_type=REPORT, bank=bank))
                self._classify(store, pid, bank=bank, pub_type=REPORT)
                store.save_fact(_mk_fact(pid, "inflation", percentage(value), bank=bank))

    def test_analyze_forex_persists_fundamentals_and_differentials(self):
        store = self._store()
        self._seed(store)
        result = analyze_forex_fundamentals(store)
        assert result.fundamentals
        assert result.differentials
        assert len(store.get_forex_fundamentals(currency="EUR")) >= 1
        assert len(store.get_forex_differentials()) >= 1

    def test_currency_scope(self):
        store = self._store()
        self._seed(store)
        result = analyze_forex_fundamentals(store)
        eur = [f for f in result.fundamentals if f.currency == "EUR"]
        usd = [f for f in result.fundamentals if f.currency == "USD"]
        assert eur and usd
        eur_usd = [d for d in result.differentials if d.base_currency == "EUR" and d.quote_currency == "USD"]
        assert eur_usd
        d = eur_usd[0]
        assert d.base_value.value - d.quote_value.value == d.value.value

    def test_idempotent_rebuild(self):
        store = self._store()
        self._seed(store)
        first = analyze_forex_fundamentals(store)
        fid1 = {f.fundamental_id for f in first.fundamentals}
        did1 = {d.differential_id for d in first.differentials}
        analyze_forex_fundamentals(store)
        assert {f.fundamental_id for f in store.get_forex_fundamentals()} == fid1
        assert {d.differential_id for d in store.get_forex_differentials()} == did1

    def test_no_duplicates_across_runs(self):
        store = self._store()
        self._seed(store)
        analyze_forex_fundamentals(store)
        analyze_forex_fundamentals(store)
        fid = [f.fundamental_id for f in store.get_forex_fundamentals()]
        did = [d.differential_id for d in store.get_forex_differentials()]
        assert len(fid) == len(set(fid))
        assert len(did) == len(set(did))

    def test_empty_result_clears_scope(self):
        store = self._store()
        self._seed(store)
        analyze_forex_fundamentals(store)
        assert store.get_forex_fundamentals(currency="EUR")
        store.rebuild_forex_fundamentals([], currency="EUR")
        assert store.get_forex_fundamentals(currency="EUR") == []
        store.rebuild_forex_differentials([], currencies=("EUR",))
        assert store.get_forex_differentials(base_currency="EUR") == []
        assert store.get_forex_differentials(quote_currency="EUR") == []

    def test_analyze_forex_empty_store(self):
        store = self._store()
        result = analyze_forex_fundamentals(store)
        assert result.fundamentals == []
        assert result.differentials == []
        assert store.get_forex_fundamentals() == []
        assert store.get_forex_differentials() == []

    def test_bank_scope_isolation(self):
        store = self._store()
        self._seed(store)
        analyze_forex_fundamentals(store)
        all_before = {d.differential_id for d in store.get_forex_differentials()}
        # rebuild only EUR: differentials involving EUR are recomputed, others
        # (none exist beyond EUR/USD) untouched; EUR-scope is exactly the set
        # freshly analyzed for EUR.
        result = analyze_forex_fundamentals(store, bank=BANK)
        eur_ids = {d.differential_id for d in result.differentials
                   if d.base_currency == "EUR" or d.quote_currency == "EUR"}
        after = store.get_forex_differentials()
        assert {d.differential_id for d in after} == eur_ids
        assert eur_ids == all_before  # every differential involves EUR here

    def test_persist_false_does_not_write(self):
        store = self._store()
        self._seed(store)
        result = analyze_forex_fundamentals(store, persist=False)
        assert result.fundamentals
        assert store.get_forex_fundamentals() == []
        assert store.get_forex_differentials() == []

    def test_get_fundamentals_as_of_no_look_ahead(self):
        store = self._store()
        self._seed(store)
        analyze_forex_fundamentals(store)
        early = store.get_fundamentals_as_of("EUR", datetime(2026, 4, 1))
        late = store.get_fundamentals_as_of("EUR")
        rates_early = {f.value.value for f in early if f.subject == "policy_rate"}
        rates_late = {f.value.value for f in late if f.subject == "policy_rate"}
        assert rates_early == {4.25}
        assert rates_late == {4.5}

    def test_get_differential_as_of_no_look_ahead(self):
        store = self._store()
        self._seed(store)
        analyze_forex_fundamentals(store)
        early = store.get_differential_as_of("EUR", "USD", "policy_rate", datetime(2026, 5, 15))
        later = store.get_differential_as_of("EUR", "USD", "policy_rate")
        assert early
        assert [d.base_observed_at for d in early] == [datetime(2026, 5, 15, tzinfo=timezone.utc)]
        assert [d.differential_id for d in early] == [d.differential_id for d in later]

    def test_get_forex_fundamentals_filters(self):
        store = self._store()
        self._seed(store)
        analyze_forex_fundamentals(store)
        assert store.get_forex_fundamentals(currency="EUR", subject="policy_rate")
        assert store.get_forex_fundamentals(currency="EUR", source_kind=SOURCE_MONETARY_STATE)
        assert store.get_forex_fundamentals(currency="EUR", source_kind=SOURCE_FACT)
        # the macro inflation fundamental is EUR-persisted
        inflation = store.get_forex_fundamentals(currency="EUR", subject="inflation")
        assert len(inflation) == 1
        assert inflation[0].value.value == 2.5

    def test_delete_by_currency(self):
        store = self._store()
        self._seed(store)
        analyze_forex_fundamentals(store)
        assert store.get_forex_fundamentals(currency="EUR")
        assert store.delete_forex_fundamentals(currency="EUR") > 0
        assert store.get_forex_fundamentals(currency="EUR") == []

    def test_delete_for_publication(self):
        store = self._store()
        self._seed(store)
        analyze_forex_fundamentals(store)
        assert store.get_forex_fundamentals(publication_id="M1")
        assert store.delete_forex_fundamentals_for_publication("M1") == 1
        assert store.get_forex_fundamentals(publication_id="M1") == []

    def test_created_at_preserved_on_upsert(self):
        store = self._store()
        self._seed(store)
        analyze_forex_fundamentals(store)
        f = store.get_forex_fundamentals(currency="EUR")[0]
        before = store._conn.execute(
            "SELECT created_at FROM forex_fundamentals WHERE fundamental_id = ?",
            (f.fundamental_id,),
        ).fetchone()["created_at"]
        store.save_forex_fundamental(f)
        after = store._conn.execute(
            "SELECT created_at FROM forex_fundamentals WHERE fundamental_id = ?",
            (f.fundamental_id,),
        ).fetchone()["created_at"]
        assert before == after

    def test_authoritative_classifications(self):
        store = self._store()
        self._seed(store)
        analyze_forex_fundamentals(store)
        assert store.get_forex_fundamentals(currency="EUR", source_kind=SOURCE_FACT)
        macro_id = store.get_facts(publication_id="M1")[0].fact_id
        # drop the macro publication's authoritative classification: the fact
        # must be skipped on re-analysis, never falling back to the
        # denormalized publications.publication_type cache.
        store._conn.execute("DELETE FROM classifications WHERE publication_id = 'M1'")
        store._conn.commit()
        result = analyze_forex_fundamentals(store)
        assert any("missing_classification:M1" in w for w in result.warnings)
        assert store.get_forex_fundamentals(source_id=macro_id) == []

    def test_save_fundamentals_type_check(self):
        store = self._store()
        with pytest.raises(TypeError):
            store.save_forex_fundamental("nope")
        with pytest.raises(TypeError):
            store.save_forex_differential(object())

    def test_rebuild_fundamentals_type_check(self):
        store = self._store()
        with pytest.raises(TypeError):
            store.rebuild_forex_fundamentals([object()])


# ---------------------------------------------------------------------------
# negative / boundaries
# ---------------------------------------------------------------------------
class TestNegative:
    def test_source_states_never_mutated(self):
        states = policy_states()
        snapshot = [copy.deepcopy(s) for s in states]
        run(states=states)
        for original, after in zip(snapshot, states):
            assert after.to_dict() == original.to_dict()

    def test_source_facts_never_mutated(self):
        pubs = {"R1": mk_pub("R1", datetime(2026, 1, 15), pub_type=REPORT)}
        facts = [mk_fact("f1", "R1", "inflation", percentage(2.5))]
        snapshot = [copy.deepcopy(f) for f in facts]
        run(facts=facts, pubs=pubs)
        for original, after in zip(snapshot, facts):
            assert after.to_dict() == original.to_dict()

    def test_no_hawkish_dovish_vocabulary(self):
        res = run(states=policy_states())
        for f in res.fundamentals:
            assert "hawkish" not in f.describe().lower()
            assert "dovish" not in f.describe().lower()
        for d in res.differentials:
            assert "hawkish" not in (d.describe() or "").lower()
            assert "dovish" not in (d.describe() or "").lower()

    def test_no_stance_score_or_signal_fields(self):
        f = ForexFundamental()
        d = ForexDifferential()
        for obj in (f, d):
            for attr in ("stance", "direction", "score", "forecast", "expectation",
                         "fair_value", "signal", "conviction", "probability",
                         "expected_return", "target", "ranking", "positioning"):
                assert not hasattr(obj, attr)

    def test_no_causality_vocabulary(self):
        res = run(states=policy_states())
        for d in res.differentials:
            text = (d.describe() or "").lower()
            assert "caus" not in text
            assert "because" not in text

    def test_no_network_or_semantic_dependency(self):
        import inspect

        from argus.forex import analyzer

        source = inspect.getsource(analyzer)
        assert "requests" not in source
        assert "openai" not in source
        assert "fuzzy" not in source
        assert "llm" not in source.lower()

    def test_analyzer_never_reads_documents(self):
        # monetary fundamentals come from the state objects, never from
        # documents: the analyzer signature takes states and facts, no
        # documents, and never touches a store or a documents API.
        import inspect

        params = inspect.signature(ForexFundamentalsAnalyzer.analyze).parameters
        assert set(params) == {"self", "states", "facts", "currencies",
                               "publications", "classifications"}
        source = inspect.getsource(ForexFundamentalsAnalyzer.analyze)
        assert "store" not in source
        assert "get_documents" not in source
        assert "request" not in source


def _mk_fact(pub_id: str, subject: str, value, *, bank: str = BANK):
    from argus.facts import fact_id_of

    return Fact(
        publication_id=pub_id,
        document_id=pub_id,
        subject=subject,
        predicate="value",
        value=value,
        central_bank=bank,
        confidence=Confidence.HIGH,
        fact_id=fact_id_of(
            publication_id=pub_id,
            document_id=pub_id,
            subject=subject,
            predicate="value",
            period=None,
            effective_date=None,
            qualifier="",
        ),
    )
