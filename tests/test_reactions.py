"""Phase 6 — temporal relationship analysis (legacy name "policy reaction analysis").

Tests the pure ``PolicyReactionAnalyzer`` (legacy class name; concept: Temporal
Relationship) and the store integration (``analyze_reactions`` +
``policy_reactions`` persistence). The code identifiers remain the legacy ones
for compatibility; the concept is a descriptive temporal relationship between
two observed FactChanges — never a causal claim nor a central-bank reaction
function.

Coverage:

- **Vocabulary**: earlier-side (legacy condition-side) vs later-side (legacy
  reaction-side) subject sets, disjointness,
  documented default window constant.
- **Model**: ``inferred`` is always ``True``; formulation is explicitly
  non-causal; deterministic description; serialization round-trip.
- **Identity**: deterministic, pair-specific, directional ``reaction_id``.
- **Temporal**: no look-ahead (condition after policy never pairs), same-time
  allowed (lag 0), window boundary honored, explicit ``max_lag_days`` parameter.
- **Matching**: every eligible (condition, policy) pair in the same bank → one
  reaction; irrelevant subjects ignored silently; multiple conditions → one
  response; one condition → multiple responses; risk is reaction-side only.
- **Provenance**: verbatim denormalization of both sides, temporal reference
  ``meeting_date`` else ``publication_date``, ``analysis_version``.
- **Warnings**: ``missing_publication``, ``undated_publication``,
  ``unplaced_change``.
- **Determinism**: identical input → identical reactions, order-independent.
- **Persistence**: idempotent rebuild, empty clears scope, bank isolation,
  filters, deletes, ``created_at`` preservation, ``persist=False``.
- **Golden/adversarial fixtures**: 8 documented scenarios.
- **Negative**: source FactChanges are never mutated; no hawkish/dovish,
  no causality; empty input.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta

import pytest

from argus.changes import ChangeType, FactChange
from argus.facts import (
    categorical,
    percentage,
    text_value,
)
from argus.facts.base import FactPeriod, PeriodKind
from argus.models import Publication, PublicationStatus
from argus.reactions import (
    CONDITION_SUBJECTS,
    DEFAULT_MAX_LAG_DAYS,
    REACTION_SUBJECTS,
    PolicyReaction,
    PolicyReactionAnalyzer,
    PolicyReactionResult,
    analyze_reactions,
    reaction_id_of,
)
from argus.store import Store

BANK = "ecb"

DECISION = "monetary_policy_decision"


def mk_pub(
    pub_id: str,
    date: datetime | None,
    *,
    meeting_date: datetime | None = None,
    bank: str = BANK,
) -> Publication:
    return Publication(
        central_bank=bank,
        title="title",
        url=f"https://example.org/{pub_id}",
        source_id="src",
        source_url="https://example.org",
        id=pub_id,
        publication_type=DECISION,
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
    max_lag_days: int = DEFAULT_MAX_LAG_DAYS,
) -> PolicyReactionResult:
    return PolicyReactionAnalyzer().analyze(
        changes, publications=pubs, max_lag_days=max_lag_days
    )


# ---------------------------------------------------------------------------
# vocabulary
# ---------------------------------------------------------------------------
class TestVocabulary:
    def test_condition_subjects(self):
        assert CONDITION_SUBJECTS == {
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

    def test_reaction_subjects(self):
        assert REACTION_SUBJECTS == {
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

    def test_sets_are_disjoint(self):
        assert CONDITION_SUBJECTS.isdisjoint(REACTION_SUBJECTS)

    def test_default_window_documented(self):
        assert DEFAULT_MAX_LAG_DAYS == 180


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------
class TestModel:
    def test_inferred_is_always_true(self):
        r = PolicyReaction()
        assert r.inferred is True

    def test_inferred_constant_even_after_analysis(self):
        cond = mk_change("c1", "inflation", cur_pub="P2")
        pol = mk_change("p1", "policy_rate", cur_pub="P2")
        res = run([cond, pol], {"P1": mk_pub("P1", datetime(2026, 1, 15)), "P2": mk_pub("P2", datetime(2026, 3, 15))})
        assert res.reactions
        for reaction in res.reactions:
            assert reaction.inferred is True

    def test_formulation_is_non_causal(self):
        cond = mk_change("c1", "inflation", cur_pub="P2")
        pol = mk_change("p1", "policy_rate", cur_pub="P2")
        res = run([cond, pol], {"P1": mk_pub("P1", datetime(2026, 1, 15)), "P2": mk_pub("P2", datetime(2026, 3, 15))})
        reaction = res.reactions[0]
        assert reaction.formulation is not None
        assert "causal" in reaction.formulation
        assert "not causal" in reaction.formulation
        assert "empirical temporal association" in reaction.formulation

    def test_describe_is_deterministic(self):
        cond = mk_change("c1", "inflation", cur_pub="P2")
        pol = mk_change("p1", "policy_rate", cur_pub="P2")
        pubs = {"P1": mk_pub("P1", datetime(2026, 1, 15)), "P2": mk_pub("P2", datetime(2026, 3, 15))}
        a = run([cond, pol], pubs).reactions[0].describe()
        b = run([cond, pol], pubs).reactions[0].describe()
        assert a == b

    def test_serialization_round_trip(self):
        cond = mk_change(
            "c1", "inflation", cur_pub="P2",
            prev_value=percentage(2.1), cur_value=percentage(2.4),
            period=FactPeriod(PeriodKind.YEAR, "2027", "2027"),
            effective=datetime(2026, 1, 16), source_text="2.1 percent",
        )
        pol = mk_change(
            "p1", "policy_rate", cur_pub="P2",
            prev_value=percentage(4.00), cur_value=percentage(4.25),
            effective=datetime(2026, 3, 16), source_text="4.25 percent",
        )
        pubs = {"P1": mk_pub("P1", datetime(2026, 1, 15)), "P2": mk_pub("P2", datetime(2026, 3, 15))}
        reaction = run([cond, pol], pubs).reactions[0]
        restored = PolicyReaction.from_dict(reaction.to_dict())
        assert restored.reaction_id == reaction.reaction_id
        assert restored.inferred is True
        assert restored.condition_subject == "inflation"
        assert restored.policy_subject == "policy_rate"
        assert restored.condition_current_value.value == 2.4
        assert restored.policy_current_value.value == 4.25
        assert restored.condition_period.canonical() == "year:2027"
        assert restored.lag_days == reaction.lag_days
        assert restored.formulation == reaction.formulation


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------
class TestIdentity:
    def test_reaction_id_is_deterministic(self):
        a = reaction_id_of(central_bank=BANK, condition_change_id="c1", policy_change_id="p1")
        b = reaction_id_of(central_bank=BANK, condition_change_id="c1", policy_change_id="p1")
        assert a == b
        assert len(a) == 64  # sha256 hex

    def test_reaction_id_pair_specific(self):
        a = reaction_id_of(central_bank=BANK, condition_change_id="c1", policy_change_id="p1")
        b = reaction_id_of(central_bank=BANK, condition_change_id="c1", policy_change_id="p2")
        c = reaction_id_of(central_bank=BANK, condition_change_id="c2", policy_change_id="p1")
        assert a != b != c

    def test_reaction_id_bank_specific(self):
        a = reaction_id_of(central_bank="ecb", condition_change_id="c1", policy_change_id="p1")
        b = reaction_id_of(central_bank="fed", condition_change_id="c1", policy_change_id="p1")
        assert a != b

    def test_reaction_id_directional(self):
        a = reaction_id_of(central_bank=BANK, condition_change_id="c1", policy_change_id="p1")
        b = reaction_id_of(central_bank=BANK, condition_change_id="p1", policy_change_id="c1")
        assert a != b

    def test_resolve_id_caches(self):
        reaction = PolicyReaction(central_bank=BANK, condition_change_id="c1", policy_change_id="p1")
        first = reaction.resolve_id()
        assert first == reaction.reaction_id  # cached back onto the field
        assert reaction.resolve_id() == first


# ---------------------------------------------------------------------------
# temporal rules
# ---------------------------------------------------------------------------
class TestTemporal:
    def test_condition_before_policy_within_window(self):
        cond = mk_change("c1", "inflation", cur_pub="P2")
        pol = mk_change("p1", "policy_rate", cur_pub="P3")
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 15)),
            "P3": mk_pub("P3", datetime(2026, 5, 15)),
        }
        res = run([cond, pol], pubs)
        assert len(res.reactions) == 1
        reaction = res.reactions[0]
        # condition obs P2 (Mar 15) → policy obs P3 (May 15) = 61 days
        assert reaction.lag_days == 61
        assert reaction.condition_observed_at == datetime(2026, 3, 15)
        assert reaction.policy_observed_at == datetime(2026, 5, 15)

    def test_no_look_ahead_condition_after_policy(self):
        cond = mk_change("c1", "inflation", cur_pub="P3")
        pol = mk_change("p1", "policy_rate", cur_pub="P2")
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 15)),
            "P3": mk_pub("P3", datetime(2026, 5, 15)),
        }
        res = run([cond, pol], pubs)
        assert res.reactions == []

    def test_same_time_reaction_allowed(self):
        cond = mk_change("c1", "inflation", cur_pub="P2")
        pol = mk_change("p1", "policy_rate", cur_pub="P2")
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 15)),
        }
        res = run([cond, pol], pubs)
        assert len(res.reactions) == 1
        assert res.reactions[0].lag_days == 0

    def test_window_boundary_included(self):
        base = datetime(2026, 1, 1)
        cond = mk_change("c1", "inflation", cur_pub="C")
        pol = mk_change("p1", "policy_rate", cur_pub="P")
        pubs = {
            "C": mk_pub("C", base),
            "P": mk_pub("P", base + timedelta(days=180)),
        }
        res = run([cond, pol], pubs)
        assert len(res.reactions) == 1
        assert res.reactions[0].lag_days == 180

    def test_window_exceeded_excluded(self):
        base = datetime(2026, 1, 1)
        cond = mk_change("c1", "inflation", cur_pub="C")
        pol = mk_change("p1", "policy_rate", cur_pub="P")
        pubs = {
            "C": mk_pub("C", base),
            "P": mk_pub("P", base + timedelta(days=181)),
        }
        res = run([cond, pol], pubs)
        assert res.reactions == []

    def test_explicit_max_lag_days_honored(self):
        base = datetime(2026, 1, 1)
        cond = mk_change("c1", "inflation", cur_pub="C")
        pol = mk_change("p1", "policy_rate", cur_pub="P")
        pubs = {
            "C": mk_pub("C", base),
            "P": mk_pub("P", base + timedelta(days=90)),
        }
        # inside a 90-day window
        res = run([cond, pol], pubs, max_lag_days=90)
        assert len(res.reactions) == 1
        assert res.reactions[0].max_lag_days == 90
        # outside an 89-day window → excluded
        res = run([cond, pol], pubs, max_lag_days=89)
        assert res.reactions == []

    def test_negative_max_lag_days_rejected(self):
        cond = mk_change("c1", "inflation", cur_pub="C")
        pol = mk_change("p1", "policy_rate", cur_pub="P")
        pubs = {"C": mk_pub("C", datetime(2026, 1, 1)), "P": mk_pub("P", datetime(2026, 3, 1))}
        with pytest.raises(ValueError):
            run([cond, pol], pubs, max_lag_days=-1)

    def test_meeting_date_preferred_over_publication_date(self):
        cond = mk_change("c1", "inflation", cur_pub="P2")
        pol = mk_change("p1", "policy_rate", cur_pub="P3")
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 20), meeting_date=datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 20), meeting_date=datetime(2026, 3, 15)),
            "P3": mk_pub("P3", datetime(2026, 5, 20), meeting_date=datetime(2026, 5, 15)),
        }
        res = run([cond, pol], pubs)
        reaction = res.reactions[0]
        # reference is the meeting date, not the publication date
        assert reaction.condition_observed_at == datetime(2026, 3, 15)
        assert reaction.policy_observed_at == datetime(2026, 5, 15)
        assert reaction.lag_days == 61  # Mar 15 → May 15


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------
class TestMatching:
    def test_condition_side_is_condition_policy_is_policy(self):
        cond = mk_change("c1", "inflation", cur_pub="P2")
        pol = mk_change("p1", "policy_rate", cur_pub="P2")
        pubs = {"P1": mk_pub("P1", datetime(2026, 1, 15)), "P2": mk_pub("P2", datetime(2026, 3, 15))}
        res = run([cond, pol], pubs)
        assert len(res.reactions) == 1
        reaction = res.reactions[0]
        assert reaction.condition_change_id == "c1"
        assert reaction.condition_subject == "inflation"
        assert reaction.policy_change_id == "p1"
        assert reaction.policy_subject == "policy_rate"

    def test_irrelevant_subject_ignored_silently(self):
        cond = mk_change("c1", "inflation", cur_pub="P2")
        pol = mk_change("p1", "policy_rate", cur_pub="P2")
        fx = mk_change("x1", "fx_rate", cur_pub="P2")
        pubs = {"P1": mk_pub("P1", datetime(2026, 1, 15)), "P2": mk_pub("P2", datetime(2026, 3, 15))}
        res = run([cond, pol, fx], pubs)
        assert len(res.reactions) == 1
        assert res.warnings == []  # irrelevant, not an error

    def test_multiple_conditions_one_response(self):
        inf = mk_change("c1", "inflation", cur_pub="P2")
        une = mk_change("c2", "unemployment", cur_pub="P2")
        pol = mk_change("p1", "policy_rate", cur_pub="P3")
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 15)),
            "P3": mk_pub("P3", datetime(2026, 5, 15)),
        }
        res = run([inf, une, pol], pubs)
        assert len(res.reactions) == 2
        assert {r.condition_subject for r in res.reactions} == {"inflation", "unemployment"}
        assert {r.policy_change_id for r in res.reactions} == {"p1"}

    def test_one_condition_multiple_responses(self):
        cond = mk_change("c1", "inflation", cur_pub="P2")
        rate = mk_change("p1", "policy_rate", cur_pub="P3")
        guid = mk_change("p2", "policy_guidance", cur_pub="P3")
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 15)),
            "P3": mk_pub("P3", datetime(2026, 5, 15)),
        }
        res = run([cond, rate, guid], pubs)
        assert len(res.reactions) == 2
        assert {r.policy_subject for r in res.reactions} == {"policy_rate", "policy_guidance"}
        assert {r.condition_change_id for r in res.reactions} == {"c1"}

    def test_every_eligible_pair_exactly_one_reaction(self):
        c1 = mk_change("c1", "inflation", cur_pub="P2")
        c2 = mk_change("c2", "unemployment", cur_pub="P2")
        p1 = mk_change("p1", "policy_rate", cur_pub="P3")
        p2 = mk_change("p2", "policy_guidance", cur_pub="P3")
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 15)),
            "P3": mk_pub("P3", datetime(2026, 5, 15)),
        }
        res = run([c1, c2, p1, p2], pubs)
        assert len(res.reactions) == 4  # 2 conditions × 2 responses
        pairs = {(r.condition_change_id, r.policy_change_id) for r in res.reactions}
        assert pairs == {("c1", "p1"), ("c1", "p2"), ("c2", "p1"), ("c2", "p2")}

    def test_risk_is_reaction_side_only(self):
        # a "risk" change can never be a condition — it is reaction-side only.
        cond = mk_change("c1", "risk", cur_pub="P2")
        pol = mk_change("p1", "policy_rate", cur_pub="P3")
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 15)),
            "P3": mk_pub("P3", datetime(2026, 5, 15)),
        }
        res = run([cond, pol], pubs)
        # "risk" is not a condition subject → cond is ignored as a condition;
        # only the real condition… wait, there is none, so no reaction.
        assert res.reactions == []
        # and risk as a response pairs with a real condition
        c2 = mk_change("c2", "inflation", cur_pub="P2")
        res = run([c2, cond, pol], pubs)
        assert len(res.reactions) == 2
        assert {r.policy_subject for r in res.reactions} == {"policy_rate", "risk"}

    def test_risk_only_never_condition_directly(self):
        # a reaction whose condition side is "risk" can never exist.
        c1 = mk_change("c1", "risk", cur_pub="P2")
        p1 = mk_change("p1", "policy_rate", cur_pub="P3")
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 15)),
            "P3": mk_pub("P3", datetime(2026, 5, 15)),
        }
        res = run([c1, p1], pubs)
        assert all(r.condition_subject != "risk" for r in res.reactions)

    def test_cross_bank_never_paired(self):
        cond = mk_change("c1", "inflation", cur_pub="P2", bank="ecb")
        pol = mk_change("p1", "policy_rate", cur_pub="F2", bank="fed")
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15), bank="ecb"),
            "P2": mk_pub("P2", datetime(2026, 3, 15), bank="ecb"),
            "F1": mk_pub("F1", datetime(2026, 1, 15), bank="fed"),
            "F2": mk_pub("F2", datetime(2026, 3, 15), bank="fed"),
        }
        res = run([cond, pol], pubs)
        assert res.reactions == []

    def test_policy_change_observed_together_with_condition_of_own_bank(self):
        # same-bank pairs pair; the fed condition never meets the ecb policy.
        ecb_cond = mk_change("e1", "inflation", cur_pub="P2", bank="ecb")
        ecb_pol = mk_change("e2", "policy_rate", cur_pub="P3", bank="ecb")
        fed_cond = mk_change("f1", "inflation", cur_pub="F2", bank="fed")
        fed_pol = mk_change("f2", "policy_rate", cur_pub="F3", bank="fed")
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15), bank="ecb"),
            "P2": mk_pub("P2", datetime(2026, 3, 15), bank="ecb"),
            "P3": mk_pub("P3", datetime(2026, 5, 15), bank="ecb"),
            "F1": mk_pub("F1", datetime(2026, 1, 15), bank="fed"),
            "F2": mk_pub("F2", datetime(2026, 3, 15), bank="fed"),
            "F3": mk_pub("F3", datetime(2026, 5, 15), bank="fed"),
        }
        res = run([ecb_cond, ecb_pol, fed_cond, fed_pol], pubs)
        assert len(res.reactions) == 2
        assert {r.central_bank for r in res.reactions} == {"ecb", "fed"}


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------
class TestProvenance:
    def test_both_sides_verbatim(self):
        cond = mk_change(
            "c1", "inflation", cur_pub="P2",
            prev_value=percentage(2.1), cur_value=percentage(2.4),
            period=FactPeriod(PeriodKind.YEAR, "2027", "2027"),
            effective=datetime(2026, 1, 16),
            source_text="HICP inflation is expected at 2.1%.",
        )
        pol = mk_change(
            "p1", "policy_rate", cur_pub="P3",
            prev_value=percentage(4.00), cur_value=percentage(4.25),
            effective=datetime(2026, 3, 16),
            source_text="The rate is increased to 4.25%.",
            predicate="change",
        )
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 15)),
            "P3": mk_pub("P3", datetime(2026, 5, 15)),
        }
        reaction = run([cond, pol], pubs).reactions[0]
        # condition side
        assert reaction.condition_change_id == "c1"
        assert reaction.condition_subject == "inflation"
        assert reaction.condition_predicate == "value"
        assert reaction.condition_value_kind == "percentage"
        assert reaction.condition_previous_value.value == 2.1
        assert reaction.condition_current_value.value == 2.4
        assert reaction.condition_period.canonical() == "year:2027"
        assert reaction.condition_publication_id == "P2"
        assert reaction.condition_document_id == "P2"
        assert reaction.condition_effective_date == datetime(2026, 1, 16)
        assert reaction.condition_source_text == "HICP inflation is expected at 2.1%."
        assert reaction.condition_observed_at == datetime(2026, 3, 15)
        # policy side
        assert reaction.policy_change_id == "p1"
        assert reaction.policy_subject == "policy_rate"
        assert reaction.policy_predicate == "change"
        assert reaction.policy_value_kind == "percentage"
        assert reaction.policy_previous_value.value == 4.00
        assert reaction.policy_current_value.value == 4.25
        assert reaction.policy_publication_id == "P3"
        assert reaction.policy_document_id == "P3"
        assert reaction.policy_effective_date == datetime(2026, 3, 16)
        assert reaction.policy_source_text == "The rate is increased to 4.25%."
        assert reaction.policy_observed_at == datetime(2026, 5, 15)

    def test_analysis_version_and_central_bank(self):
        cond = mk_change("c1", "inflation", cur_pub="P2", bank="ecb")
        pol = mk_change("p1", "policy_rate", cur_pub="P2", bank="ecb")
        pubs = {"P1": mk_pub("P1", datetime(2026, 1, 15)), "P2": mk_pub("P2", datetime(2026, 3, 15))}
        reaction = run([cond, pol], pubs).reactions[0]
        assert reaction.analysis_version == "13.0.0"
        assert reaction.central_bank == "ecb"
        assert reaction.max_lag_days == DEFAULT_MAX_LAG_DAYS

    def test_bank_comes_from_change_never_publication(self):
        # a change whose central_bank is absent is never placed from the
        # publication: no fallback, no reaction, unplaced_change warning.
        cond = mk_change("c1", "inflation", cur_pub="P2", bank=None)
        pol = mk_change("p1", "policy_rate", cur_pub="P2", bank=None)
        pubs = {"P1": mk_pub("P1", datetime(2026, 1, 15)), "P2": mk_pub("P2", datetime(2026, 3, 15))}
        res = run([cond, pol], pubs)
        assert res.reactions == []
        assert any(w.startswith("unplaced_change:c1") for w in res.warnings)
        assert any(w.startswith("unplaced_change:p1") for w in res.warnings)

    def test_publication_bank_does_not_place_change(self):
        # even though the publication carries central_bank="ecb", a change
        # without a central_bank is ignored (never resolved from the publication).
        cond = mk_change("c1", "inflation", cur_pub="P2", bank=None)
        pol = mk_change("p1", "policy_rate", cur_pub="P2", bank="ecb")
        pubs = {"P1": mk_pub("P1", datetime(2026, 1, 15)), "P2": mk_pub("P2", datetime(2026, 3, 15))}
        res = run([cond, pol], pubs)
        assert res.reactions == []
        assert any(w.startswith("unplaced_change:c1") for w in res.warnings)


# ---------------------------------------------------------------------------
# warnings
# ---------------------------------------------------------------------------
class TestWarnings:
    def test_missing_publication_warns(self):
        cond = mk_change("c1", "inflation", cur_pub="PX")
        pol = mk_change("p1", "policy_rate", cur_pub="P2")
        pubs = {"P1": mk_pub("P1", datetime(2026, 1, 15)), "P2": mk_pub("P2", datetime(2026, 3, 15))}
        res = run([cond, pol], pubs)
        assert res.reactions == []
        assert any(w.startswith("missing_publication:PX") for w in res.warnings)

    def test_no_current_publication_warns_with_change_id(self):
        # a change without any current_publication_id is referenced by its
        # change_id, never as missing_publication:None.
        cond = mk_change("c1", "inflation", cur_pub="")
        pol = mk_change("p1", "policy_rate", cur_pub="P2")
        pubs = {"P1": mk_pub("P1", datetime(2026, 1, 15)), "P2": mk_pub("P2", datetime(2026, 3, 15))}
        res = run([cond, pol], pubs)
        assert res.reactions == []
        assert any(w.startswith("missing_publication:c1") for w in res.warnings)
        assert not any(w == "missing_publication:None" for w in res.warnings)

    def test_undated_publication_warns(self):
        cond = mk_change("c1", "inflation", cur_pub="P2")
        pol = mk_change("p1", "policy_rate", cur_pub="N")
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 15)),
            "N": mk_pub("N", None),
        }
        res = run([cond, pol], pubs)
        assert res.reactions == []
        assert any(w.startswith("undated_publication:N") for w in res.warnings)

    def test_unplaced_change_warns(self):
        cond = mk_change("c1", "inflation", cur_pub="P2", bank=None)
        pol = mk_change("p1", "policy_rate", cur_pub="P2", bank=None)
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15), bank=None),
            "P2": mk_pub("P2", datetime(2026, 3, 15), bank=None),
        }
        res = run([cond, pol], pubs)
        assert res.reactions == []
        assert any(w.startswith("unplaced_change:c1") for w in res.warnings)
        assert any(w.startswith("unplaced_change:p1") for w in res.warnings)

    def test_unplaced_change_warns_even_when_publication_has_bank(self):
        # the publication's central_bank never places a change: a change without
        # its own central_bank is unplaced regardless.
        cond = mk_change("c1", "inflation", cur_pub="P2", bank=None)
        pol = mk_change("p1", "policy_rate", cur_pub="P2", bank=None)
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 15)),
        }
        res = run([cond, pol], pubs)
        assert res.reactions == []
        assert any(w.startswith("unplaced_change:c1") for w in res.warnings)


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------
class TestDeterminism:
    def test_same_input_same_reactions(self):
        cond = mk_change("c1", "inflation", cur_pub="P2")
        pol = mk_change("p1", "policy_rate", cur_pub="P3")
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 15)),
            "P3": mk_pub("P3", datetime(2026, 5, 15)),
        }
        a = run([cond, pol], pubs)
        b = run([cond, pol], pubs)
        assert [r.reaction_id for r in a.reactions] == [r.reaction_id for r in b.reactions]
        assert a.warnings == b.warnings

    def test_input_order_independent(self):
        c1 = mk_change("c1", "inflation", cur_pub="P2")
        c2 = mk_change("c2", "unemployment", cur_pub="P2")
        p1 = mk_change("p1", "policy_rate", cur_pub="P3")
        p2 = mk_change("p2", "policy_guidance", cur_pub="P3")
        pubs = {
            "P1": mk_pub("P1", datetime(2026, 1, 15)),
            "P2": mk_pub("P2", datetime(2026, 3, 15)),
            "P3": mk_pub("P3", datetime(2026, 5, 15)),
        }
        a = run([c1, c2, p1, p2], pubs)
        b = run([p2, c2, p1, c1], pubs)
        assert [r.reaction_id for r in a.reactions] == [r.reaction_id for r in b.reactions]


# ---------------------------------------------------------------------------
# negative
# ---------------------------------------------------------------------------
class TestNegative:
    def test_source_changes_never_mutated(self):
        cond = mk_change("c1", "inflation", cur_pub="P2",
                         prev_value=percentage(2.1), cur_value=percentage(2.4))
        pol = mk_change("p1", "policy_rate", cur_pub="P2",
                        prev_value=percentage(4.00), cur_value=percentage(4.25))
        snap1, snap2 = copy.deepcopy(cond), copy.deepcopy(pol)
        pubs = {"P1": mk_pub("P1", datetime(2026, 1, 15)), "P2": mk_pub("P2", datetime(2026, 3, 15))}
        res = run([cond, pol], pubs)
        assert res.reactions
        assert cond == snap1
        assert pol == snap2

    def test_no_hawkish_dovish_vocabulary(self):
        cond = mk_change("c1", "inflation", cur_pub="P2")
        pol = mk_change("p1", "policy_rate", cur_pub="P2")
        pubs = {"P1": mk_pub("P1", datetime(2026, 1, 15)), "P2": mk_pub("P2", datetime(2026, 3, 15))}
        reaction = run([cond, pol], pubs).reactions[0]
        assert "hawkish" not in reaction.formulation.lower()
        assert "dovish" not in reaction.formulation.lower()
        assert "tighten" not in reaction.formulation.lower()
        assert "ease" not in reaction.formulation.lower()

    def test_empty_input(self):
        res = run([], {})
        assert res.reactions == []
        assert res.warnings == []


# ---------------------------------------------------------------------------
# golden / adversarial fixtures (8 documented scenarios)
# ---------------------------------------------------------------------------
class TestGoldenFixtures:
    """Eight documented scenarios with explicitly expected outcomes.

    Each scenario asserts the *exact* set of reactions produced from a given
    combination of condition-side and reaction-side changes.
    """

    def scenario_pubs(self, dates: dict[str, datetime], *, bank=BANK) -> dict[str, Publication]:
        return {pid: mk_pub(pid, dt, bank=bank) for pid, dt in dates.items()}

    def test_scenario_1_standard_reaction(self):
        # inflation change (P2) then policy_rate change (P3) → one reaction.
        cond = mk_change("c1", "inflation", cur_pub="P2")
        pol = mk_change("p1", "policy_rate", cur_pub="P3")
        pubs = self.scenario_pubs({
            "P1": datetime(2026, 1, 15),
            "P2": datetime(2026, 3, 15),
            "P3": datetime(2026, 5, 15),
        })
        res = run([cond, pol], pubs)
        assert len(res.reactions) == 1
        r = res.reactions[0]
        assert (r.condition_change_id, r.policy_change_id) == ("c1", "p1")
        assert r.lag_days == 61  # Mar 15 → May 15

    def test_scenario_2_same_meeting(self):
        # condition and policy both observed at the same meeting → lag 0.
        cond = mk_change("c1", "inflation", cur_pub="P2")
        pol = mk_change("p1", "policy_rate", cur_pub="P2")
        pubs = self.scenario_pubs({
            "P1": datetime(2026, 1, 15),
            "P2": datetime(2026, 3, 15),
        })
        res = run([cond, pol], pubs)
        assert len(res.reactions) == 1
        assert res.reactions[0].lag_days == 0

    def test_scenario_3_condition_chain_all_react_to_one_policy(self):
        # two consecutive inflation changes, each followed by the policy change.
        c1 = mk_change("c1", "inflation", cur_pub="P2")
        c2 = mk_change("c2", "inflation", cur_pub="P3")
        pol = mk_change("p1", "policy_rate", cur_pub="P3")
        pubs = self.scenario_pubs({
            "P1": datetime(2026, 1, 15),
            "P2": datetime(2026, 3, 15),
            "P3": datetime(2026, 5, 15),
        })
        res = run([c1, c2, pol], pubs)
        # c2 (obs P3) pairs with pol (obs P3, lag 0); c1 (obs P2) also pairs
        # with pol (obs P3, lag 61) — both are eligible.
        assert len(res.reactions) == 2
        assert {r.condition_change_id for r in res.reactions} == {"c1", "c2"}
        assert {r.policy_change_id for r in res.reactions} == {"p1"}

    def test_scenario_4_multiple_conditions_one_response(self):
        inf = mk_change("c1", "inflation", cur_pub="P2")
        gdp = mk_change("c2", "gdp", cur_pub="P2")
        pol = mk_change("p1", "policy_rate", cur_pub="P3")
        pubs = self.scenario_pubs({
            "P1": datetime(2026, 1, 15),
            "P2": datetime(2026, 3, 15),
            "P3": datetime(2026, 5, 15),
        })
        res = run([inf, gdp, pol], pubs)
        assert len(res.reactions) == 2
        assert {r.condition_subject for r in res.reactions} == {"inflation", "gdp"}

    def test_scenario_5_one_condition_multiple_responses(self):
        cond = mk_change("c1", "inflation", cur_pub="P2")
        rate = mk_change("p1", "policy_rate", cur_pub="P3")
        guid = mk_change("p2", "policy_guidance", cur_pub="P3")
        pubs = self.scenario_pubs({
            "P1": datetime(2026, 1, 15),
            "P2": datetime(2026, 3, 15),
            "P3": datetime(2026, 5, 15),
        })
        res = run([cond, rate, guid], pubs)
        assert len(res.reactions) == 2
        assert {r.policy_subject for r in res.reactions} == {"policy_rate", "policy_guidance"}

    def test_scenario_6_window_boundary_and_exclusion(self):
        base = datetime(2026, 1, 1)
        # inside window (180 days) → reaction
        c_in = mk_change("c1", "inflation", cur_pub="C_IN")
        p_in = mk_change("p1", "policy_rate", cur_pub="P_IN")
        pubs_in = {
            "C_IN": mk_pub("C_IN", base),
            "P_IN": mk_pub("P_IN", base + timedelta(days=180)),
        }
        assert len(run([c_in, p_in], pubs_in).reactions) == 1
        # outside window (181 days) → no reaction
        c_out = mk_change("c2", "inflation", cur_pub="C_OUT")
        p_out = mk_change("p2", "policy_rate", cur_pub="P_OUT")
        pubs_out = {
            "C_OUT": mk_pub("C_OUT", base),
            "P_OUT": mk_pub("P_OUT", base + timedelta(days=181)),
        }
        assert run([c_out, p_out], pubs_out).reactions == []

    def test_scenario_7_no_look_ahead_rejected(self):
        # policy observed before the condition → never paired (adversarial).
        cond = mk_change("c1", "inflation", cur_pub="P3")
        pol = mk_change("p1", "policy_rate", cur_pub="P2")
        pubs = self.scenario_pubs({
            "P1": datetime(2026, 1, 15),
            "P2": datetime(2026, 3, 15),
            "P3": datetime(2026, 5, 15),
        })
        assert run([cond, pol], pubs).reactions == []
        # and the mirror direction works: condition first, policy later.
        cond = mk_change("c1", "inflation", cur_pub="P2")
        pol = mk_change("p1", "policy_rate", cur_pub="P3")
        assert len(run([cond, pol], pubs).reactions) == 1

    def test_scenario_8_irrelevant_subjects_never_pair(self):
        # non-vocabulary changes are silently irrelevant.
        fx = mk_change("x1", "fx_rate", cur_pub="P2")
        vol = mk_change("x2", "volatility", cur_pub="P3")
        pubs = self.scenario_pubs({
            "P1": datetime(2026, 1, 15),
            "P2": datetime(2026, 3, 15),
            "P3": datetime(2026, 5, 15),
        })
        assert run([fx, vol], pubs).reactions == []
        assert run([fx, vol], pubs).warnings == []


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

    def _mk_fact(self, pub_id: str, subject: str, value, *, bank: str = BANK):
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

    def _seed_inflation_policy(self, store: Store) -> None:
        # P1 (Jan): inflation 2.1, rate 4.00 | P2 (Mar): inflation 2.4, rate 4.25
        # P3 (May): inflation 2.7, rate 4.50
        for pid, date, meeting in (
            ("P1", datetime(2026, 1, 15), datetime(2026, 1, 15)),
            ("P2", datetime(2026, 3, 15), datetime(2026, 3, 15)),
            ("P3", datetime(2026, 5, 15), datetime(2026, 5, 15)),
        ):
            pub = mk_pub(pid, date, meeting_date=meeting)
            store.upsert_publication(pub)
            self._classify(store, pid)
            store.save_fact(self._mk_fact(pid, "inflation", percentage({1: 2.1, 2: 2.4, 3: 2.7}[int(pid[1])])))
            store.save_fact(self._mk_fact(pid, "policy_rate", percentage({1: 4.00, 2: 4.25, 3: 4.50}[int(pid[1])])))
        from argus.changes import analyze_changes

        analyze_changes(store, bank=BANK)

    def test_analyze_reactions_persists(self):
        store = self._store()
        self._seed_inflation_policy(store)
        result = analyze_reactions(store, bank=BANK)
        # eligible pairs: i1(obs Mar)→r1(obs Mar), i1(obs Mar)→r2(obs May),
        # i2(obs May)→r2(obs May); i2→r1 is a look-ahead and is rejected.
        assert len(result.reactions) == 3
        assert len(store.get_reactions()) == 3

    def test_idempotent_rebuild(self):
        store = self._store()
        self._seed_inflation_policy(store)
        first = analyze_reactions(store, bank=BANK)
        ids1 = [r.reaction_id for r in first.reactions]
        second = analyze_reactions(store, bank=BANK)
        assert len(store.get_reactions()) == 3
        assert [r.reaction_id for r in second.reactions] == ids1

    def test_first_and_second_run_no_duplicates(self):
        store = self._store()
        self._seed_inflation_policy(store)
        analyze_reactions(store, bank=BANK)
        analyze_reactions(store, bank=BANK)
        rows = store.get_reactions()
        ids = [r.reaction_id for r in rows]
        assert len(ids) == len(set(ids)) == 3

    def test_rebuild_twice_same_state(self):
        store = self._store()
        self._seed_inflation_policy(store)
        first = analyze_reactions(store, bank=BANK, persist=False).reactions
        store.rebuild_reactions(first, bank=BANK)
        after = store.get_reactions()
        assert {r.reaction_id for r in after} == {r.reaction_id for r in first}
        assert len(after) == len(first) == 3

    def test_empty_result_clears_scope(self):
        store = self._store()
        self._seed_inflation_policy(store)
        analyze_reactions(store, bank=BANK)
        assert len(store.get_reactions()) == 3
        store.rebuild_reactions([], bank=BANK)
        assert store.get_reactions() == []

    def test_analyze_reactions_empty_store(self):
        store = self._store()
        result = analyze_reactions(store, bank=BANK)
        assert result.reactions == []
        assert result.warnings == []
        assert store.get_reactions() == []

    def test_bank_isolation(self):
        store = self._store()
        self._seed_inflation_policy(store)
        # seed a fed bank with its own inflation + policy_rate series
        for pid, date, meeting, inf, rate in (
            ("F1", datetime(2026, 1, 15), datetime(2026, 1, 15), 2.0, 5.00),
            ("F2", datetime(2026, 3, 15), datetime(2026, 3, 15), 2.2, 5.25),
        ):
            pub = mk_pub(pid, date, meeting_date=meeting, bank="fed")
            store.upsert_publication(pub)
            self._classify(store, pid, bank="fed")
            store.save_fact(self._mk_fact(pid, "inflation", percentage(inf), bank="fed"))
            store.save_fact(self._mk_fact(pid, "policy_rate", percentage(rate), bank="fed"))
        from argus.changes import analyze_changes

        analyze_changes(store, bank="fed")
        analyze_reactions(store, bank="fed")
        fed_before = {r.reaction_id for r in store.get_reactions(bank="fed")}
        assert len(fed_before) >= 1
        # rebuild ecb only; fed untouched
        analyze_reactions(store, bank=BANK)
        assert {r.reaction_id for r in store.get_reactions(bank="fed")} == fed_before
        assert len(store.get_reactions(bank=BANK)) == 3

    def test_get_reactions_filters(self):
        store = self._store()
        self._seed_inflation_policy(store)
        analyze_reactions(store, bank=BANK)
        assert len(store.get_reactions(subject="inflation")) == 3  # condition side
        assert len(store.get_reactions(subject="policy_rate")) == 3  # policy side
        reaction = store.get_reactions()[0]
        assert len(store.get_reactions(condition_change_id=reaction.condition_change_id)) >= 1
        assert len(store.get_reactions(policy_change_id=reaction.policy_change_id)) >= 1
        assert len(store.get_reactions(limit=2)) == 2
        assert len(store.get_reactions(limit=0)) == 0

    def test_delete_reactions(self):
        store = self._store()
        self._seed_inflation_policy(store)
        analyze_reactions(store, bank=BANK)
        assert store.delete_reactions(bank=BANK) == 3
        assert store.get_reactions() == []

    def test_delete_reactions_for_document(self):
        store = self._store()
        self._seed_inflation_policy(store)
        analyze_reactions(store, bank=BANK)
        # P2 is the current-side document of the P1→P2 changes (i1, r1). The
        # reaction (i2, r2) — both sides current on P3 — is untouched.
        assert store.delete_reactions_for_document("P2") == 2
        remaining = store.get_reactions()
        assert len(remaining) == 1
        assert remaining[0].condition_document_id == "P3"
        assert remaining[0].policy_document_id == "P3"
        # deleting P3 now clears the store
        assert store.delete_reactions_for_document("P3") == 1
        assert store.get_reactions() == []

    def test_delete_reactions_for_publication(self):
        store = self._store()
        self._seed_inflation_policy(store)
        analyze_reactions(store, bank=BANK)
        # P1 is never a current-side publication of any change → never in a reaction.
        assert store.delete_reactions_for_publication("P1") == 0
        # P2 is the current-side publication of the P1→P2 changes: two reactions.
        assert store.delete_reactions_for_publication("P2") == 2
        remaining = store.get_reactions()
        assert len(remaining) == 1
        assert remaining[0].condition_publication_id == "P3"
        assert remaining[0].policy_publication_id == "P3"
        assert store.delete_reactions_for_publication("P3") == 1
        assert store.get_reactions() == []

    def test_save_reaction_preserves_created_at(self):
        store = self._store()
        cond = mk_change("c1", "inflation", cur_pub="P2")
        pol = mk_change("p1", "policy_rate", cur_pub="P2")
        pubs = {"P1": mk_pub("P1", datetime(2026, 1, 15)), "P2": mk_pub("P2", datetime(2026, 3, 15))}
        reaction = run([cond, pol], pubs).reactions[0]
        reaction.analyzed_at = datetime(2026, 1, 1)
        store.save_reaction(reaction)
        reaction_id = reaction.reaction_id
        row = store._conn.execute(
            "SELECT created_at FROM policy_reactions WHERE reaction_id = ?", (reaction_id,)
        ).fetchone()
        first_created = row["created_at"]
        reaction.analyzed_at = datetime(2026, 2, 1)
        store.save_reaction(reaction)
        row = store._conn.execute(
            "SELECT created_at, updated_at FROM policy_reactions WHERE reaction_id = ?", (reaction_id,)
        ).fetchone()
        assert row["created_at"] == first_created  # upsert preserves created_at
        assert row["updated_at"] is not None

    def test_analyze_reactions_persist_false(self):
        store = self._store()
        self._seed_inflation_policy(store)
        result = analyze_reactions(store, bank=BANK, persist=False)
        assert len(result.reactions) == 3
        assert store.get_reactions() == []

    def test_reaction_traces_to_sources(self):
        store = self._store()
        self._seed_inflation_policy(store)
        result = analyze_reactions(store, bank=BANK)
        reaction = next(r for r in result.reactions if r.condition_subject == "inflation")
        cond_change = store.get_change(reaction.condition_change_id)
        pol_change = store.get_change(reaction.policy_change_id)
        assert cond_change is not None and pol_change is not None
        # reaction → condition change → current fact → publication
        cond_fact = store.get_fact(cond_change.current_fact_id)
        assert cond_fact.publication_id == cond_change.current_publication_id == reaction.condition_publication_id
        cond_pub = store.get_publication(reaction.condition_publication_id)
        assert cond_pub.central_bank == "ecb"
        assert cond_pub.publication_type == DECISION
        # reaction → policy change → current fact → publication
        pol_fact = store.get_fact(pol_change.current_fact_id)
        assert pol_fact.publication_id == pol_change.current_publication_id == reaction.policy_publication_id
        pol_pub = store.get_publication(reaction.policy_publication_id)
        assert pol_pub.central_bank == "ecb"
        assert pol_pub.publication_type == DECISION
        # reaction id derivable from the two change ids + bank
        assert reaction.reaction_id == reaction_id_of(
            central_bank="ecb",
            condition_change_id=cond_change.change_id,
            policy_change_id=pol_change.change_id,
        )

    def test_phase5_12_untouched(self):
        # reactions never modify facts or fact_changes.
        store = self._store()
        self._seed_inflation_policy(store)
        facts_before = {(f.fact_id, f.subject, f.publication_id) for f in store.get_facts()}
        changes_before = {(c.change_id, c.subject) for c in store.get_changes()}
        analyze_reactions(store, bank=BANK)
        facts_after = {(f.fact_id, f.subject, f.publication_id) for f in store.get_facts()}
        changes_after = {(c.change_id, c.subject) for c in store.get_changes()}
        assert facts_before == facts_after
        assert changes_before == changes_after