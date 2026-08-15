"""Phase 4.x — BoC Monetary Policy Report coverage: discovery, classification,
dispatch, extraction, contract, negative boundaries, determinism, immutability
and end-to-end persistence for the **official** Bank of Canada MPR pipeline.

The BoC MPR (``/publications/mpr/mpr-<date>/``) was previously reachable only
indirectly through the key-interest-rate schedule page, whose ``monetary_policy_decision``
source type-hint swallowed it — so ``BocReportExtractor`` (gated on
``monetary_policy_report``) never dispatched. This suite verifies the closed
pipeline: official MPR feed discovery (``boc_mpr_feed``) → ``monetary_policy_report``
classification → generic Report dispatch → ``BocReportExtractor`` → canonical
Facts → idempotent persistence.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from argus.adapters.boc import BoCAdapter
from argus.classification import PublicationClassifier
from argus.classification.base import Confidence
from argus.discovery import create as create_strategy
from argus.documents import Normalizer
from argus.facts import LocationKind, PeriodKind, ValueKind
from argus.models import Document, DocumentStatus, Publication
from argus.registry import SourceRegistry
from argus.reports import get_extractor, extract_report
from argus.reports.boc import BocReportExtractor
from argus.store import Store
from conftest import FakeSession, make_client, make_store, response

FIXTURES = Path(__file__).parent / "fixtures"

MPR_FEED_URL = "https://www.bankofcanada.ca/content_type/mpr/feed/"
MPR_URL = "https://www.bankofcanada.ca/publications/mpr/mpr-2026-07-15/"
FAD_URL = "https://www.bankofcanada.ca/2026/07/fad-press-release-2026-07-15/"


def _adapter_source(source_id: str):
    return next(s for s in BoCAdapter().sources if s.id == source_id)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_boc_mpr_feed_source_is_registered():
    source = _adapter_source("boc_mpr_feed")
    assert source.discovery.kind == "rss"
    assert source.discovery.url == MPR_FEED_URL
    assert source.publication_types == ("monetary_policy_report",)
    assert source.priority == 3


def test_boc_mpr_feed_discovers_official_publications(fixture_bytes):
    source = _adapter_source("boc_mpr_feed")
    session = FakeSession({
        source.discovery.url: response(
            fixture_bytes("boc_mpr_feed.xml"), url=source.discovery.url, content_type="application/xml"
        ),
    })
    pubs = create_strategy(source, make_client(session)).discover()
    urls = [p.url for p in pubs]
    assert MPR_URL in urls
    assert any("/publications/mpr/mpr-2026-04-29/" in u for u in urls)
    assert any("/publications/mpr/mpr-2026-01-28/" in u for u in urls)
    for pub in pubs:
        assert pub.central_bank == "boc"
        assert pub.source_id == "boc_mpr_feed"
        assert pub.publication_date is not None


def test_key_rate_schedule_no_longer_discovers_mpr(fixture_bytes):
    """The MPR publication pages are excluded from the decision-typed schedule
    source, so the report is not swallowed by a decision hint."""
    source = _adapter_source("boc_key_interest_rate_schedule")
    assert "/publications/mpr/" in source.discovery.exclude
    session = FakeSession({
        source.discovery.url: response(
            "<html><body>"
            '<a href="/publications/mpr/mpr-2026-07-15/">Monetary Policy Report</a>'
            '<a href="/2026/07/fad-press-release-2026-07-15/">FAD</a>'
            "</body></html>",
            url=source.discovery.url,
        ),
    })
    pubs = create_strategy(source, make_client(session)).discover()
    urls = [p.url for p in pubs]
    assert MPR_URL not in urls
    assert FAD_URL in urls


# ---------------------------------------------------------------------------
# Classification — positive / negative / collision
# ---------------------------------------------------------------------------


def _classify(url: str, title: str, source_id: str, registry=None):
    pub = Publication(
        central_bank="boc", title=title, url=url,
        source_id=source_id, source_url="https://www.bankofcanada.ca/", publication_date=None,
    )
    classifier = PublicationClassifier(registry=registry or SourceRegistry())
    return classifier.classify(pub)


def test_mpr_classifies_as_monetary_policy_report():
    result = _classify(MPR_URL, "Monetary Policy Report—July 2026", "boc_mpr_feed")
    assert result.publication_type == "monetary_policy_report"
    assert result.method == "source_type_hint"
    assert result.confidence == Confidence.HIGH


def test_mpr_also_classifies_via_generic_url_rule_without_hint():
    # If discovered through an untyped channel, the generic mpr[_-]\d{4} report
    # rule still resolves the MPR URL.
    result = _classify(MPR_URL, "Monetary Policy Report—July 2026", "boc_press_releases_rss")
    assert result.publication_type == "monetary_policy_report"
    assert result.method in ("url_pattern", "title_pattern")


def test_fad_decision_classifies_as_decision():
    result = _classify(
        FAD_URL, "Bank of Canada maintains the policy rate at 2¼%", "boc_press_releases_rss"
    )
    assert result.publication_type == "monetary_policy_decision"


def test_mpr_is_not_a_decision():
    result = _classify(MPR_URL, "Monetary Policy Report—July 2026", "boc_mpr_feed")
    assert result.publication_type != "monetary_policy_decision"


def test_non_mpr_boc_publication_is_not_a_report():
    result = _classify(
        "https://www.bankofcanada.ca/2026/07/summary-of-governing-council-deliberations-fixed-announcement-date-of-july-15-2026/",
        "Summary of Governing Council deliberations: Fixed announcement date of July 15, 2026",
        "boc_announcements_rss",
    )
    assert result.publication_type != "monetary_policy_report"


def test_no_classification_collision_for_mpr():
    """The MPR URL must resolve to exactly one document family, deterministically,
    at every tier (source hint, url, title) — never two competing families."""
    registry = SourceRegistry()
    for source_id in ("boc_mpr_feed", "boc_press_releases_rss", "boc_announcements_rss"):
        result = _classify(MPR_URL, "Monetary Policy Report—July 2026", source_id, registry=registry)
        assert result.publication_type == "monetary_policy_report"
        assert result.publication_type != "monetary_policy_decision"


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def test_dispatch_resolves_boc_report_extractor():
    assert get_extractor("boc") is not None
    assert get_extractor("boc").__class__ is BocReportExtractor
    assert get_extractor("boc").bank == "boc"


# ---------------------------------------------------------------------------
# Extraction from the synthetic MPR fixture
# ---------------------------------------------------------------------------

FIXTURE = FIXTURES / "documents" / "boc_report.html"


def _normalized_fixture():
    return Normalizer().parse(
        Document(
            publication_id="pub-boc-mpr",
            url=MPR_URL,
            kind="html",
            status=DocumentStatus.FETCHED,
            local_path=str(FIXTURE),
        )
    )


def _publication(store: Store) -> Publication:
    pub = Publication(
        central_bank="boc",
        title="Monetary Policy Report—July 2026",
        url=MPR_URL,
        source_id="boc_mpr_feed",
        source_url=MPR_FEED_URL,
        id="pub-boc-mpr",
    )
    store.upsert_publication(pub)
    return store.get_publication(pub.id)


def _classify_report(store: Store, pub_id: str) -> None:
    store.set_classification(
        pub_id,
        central_bank="boc",
        publication_type="monetary_policy_report",
        confidence=Confidence.HIGH.value,
        method="source_type_hint",
        evidence=["source_id=boc_mpr_feed"],
    )


def _extract_direct():
    return BocReportExtractor().extract(
        Publication(central_bank="boc", title="R", url=MPR_URL, source_id="s", source_url="su", id="pub-boc-mpr"),
        _normalized_fixture(),
    )


def test_extraction_produces_canonical_facts():
    result = _extract_direct()
    assert result.warnings == []
    subjects = {f.subject for f in result.facts}
    assert {
        "inflation", "gdp", "unemployment", "wages", "financial_conditions",
        "monetary_policy", "policy_guidance", "inflation_risk", "growth_risk",
    }.issubset(subjects)
    # representative golden facts
    sig = {_signature(f) for f in result.facts}
    assert ("inflation", "value", "percentage", 2.1, "year:2026") in sig
    assert ("gdp", "value", "percentage", 1.8, "year:2027") in sig
    assert ("unemployment", "value", "percentage", 5.8, "year:2026") in sig
    assert ("inflation_risk", "assessment", "categorical", "balanced", None) in sig


def test_contract_fields():
    result = _extract_direct()
    doc = _normalized_fixture()
    section_count = len(doc.sections)
    for fact in result.facts:
        assert fact.publication_id == "pub-boc-mpr"
        assert fact.document_id == doc.document_id
        assert fact.speaker is None
        assert fact.effective_date is None
        assert fact.source_location.kind == LocationKind.SECTION
        assert 0 <= fact.source_location.section < section_count
        assert fact.source_text
        assert fact.value.source_text
        assert fact.extraction_method in ("regex", "table_extraction")
        assert fact.extraction_version == BocReportExtractor.extraction_version
        assert fact.confidence is not None
        assert fact.identity_qualifier.startswith("report:")
        owning = doc.sections[fact.source_location.section].text or ""
        assert fact.source_text in owning, "provenance must be verbatim"
        # value facts carry an explicit percentage kind and reference period
        if fact.predicate == "value":
            assert fact.value.kind == ValueKind.PERCENTAGE
            assert fact.period is not None
            assert fact.period.kind in (PeriodKind.YEAR, PeriodKind.MONTH, PeriodKind.QUARTER)


def test_non_claim_sentence_yields_no_fact():
    """The fixture's non-claim sentence ('This report was prepared under the
    responsibility of the Governing Council.') must not invent a fact."""
    result = _extract_direct()
    assert not any("prepared under the responsibility" in (f.source_text or "") for f in result.facts)


def test_no_policy_decision_facts_invented():
    """MPR narrative must never be priced as a Phase-5 decision or a Phase-9
    policy-rate projection."""
    result = _extract_direct()
    for fact in result.facts:
        assert fact.subject != "policy_rate"
        assert fact.predicate != "projection"
        assert fact.subject != "monetary_policy_decision"


def test_no_phase_12_to_15_semantics():
    result = _extract_direct()
    for fact in result.facts:
        assert fact.subject not in ("hawkish", "dovish", "stance", "rate_expectation", "policy_state")
        assert "hawkish" not in (fact.source_text or "").lower()
        assert "dovish" not in (fact.source_text or "").lower()


def test_generic_qualitative_language_no_fact():
    result = _extract_direct()
    assert not any("is important" in (f.source_text or "").lower() for f in result.facts)


def _signature(fact) -> tuple:
    kind = fact.value.kind.value if hasattr(fact.value.kind, "value") else fact.value.kind
    if fact.period:
        pkind = fact.period.kind
        pkind_str = pkind.value if hasattr(pkind, "value") else pkind
        period = f"{pkind_str}:{fact.period.value}"
    else:
        period = None
    return (fact.subject, fact.predicate, kind, fact.value.value, period)


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
    fwd = {_signature(f) for f in BocReportExtractor().extract(
        Publication(central_bank="boc", title="R", url=MPR_URL, source_id="s", source_url="su", id="pub-boc-mpr"),
        doc,
    ).facts}
    rev = copy.deepcopy(doc)
    rev.sections = list(reversed(rev.sections))
    back = {_signature(f) for f in BocReportExtractor().extract(
        Publication(central_bank="boc", title="R", url=MPR_URL, source_id="s", source_url="su", id="pub-boc-mpr"),
        rev,
    ).facts}
    assert back == fwd


def test_source_immutability():
    pub = Publication(central_bank="boc", title="R", url=MPR_URL, source_id="s", source_url="su", id="pub-boc-mpr")
    doc = _normalized_fixture()
    pub_before = copy.deepcopy(pub)
    sections_before = copy.deepcopy(doc.sections)
    BocReportExtractor().extract(pub, doc)
    assert pub.central_bank == pub_before.central_bank
    assert pub.id == pub_before.id
    assert [s.heading for s in doc.sections] == [s.heading for s in sections_before]
    assert [s.text for s in doc.sections] == [s.text for s in sections_before]


# ---------------------------------------------------------------------------
# End-to-end persistence: discover → fetch → normalize → classify → extract →
# persist → retrieve, with idempotent re-extraction.
# ---------------------------------------------------------------------------


def test_integration_end_to_end_persistence(tmp_path, fixture_bytes):
    store = make_store(tmp_path)
    session = FakeSession({
        MPR_FEED_URL: response(fixture_bytes("boc_mpr_feed.xml"), url=MPR_FEED_URL, content_type="application/xml"),
        MPR_URL: response(fixture_bytes("documents/boc_report.html"), url=MPR_URL, content_type="text/html"),
    })
    registry = SourceRegistry()
    client = make_client(session)

    # discover
    source = _adapter_source("boc_mpr_feed")
    pubs = create_strategy(source, client).discover()
    mpr_pub = next(p for p in pubs if p.url == MPR_URL)
    stored = store.upsert_publication(mpr_pub)
    assert stored.id

    # classify (authoritative classifications table)
    classifier = PublicationClassifier(store=store, registry=registry)
    classification = classifier.classify(stored)
    assert classification.publication_type == "monetary_policy_report"
    store.set_classification(
        stored.id, central_bank="boc", publication_type=classification.publication_type,
        confidence=classification.confidence.value, method=classification.method,
        evidence=classification.evidence, classified_at=classification.classified_at,
    )

    # fetch + normalize the official MPR page
    from argus.documents import Normalizer
    from argus.fetcher import Fetcher

    fetcher = Fetcher(client, store, tmp_path / "raw")
    fetch = fetcher.fetch(stored)
    assert fetch.ok, fetch.failed_urls
    Normalizer(store=store, raw_root=tmp_path / "raw").normalize_publication(stored)
    docs = store.normalized_documents_for_publication(stored.id)
    assert docs and all(getattr(d, "ok", False) for d in docs)

    # extract via the generic Report entry point (classification-gated)
    results = extract_report(store, stored)
    assert len(results) == 1
    result = results[0]
    assert result.publication_id == stored.id
    assert {_signature(f) for f in result.facts}

    # retrieve persisted facts and verify provenance survived
    facts = store.get_facts(publication_id=stored.id)
    assert facts
    for fact in facts:
        assert fact.source_text
        assert fact.extraction_version
        assert fact.identity_qualifier.startswith("report:")
        assert fact.document_id

    # idempotent re-extraction: deterministic fact_ids, no duplication
    extract_report(store, stored)
    again = store.get_facts(publication_id=stored.id)
    assert [f.resolve_id() for f in facts] == [f.resolve_id() for f in again]
