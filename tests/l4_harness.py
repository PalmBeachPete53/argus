"""Parametrizable L4 end-to-end pipeline harness.

Extracts the scenario of
``tests/test_reports_boc_mpr.py::test_integration_end_to_end_persistence`` so a
single deterministic full-pipeline run (discovery -> publication -> fetch ->
normalization -> classification -> gated dispatch -> extraction -> persistence)
can be driven for any bank/family by supplying input data only:

- the adapter that declares the source;
- the discovery fixture served at the source URL;
- the target publication URL to select after discovery;
- the document fixture served at the target publication URL;
- the expected canonical ``publication_type``;
- the classification-gated family entry point (``extract_decision``,
  ``extract_report``, ...);
- optionally the expected extractor class, an identity-qualifier prefix and a
  set of expected canonical fact signatures.

The L4 invariants asserted here mirror the original BoC test: the publication,
its normalized document and its Facts are persisted; provenance survives;
``resolve_id()`` is stable across a second extraction run (no duplicates).

``run_l4_end_to_end_twice`` additionally runs the **complete pipeline a second
time against the same Store** and asserts end-to-end idempotence: identical
business identities and zero duplication (no new publication/document/fact
rows).
"""

from __future__ import annotations

from typing import Callable

from conftest import FakeSession, make_client, make_store, response

from argus.classification import PublicationClassifier
from argus.discovery import create as create_strategy
from argus.documents import Normalizer
from argus.fetcher import Fetcher
from argus.registry import SourceRegistry
from argus.store import Store


def fact_signature(fact) -> tuple:
    """Canonical (subject, predicate, value_kind, value, period) signature."""
    kind = fact.value.kind.value if hasattr(fact.value.kind, "value") else fact.value.kind
    if fact.period:
        pkind = fact.period.kind
        pkind_str = pkind.value if hasattr(pkind, "value") else pkind
        period = f"{pkind_str}:{fact.period.value}"
    else:
        period = None
    return (fact.subject, fact.predicate, kind, fact.value.value, period)


def _make_source_client(
    *,
    adapter,
    source_id,
    discovery_fixture,
    document_fixture,
    target_url,
    fixture_bytes,
    search_discovery: bool = False,
    search_provider_base="https://searxng.test/",
):
    source = next(s for s in adapter.sources if s.id == source_id)
    discovery_content_type = "application/xml" if source.discovery.kind in ("rss", "sitemap") else "text/html"
    document_content_type = "application/pdf" if document_fixture.endswith(".pdf") else "text/html"
    routes = {
        source.discovery.url: response(
            fixture_bytes(discovery_fixture), url=source.discovery.url, content_type=discovery_content_type
        ),
        target_url: response(
            fixture_bytes(document_fixture), url=target_url, content_type=document_content_type
        ),
    }
    session = FakeSession(routes)
    client = make_client(session)
    search_provider = None
    if search_discovery and source.discovery.search_query:
        # Replay a captured SearXNG JSON response at the provider's search URL so
        # the Search Discovery path can be validated offline (generic, per source).
        from argus.search import SearxngSearchProvider

        search_provider = SearxngSearchProvider(search_provider_base, client=client)
        search_url = search_provider._search_url(source.discovery.search_query, ())
        session.routes[search_url] = response(
            fixture_bytes(discovery_fixture), url=search_url, content_type="application/json"
        )
    return source, client, search_provider


def _discover_pubs(source, client, provider, *, now=None):
    if provider is not None and source.discovery.search_query:
        from argus.discovery.search import SearchDiscovery

        return SearchDiscovery(source, provider, now=now).discover()
    return create_strategy(source, client, now=now).discover()


def _pipeline_once(
    store: Store,
    source,
    client,
    tmp_path,
    *,
    target_url: str,
    expected_type: str,
    extract: Callable,
    page_doc_extraction: bool = True,
    search_provider=None,
):
    """Run the full L4 pipeline once against ``store``.

    Returns ``(stored_publication, extraction_results, persisted_facts)``.
    """
    registry = SourceRegistry()

    # discover -> publication -> persist
    pubs = _discover_pubs(source, client, search_provider)
    target = next(p for p in pubs if p.url == target_url)
    stored = store.upsert_publication(target)
    assert stored.id

    # classify (authoritative classifications table)
    classifier = PublicationClassifier(store=store, registry=registry)
    classification = classifier.classify(stored)
    assert classification.publication_type == expected_type
    store.set_classification(
        stored.id,
        central_bank=stored.central_bank,
        publication_type=classification.publication_type,
        confidence=classification.confidence.value,
        method=classification.method,
        evidence=classification.evidence,
        classified_at=classification.classified_at,
    )

    # fetch + normalize
    fetcher = Fetcher(client, store, tmp_path / "raw", page_doc_extraction=page_doc_extraction)
    fetch = fetcher.fetch(stored)
    assert fetch.ok, fetch.failed_urls
    Normalizer(store=store, raw_root=tmp_path / "raw").normalize_publication(stored)
    docs = store.normalized_documents_for_publication(stored.id)
    assert docs and all(getattr(d, "ok", False) for d in docs)

    # extract via the generic family entry point (classification-gated dispatch)
    results = extract(store, stored)
    assert len(results) == 1
    assert results[0].publication_id == stored.id

    facts = store.get_facts(publication_id=stored.id)
    assert facts
    return stored, results, facts


def _assert_l4_invariants(
    stored,
    result,
    facts,
    *,
    expected_extractor=None,
    qualifier_prefix: str | None = None,
    expected_facts: set | None = None,
) -> None:
    """Provenance / dispatch / canonical-facts assertions shared by every run."""
    signatures = {fact_signature(f) for f in result.facts}
    assert signatures
    if expected_facts is not None:
        assert expected_facts.issubset(signatures)
    for fact in facts:
        assert fact.source_text
        assert fact.extraction_version
        assert fact.document_id
        if qualifier_prefix is not None:
            assert fact.identity_qualifier.startswith(qualifier_prefix)
        if expected_extractor is not None:
            assert fact.extraction_version == expected_extractor.extraction_version


def run_l4_end_to_end(
    *,
    adapter,
    source_id: str,
    discovery_fixture: str,
    document_fixture: str,
    target_url: str,
    expected_type: str,
    extract: Callable,
    expected_extractor=None,
    qualifier_prefix: str | None = None,
    expected_facts: set | None = None,
    page_doc_extraction: bool = True,
    search_discovery: bool = False,
    fixture_bytes,
    tmp_path,
) -> tuple:
    """Run the full L4 pipeline for one publication and assert the L4 invariants.

    Returns ``(store, stored_publication, extraction_result, facts,
    facts_after_reextraction)`` so callers can assert scenario-specific details.
    """
    store = make_store(tmp_path)
    source, client, search_provider = _make_source_client(
        adapter=adapter, source_id=source_id, discovery_fixture=discovery_fixture,
        document_fixture=document_fixture, target_url=target_url, fixture_bytes=fixture_bytes,
        search_discovery=search_discovery,
    )
    stored, results, facts = _pipeline_once(
        store, source, client, tmp_path, target_url=target_url, expected_type=expected_type,
        extract=extract, page_doc_extraction=page_doc_extraction, search_provider=search_provider,
    )
    result = results[0]
    _assert_l4_invariants(
        stored, result, facts,
        expected_extractor=expected_extractor, qualifier_prefix=qualifier_prefix, expected_facts=expected_facts,
    )

    # idempotent re-extraction at the extraction stage: deterministic fact_ids
    extract(store, stored)
    again = store.get_facts(publication_id=stored.id)
    assert [f.resolve_id() for f in facts] == [f.resolve_id() for f in again]

    return store, stored, result, facts, again


# ---------------------------------------------------------------------------
# business-identity snapshot (temporal/technical fields excluded)
# ---------------------------------------------------------------------------


def store_business_snapshot(store: Store) -> dict:
    """Capture the persisted business identity, excluding temporal/technical
    fields that legitimately differ between two executions."""
    publications = []
    documents = []
    normalized = []
    classifications = []
    facts = []
    for pub in store.list_publications():
        publications.append({
            "id": pub.id,
            "dedup_key": pub.dedup_key,
            "central_bank": pub.central_bank,
            "url": pub.url,
            "title": pub.title,
            "publication_date": pub.publication_date.isoformat() if pub.publication_date else None,
            "publication_type": pub.publication_type,
            "status": pub.status.value,
            "source_id": pub.source_id,
        })
        for doc in store.list_documents(pub.id):
            documents.append({
                "id": doc.id,
                "publication_id": doc.publication_id,
                "url": doc.url,
                "kind": doc.kind,
                "status": doc.status.value,
                "sha256": doc.sha256,
            })
        for nd in store.normalized_documents_for_publication(pub.id):
            normalized.append({
                "document_id": nd.document_id,
                "publication_id": nd.publication_id,
                "source_url": nd.source_url,
                "document_kind": nd.document_kind,
                "title": nd.title,
            })
        for fact in store.get_facts(publication_id=pub.id):
            period = None
            if fact.period:
                pkind = fact.period.kind
                pkind_str = pkind.value if hasattr(pkind, "value") else pkind
                period = f"{pkind_str}:{fact.period.value}"
            facts.append({
                "fact_id": fact.resolve_id(),
                "publication_id": fact.publication_id,
                "document_id": fact.document_id,
                "subject": fact.subject,
                "predicate": fact.predicate,
                "value": fact.value.to_dict() if fact.value else None,
                "period": period,
                "effective_date": fact.effective_date.isoformat() if fact.effective_date else None,
                "identity_qualifier": fact.identity_qualifier,
                "extraction_version": fact.extraction_version,
            })
    for record in store.list_classifications():
        classifications.append({
            "publication_id": record["publication_id"],
            "central_bank": record["central_bank"],
            "publication_type": record["publication_type"],
            "confidence": record["confidence"],
            "method": record["method"],
            "evidence": sorted(record["evidence"] or []),
        })
    return {
        "publications": sorted(publications, key=lambda p: p["id"]),
        "documents": sorted(documents, key=lambda d: (d["publication_id"], d["url"])),
        "normalized_documents": sorted(normalized, key=lambda n: n["document_id"]),
        "classifications": sorted(classifications, key=lambda c: c["publication_id"]),
        "facts": sorted(facts, key=lambda f: f["fact_id"]),
    }


def run_l4_end_to_end_twice(
    *,
    adapter,
    source_id: str,
    discovery_fixture: str,
    document_fixture: str,
    target_url: str,
    expected_type: str,
    extract: Callable,
    expected_extractor=None,
    qualifier_prefix: str | None = None,
    expected_facts: set | None = None,
    page_doc_extraction: bool = True,
    search_discovery: bool = False,
    fixture_bytes,
    tmp_path,
) -> tuple:
    """Run the complete L4 pipeline twice against the same Store.

    The second run redoes discovery -> upsert -> classification -> fetch ->
    normalization -> gated dispatch -> extraction -> persistence on the same
    persisted environment. Returns ``(store, run1, run2, snapshot1, snapshot2)``
    where ``runN = (stored, result, facts)``.
    """
    store = make_store(tmp_path)
    source, client, search_provider = _make_source_client(
        adapter=adapter, source_id=source_id, discovery_fixture=discovery_fixture,
        document_fixture=document_fixture, target_url=target_url, fixture_bytes=fixture_bytes,
        search_discovery=search_discovery,
    )

    run1 = _pipeline_once(
        store, source, client, tmp_path, target_url=target_url, expected_type=expected_type,
        extract=extract, page_doc_extraction=page_doc_extraction, search_provider=search_provider,
    )
    stored1, results1, facts1 = run1
    _assert_l4_invariants(
        stored1, results1[0], facts1,
        expected_extractor=expected_extractor, qualifier_prefix=qualifier_prefix, expected_facts=expected_facts,
    )
    snapshot1 = store_business_snapshot(store)

    run2 = _pipeline_once(
        store, source, client, tmp_path, target_url=target_url, expected_type=expected_type,
        extract=extract, page_doc_extraction=page_doc_extraction, search_provider=search_provider,
    )
    stored2, results2, facts2 = run2
    _assert_l4_invariants(
        stored2, results2[0], facts2,
        expected_extractor=expected_extractor, qualifier_prefix=qualifier_prefix, expected_facts=expected_facts,
    )
    snapshot2 = store_business_snapshot(store)

    # --- end-to-end idempotence -------------------------------------------------
    # no new publication / document / fact / classification rows
    assert len(snapshot1["publications"]) == len(snapshot2["publications"]) == 1
    assert len(snapshot1["documents"]) == len(snapshot2["documents"])
    assert len(snapshot1["normalized_documents"]) == len(snapshot2["normalized_documents"])
    assert len(snapshot1["facts"]) == len(snapshot2["facts"])
    assert len(snapshot1["classifications"]) == len(snapshot2["classifications"])

    # same persisted business identity after the second full run
    assert snapshot1 == snapshot2

    # same publication, same document, same fact identities, same relations
    assert stored2.id == stored1.id
    assert set(f.resolve_id() for f in facts2) == set(f.resolve_id() for f in facts1)
    assert all(f.publication_id == stored1.id for f in facts2)
    assert all(f.document_id in {s["document_id"] for s in snapshot1["normalized_documents"]} for f in facts2)

    return store, (stored1, results1[0], facts1), (stored2, results2[0], facts2), snapshot1, snapshot2
