"""Phase 4.x — Riksbank Monetary Policy Report extraction: contract, dispatch,
provenance, boundaries, determinism, immutability and end-to-end integration.

The Riksbank MPR is the report-family publication type for Sweden. It is
already classified ``monetary_policy_report`` by the generic ``url_pattern``
rule; this suite verifies the bank-specific ``RiksbankReportExtractor``
(v10.6.0): generic registry dispatch, the canonical Fact contract,
provenance, deterministic output, source immutability, the Phase 5/9
boundaries (the decision narrative is kept verbatim and never priced; the
"forecast tables" section is never mined) and the full publication →
classification → extractor → Fact → persistence → retrieval vertical slice.
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
    RiksbankReportExtractor,
    extract_report,
    get_extractor,
)
from argus.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "documents"
FIXTURE_FILE = "riksbank_report.html"
BANK = "riksbank"

# Golden signatures: (subject, predicate, value_kind, value, period)
GOLDEN = {
    ("inflation", "value", "percentage", 2.0, "year:2026"),
    ("inflation", "value", "percentage", 1.9, "year:2027"),
    ("core_inflation", "value", "percentage", 2.3, "year:2026"),
    ("gdp", "value", "percentage", 2.1, "year:2026"),
    ("growth", "assessment", "text", "Economic activity is gradually strengthening.", None),
    ("unemployment", "value", "percentage", 7.2, "year:2026"),
    ("wages", "value", "percentage", 3.3, "year:2026"),
    ("inflation", "assessment", "text", "Inflation has fallen markedly from the peak.", None),
    ("financial_conditions", "assessment", "text", "Financial conditions have eased somewhat in recent months.", None),
    ("monetary_policy", "statement", "text", "Monetary policy in Sweden is contractionary, aimed at bringing inflation back to the target of 2 per cent.", None),
    ("monetary_policy", "statement", "text", "The Executive Board decided to cut the policy rate by 0.25 percentage points to 2.5 per cent.", None),
    ("monetary_policy", "statement", "text", "Monetary policy needs to remain contractionary for some time to come.", None),
    ("policy_guidance", "statement", "text", "The policy rate can also be cut or lowered further if inflation remains low.", None),
    ("inflation_risk", "assessment", "categorical", "balanced", None),
    ("growth_risk", "assessment", "categorical", "downside", None),
}


def _normalized(name: str = FIXTURE_FILE):
    return Normalizer().parse(
        Document(
            publication_id=f"pub-{BANK}-report",
            url=f"https://example.org/{BANK}/{name}",
            kind="html",
            status=DocumentStatus.FETCHED,
            local_path=str(FIXTURES / name),
        )
    )


def _publication() -> Publication:
    return Publication(
        central_bank=BANK,
        title="Monetary policy report",
        url=f"https://example.org/{BANK}/report",
        source_id=f"{BANK}-report",
        source_url=f"https://example.org/{BANK}/feed.xml",
        id=f"pub-{BANK}-report",
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


def _extract():
    return get_extractor(BANK).extract(_publication(), _normalized())


# ---------------------------------------------------------------------------
# dispatch & identity
# ---------------------------------------------------------------------------


def test_extractor_bank_identity():
    assert get_extractor(BANK) is not None
    assert get_extractor(BANK).bank == BANK
    assert get_extractor(BANK).__class__.__name__ == "RiksbankReportExtractor"


def test_dispatch_does_not_resolve_non_report_banks():
    for bank in ("fed", "boj", "snb"):
        assert get_extractor(bank) is None


# ---------------------------------------------------------------------------
# golden facts & contract
# ---------------------------------------------------------------------------


def test_golden_facts_no_warnings():
    result = _extract()
    assert result.warnings == []
    got = {_signature(f) for f in result.facts}
    assert got == GOLDEN


def test_contract_fields():
    result = _extract()
    doc = _normalized()
    section_count = len(doc.sections)
    for fact in result.facts:
        assert fact.publication_id == f"pub-{BANK}-report"
        assert fact.document_id == doc.document_id
        assert fact.central_bank is None or fact.central_bank == BANK
        assert fact.speaker is None
        assert fact.effective_date is None
        assert fact.source_location is not None
        assert fact.source_location.kind == LocationKind.SECTION
        assert 0 <= fact.source_location.section < section_count
        assert fact.source_text  # verbatim provenance always present
        assert fact.value.source_text  # token/cell provenance
        assert fact.extraction_method in ("regex", "table_extraction")
        assert fact.extraction_version == RiksbankReportExtractor.extraction_version
        assert fact.confidence is not None
        assert fact.identity_qualifier.startswith("report:")
        owning = doc.sections[fact.source_location.section].text or ""
        assert fact.source_text in owning


def test_value_facts_carry_percentage_kind_and_period():
    for fact in _extract().facts:
        if fact.predicate == "value":
            assert fact.value.kind == ValueKind.PERCENTAGE
            assert fact.period is not None
            assert fact.period.kind in (PeriodKind.YEAR, PeriodKind.MONTH, PeriodKind.QUARTER)
            assert fact.confidence == Confidence.HIGH


# ---------------------------------------------------------------------------
# determinism & immutability
# ---------------------------------------------------------------------------


def test_deterministic_repeated_extraction():
    r1 = _extract()
    r2 = _extract()
    assert [f.resolve_id() for f in r1.facts] == [f.resolve_id() for f in r2.facts]
    assert [fct.to_dict() for fct in r1.facts] == [fct.to_dict() for fct in r2.facts]


def test_order_independence():
    doc = _normalized()
    fwd = {_signature(f) for f in get_extractor(BANK).extract(_publication(), doc).facts}

    reversed_doc = copy.deepcopy(doc)
    reversed_doc.sections = list(reversed(reversed_doc.sections))
    rev = {_signature(f) for f in get_extractor(BANK).extract(_publication(), reversed_doc).facts}
    assert rev == fwd


def test_source_immutability():
    pub = _publication()
    doc = _normalized()
    pub_before = copy.deepcopy(pub)
    sections_before = copy.deepcopy(doc.sections)
    get_extractor(BANK).extract(pub, doc)
    assert pub.central_bank == pub_before.central_bank
    assert pub.id == pub_before.id
    assert [s.heading for s in doc.sections] == [s.heading for s in sections_before]
    assert [s.text for s in doc.sections] == [s.text for s in sections_before]


# ---------------------------------------------------------------------------
# Riksbank-specific boundaries
# ---------------------------------------------------------------------------


def _synthetic(sections_text: list[tuple[str, str]], doc_id: str = "sha-synthetic") -> NormalizedDocument:
    sections = [
        DocumentSection(order=i, heading=heading, text=text)
        for i, (heading, text) in enumerate(sections_text)
    ]
    return NormalizedDocument(
        publication_id=f"pub-{BANK}-report",
        document_id=doc_id,
        source_url=f"https://example.org/{BANK}",
        local_path=None,
        document_kind="html",
        sections=sections,
    )


def test_unknown_heading_is_never_mined():
    doc = _synthetic([("Miscellaneous notes", "Inflation is projected to average 2.0 per cent in 2026.")])
    result = get_extractor(BANK).extract(_publication(), doc)
    assert result.facts == []
    assert "no_economic_sections" in result.warnings
    assert "no_risk_assessment" in result.warnings
    assert "no_forward_guidance" in result.warnings


def test_forecast_tables_section_is_never_mined():
    doc = _synthetic([
        ("Forecast tables", "CPIF inflation is projected to be 2.0 per cent in 2026. "
                            "Table 2 shows the policy rate forecast of 2.5 per cent in 2027."),
    ])
    result = get_extractor(BANK).extract(_publication(), doc)
    assert result.facts == []
    assert "no_economic_sections" in result.warnings


def test_decision_narrative_is_never_priced():
    """The MPR's narrative of the latest policy decision stays verbatim
    ``monetary_policy/statement`` — it is never priced as a policy-rate value
    (Phase 5 boundary)."""
    result = _extract()
    assert not any(f.subject == "policy_rate" for f in result.facts)
    assert not any(f.predicate == "value"
                   and ("0.25" in f.source_text or "2.5 per cent" in f.source_text) for f in result.facts)
    # but the verbatim narrative is preserved as a policy statement
    assert any(
        f.subject == "monetary_policy" and f.predicate == "statement"
        and "The Executive Board decided to cut the policy rate by 0.25 percentage points to 2.5 per cent."
        in f.source_text for f in result.facts
    )


def test_no_downstream_semantics():
    for fact in _extract().facts:
        assert fact.subject not in ("hawkish", "dovish", "stance", "rate_expectation")
        assert "hawkish" not in (fact.source_text or "").lower()


def test_cpif_is_headline_inflation():
    """CPIF is the Riksbank's target measure → ``inflation`` (headline), while
    "underlying inflation" / CPIF excluding energy is the core measure."""
    result = _extract()
    assert ("inflation", "value", "percentage", 2.0, "year:2026") in {_signature(f) for f in result.facts}
    assert ("core_inflation", "value", "percentage", 2.3, "year:2026") in {_signature(f) for f in result.facts}


def test_dash_heading_variants_route_to_policy():
    """Section-title dash glyphs (−, –, -, —) never change heading identity."""
    text = "Monetary policy needs to remain contractionary for some time to come."
    for dash in ("—", "–", "-", "−"):
        heading = f"Monetary policy in Sweden {dash} the Riksbank's strategy"
        doc = _synthetic([(heading, text)], doc_id=f"sha-{dash!r}")
        result = get_extractor(BANK).extract(_publication(), doc)
        assert any(
            f.subject == "monetary_policy" and f.predicate == "statement"
            and f.source_text == text for f in result.facts
        ), f"dash {dash!r} not routed to policy"


def test_forecast_without_period_is_ignored():
    doc = _synthetic([("Inflation", "CPIF inflation is projected to average 2.3 per cent.")])
    result = get_extractor(BANK).extract(_publication(), doc)
    assert not any(f.subject == "inflation" and f.predicate == "value" for f in result.facts)


def test_share_units_are_never_percentages():
    doc = _synthetic([("Fiscal policy", "The deficit is projected to be 3.0 per cent of GDP in 2026.")])
    result = get_extractor(BANK).extract(_publication(), doc)
    assert not any(f.predicate == "value" for f in result.facts)


def test_no_value_without_explicit_claim_verb():
    doc = _synthetic([("Inflation", "CPIF inflation is 2.4 per cent in 2025.")])
    result = get_extractor(BANK).extract(_publication(), doc)
    assert not any(f.subject == "inflation" and f.predicate == "value" for f in result.facts)


def test_gdp_near_miss_never_yields_gdp_value():
    doc = _synthetic([("Economic activity", "Real GDP growth held steady while the GDP deflator rose by 2.1 per cent in 2026.")])
    result = get_extractor(BANK).extract(_publication(), doc)
    assert not any(f.subject == "gdp" and f.predicate == "value" for f in result.facts)


def test_risk_verbatim_when_no_explicit_orientation():
    doc = _synthetic([("Risks", "The uncertainty regarding the global economy is unusually high.")])
    result = get_extractor(BANK).extract(_publication(), doc)
    assert any(
        f.subject == "risk" and f.predicate == "assessment" and f.value.kind == ValueKind.TEXT
        for f in result.facts
    )
    assert not any(f.subject == "risk" and f.predicate == "assessment" and f.value.kind == ValueKind.CATEGORICAL for f in result.facts)


def test_extractor_refuses_empty_document():
    doc = NormalizedDocument(
        publication_id=f"pub-{BANK}-report", document_id="sha-empty",
        source_url=f"https://example.org/{BANK}", local_path=None,
        document_kind="html", sections=[],
    )
    result = get_extractor(BANK).extract(_publication(), doc)
    assert result.facts == []
    assert "no_sections" in result.warnings


def test_subject_vocabulary_stays_canonical():
    """No MPR-specific Fact type is invented: subjects stay in the canonical
    Report-family vocabulary (+ the shared core_inflation subject)."""
    allowed = {
        "inflation", "core_inflation", "inflation_expectations", "growth", "gdp",
        "labour_market", "unemployment", "wages", "financial_conditions",
        "risk", "inflation_risk", "growth_risk", "monetary_policy",
        "policy_guidance", "fiscal_policy",
    }
    for fact in _extract().facts:
        assert fact.subject in allowed, f"non-canonical subject: {fact.subject}"


# ---------------------------------------------------------------------------
# classification gating & integration
# ---------------------------------------------------------------------------


def test_classification_refuses_and_persists(tmp_path):
    store = Store(tmp_path / "riksbank_report.db")
    pub = _publication()
    store.upsert_publication(pub)
    doc = _normalized()
    store.upsert_normalized_document(doc)
    # no classification → extraction refuses and persists nothing
    results = extract_report(store, pub)
    assert results == []
    assert store.get_facts(publication_id=pub.id) == []


def _classify_report(store, pub_id) -> None:
    store.set_classification(
        pub_id,
        central_bank=BANK,
        publication_type="monetary_policy_report",
        confidence=Confidence.HIGH.value,
        method="url_pattern",
        evidence=[],
    )


def test_integration_end_to_end(tmp_path):
    """Publication → classification → generic dispatch → extractor → facts →
    persistence → retrieval."""
    store = Store(tmp_path / "riksbank_report.db")
    pub = _publication()
    store.upsert_publication(pub)
    doc = _normalized()
    store.upsert_normalized_document(doc)
    _classify_report(store, pub.id)

    results = extract_report(store, pub)
    assert len(results) == 1
    result = results[0]
    assert result.publication_id == pub.id
    assert result.document_id == doc.document_id
    assert result.warnings == []
    assert {_signature(f) for f in result.facts} == GOLDEN

    retrieved = store.get_facts(publication_id=pub.id)
    assert {_signature(f) for f in retrieved} == GOLDEN
    for fact in retrieved:
        assert fact.source_text
        assert fact.extraction_version
        assert fact.extraction_method
        assert fact.identity_qualifier.startswith("report:")


def test_integration_idempotent_re_extraction(tmp_path):
    store = Store(tmp_path / "riksbank_report.db")
    pub = _publication()
    store.upsert_publication(pub)
    doc = _normalized()
    store.upsert_normalized_document(doc)
    _classify_report(store, pub.id)

    extract_report(store, pub)
    first = {f.resolve_id() for f in store.get_facts(publication_id=pub.id)}
    extract_report(store, pub)
    second = {f.resolve_id() for f in store.get_facts(publication_id=pub.id)}
    assert first == second
    assert first  # facts persisted, not cleared


def test_classification_is_already_authoritative_for_riksbank():
    """The Riksbank MPR is classified ``monetary_policy_report`` by the generic
    URL rule (no bank-specific rule needed); this extractor just consumes it."""
    from argus.classification.classifier import PublicationClassifier

    pub = Publication(
        central_bank=BANK,
        title="Monetary Policy Report, December 2024",
        url="https://www.riksbank.se/en-gb/monetary-policy/monetary-policy-report/2024/monetary-policy-report-december-2024/",
        source_id="feed", source_url="url",
    )
    c = PublicationClassifier().classify(pub)
    assert c.publication_type == "monetary_policy_report"
    assert c.method == "url_pattern"