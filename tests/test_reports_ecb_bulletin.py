"""Phase 4.x — ECB Economic Bulletin / Report coverage: discovery,
classification, dispatch, extraction, contract, negative boundaries,
determinism, immutability and end-to-end persistence for the **official**
ECB Economic Bulletin pipeline.

The ECB Economic Bulletin (``/press/economic-bulletin/html/eb<yyyymm>.en.html``)
is the ECB's report-like publication and the Report-family document. It was
discovered via ``ecb_publications_rss`` but classified ``unknown`` (no URL/title
rule mapped ``economic-bulletin`` into ``monetary_policy_report``), so
``EcbReportsExtractor`` — the reference Phase-10 extractor — never dispatched
on real bulletins. This suite verifies the closed pipeline: bulletin discovery
→ ``monetary_policy_report`` classification → generic Report dispatch →
``EcbReportsExtractor`` → canonical Facts → idempotent persistence.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from argus.adapters.ecb import ECBAdapter
from argus.classification import PublicationClassifier
from argus.classification.base import Confidence
from argus.discovery import create as create_strategy
from argus.documents import Normalizer
from argus.facts import LocationKind, PeriodKind, ValueKind
from argus.models import Document, DocumentStatus, Publication
from argus.registry import SourceRegistry
from argus.reports import get_extractor, extract_report
from argus.reports.ecb import EcbReportsExtractor
from argus.store import Store
from conftest import FakeSession, make_client, make_store, response

FIXTURES = Path(__file__).parent / "fixtures"

PUBLICATIONS_FEED_URL = "https://www.ecb.europa.eu/rss/pub.html"
BULLETIN_URL = "https://www.ecb.europa.eu/press/economic-bulletin/html/eb202605.en.html"
DECISION_URL = "https://www.ecb.europa.eu/press/govcdec/2026/html/ecb.gc260715~a1b2.en.html"


def _adapter_source(source_id: str):
    return next(s for s in ECBAdapter().sources if s.id == source_id)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_ecb_publications_rss_is_the_bulletin_discovery_path():
    source = _adapter_source("ecb_publications_rss")
    assert source.discovery.kind == "rss"
    assert source.discovery.url == PUBLICATIONS_FEED_URL


def test_bulletin_discovered_via_publications_rss(fixture_bytes):
    source = _adapter_source("ecb_publications_rss")
    session = FakeSession({
        source.discovery.url: response(
            fixture_bytes("ecb_publications.xml"), url=source.discovery.url, content_type="application/xml"
        ),
    })
    pubs = create_strategy(source, make_client(session)).discover()
    bulletin = [p for p in pubs if "economic-bulletin" in p.url]
    assert len(bulletin) == 1
    pub = bulletin[0]
    assert pub.url == BULLETIN_URL
    assert pub.central_bank == "ecb"
    assert pub.source_id == "ecb_publications_rss"
    assert pub.title
    assert pub.publication_date is not None


def test_bulletin_not_duplicated_across_ecb_sources(fixture_bytes):
    """The bulletin is surfaced by the publications RSS only; the sitemap and
    press-RSS include filters keep it out, so no duplicate publication arises."""
    urls = []
    for sid in ("ecb_press_rss", "ecb_publications_rss", "ecb_sitemap_monetary"):
        source = _adapter_source(sid)
        if sid == "ecb_publications_rss":
            body = fixture_bytes("ecb_publications.xml")
            content_type = "application/xml"
        elif sid == "ecb_sitemap_monetary":
            body = fixture_bytes("ecb_sitemap.xml")
            content_type = "application/xml"
        else:
            body = fixture_bytes("ecb_press.xml")
            content_type = "application/xml"
        session = FakeSession({source.discovery.url: response(body, url=source.discovery.url, content_type=content_type)})
        pubs = create_strategy(source, make_client(session)).discover()
        urls.extend(p.url for p in pubs if "economic-bulletin" in p.url)
    # only the publications RSS surfaces the bulletin
    assert urls.count(BULLETIN_URL) == 1


# ---------------------------------------------------------------------------
# Classification — positive / negative
# ---------------------------------------------------------------------------


def _classify(url: str, title: str, source_id: str, registry=None):
    pub = Publication(
        central_bank="ecb", title=title, url=url,
        source_id=source_id, source_url=PUBLICATIONS_FEED_URL, publication_date=None,
    )
    return PublicationClassifier(registry=registry or SourceRegistry()).classify(pub)


def test_bulletin_classifies_as_monetary_policy_report():
    result = _classify(BULLETIN_URL, "Economic Bulletin Issue 5, 2026", "ecb_publications_rss")
    assert result.publication_type == "monetary_policy_report"
    assert result.method == "url_pattern"
    assert result.confidence == Confidence.MEDIUM


def test_bulletin_title_classifies_as_report():
    result = _classify(
        "https://www.ecb.europa.eu/pub/economic-bulletin/html/ecb.eb202605.en.html",
        "Economic Bulletin Issue 5, 2026", "ecb_publications_rss",
    )
    assert result.publication_type == "monetary_policy_report"


def test_decision_is_not_a_report():
    result = _classify(DECISION_URL, "Monetary policy decisions", "ecb_press_rss")
    assert result.publication_type == "monetary_policy_decision"
    assert result.publication_type != "monetary_policy_report"


def test_statement_is_not_a_report():
    result = _classify(
        "https://www.ecb.europa.eu/press/press_conference/monetary-policy-statement/2026/html/ecb.is260715~a1b2.en.html",
        "Monetary policy statement", "ecb_press_rss",
    )
    assert result.publication_type == "monetary_policy_statement"
    assert result.publication_type != "monetary_policy_report"


def test_minutes_is_not_a_report():
    result = _classify(
        "https://www.ecb.europa.eu/press/accounts/2026/html/ecb.acc260715~a1b2.en.html",
        "Account of the monetary policy meeting", "ecb_publications_rss",
    )
    assert result.publication_type == "meeting_account"
    assert result.publication_type != "monetary_policy_report"


def test_press_conference_is_not_a_report():
    result = _classify(
        "https://www.ecb.europa.eu/press/press_conference/monetary-policy-statement/2026/html/ecb.pc260715~a1b2.en.html",
        "Press conference", "ecb_press_rss",
    )
    assert result.publication_type == "press_conference"
    assert result.publication_type != "monetary_policy_report"


def test_speech_is_not_a_report():
    result = _classify(
        "https://www.ecb.europa.eu/press/key/date/2026/html/ecb.sp260714~a1b2.en.html",
        "Speech by Christine Lagarde, President of the ECB", "ecb_press_rss",
    )
    assert result.publication_type == "speech"
    assert result.publication_type != "monetary_policy_report"


def test_unrelated_ecb_publication_is_not_a_report():
    result = _classify(
        "https://www.ecb.europa.eu/press/pr/date/2026/html/ecb.pr260801~a1b2.en.html",
        "ECB announces new banknote series", "ecb_press_rss",
    )
    assert result.publication_type != "monetary_policy_report"


def test_no_classification_collision_for_bulletin():
    for url, title in (
        (BULLETIN_URL, "Economic Bulletin Issue 5, 2026"),
        ("https://www.ecb.europa.eu/pub/economic-bulletin/html/ecb.eb202605.en.html", "Economic Bulletin, Issue 5/2026"),
    ):
        result = _classify(url, title, "ecb_publications_rss")
        assert result.publication_type == "monetary_policy_report"
        assert result.publication_type not in (
            "monetary_policy_decision", "monetary_policy_statement", "press_conference",
            "minutes", "meeting_account", "speech",
        )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def test_dispatch_resolves_ecb_report_extractor():
    assert get_extractor("ecb") is not None
    assert get_extractor("ecb").__class__ is EcbReportsExtractor
    assert get_extractor("ecb").bank == "ecb"


# ---------------------------------------------------------------------------
# Extraction from the synthetic Economic Bulletin fixture
# ---------------------------------------------------------------------------


def _normalized_fixture(name: str = "ecb_report.html"):
    return Normalizer().parse(
        Document(
            publication_id="pub-ecb-bulletin",
            url=BULLETIN_URL,
            kind="html",
            status=DocumentStatus.FETCHED,
            local_path=str(FIXTURES / "documents" / name),
        )
    )


def _publication(store: Store) -> Publication:
    pub = Publication(
        central_bank="ecb",
        title="Economic Bulletin Issue 5, 2026",
        url=BULLETIN_URL,
        source_id="ecb_publications_rss",
        source_url=PUBLICATIONS_FEED_URL,
        id="pub-ecb-bulletin",
    )
    store.upsert_publication(pub)
    return store.get_publication(pub.id)


def _classify_report(store: Store, pub_id: str) -> None:
    store.set_classification(
        pub_id,
        central_bank="ecb",
        publication_type="monetary_policy_report",
        confidence=Confidence.HIGH.value,
        method="url_pattern",
        evidence=["url_pattern=monetary_policy_report (economic-bulletin)"],
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


def _extract_direct():
    return EcbReportsExtractor().extract(
        Publication(central_bank="ecb", title="Economic Bulletin Issue 5, 2026", url=BULLETIN_URL,
                    source_id="ecb_publications_rss", source_url=PUBLICATIONS_FEED_URL, id="pub-ecb-bulletin"),
        _normalized_fixture(),
    )


def test_extraction_produces_canonical_facts():
    result = _extract_direct()
    assert result.warnings == []
    subjects = {f.subject for f in result.facts}
    assert {
        "inflation", "core_inflation", "inflation_expectations", "gdp", "growth",
        "unemployment", "wages", "financial_conditions", "fiscal_policy",
        "monetary_policy", "policy_guidance", "inflation_risk", "growth_risk",
    }.issubset(subjects)
    sig = {_signature(f) for f in result.facts}
    assert ("inflation", "value", "percentage", 2.4, "year:2025") in sig
    assert ("gdp", "value", "percentage", 0.4, "quarter:2026-Q1") in sig
    assert ("gdp", "value", "percentage", 1.4, "year:2027") in sig
    assert ("unemployment", "value", "percentage", 6.5, "year:2026") in sig
    assert ("inflation_risk", "assessment", "categorical", "upside", None) in sig


def test_contract_fields():
    result = _extract_direct()
    doc = _normalized_fixture()
    section_count = len(doc.sections)
    for fact in result.facts:
        assert fact.publication_id == "pub-ecb-bulletin"
        assert fact.document_id == doc.document_id
        assert fact.speaker is None
        assert fact.effective_date is None
        assert fact.source_location.kind == LocationKind.SECTION
        assert 0 <= fact.source_location.section < section_count
        assert fact.source_text
        assert fact.value.source_text
        assert fact.extraction_method in ("regex", "table_extraction")
        assert fact.extraction_version == EcbReportsExtractor.extraction_version
        assert fact.confidence is not None
        assert fact.identity_qualifier.startswith("report:")
        owning = doc.sections[fact.source_location.section].text or ""
        assert fact.source_text in owning
        if fact.predicate == "value":
            assert fact.value.kind == ValueKind.PERCENTAGE
            assert fact.period is not None
            assert fact.period.kind in (PeriodKind.YEAR, PeriodKind.MONTH, PeriodKind.QUARTER)


def test_non_claim_sentence_yields_no_fact():
    result = _extract_direct()
    assert not any("summarises data collected across" in (f.source_text or "") for f in result.facts)


def test_policy_reference_does_not_become_a_decision():
    """The bulletin's narrative reference to a previous rate decision must never
    be priced as a new policy-rate decision or projection."""
    result = _extract_direct()
    for fact in result.facts:
        assert fact.subject not in ("monetary_policy_decision", "policy_rate")
        assert fact.predicate != "projection"


def test_no_phase_12_to_15_semantics():
    result = _extract_direct()
    for fact in result.facts:
        assert fact.subject not in ("hawkish", "dovish", "stance", "rate_expectation", "policy_state")
        assert "hawkish" not in (fact.source_text or "").lower()
        assert "dovish" not in (fact.source_text or "").lower()


# ---------------------------------------------------------------------------
# Determinism, order independence, immutability
# ---------------------------------------------------------------------------


def test_deterministic_repeated_extraction():
    r1 = _extract_direct()
    r2 = _extract_direct()
    assert [f.resolve_id() for f in r1.facts] == [f.resolve_id() for f in r2.facts]
    assert [fct.to_dict() for fct in r1.facts] == [fct.to_dict() for fct in r2.facts]


def test_input_order_independence():
    doc = _normalized_fixture()
    fwd = {_signature(f) for f in EcbReportsExtractor().extract(
        Publication(central_bank="ecb", title="R", url=BULLETIN_URL, source_id="s", source_url="su", id="pub-ecb-bulletin"),
        doc,
    ).facts}
    rev = copy.deepcopy(doc)
    rev.sections = list(reversed(rev.sections))
    back = {_signature(f) for f in EcbReportsExtractor().extract(
        Publication(central_bank="ecb", title="R", url=BULLETIN_URL, source_id="s", source_url="su", id="pub-ecb-bulletin"),
        rev,
    ).facts}
    assert back == fwd


def test_source_immutability():
    pub = Publication(central_bank="ecb", title="R", url=BULLETIN_URL, source_id="s", source_url="su", id="pub-ecb-bulletin")
    doc = _normalized_fixture()
    pub_before = copy.deepcopy(pub)
    sections_before = copy.deepcopy(doc.sections)
    EcbReportsExtractor().extract(pub, doc)
    assert pub.central_bank == pub_before.central_bank
    assert pub.id == pub_before.id
    assert [s.heading for s in doc.sections] == [s.heading for s in sections_before]
    assert [s.text for s in doc.sections] == [s.text for s in sections_before]


# ---------------------------------------------------------------------------
# End-to-end persistence
# ---------------------------------------------------------------------------


def test_integration_end_to_end_persistence(tmp_path):
    store = make_store(tmp_path)
    store.upsert_publication(_publication_standalone())
    pub = store.get_publication("pub-ecb-bulletin")
    _classify_report(store, pub.id)

    # normalize the synthetic bulletin body
    doc = _normalized_fixture()
    store.upsert_normalized_document(doc)

    results = extract_report(store, pub)
    assert len(results) == 1
    result = results[0]
    assert result.publication_id == pub.id
    assert result.document_id == doc.document_id

    retrieved = store.get_facts(publication_id=pub.id)
    assert {_signature(f) for f in retrieved} == {_signature(f) for f in result.facts}
    for fact in retrieved:
        assert fact.source_text
        assert fact.extraction_version
        assert fact.identity_qualifier.startswith("report:")
        assert fact.document_id == doc.document_id

    # idempotent re-extraction: same deterministic fact_ids
    extract_report(store, pub)
    again = store.get_facts(publication_id=pub.id)
    assert [f.resolve_id() for f in retrieved] == [f.resolve_id() for f in again]


def _publication_standalone() -> Publication:
    return Publication(
        central_bank="ecb",
        title="Economic Bulletin Issue 5, 2026",
        url=BULLETIN_URL,
        source_id="ecb_publications_rss",
        source_url=PUBLICATIONS_FEED_URL,
        id="pub-ecb-bulletin",
    )
