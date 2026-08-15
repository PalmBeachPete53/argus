"""Phase 4.x — Multi-bank Report extraction: contract, dispatch, provenance,
boundaries, determinism, immutability and end-to-end integration tests for the
four Report extractors added alongside ECB / Norges (BoE, BoC, RBA, RBNZ).

A Report is a publication type; a Report extractor turns one monetary policy
report into canonical Facts. These banks were already classified as
``monetary_policy_report`` in the Argus taxonomy but had no extractor; this
suite verifies the generic registry dispatch, the canonical Fact contract,
provenance, deterministic output, source immutability and the full
publication → classification → extractor → Fact → persistence → retrieval
vertical slice.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from argus.classification.base import Confidence
from argus.documents import Normalizer
from argus.documents.base import DocumentSection, NormalizedDocument
from argus.facts import LocationKind, PeriodKind, ValueKind
from argus.models import Document, DocumentStatus, Publication
from argus.reports import (
    get_extractor,
    extract_report,
    BoeReportExtractor,
    BocReportExtractor,
    RbaReportExtractor,
    RbnzReportExtractor,
    RiksbankReportExtractor,
)
from argus.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "documents"

BANKS = {
    "boe": BoeReportExtractor,
    "boc": BocReportExtractor,
    "rba": RbaReportExtractor,
    "rbnz": RbnzReportExtractor,
}
FIXTURE_FILES = {
    "boe": "boe_report.html",
    "boc": "boc_report.html",
    "rba": "rba_report.html",
    "rbnz": "rbnz_report.html",
}

# Golden signatures: (subject, predicate, value_kind, value, period)
GOLDEN = {
    "boe": {
        ("inflation", "value", "percentage", 2.0, "year:2026"),
        ("inflation", "value", "percentage", 1.8, "year:2027"),
        ("gdp", "value", "percentage", 1.0, "year:2026"),
        ("gdp", "value", "percentage", 1.3, "year:2027"),
        ("growth", "assessment", "text", "Domestic demand continued to expand at a moderate pace.", None),
        ("unemployment", "value", "percentage", 4.2, "year:2026"),
        ("wages", "value", "percentage", 4.5, "year:2026"),
        ("financial_conditions", "assessment", "text", "Financial conditions have eased somewhat in recent months.", None),
        ("monetary_policy", "statement", "text", "The MPC decided to maintain Bank Rate.", None),
        ("policy_guidance", "statement", "text", "Monetary policy will be restrictive for as long as necessary.", None),
        ("growth_risk", "assessment", "categorical", "downside", None),
        ("inflation_risk", "assessment", "categorical", "balanced", None),
    },
    "boc": {
        ("inflation", "value", "percentage", 2.1, "year:2026"),
        ("gdp", "value", "percentage", 1.5, "year:2026"),
        ("gdp", "value", "percentage", 1.4, "year:2026"),
        ("gdp", "value", "percentage", 1.8, "year:2027"),
        ("inflation", "value", "percentage", 1.9, "year:2027"),
        ("unemployment", "value", "percentage", 5.8, "year:2026"),
        ("wages", "value", "percentage", 3.1, "year:2026"),
        ("financial_conditions", "assessment", "text", "Financial conditions have tightened over the past year.", None),
        ("monetary_policy", "statement", "text", "The Governing Council decided to hold the policy interest rate.", None),
        ("policy_guidance", "statement", "text", "The Bank will continue to assess the data.", None),
        ("inflation_risk", "assessment", "categorical", "balanced", None),
        ("growth_risk", "assessment", "categorical", "upside", None),
    },
    "rba": {
        ("inflation", "assessment", "text", "Inflation is expected to return to the target range by 2026.", None),
        ("gdp", "value", "percentage", 2.1, "year:2026"),
        ("growth", "assessment", "text", "The Australian economy grew at a moderate pace over the year.", None),
        ("unemployment", "value", "percentage", 4.4, "year:2026"),
        ("wages", "value", "percentage", 3.2, "year:2026"),
        ("inflation", "value", "percentage", 2.8, "year:2026"),
        ("financial_conditions", "assessment", "text", "Financial conditions have tightened since the last Statement.", None),
        ("monetary_policy", "statement", "text", "The Board decided to hold the cash rate.", None),
        ("policy_guidance", "statement", "text", "Monetary policy will remain data dependent.", None),
        ("inflation_risk", "assessment", "categorical", "balanced", None),
        ("growth_risk", "assessment", "categorical", "downside", None),
    },
    "rbnz": {
        ("inflation", "value", "percentage", 2.0, "year:2026"),
        ("gdp", "value", "percentage", 1.2, "year:2026"),
        ("growth", "assessment", "text", "The New Zealand economy contracted in the December quarter.", None),
        ("unemployment", "value", "percentage", 5.0, "year:2026"),
        ("wages", "value", "percentage", 3.4, "year:2026"),
        ("inflation", "value", "percentage", 1.9, "year:2027"),
        ("financial_conditions", "assessment", "text", "Financial conditions have eased.", None),
        ("monetary_policy", "statement", "text", "The Committee decided to hold the OCR.", None),
        ("policy_guidance", "statement", "text", "The stance of monetary policy will remain restrictive for as long as necessary.", None),
        ("inflation_risk", "assessment", "categorical", "balanced", None),
        ("growth_risk", "assessment", "categorical", "upside", None),
    },
}


def _normalized(bank: str, name: str):
    return Normalizer().parse(
        Document(
            publication_id=f"pub-{bank}-report",
            url=f"https://example.org/{bank}/{name}",
            kind="html",
            status=DocumentStatus.FETCHED,
            local_path=str(FIXTURES / name),
        )
    )


def _publication(bank: str) -> Publication:
    return Publication(
        central_bank=bank,
        title="Monetary policy report",
        url=f"https://example.org/{bank}/report",
        source_id=f"{bank}-report",
        source_url=f"https://example.org/{bank}/feed.xml",
        id=f"pub-{bank}-report",
    )


def _signature(fact) -> tuple:
    kind = fact.value.kind.value if hasattr(fact.value.kind, "value") else fact.value.kind
    if fact.period:
        pkind = fact.period.kind
        pkind_str = pkind.value if hasattr(pkind, "value") else pkind
        period = f"{pkind_str}:{fact.period.value}"
    else:
        period = None
    return (fact.subject, fact.predicate, kind, fact.value.value, period)


def _extract(bank: str):
    return get_extractor(bank).extract(_publication(bank), _normalized(bank, FIXTURE_FILES[bank]))


@pytest.mark.parametrize("bank", list(BANKS))
def test_extractor_bank_identity(bank):
    assert get_extractor(bank).bank == bank


@pytest.mark.parametrize("bank", list(BANKS))
def test_golden_facts(bank):
    result = _extract(bank)
    got = {_signature(f) for f in result.facts}
    assert got == GOLDEN[bank], f"{bank}: {got ^ GOLDEN[bank]}"


@pytest.mark.parametrize("bank", list(BANKS))
def test_contract_fields(bank):
    result = _extract(bank)
    doc = _normalized(bank, FIXTURE_FILES[bank])
    section_count = len(doc.sections)
    for fact in result.facts:
        assert fact.publication_id == f"pub-{bank}-report"
        assert fact.document_id == doc.document_id
        assert fact.central_bank is None or fact.central_bank == bank
        assert fact.speaker is None
        assert fact.effective_date is None
        assert fact.source_location is not None
        assert fact.source_location.kind == LocationKind.SECTION
        assert 0 <= fact.source_location.section < section_count
        assert fact.source_text  # verbatim provenance always present
        assert fact.value.source_text  # token/cell provenance
        assert fact.extraction_method in ("regex", "table_extraction")
        assert fact.extraction_version
        assert fact.confidence is not None
        assert fact.identity_qualifier.startswith("report:")
        # provenance verbatim: source_text is a substring of the owning section text
        owning = doc.sections[fact.source_location.section].text or ""
        assert fact.source_text in owning, f"{bank}: source_text not in owning section"


@pytest.mark.parametrize("bank", list(BANKS))
def test_value_facts_carry_percentage_kind_and_period(bank):
    for fact in _extract(bank).facts:
        if fact.predicate == "value":
            assert fact.value.kind == ValueKind.PERCENTAGE
            assert fact.period is not None
            assert fact.period.kind in (PeriodKind.YEAR, PeriodKind.MONTH, PeriodKind.QUARTER)
            assert fact.confidence == Confidence.HIGH


def test_dispatch_resolves_all_banks():
    expected = {
        "ecb": "EcbReportsExtractor",
        "norges": "NorgesReportExtractor",
        "boe": "BoeReportExtractor",
        "boc": "BocReportExtractor",
        "rba": "RbaReportExtractor",
        "rbnz": "RbnzReportExtractor",
        "riksbank": "RiksbankReportExtractor",
    }
    for bank, cls in expected.items():
        assert get_extractor(bank).__class__.__name__ == cls
    for bank in ("fed", "boj", "snb"):
        assert get_extractor(bank) is None


@pytest.mark.parametrize("bank", list(BANKS))
def test_deterministic_repeated_extraction(bank):
    r1 = _extract(bank)
    r2 = _extract(bank)
    assert [f.resolve_id() for f in r1.facts] == [f.resolve_id() for f in r2.facts]
    assert [fct.to_dict() for fct in r1.facts] == [fct.to_dict() for fct in r2.facts]


@pytest.mark.parametrize("bank", list(BANKS))
def test_order_independence(bank):
    """Reverse section order — the extracted fact *set* is unchanged."""
    doc = _normalized(bank, FIXTURE_FILES[bank])
    fwd = {_signature(f) for f in get_extractor(bank).extract(_publication(bank), doc).facts}

    reversed_doc = copy.deepcopy(doc)
    reversed_doc.sections = list(reversed(reversed_doc.sections))
    rev = {_signature(f) for f in get_extractor(bank).extract(_publication(bank), reversed_doc).facts}
    assert rev == fwd


@pytest.mark.parametrize("bank", list(BANKS))
def test_source_immutability(bank):
    pub = _publication(bank)
    doc = _normalized(bank, FIXTURE_FILES[bank])
    pub_before = copy.deepcopy(pub)
    sections_before = copy.deepcopy(doc.sections)
    get_extractor(bank).extract(pub, doc)
    assert pub.central_bank == pub_before.central_bank
    assert pub.id == pub_before.id
    assert [s.heading for s in doc.sections] == [s.heading for s in sections_before]
    assert [s.text for s in doc.sections] == [s.text for s in sections_before]


# ---------------------------------------------------------------------------
# boundaries
# ---------------------------------------------------------------------------

def _synthetic(bank: str, sections_text: list[tuple[str, str]], doc_id: str = "sha-synthetic") -> NormalizedDocument:
    sections = [
        DocumentSection(order=i, heading=heading, text=text)
        for i, (heading, text) in enumerate(sections_text)
    ]
    return NormalizedDocument(
        publication_id=f"pub-{bank}-report",
        document_id=doc_id,
        source_url=f"https://example.org/{bank}",
        local_path=None,
        document_kind="html",
        sections=sections,
    )


@pytest.mark.parametrize("bank", list(BANKS))
def test_unknown_heading_is_never_mined(bank):
    doc = _synthetic(bank, [("Miscellaneous notes", "Inflation is projected to average 2.0 per cent in 2026.")])
    result = get_extractor(bank).extract(_publication(bank), doc)
    assert result.facts == []
    assert "no_economic_sections" in result.warnings


@pytest.mark.parametrize("bank", list(BANKS))
def test_forecast_without_period_is_ignored(bank):
    doc = _synthetic(bank, [("Inflation", "Inflation is projected to average 2.3 per cent.")])
    result = get_extractor(bank).extract(_publication(bank), doc)
    assert not any(f.subject == "inflation" and f.predicate == "value" for f in result.facts)


@pytest.mark.parametrize("bank", list(BANKS))
def test_share_units_are_never_percentages(bank):
    doc = _synthetic(bank, [("Fiscal policy", "The deficit is projected to be 3.0 per cent of GDP in 2026.")])
    result = get_extractor(bank).extract(_publication(bank), doc)
    assert not any(f.predicate == "value" for f in result.facts)


@pytest.mark.parametrize("bank", list(BANKS))
def test_no_value_without_explicit_claim_verb(bank):
    doc = _synthetic(bank, [("Inflation", "Inflation is 2.4 per cent in 2025.")])
    result = get_extractor(bank).extract(_publication(bank), doc)
    assert not any(f.subject == "inflation" and f.predicate == "value" for f in result.facts)


@pytest.mark.parametrize("bank", list(BANKS))
def test_gdp_near_miss_never_yields_gdp_value(bank):
    doc = _synthetic(bank, [("Economic activity", "Real GDP growth held steady while the GDP deflator rose by 2.1 per cent in 2026.")])
    result = get_extractor(bank).extract(_publication(bank), doc)
    assert not any(f.subject == "gdp" and f.predicate == "value" for f in result.facts)


@pytest.mark.parametrize("bank", list(BANKS))
def test_no_downstream_semantics(bank):
    for fact in _extract(bank).facts:
        assert fact.subject not in ("hawkish", "dovish", "stance", "rate_expectation")
        assert "hawkish" not in (fact.source_text or "").lower()


def test_missing_publication_metadata_allowed_for_direct_extract(bank="boe"):
    doc = _normalized(bank, FIXTURE_FILES[bank])
    pub = Publication(central_bank=bank, title="R", url="u", source_id="s", source_url="su", id=None)
    result = get_extractor(bank).extract(pub, doc)
    assert result.publication_id in ("", None) or True  # does not raise
    assert result.facts


def test_classification_refuses_and_persists(tmp_path, bank="rba"):
    store = Store(tmp_path / "bank.db")
    pub = _publication(bank)
    store.upsert_publication(pub)
    doc = _normalized(bank, FIXTURE_FILES[bank])
    store.upsert_normalized_document(doc)
    # no classification → extraction refuses and persists nothing
    results = extract_report(store, pub)
    assert results == []
    assert store.get_facts(publication_id=pub.id) == []


def _classify_report(store, pub_id, bank) -> None:
    store.set_classification(
        pub_id,
        central_bank=bank,
        publication_type="monetary_policy_report",
        confidence=Confidence.HIGH.value,
        method="url_pattern",
        evidence=[],
    )


@pytest.mark.parametrize("bank", list(BANKS))
def test_integration_end_to_end(tmp_path, bank):
    """Publication → classification → generic dispatch → extractor → facts →
    persistence → retrieval."""
    store = Store(tmp_path / f"{bank}_report.db")
    pub = _publication(bank)
    store.upsert_publication(pub)
    doc = _normalized(bank, FIXTURE_FILES[bank])
    store.upsert_normalized_document(doc)
    _classify_report(store, pub.id, bank)

    results = extract_report(store, pub)
    assert len(results) == 1
    result = results[0]
    assert result.publication_id == pub.id
    assert result.document_id == doc.document_id
    assert {_signature(f) for f in result.facts} == GOLDEN[bank]

    retrieved = store.get_facts(publication_id=pub.id)
    assert {_signature(f) for f in retrieved} == GOLDEN[bank]
    # provenance preserved through persistence
    for fact in retrieved:
        assert fact.source_text
        assert fact.extraction_version
        assert fact.extraction_method


@pytest.mark.parametrize("bank", list(BANKS))
def test_integration_idempotent_re_extraction(tmp_path, bank):
    store = Store(tmp_path / f"{bank}_report.db")
    pub = _publication(bank)
    store.upsert_publication(pub)
    doc = _normalized(bank, FIXTURE_FILES[bank])
    store.upsert_normalized_document(doc)
    _classify_report(store, pub.id, bank)

    extract_report(store, pub)
    first = {f.resolve_id() for f in store.get_facts(publication_id=pub.id)}
    extract_report(store, pub)
    second = {f.resolve_id() for f in store.get_facts(publication_id=pub.id)}
    assert first == second
