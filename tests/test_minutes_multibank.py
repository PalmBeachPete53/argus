"""Phase 4.4.x — multi-bank Minutes extractors (BoE, BoJ, Norges, RBA, Riksbank):
golden + contract + dispatch + integration tests.

Covers: per-bank extractor identity and registration, golden facts over the new
fixtures, the canonical Fact contract (provenance, `minutes:` qualifier with
attribution, `speaker=None`), deterministic and order-independent extraction,
source immutability, unknown-heading refusal, classification gating via the
`classifications` table, idempotent persistence and empty-result persistence,
and Phase 4.1/6/7 coexistence.

The five banks are the second half of the minutes family: they join ECB/Fed
(so all seven minutes-classified banks have an extractor).
"""

from __future__ import annotations

import copy

import pytest
from pathlib import Path

from argus.classification.base import Confidence
from argus.documents import Normalizer
from argus.documents.base import DocumentSection, NormalizedDocument
from argus.facts import ExtractionResult, LocationKind
from argus.models import Document, DocumentStatus, Publication
from argus.minutes import (
    BoeMinutesExtractor,
    BojMinutesExtractor,
    NorgesMinutesExtractor,
    RbaMinutesExtractor,
    RiksbankMinutesExtractor,
    extract_minutes,
    extract_minutes_batch,
    get_extractor,
)
from argus.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "documents"

BANKS = {
    "boe": BoeMinutesExtractor,
    "boj": BojMinutesExtractor,
    "norges": NorgesMinutesExtractor,
    "rba": RbaMinutesExtractor,
    "riksbank": RiksbankMinutesExtractor,
}
FIXTURE_FILES = {
    "boe": "boe_minutes_full.html",
    "boj": "boj_minutes.html",
    "norges": "norges_minutes.html",
    "rba": "rba_minutes.html",
    "riksbank": "riksbank_minutes.html",
}

# Golden signature: (subject, predicate, value_kind, value, period, attribution)
GOLDEN = {
    "boe": {
        ("growth", "assessment", "text",
         "Surveys suggest that underlying demand growth remains subdued.", None, "collective"),
        ("gdp", "value", "percentage", 0.3, None, "collective"),
        ("gdp", "value", "percentage", 1.1, "year:2027", "some_members"),
        ("inflation", "value", "percentage", 2.9, "year:2026", "some_members"),
        ("wages", "assessment", "text",
         "Wage growth remained elevated in the recent data.", None, "collective"),
        ("unemployment", "value", "percentage", 4.5, None, "committee"),
        ("financial_conditions", "assessment", "text",
         "Financial conditions had eased modestly, and credit spreads had narrowed over the inter-meeting period.",
         None, "collective"),
        ("inflation_risk", "assessment", "categorical", "upside", None, "most_members"),
        ("growth_risk", "assessment", "categorical", "downside", None, "dissent"),
        ("policy_guidance", "statement", "text",
         "The Committee judged that monetary policy needed to remain restrictive for as long as necessary.",
         None, "committee"),
        ("policy_guidance", "statement", "text",
         "The MPC confirmed that future decisions would depend on the incoming data.", None, "committee"),
    },
    "boj": {
        ("growth", "assessment", "text",
         "Japan's economy has recovered moderately, as exports and public investment have increased.",
         None, "collective"),
        ("financial_conditions", "assessment", "text",
         "Financial conditions remained accommodative.", None, "collective"),
        ("growth_risk", "assessment", "categorical", "balanced", None, "most_members"),
        ("inflation_risk", "assessment", "categorical", "downside", None, "dissent"),
        ("policy_guidance", "statement", "text",
         "The Policy Board confirmed that it would continue with monetary easing as long as necessary.",
         None, "committee"),
        ("monetary_policy", "statement", "text",
         "One member judged that the policy rate should be kept at an appropriate accommodative level.",
         None, "one_member"),
    },
    "norges": {
        ("growth", "assessment", "text",
         "Developments in the Norwegian economy are broadly in line with the projections.", None, "collective"),
        ("unemployment", "value", "percentage", 3.6, None, "collective"),
        ("inflation", "assessment", "text",
         "Underlying inflation was estimated at 3.0% in 2026.", None, "collective"),
        ("growth", "assessment", "text",
         "Activity among Norway's trading partners remains moderate.", None, "collective"),
        ("gdp", "value", "percentage", 1.9, "year:2026", "collective"),
        ("monetary_policy", "statement", "text",
         "The Committee assessed that the policy rate should be kept at a restrictive level for longer.",
         None, "committee"),
        ("policy_guidance", "statement", "text",
         "The Committee stood ready to adjust the policy rate if necessary.", None, "committee"),
        ("inflation_risk", "assessment", "categorical", "upside", None, "some_members"),
    },
    "rba": {
        ("financial_conditions", "assessment", "text",
         "Members observed that financial conditions had eased modestly since the previous meeting, and that credit spreads had narrowed.",
         None, "members"),
        ("gdp", "value", "percentage", 0.9, "quarter:2026-Q2", "members"),
        ("unemployment", "value", "percentage", 4.1, "quarter:2026-Q1", "members"),
        ("wages", "assessment", "text",
         "Members observed that wage growth remained elevated.", None, "members"),
        ("monetary_policy", "statement", "text",
         "Members judged that the stance of monetary policy remained appropriate.", None, "members"),
        ("policy_guidance", "statement", "text",
         "The Board agreed that future decisions would depend on the incoming data.", None, "committee"),
        ("inflation_risk", "assessment", "categorical", "upside", None, "most_members"),
        ("growth_risk", "assessment", "categorical", "downside", None, "one_member"),
        ("risk", "assessment", "categorical", "balanced", None, "some_members"),
        ("inflation", "value", "percentage", 2.4, "quarter:2026-Q2", "members"),
        ("inflation_expectations", "assessment", "text",
         "Members noted that inflation expectations remained well anchored.", None, "members"),
    },
    "riksbank": {
        ("growth", "assessment", "text",
         "The Swedish economy is growing at a moderate pace.", None, "collective"),
        ("gdp", "value", "percentage", 1.7, "year:2026", "collective"),
        ("inflation", "value", "percentage", 2.3, "year:2027", "committee"),
        ("financial_conditions", "assessment", "text",
         "Financial conditions have improved, and funding costs have fallen.", None, "collective"),
        ("monetary_policy", "statement", "text",
         "The Executive Board judged that the policy rate would be appropriate to keep at a restrictive level.",
         None, "committee"),
        ("policy_guidance", "statement", "text",
         "The Board agreed that future decisions would depend on the incoming data.", None, "committee"),
        ("inflation_risk", "assessment", "categorical", "balanced", None, "most_members"),
    },
}


def _normalized(bank: str, name: str) -> NormalizedDocument:
    return Normalizer().parse(
        Document(
            publication_id=f"pub-{bank}-minutes",
            url=f"https://example.org/{bank}/{name}",
            kind="html",
            status=DocumentStatus.FETCHED,
            local_path=str(FIXTURES / name),
        )
    )


def _publication(bank: str) -> Publication:
    return Publication(
        central_bank=bank,
        title="Minutes of the monetary policy meeting",
        url=f"https://example.org/{bank}/minutes",
        source_id=f"{bank}-minutes",
        source_url=f"https://example.org/{bank}/feed.xml",
        id=f"pub-{bank}-minutes",
    )


def _signature(fact) -> tuple:
    kind = fact.value.kind.value if hasattr(fact.value.kind, "value") else fact.value.kind
    period = None
    if fact.period:
        pkind = fact.period.kind.value if hasattr(fact.period.kind, "value") else fact.period.kind
        period = f"{pkind}:{fact.period.value}"
    attribution = fact.identity_qualifier.split(":")[1] if fact.identity_qualifier else ""
    return (fact.subject, fact.predicate, kind, fact.value.value, period, attribution)


def _extract(bank: str):
    return get_extractor(bank).extract(_publication(bank), _normalized(bank, FIXTURE_FILES[bank]))


# ---------------------------------------------------------------------------
# extractor identity + registration
# ---------------------------------------------------------------------------
def test_extractor_bank_identity():
    for bank, cls in BANKS.items():
        ext = get_extractor(bank)
        assert ext is not None, f"{bank}: not registered"
        assert ext.__class__ is cls, f"{bank}: wrong extractor"
        assert get_extractor(bank).bank == bank


# ---------------------------------------------------------------------------
# golden facts over the new fixtures
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bank", list(BANKS))
def test_golden_facts(bank):
    result = _extract(bank)
    got = {_signature(f) for f in result.facts}
    assert got == GOLDEN[bank], bank


@pytest.mark.parametrize("bank", list(BANKS))
def test_contract_fields(bank):
    result = _extract(bank)
    assert result.warnings == [], (bank, result.warnings)
    document = _normalized(bank, FIXTURE_FILES[bank])
    section_count = len(document.sections)
    for fact in result.facts:
        assert fact.publication_id == f"pub-{bank}-minutes"
        assert fact.document_id == document.document_id
        assert fact.speaker is None, f"{bank}: speaker must stay None"
        assert fact.identity_qualifier.startswith("minutes:"), f"{bank}: {fact.identity_qualifier}"
        assert fact.extraction_method == "regex"
        assert fact.extraction_version == BANKS[bank].extraction_version
        assert fact.source_location.kind == LocationKind.SECTION
        assert 0 <= fact.source_location.section < section_count, (bank, fact.source_location)
        assert fact.source_text, f"{bank}: empty source_text"
        owning = document.sections[fact.source_location.section].text
        assert fact.source_text in owning or fact.source_text in " ".join(
            s.text for s in document.sections
        ), f"{bank}: source_text not in any section"


@pytest.mark.parametrize("bank", list(BANKS))
def test_value_facts_carry_percentage_kind(bank):
    for fact in _extract(bank).facts:
        if fact.predicate == "value":
            assert fact.value.kind.value == "percentage", fact


# ---------------------------------------------------------------------------
# determinism + order independence + immutability
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bank", list(BANKS))
def test_deterministic_repeated_extraction(bank):
    first = {f.resolve_id() for f in _extract(bank).facts}
    second = {f.resolve_id() for f in _extract(bank).facts}
    assert first == second, bank
    ids = [f.resolve_id() for f in _extract(bank).facts]
    assert len(ids) == len(set(ids)), f"{bank}: fact_id collisions"


@pytest.mark.parametrize("bank", list(BANKS))
def test_order_independence(bank):
    doc = _normalized(bank, FIXTURE_FILES[bank])
    doc.sections = list(reversed(doc.sections))
    before = {_signature(f) for f in _extract(bank).facts}
    after = {_signature(f) for f in get_extractor(bank).extract(_publication(bank), doc).facts}
    assert before == after, bank


@pytest.mark.parametrize("bank", list(BANKS))
def test_source_immutability(bank):
    pub = _publication(bank)
    doc = _normalized(bank, FIXTURE_FILES[bank])
    pub_orig = copy.deepcopy(pub)
    doc_orig = copy.deepcopy(doc)
    get_extractor(bank).extract(pub, doc)
    assert pub == pub_orig and doc == doc_orig, bank


# ---------------------------------------------------------------------------
# unknown / non-economic headings are never mined
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bank", list(BANKS))
def test_unknown_heading_is_never_mined(bank):
    doc = NormalizedDocument(
        publication_id=f"pub-{bank}-minutes",
        document_id="sha-ne",
        source_url=f"https://example.org/{bank}",
        local_path=None,
        document_kind="html",
        sections=[
            DocumentSection(
                order=0,
                heading="An entirely unknown future section",
                text="Inflation is expected to remain elevated for some time amid strong demand growth.",
            ),
            DocumentSection(
                order=1,
                heading="Statistical appendix",
                text="The tables in this annex accompany the minutes.",
            ),
        ],
    )
    result = get_extractor(bank).extract(_publication(bank), doc)
    assert result.facts == [], bank
    assert "no_economic_sections" in result.warnings, bank


# ---------------------------------------------------------------------------
# no downstream semantics, no reporting/decision facts
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bank", list(BANKS))
def test_no_downstream_semantics(bank):
    result = _extract(bank)
    for fact in result.facts:
        assert fact.predicate != "projection"
        assert fact.subject not in {
            "main_refinancing_rate", "deposit_facility_rate", "marginal_lending_rate",
            "monetary_policy_decision", "policy_rate", "asset_purchase",
        }
        if isinstance(fact.value.value, str):
            low = fact.value.value.lower()
            assert "hawkish" not in low
            assert "dovish" not in low


# ---------------------------------------------------------------------------
# classification gating + persistence (vertical slice)
# ---------------------------------------------------------------------------
def _classify(store, pub_id, bank) -> None:
    store.set_classification(
        pub_id,
        central_bank=bank,
        publication_type="minutes",
        confidence=Confidence.HIGH.value,
        method="url_pattern",
        evidence=[],
    )


@pytest.mark.parametrize("bank", list(BANKS))
def test_integration_end_to_end(tmp_path, bank):
    store = Store(tmp_path / f"{bank}_minutes.db")
    pub = _publication(bank)
    store.upsert_publication(pub)
    doc = _normalized(bank, FIXTURE_FILES[bank])
    store.upsert_normalized_document(doc)
    _classify(store, pub.id, bank)

    results = extract_minutes(store, pub)
    assert len(results) == 1
    result = results[0]
    assert result.publication_id == pub.id
    assert result.document_id == doc.document_id
    assert {_signature(f) for f in result.facts} == GOLDEN[bank]

    retrieved = store.get_facts(publication_id=pub.id)
    assert {_signature(f) for f in retrieved} == GOLDEN[bank]
    for fact in retrieved:
        assert fact.source_text
        assert fact.extraction_version
        assert fact.extraction_method
        assert fact.speaker is None
        assert fact.identity_qualifier.startswith("minutes:")


@pytest.mark.parametrize("bank", list(BANKS))
def test_integration_idempotent_re_extraction(tmp_path, bank):
    store = Store(tmp_path / f"{bank}_minutes.db")
    pub = _publication(bank)
    store.upsert_publication(pub)
    store.upsert_normalized_document(_normalized(bank, FIXTURE_FILES[bank]))
    _classify(store, pub.id, bank)

    extract_minutes(store, pub)
    first = {f.resolve_id() for f in store.get_facts(publication_id=pub.id)}
    extract_minutes(store, pub)
    second = {f.resolve_id() for f in store.get_facts(publication_id=pub.id)}
    assert first == second, bank
    assert len(second) == len(GOLDEN[bank]), bank


@pytest.mark.parametrize("bank", list(BANKS))
def test_classification_refuses_and_persists(tmp_path, bank):
    store = Store(tmp_path / f"{bank}_minutes.db")
    pub = _publication(bank)
    store.upsert_publication(pub)
    store.upsert_normalized_document(_normalized(bank, FIXTURE_FILES[bank]))

    assert extract_minutes(store, pub) == []
    assert store.get_facts(publication_id=pub.id) == []


def test_batch_extracts_all_unclassified_skipped(tmp_path):
    store = Store(tmp_path / "batch_minutes.db")
    for bank in BANKS:
        pub = _publication(bank)
        store.upsert_publication(pub)
        store.upsert_normalized_document(_normalized(bank, FIXTURE_FILES[bank]))
        _classify(store, pub.id, bank)
    results = extract_minutes_batch(store)
    assert len(results) == len(BANKS)
    for bank in BANKS:
        assert store.get_facts(publication_id=f"pub-{bank}-minutes"), bank


class _ZeroFactMinutesExtractor(BANKS["boe"]):
    """Stub that yields no facts — simulates a re-extraction that now produces
    nothing, to verify empty-result persistence clears stale facts."""

    def extract(self, publication, document):
        return ExtractionResult(
            publication_id=publication.id or publication.dedup_key or "",
            document_id=document.document_id,
        )


def test_empty_result_clears_stale_facts(tmp_path):
    store = Store(tmp_path / "empty.db")
    pub = _publication("boe")
    store.upsert_publication(pub)
    store.upsert_normalized_document(_normalized("boe", FIXTURE_FILES["boe"]))
    _classify(store, pub.id, "boe")

    extract_minutes(store, pub)
    assert store.get_facts(publication_id=pub.id)
    extract_minutes(store, pub, extractor=_ZeroFactMinutesExtractor())
    assert store.get_facts(publication_id=pub.id) == []
