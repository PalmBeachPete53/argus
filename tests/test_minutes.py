"""Phase 8 — ECB Minutes / Meeting Account extractor: end-to-end tests using
the local HTML fixtures and the existing Store (vertical slice).

Covers: classification gating (``minutes`` and ``meeting_account``), section
routing (known economic headings mined, unknown and known non-economic headings
ignored), content-first categories A-G, quantitative values with periods,
discussion wording handled faithfully (theme-only sentences suppressed, explicit
content mined), attribution traced without inventing speakers or vote counts,
no invented values / interpretations, deterministic extraction, idempotent and
empty-result persistence, and Phase 5/6/7 coexistence.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from argus.classification.base import Confidence
from argus.decisions import extract_decision
from argus.documents import Normalizer
from argus.documents.base import DocumentSection, NormalizedDocument
from argus.facts import ExtractionResult, LocationKind, ValueKind
from argus.models import Document, DocumentStatus, Publication
from argus.minutes import (
    MINUTES_PUBLICATION_TYPES,
    SUBJECT_CORE_INFLATION,
    SUBJECT_FINANCIAL_CONDITIONS,
    SUBJECT_GDP,
    SUBJECT_GROWTH,
    SUBJECT_GROWTH_RISK,
    SUBJECT_INFLATION,
    SUBJECT_INFLATION_EXPECTATIONS,
    SUBJECT_INFLATION_RISK,
    SUBJECT_LABOUR_MARKET,
    SUBJECT_MONETARY_POLICY,
    SUBJECT_POLICY_GUIDANCE,
    SUBJECT_RISK,
    SUBJECT_UNEMPLOYMENT,
    SUBJECT_WAGES,
    EcbMinutesExtractor,
    MinutesExtractor,
    extract_minutes,
    extract_minutes_batch,
)
from argus.minutes.ecb import (
    CAT_FINANCIAL,
    CAT_GENERAL,
    CAT_GROWTH,
    CAT_IGNORE,
    CAT_INFLATION,
    CAT_LABOUR,
    CAT_POLICY,
    CAT_RISK,
    _section_category,
)
from argus.press_conferences import extract_press_conference
from argus.statements import extract_statement
from argus.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "documents"
ECB_URL = "https://www.ecb.europa.eu/press/accounts/2026/html/ecb.acc260723.en.html"


def minutes_publication(**kw) -> Publication:
    fields = dict(
        central_bank="ecb",
        title="Account of the monetary policy meeting",
        url=ECB_URL,
        source_id="ecb-accounts",
        source_url="https://www.ecb.europa.eu/press/accounts/html/feed.xml",
        id="pub-ecb-accounts",
    )
    fields.update(kw)
    return Publication(**fields)


def normalized_fixture(name: str) -> object:
    doc = Document(
        publication_id="pub-ecb-accounts",
        url=ECB_URL,
        kind="html",
        status=DocumentStatus.FETCHED,
        local_path=str(FIXTURES / name),
    )
    return Normalizer().parse(doc)


def extract_fixture(name: str):
    return EcbMinutesExtractor().extract(minutes_publication(), normalized_fixture(name))


def fact_by(result, subject: str, predicate: str):
    matches = [f for f in result.facts if f.subject == subject and f.predicate == predicate]
    assert len(matches) == 1, f"{subject}/{predicate}: {len(matches)} facts"
    return matches[0]


def period_of(fact) -> str | None:
    if fact.period is None:
        return None
    kind = fact.period.kind
    kind_str = kind.value if hasattr(kind, "value") else kind  # persisted rows keep a plain string
    return f"{kind_str}:{fact.period.value}"


# ---------------------------------------------------------------------------
# golden facts across all ECB minutes fixtures
# ---------------------------------------------------------------------------

GOLDEN = {
    "ecb_minutes.html": {
        "warnings": [],
        "count": 19,
        "subjects": {
            SUBJECT_MONETARY_POLICY: {
                "texts": [
                    "Members agreed that the monetary policy stance was appropriately calibrated and that the current level of interest rates remained restrictive.",
                    "The Governing Council decided to keep the key ECB interest rates unchanged.",
                    "Some members would have preferred a more accommodative stance, but a majority considered the current calibration appropriate.",
                ]
            },
            SUBJECT_POLICY_GUIDANCE: {
                "texts": [
                    "The Council stood ready to adjust all of its instruments within its mandate.",
                    "The Governing Council confirmed that it would be guided by the incoming data.",
                    "Future policy decisions would depend on the evolution of the inflation outlook.",
                ]
            },
            SUBJECT_GROWTH: {"texts": ["Economic activity continued to expand at a moderate pace."]},
            SUBJECT_GROWTH_RISK: {
                "texts": [
                    "Global economic activity remained resilient, while the outlook for global demand was subject to heightened uncertainty.",
                    "balanced",
                ]
            },
            SUBJECT_GDP: {"values": {1.4: "year:2027"}},
            SUBJECT_LABOUR_MARKET: {"texts": ["The labour market remained resilient."]},
            SUBJECT_UNEMPLOYMENT: {"texts": ["The unemployment rate was expected to decline gradually."]},
            SUBJECT_WAGES: {"values": {3.0: "year:2027"}},
            SUBJECT_INFLATION: {"values": {2.4: "month:2026-06", 2.2: "year:2027"}},
            SUBJECT_CORE_INFLATION: {"texts": ["Core inflation remained elevated but was expected to decline gradually."]},
            SUBJECT_INFLATION_EXPECTATIONS: {"texts": ["Inflation expectations remained well anchored."]},
            SUBJECT_FINANCIAL_CONDITIONS: {
                "texts": ["Financing conditions remained tight, and monetary policy transmission continued to function smoothly."]
            },
            SUBJECT_INFLATION_RISK: {"texts": ["upside"]},
        },
    },
    "ecb_minutes_discussion.html": {
        "warnings": [],
        "count": 6,
        "subjects": {
            SUBJECT_MONETARY_POLICY: {"texts": ["One member dissented from the decision to keep rates unchanged."]},
            SUBJECT_INFLATION: {
                "values": {2.0: "year:2027"},
                "texts": ["Several members observed that the disinflation process was proceeding broadly as expected."],
            },
            SUBJECT_INFLATION_RISK: {"texts": ["balanced", "upside"]},
            SUBJECT_POLICY_GUIDANCE: {"texts": ["The Governing Council confirmed that it would be guided by the incoming data."]},
        },
    },
    "ecb_minutes_unknown.html": {
        "warnings": ["no_risk_assessment", "no_forward_guidance"],
        "count": 0,
        "subjects": {},
    },
    "ecb_minutes_minimal.html": {
        "warnings": ["no_forward_guidance"],
        "count": 1,
        "subjects": {
            SUBJECT_GROWTH_RISK: {"texts": ["balanced"]},
        },
    },
}


def test_golden_facts_across_all_fixtures():
    for name, expected in GOLDEN.items():
        result = extract_fixture(name)
        assert result.warnings == expected["warnings"], (name, result.warnings)
        assert len(result.facts) == expected["count"], name
        present = {f.subject for f in result.facts}
        assert present == set(expected["subjects"]), (name, present)

        for subject, spec in expected["subjects"].items():
            facts = [f for f in result.facts if f.subject == subject]
            value_facts = [f for f in facts if f.predicate == "value"]
            other_facts = [f for f in facts if f.predicate != "value"]
            if "values" in spec:
                got = {(f.value.value, period_of(f)) for f in value_facts}
                assert got == set(spec["values"].items()), (name, subject, got)
            else:
                assert value_facts == [], (name, subject)
            if "texts" in spec:
                assert sorted(f.value.value for f in other_facts) == sorted(spec["texts"]), (name, subject)
            else:
                assert other_facts == [], (name, subject)


# ---------------------------------------------------------------------------
# attribution: traced what the source states, never invented
# ---------------------------------------------------------------------------


def test_speaker_never_invented():
    for name in GOLDEN:
        result = extract_fixture(name)
        assert all(f.speaker is None for f in result.facts), name


def test_attribution_qualifiers():
    result = extract_fixture("ecb_minutes.html")
    by = {f.identity_qualifier: f for f in result.facts}
    assert by["minutes:members:0"].source_text.startswith("Members agreed")
    assert by["minutes:some_members:0"].source_text.startswith("Some members")
    assert by["minutes:council:0"].source_text.startswith("The Governing Council decided")
    assert by["minutes:council:1"].source_text.startswith("The Council stood ready")
    assert by["minutes:council:2"].source_text.startswith("The Governing Council confirmed")
    collectives = [f for f in result.facts if f.identity_qualifier.startswith("minutes:collective:")]
    assert len(collectives) == 14
    assert all(f.identity_qualifier.startswith("minutes:") for f in result.facts)


def test_dissent_is_traced_but_never_becomes_a_vote():
    result = extract_fixture("ecb_minutes_discussion.html")
    dissents = [f for f in result.facts if f.identity_qualifier.startswith("minutes:dissent:")]
    assert len(dissents) == 1
    dissent = dissents[0]
    assert dissent.subject == SUBJECT_MONETARY_POLICY
    assert dissent.source_text == "One member dissented from the decision to keep rates unchanged."
    assert dissent.speaker is None  # the dissenting member is never named
    # no invented vote count, no vote subject
    assert not any(f.subject == "vote" for f in result.facts)


def test_some_members_attribution():
    result = extract_fixture("ecb_minutes_discussion.html")
    some = [f for f in result.facts if f.identity_qualifier.startswith("minutes:some_members:")]
    assert len(some) == 3  # disinflation, two-sided risk, upside risk
    assert all(f.subject in (SUBJECT_INFLATION, SUBJECT_INFLATION_RISK) for f in some)
    assert {f.source_text for f in some} == {
        "Several members observed that the disinflation process was proceeding broadly as expected.",
        "Some members considered the risks to the inflation outlook to be two-sided.",
        "A few members saw the risks to inflation as slightly tilted to the upside.",
    }
    # a group position is never recast as a collective or an individual one
    assert not any(f.identity_qualifier.startswith("minutes:collective:") for f in some)


def test_one_member_attribution():
    # D8-3: "one member" / "a single member" is its own attribution bucket.
    for sentence in (
        "One member argued that inflation would remain elevated.",
        "A single member saw the risks to inflation as tilted to the upside.",
    ):
        doc = _one_section_doc("Economic analysis", sentence, document_id=f"sha-{sentence[:12]}")
        result = EcbMinutesExtractor().extract(minutes_publication(), doc)
        assert len(result.facts) == 1, sentence
        fact = result.facts[0]
        assert fact.identity_qualifier.startswith("minutes:one_member:"), fact.identity_qualifier
        assert fact.speaker is None  # the member is never named


def test_voted_against_is_traced_as_dissent():
    # D8-3: "voted against" is a dissent marker (precedence over one_member)
    # and never becomes a vote count or a vote subject.
    doc = _one_section_doc(
        "Monetary policy stance and policy considerations",
        "One member voted against the decision to raise rates.",
    )
    result = EcbMinutesExtractor().extract(minutes_publication(), doc)
    assert len(result.facts) == 1
    fact = result.facts[0]
    assert fact.identity_qualifier.startswith("minutes:dissent:")
    assert not any(f.subject == "vote" for f in result.facts)


# ---------------------------------------------------------------------------
# discussion wording: theme-only sentences suppressed, content mined
# ---------------------------------------------------------------------------


def _one_section_doc(heading: str, text: str, *, document_id: str = "sha-route") -> NormalizedDocument:
    return NormalizedDocument(
        publication_id="pub-ecb-accounts",
        document_id=document_id,
        source_url=ECB_URL,
        local_path=None,
        document_kind="html",
        sections=[DocumentSection(order=0, heading=heading, text=text)],
    )


def test_discussion_theme_only_sentences_are_suppressed():
    for sentence in (
        "The discussion focused on inflation.",
        "The discussion centred on the outlook for prices.",
        "The topic of the discussion was monetary policy.",
        "The subject of the discussion was the recent inflation data.",
        "Members discussed the possibility of further rate adjustments.",
        "Members discussed the implications for the inflation outlook.",
        "Members discussed the prospects for economic activity.",
    ):
        doc = _one_section_doc(
            "Monetary policy stance and policy considerations",
            sentence,
            document_id=f"sha-{sentence[:12]}",
        )
        result = EcbMinutesExtractor().extract(minutes_publication(), doc)
        assert result.facts == [], sentence


def test_discussion_sentence_with_explicit_content_is_mined():
    doc = _one_section_doc("Economic analysis", "Members noted that inflation was expected to average 2.0% in 2027.")
    result = EcbMinutesExtractor().extract(minutes_publication(), doc)
    assert len(result.facts) == 1
    fact = result.facts[0]
    assert fact.subject == SUBJECT_INFLATION
    assert fact.predicate == "value"
    assert fact.value.value == 2.0
    assert period_of(fact) == "year:2027"
    assert fact.identity_qualifier == "minutes:members:0"
    assert fact.source_text == "Members noted that inflation was expected to average 2.0% in 2027."
    assert fact.source_location.section == 0
    assert fact.speaker is None


def test_discussion_wording_in_full_fixture():
    result = extract_fixture("ecb_minutes_discussion.html")
    # the theme-only sentences are suppressed, never mined
    assert not any("discussed the possibility" in f.source_text for f in result.facts)
    assert not any("topic of the discussion" in f.source_text for f in result.facts)
    # the explicit-content discussion sentences are mined with their attribution
    assert any("Several members observed" in f.source_text for f in result.facts)
    assert any("One member dissented" in f.source_text for f in result.facts)


# ---------------------------------------------------------------------------
# section routing: known economic headings mined, unknown/non-economic ignored
# ---------------------------------------------------------------------------


def test_known_economic_heading_is_mined():
    doc = _one_section_doc("Risk assessment", "Risks to economic growth were broadly balanced.")
    result = EcbMinutesExtractor().extract(minutes_publication(), doc)
    assert len(result.facts) == 1
    assert result.facts[0].subject == SUBJECT_GROWTH_RISK


def test_risk_near_misses_never_mined_even_in_risk_section():
    # X-2: "risky", "risk-free", "riskiness" carry the prefix "risk" but are
    # never risk anchors — precision first, even inside a risk section.
    for sentence in ("The approach was risky.", "The strategy is risk-free.", "There is riskiness in the plan."):
        doc = _one_section_doc("Risk assessment", sentence, document_id=f"sha-{sentence[:12]}")
        result = EcbMinutesExtractor().extract(minutes_publication(), doc)
        assert result.facts == [], sentence


def test_recognized_heading_variants_are_mined():
    for heading in (
        "Monetary policy stance and policy considerations",
        "Economic analysis",
        "External environment",
        "Real economy",
        "Prices and costs",
        "Money, credit and financial conditions",
        "Risk assessment",
        "Policy conclusions",
    ):
        doc = _one_section_doc(heading, "Risks to economic growth were broadly balanced.", document_id=f"sha-{heading}")
        result = EcbMinutesExtractor().extract(minutes_publication(), doc)
        assert len(result.facts) == 1, heading


def test_non_economic_near_miss_headings_are_never_mined():
    # D8-1: the bare "economic" marker is gone from the general-heading set.
    # "Non-economic developments" shares the word "economic" with the mined
    # headings but must route to IGNORE — even when the content is economic.
    for heading, text in (
        ("Non-economic developments", "Inflation is projected to average 2.2% in 2027."),
        ("Economic", "Risks to economic growth were broadly balanced."),
        ("External economic environment", "Risks to economic growth were broadly balanced."),
        ("Economic outlook beyond the euro area", "Inflation is projected to average 2.2% in 2027."),
    ):
        doc = _one_section_doc(heading, text, document_id=f"sha-{heading}")
        result = EcbMinutesExtractor().extract(minutes_publication(), doc)
        assert result.facts == [], heading


def test_heading_normalization_controls_case_numbering_punctuation_and_the():
    for heading in (
        "1. Risk assessment",
        "Risk Assessment.",
        "The Risk Assessment",
        "2 Economic Analysis (1)",
        "Prices and Costs",
    ):
        doc = _one_section_doc(heading, "Risks to economic growth were broadly balanced.", document_id=f"sha-{heading}")
        result = EcbMinutesExtractor().extract(minutes_publication(), doc)
        assert len(result.facts) == 1, heading


def test_heading_routing_is_exact_identity_not_substring():
    # "Risk" alone is a known heading; "Risk management" is not. The substring
    # coincidence must never route the near-miss heading to a mined category.
    doc = _one_section_doc("Risk management", "Risks to economic growth were broadly balanced.")
    result = EcbMinutesExtractor().extract(minutes_publication(), doc)
    assert result.facts == []


# ---------------------------------------------------------------------------
# Phase 8 corrective — controlled IGNORE routing (no substring matching)
# ---------------------------------------------------------------------------


def test_ignore_heading_exact_identity_is_ignored():
    # A. Every contractual exact IGNORE heading stays ignored, even when the
    # content under it is economic.
    for heading in (
        "Legal notice",
        "Statistical annex",
        "Copyright",
        "Imprint",
        "Disclaimer",
        "External monetary policy",
    ):
        assert _section_category(heading) == CAT_IGNORE, heading
        doc = _one_section_doc(heading, "Inflation is projected to average 2.2% in 2027.")
        result = EcbMinutesExtractor().extract(minutes_publication(), doc)
        assert result.facts == [], heading


def test_ignore_heading_near_misses_never_mined_and_never_economic():
    # B. A heading that merely CONTAINS a known non-economic phrase is not that
    # heading — there is no substring routing. It is an unknown heading: never
    # mined, never silently read as ECONOMIC.
    for heading in (
        "External monetary policy developments",
        "Statistical annexes",
        "Copyright notice",
        "Disclaimer and legal notice",
        "Minutes of something",
    ):
        doc = _one_section_doc(heading, "Risks to economic growth were broadly balanced.")
        result = EcbMinutesExtractor().extract(minutes_publication(), doc)
        assert result.facts == [], heading


def test_ignore_heading_title_families_are_still_ignored():
    # E. The explicit title-style families stay supported: the dated
    # meeting-account title and the "Minutes of …" forms (numbering, "the",
    # meeting name, date) are all ignored, never mined.
    for heading in (
        "Account of the monetary policy meeting",
        "Account of the monetary policy meeting of the Governing Council held on 23 July 2026",
        "Minutes of the Governing Council",
        "Minutes of the Governing Council meeting held on 23 July 2026",
        "The Minutes of the Governing Council",
        "1. Minutes of the Governing Council",
    ):
        doc = _one_section_doc(heading, "Inflation is projected to average 2.2% in 2027.")
        result = EcbMinutesExtractor().extract(minutes_publication(), doc)
        assert result.facts == [], heading


def test_near_miss_headings_route_to_ignore_not_economic():
    # C/D. Headings that share a word with a mined category or a known
    # non-economic phrase ("economic", "monetary policy", "risk", "statistical
    # annex", "copyright") but are not an exact controlled heading route to
    # IGNORE — never to a mined category (UNKNOWN ≠ ECONOMIC). "Non-economic
    # developments" triggers no economic extraction.
    for heading in (
        "Non-economic developments",
        "External economic environment",
        "Economic outlook beyond the euro area",
        "Economic",
        "External monetary policy developments",
        "Risk management",
        "Statistical annexes",
        "Copyright notice",
        "Minutes of the Governing Council",  # explicit "minutes of …" family
        "Account of the monetary policy meeting of the Governing Council held on 23 July 2026",  # title family
    ):
        assert _section_category(heading) == CAT_IGNORE, heading


def test_economic_heading_never_collides_with_an_ignore_heading():
    # Precision guarantee: no controlled economic heading may ever route to
    # IGNORE — the exact IGNORE set and the title-family prefixes are disjoint
    # from every mined category set.
    mined = {
        "monetary policy stance",
        "policy considerations",
        "policy conclusions",
        "monetary policy",
        "monetary policy stance and policy considerations",
        "risk assessment",
        "risks",
        "risk",
        "prices and costs",
        "price developments",
        "inflation",
        "real economy",
        "economic activity",
        "growth",
        "labour market",
        "employment",
        "money, credit and financial conditions",
        "financial conditions",
        "monetary and financial",
        "economic analysis",
        "external environment",
    }
    for heading in mined:
        assert _section_category(heading) != CAT_IGNORE, heading


def test_section_routing_contract_is_deterministic():
    # F. Same heading -> same category on every call: the routing table is a
    # pure function of the cleaned heading.
    probes = (
        "Risk assessment",
        "Non-economic developments",
        "External monetary policy",
        "External monetary policy developments",
        "Minutes of the Governing Council",
        "Monetary policy stance and policy considerations",
        "Additional Information",
    )
    for heading in probes:
        first = _section_category(heading)
        assert all(_section_category(heading) == first for _ in range(10)), heading


def test_unknown_heading_with_economic_content_is_ignored():
    doc = _one_section_doc(
        "Some other heading",
        "Inflation is projected to average 2.2% in 2027. The unemployment rate stood at 6.3%. Risks are broadly balanced.",
    )
    result = EcbMinutesExtractor().extract(minutes_publication(), doc)
    assert result.facts == []
    assert "no_risk_assessment" in result.warnings
    assert "no_forward_guidance" in result.warnings


def test_known_non_economic_headings_are_ignored():
    for heading, text in (
        ("Account of the monetary policy meeting", "Inflation is projected to average 2.2% in 2027."),
        ("Legal notice", "Inflation is projected to average 2.2% in 2027."),
        ("Statistical annex", "Risks are broadly balanced."),
        ("External monetary policy", "The Federal Reserve raised its policy rate by 25 basis points. Risks to global growth are skewed to the downside."),
        ("Minutes of the Governing Council", "Inflation is projected to average 2.2% in 2027."),
    ):
        doc = _one_section_doc(heading, text, document_id=f"sha-{heading}")
        result = EcbMinutesExtractor().extract(minutes_publication(), doc)
        assert result.facts == [], heading


def test_external_monetary_policy_content_never_mined():
    result = extract_fixture("ecb_minutes.html")
    assert not any("Federal Reserve" in f.source_text for f in result.facts)
    assert not any("Bank of England" in f.source_text for f in result.facts)
    assert not any("basis points" in f.source_text for f in result.facts)


def test_unknown_heading_economic_phrases_never_extracted():
    # the audit's flagship case: an unknown section whose sentences WOULD match
    # the economic patterns must still produce 0 facts — UNKNOWN != ECONOMIC
    doc = _one_section_doc("Additional Information", "Inflation is expected to remain elevated.")
    result = EcbMinutesExtractor().extract(minutes_publication(), doc)
    assert result.facts == []
    assert not any(f.subject == SUBJECT_INFLATION for f in result.facts)


def test_empty_heading_section_is_ignored():
    doc = _one_section_doc(
        "",
        "23 July 2026\nInflation is projected to average 2.2% in 2027.",
        document_id="sha-empty-heading",
    )
    result = EcbMinutesExtractor().extract(minutes_publication(), doc)
    assert result.facts == []


def test_empty_document_warns_no_sections():
    doc = NormalizedDocument(
        publication_id="pub-ecb-accounts",
        document_id="sha-empty",
        source_url=ECB_URL,
        local_path=None,
        document_kind="html",
        sections=[],
    )
    result = EcbMinutesExtractor().extract(minutes_publication(), doc)
    assert result.warnings == ["no_sections"]
    assert result.facts == []


# ---------------------------------------------------------------------------
# categories + quantitative values
# ---------------------------------------------------------------------------


def test_quantitative_values_carry_periods():
    result = extract_fixture("ecb_minutes.html")
    inflation_jun = next(f for f in result.facts if f.subject == SUBJECT_INFLATION and period_of(f) == "month:2026-06")
    assert inflation_jun.value.kind is ValueKind.PERCENTAGE
    assert inflation_jun.value.value == 2.4
    assert inflation_jun.value.source_text == "2.4%"
    assert inflation_jun.confidence is Confidence.HIGH
    inflation_2027 = next(f for f in result.facts if f.subject == SUBJECT_INFLATION and period_of(f) == "year:2027")
    assert inflation_2027.period.label == "in 2027"
    gdp = fact_by(result, SUBJECT_GDP, "value")
    assert gdp.value.value == 1.4
    assert period_of(gdp) == "year:2027"


def test_target_phrasing_never_becomes_a_value():
    doc = _one_section_doc("Prices and costs", "Inflation was expected to converge towards 2% over the medium term.")
    result = EcbMinutesExtractor().extract(minutes_publication(), doc)
    assert len(result.facts) == 1
    fact = result.facts[0]
    assert fact.subject == SUBJECT_INFLATION
    assert fact.predicate == "assessment"
    assert fact.value.value == "Inflation was expected to converge towards 2% over the medium term."
    assert not any(f.predicate == "value" and f.value.value == 2.0 for f in result.facts)


def test_risk_orientations_are_categorical_only_when_explicit():
    result = extract_fixture("ecb_minutes.html")
    balanced = next(f for f in result.facts if f.subject == SUBJECT_GROWTH_RISK and f.value.value == "balanced")
    assert balanced.value.kind is ValueKind.CATEGORICAL
    assert balanced.confidence is Confidence.HIGH
    upside = next(f for f in result.facts if f.subject == SUBJECT_INFLATION_RISK and f.value.value == "upside")
    assert upside.value.kind is ValueKind.CATEGORICAL
    # uncertainty without an orientation stays verbatim text — never a direction
    text = next(f for f in result.facts if f.subject == SUBJECT_GROWTH_RISK and f.value.kind is ValueKind.TEXT)
    assert "uncertainty" in text.source_text
    assert text.confidence is Confidence.MEDIUM


# ---------------------------------------------------------------------------
# provenance + no interpretation
# ---------------------------------------------------------------------------


def test_provenance_is_traceable():
    for name in GOLDEN:
        document = normalized_fixture(name)
        result = extract_fixture(name)
        for fact in result.facts:
            assert fact.extraction_version == EcbMinutesExtractor.extraction_version
            assert fact.extraction_method
            assert fact.source_location is not None
            assert fact.source_location.kind is LocationKind.SECTION
            assert fact.source_text
            assert fact.publication_id == "pub-ecb-accounts"
            assert fact.document_id
            assert fact.effective_date is None
            section_text = document.sections[fact.source_location.section].text or ""
            assert fact.source_text in section_text, (name, fact.subject, fact.predicate)
            assert fact.value.source_text in section_text, (name, fact.subject, fact.predicate)


def test_no_hawkish_dovish_or_forex_interpretation():
    for name in GOLDEN:
        result = extract_fixture(name)
        for fact in result.facts:
            raw = str(fact.value.value or "").lower()
            assert "hawkish" not in raw
            assert "dovish" not in raw
            assert "bullish" not in raw and "bearish" not in raw
            assert "forex" not in raw and "eur/usd" not in raw
            assert "stance" not in fact.predicate
            assert fact.predicate not in ("sentiment", "market_reaction")


def test_no_decision_or_rationale_facts_from_minutes():
    result = extract_fixture("ecb_minutes.html")
    phase5_subjects = {
        "monetary_policy_decision", "main_refinancing_rate", "marginal_lending_rate",
        "deposit_facility_rate", "asset_purchase", "vote",
    }
    assert not phase5_subjects & {f.subject for f in result.facts}
    assert not any(f.predicate == "rationale" for f in result.facts)  # Phase 6 rationale stays in the statement
    assert not any(f.predicate == "change" for f in result.facts)
    assert not any(f.predicate == "date" for f in result.facts)
    # the verbatim policy statement of the account is kept, never a rate value
    assert not any(f.subject == "main_refinancing_rate" for f in result.facts)


# ---------------------------------------------------------------------------
# determinism + idempotent persistence (vertical slice)
# ---------------------------------------------------------------------------


def _store_minutes(tmp_path, name: str = "ecb_minutes.html") -> Store:
    store = Store(tmp_path / f"{name}.db")
    store.upsert_publication(minutes_publication())
    store.upsert_normalized_document(normalized_fixture(name))
    return store


def classify_minutes(store: Store, *, publication_type: str = "meeting_account") -> None:
    store.set_classification(
        "pub-ecb-accounts",
        central_bank="ecb",
        publication_type=publication_type,
        confidence=Confidence.HIGH.value,
        method="url_pattern",
        evidence=[],
    )


class _ZeroFactMinutesExtractor(MinutesExtractor):
    """Stub minutes extractor that yields no facts — used to simulate a
    re-extraction of an already-persisted document that now produces nothing."""

    bank = "ecb"
    extraction_version = "test-zero"

    def extract(self, publication, document):
        return ExtractionResult(
            publication_id=publication.id or publication.dedup_key or "",
            document_id=document.document_id,
        )


def test_extract_minutes_persists_facts(tmp_path):
    store = _store_minutes(tmp_path)
    classify_minutes(store)
    results = extract_minutes(store, minutes_publication())
    assert len(results) == 1
    persisted = store.get_facts(publication_id="pub-ecb-accounts")
    assert len(persisted) == 19
    by = {(f.subject, f.predicate) for f in persisted}
    assert (SUBJECT_INFLATION, "value") in by
    assert (SUBJECT_POLICY_GUIDANCE, "statement") in by
    assert (SUBJECT_GROWTH_RISK, "assessment") in by
    inflation = next(f for f in persisted if (f.subject, f.predicate) == (SUBJECT_INFLATION, "value") and period_of(f) == "year:2027")
    assert inflation.value.value == 2.2
    assert inflation.central_bank == "ecb"  # filled from the publication
    assert inflation.period.label == "in 2027"


def test_attribution_qualifier_is_persisted_roundtrip(tmp_path):
    store = _store_minutes(tmp_path, "ecb_minutes.html")
    classify_minutes(store)
    extract_minutes(store, minutes_publication())
    persisted = store.get_facts(publication_id="pub-ecb-accounts")
    qualifiers = {f.identity_qualifier for f in persisted}
    assert "minutes:members:0" in qualifiers
    assert "minutes:some_members:0" in qualifiers
    assert "minutes:council:0" in qualifiers
    assert any(q.startswith("minutes:collective:") for q in qualifiers)
    assert all(f.speaker is None for f in persisted)


def test_extract_minutes_is_idempotent(tmp_path):
    store = _store_minutes(tmp_path)
    classify_minutes(store)
    pub = minutes_publication()
    extract_minutes(store, pub)
    first = sorted((f.fact_id, f.subject, f.predicate, f.value.value) for f in store.get_facts(publication_id="pub-ecb-accounts"))
    extract_minutes(store, pub)  # re-run: same deterministic fact_ids
    second = sorted((f.fact_id, f.subject, f.predicate, f.value.value) for f in store.get_facts(publication_id="pub-ecb-accounts"))
    assert first == second
    assert len(second) == 19


def test_extraction_is_deterministic(tmp_path):
    for name in GOLDEN:
        store = _store_minutes(tmp_path, name)
        result = extract_fixture(name)
        store.rebuild_facts_for_document(result.document_id, result)
        first = sorted((f.fact_id, f.subject, f.predicate, f.value.value) for f in store.get_facts(publication_id="pub-ecb-accounts"))
        store.rebuild_facts_for_document(result.document_id, result)
        second = sorted((f.fact_id, f.subject, f.predicate, f.value.value) for f in store.get_facts(publication_id="pub-ecb-accounts"))
        assert first == second, name
        assert len(first) == len(result.facts), name
        ids = [f.fact_id for f in result.facts]
        assert len(ids) == len(set(ids)), name  # no fact_id collisions


# ---------------------------------------------------------------------------
# classification gating (single source of truth = classifications table)
# ---------------------------------------------------------------------------


def test_gating_meeting_account_classification_allows_extraction(tmp_path):
    store = _store_minutes(tmp_path)
    classify_minutes(store, publication_type="meeting_account")
    results = extract_minutes(store, minutes_publication())
    assert len(results) == 1
    assert len(store.get_facts(publication_id="pub-ecb-accounts")) == 19


def test_gating_minutes_classification_allows_extraction(tmp_path):
    store = _store_minutes(tmp_path)
    classify_minutes(store, publication_type="minutes")
    results = extract_minutes(store, minutes_publication())
    assert len(results) == 1
    assert len(store.get_facts(publication_id="pub-ecb-accounts")) == 19


def test_gating_other_classification_refuses_extraction(tmp_path):
    store = _store_minutes(tmp_path)
    classify_minutes(store, publication_type="press_conference")
    assert extract_minutes(store, minutes_publication()) == []
    assert store.get_facts(publication_id="pub-ecb-accounts") == []


def test_gating_absent_classification_refuses_extraction(tmp_path):
    store = _store_minutes(tmp_path)
    assert extract_minutes(store, minutes_publication()) == []
    assert store.get_facts(publication_id="pub-ecb-accounts") == []


def test_gating_publication_type_cache_alone_never_authorizes(tmp_path):
    store = _store_minutes(tmp_path)
    pub = minutes_publication(publication_type="minutes")
    # the denormalized cache says minutes, but there is no authoritative
    # classification record -> extraction must be refused
    assert extract_minutes(store, pub) == []
    assert store.get_facts(publication_id="pub-ecb-accounts") == []


def test_gating_batch_respects_classification(tmp_path):
    store = _store_minutes(tmp_path)
    assert extract_minutes_batch(store) == []  # unclassified -> nothing extracted
    assert store.get_facts(publication_id="pub-ecb-accounts") == []
    classify_minutes(store)
    results = extract_minutes_batch(store)
    assert len(results) == 1
    assert len(store.get_facts(bank="ecb")) == 19


def test_gating_never_persists_facts_when_not_authorized(tmp_path):
    store = _store_minutes(tmp_path)
    classify_minutes(store, publication_type="monetary_policy_report")
    assert extract_minutes(store, minutes_publication()) == []
    assert extract_minutes_batch(store) == []
    assert store.get_facts(publication_id="pub-ecb-accounts") == []


def test_gating_refusal_never_deletes_existing_facts(tmp_path):
    """A classification that refuses extraction must NOT delete facts that an
    earlier authorized extraction persisted (X-1)."""
    store = _store_minutes(tmp_path)
    classify_minutes(store)
    assert len(extract_minutes(store, minutes_publication())) == 1
    assert len(store.get_facts(publication_id="pub-ecb-accounts")) == 19
    classify_minutes(store, publication_type="press_conference")
    assert extract_minutes(store, minutes_publication()) == []
    assert len(store.get_facts(publication_id="pub-ecb-accounts")) == 19


def test_minutes_publication_types_are_recognized():
    assert MINUTES_PUBLICATION_TYPES == ("minutes", "meeting_account")


# ---------------------------------------------------------------------------
# empty-result persistence: the current extraction result is the source of truth
# ---------------------------------------------------------------------------


def test_empty_result_persistence_clears_stale_facts(tmp_path):
    store = _store_minutes(tmp_path)
    classify_minutes(store)
    pub = minutes_publication()
    extract_minutes(store, pub)
    assert len(store.get_facts(publication_id="pub-ecb-accounts")) == 19
    results = extract_minutes(store, pub, extractor=_ZeroFactMinutesExtractor())
    assert len(results) == 1
    assert results[0].facts == []
    assert store.get_facts(publication_id="pub-ecb-accounts") == []


def test_empty_result_persistence_preserves_other_documents(tmp_path):
    store = _store_minutes(tmp_path)
    classify_minutes(store)
    pub = minutes_publication()
    extract_minutes(store, pub)
    extract_minutes(store, pub, document=normalized_fixture("ecb_minutes_minimal.html"))
    assert len(store.get_facts(publication_id="pub-ecb-accounts")) == 20
    # zero-out only the nominal document; the other document's facts must stay
    extract_minutes(store, pub, document=normalized_fixture("ecb_minutes.html"), extractor=_ZeroFactMinutesExtractor())
    persisted = store.get_facts(publication_id="pub-ecb-accounts")
    assert len(persisted) == 1
    assert persisted[0].subject == SUBJECT_GROWTH_RISK
    assert persisted[0].document_id == normalized_fixture("ecb_minutes_minimal.html").document_id


def test_empty_result_persistence_is_idempotent(tmp_path):
    store = _store_minutes(tmp_path)
    classify_minutes(store)
    pub = minutes_publication()
    extract_minutes(store, pub)
    assert len(store.get_facts(publication_id="pub-ecb-accounts")) == 19
    zero = _ZeroFactMinutesExtractor()
    extract_minutes(store, pub, extractor=zero)
    extract_minutes(store, pub, extractor=zero)
    assert store.get_facts(publication_id="pub-ecb-accounts") == []


# ---------------------------------------------------------------------------
# Phase 5 / 6 / 7 coexistence
# ---------------------------------------------------------------------------


def test_other_extractors_do_not_overlap_with_minutes(tmp_path):
    """A minutes publication never feeds the decision, statement or press
    conference extractors (gating on classification), and Phase 8 never emits
    Phase 5/6 fact subjects."""
    store = _store_minutes(tmp_path)
    pub = minutes_publication()
    store.set_classification(
        "pub-ecb-accounts",
        central_bank="ecb",
        publication_type="meeting_account",
        confidence=Confidence.HIGH.value,
        method="url_pattern",
        evidence=[],
    )
    # store-level helpers are gated on classification
    assert extract_decision(store, pub) == []
    assert extract_statement(store, pub) == []
    assert extract_press_conference(store, pub) == []
    # Phase 8 extraction produces its own facts only
    extract_minutes(store, pub)
    persisted = store.get_facts(publication_id="pub-ecb-accounts")
    phase5_subjects = {
        "monetary_policy_decision", "main_refinancing_rate", "marginal_lending_rate",
        "deposit_facility_rate", "asset_purchase", "vote",
    }
    assert not phase5_subjects & {f.subject for f in persisted}
    assert not any(f.predicate == "rationale" for f in persisted)  # Phase 6 rationale is not a Phase 8 category
    assert not any(f.predicate == "change" for f in persisted)
    assert not any(f.predicate == "date" for f in persisted)
    assert all(f.extraction_version == EcbMinutesExtractor.extraction_version for f in persisted)
    assert all(f.identity_qualifier.startswith("minutes:") for f in persisted)


# ---------------------------------------------------------------------------
# generic dispatch integration tests (Phase 4 hardening)
# ---------------------------------------------------------------------------


def test_get_minutes_extractor_resolves_registered_banks():
    """Verify the generic registry resolves the correct extractor for each bank."""
    from argus.minutes import get_extractor

    expected = {
        "ecb": "EcbMinutesExtractor",
        "fed": "FedMinutesExtractor",
        "boe": "BoeMinutesExtractor",
        "boj": "BojMinutesExtractor",
        "norges": "NorgesMinutesExtractor",
        "riksbank": "RiksbankMinutesExtractor",
    }
    for bank, class_name in expected.items():
        ext = get_extractor(bank)
        assert ext is not None, f"{bank}: extractor not registered"
        assert ext.__class__.__name__ == class_name, f"{bank}: wrong extractor {ext.__class__.__name__}"


MINUTES_FIXTURE_MAP = {
    "ecb": "ecb_minutes.html",
    "fed": "fed_minutes.html",
    "boe": "boe_minutes_full.html",
    "boj": "boj_minutes.html",
    "norges": "norges_minutes.html",
    "riksbank": "riksbank_minutes.html",
}


def _normalized_minutes_fixture(bank: str, name: str):
    from argus.documents import Normalizer
    from argus.models import Document, DocumentStatus
    return Normalizer().parse(
        Document(
            publication_id=f"pub-{bank}-minutes",
            url=f"https://example.com/{bank}/{name}",
            kind="html",
            status=DocumentStatus.FETCHED,
            local_path=str(FIXTURES / name),
        )
    )


def _minutes_publication(bank: str, pub_id: str = None) -> Publication:
    return Publication(
        central_bank=bank,
        title="Minutes of the monetary policy meeting",
        url=f"https://example.com/{bank}/minutes",
        source_id=f"{bank}-minutes",
        source_url=f"https://example.com/{bank}/feed.xml",
        id=pub_id or f"pub-{bank}-minutes",
    )


def _classify_minutes(store: Store, pub_id: str, bank: str, pub_type: str = "meeting_account") -> None:
    store.set_classification(
        pub_id,
        central_bank=bank,
        publication_type=pub_type,
        confidence=Confidence.HIGH.value,
        method="url_pattern",
        evidence=[],
    )


@pytest.mark.parametrize("bank", list(MINUTES_FIXTURE_MAP.keys()))
def test_extract_minutes_generic_dispatch(tmp_path, bank):
    """Test the generic extract_minutes dispatch for each registered bank."""
    store = Store(tmp_path / f"{bank}_minutes.db")
    pub = _minutes_publication(bank)
    store.upsert_publication(pub)
    doc = _normalized_minutes_fixture(bank, MINUTES_FIXTURE_MAP[bank])
    store.upsert_normalized_document(doc)
    pub_type = "meeting_account" if bank == "ecb" else "minutes"
    _classify_minutes(store, pub.id, bank, pub_type)

    results = extract_minutes(store, pub)
    assert len(results) == 1, f"{bank}: expected 1 result"
    result = results[0]
    assert result.publication_id == pub.id
    assert result.document_id == doc.document_id

    # Verify some facts were produced
    assert len(result.facts) > 0, f"{bank}: no facts extracted"

    # Verify provenance is preserved
    for fact in result.facts:
        assert fact.extraction_version
        assert fact.extraction_method
        assert fact.source_location is not None
        assert fact.source_text
        assert fact.confidence is not None
        assert fact.identity_qualifier.startswith("minutes:")


def test_extract_minutes_batch_generic_dispatch(tmp_path):
    """Test extract_minutes_batch runs all classified minutes via generic dispatch."""
    store = Store(tmp_path / "batch_minutes.db")
    for bank in MINUTES_FIXTURE_MAP:
        pub = _minutes_publication(bank, pub_id=f"pub-{bank}-minutes")
        store.upsert_publication(pub)
        doc = _normalized_minutes_fixture(bank, MINUTES_FIXTURE_MAP[bank])
        store.upsert_normalized_document(doc)
        pub_type = "meeting_account" if bank == "ecb" else "minutes"
        _classify_minutes(store, pub.id, bank, pub_type)

    results = extract_minutes_batch(store)
    assert len(results) == len(MINUTES_FIXTURE_MAP)

    for bank in MINUTES_FIXTURE_MAP:
        facts = store.get_facts(publication_id=f"pub-{bank}-minutes")
        assert facts, f"{bank}: no facts persisted"
        assert all(f.identity_qualifier.startswith("minutes:") for f in facts)
