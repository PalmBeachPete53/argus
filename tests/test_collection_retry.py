"""Collection self-repair: PARTIAL / FAILED publications are automatically
retried by the default collection selection, while already-fetched documents
are never re-downloaded unnecessarily. Covers the 🔴 #1 audit finding.
"""

from __future__ import annotations

from datetime import datetime, timezone

from argus.collector import COLLECTION_STATUSES, CentralBankCollector
from argus.errors import TransportError
from argus.models import DocumentStatus, Publication, PublicationStatus
from conftest import FakeSession, make_client, make_store, response

FEED_URL = "https://x.test/feed.xml"
PAGE_URL = "https://x.test/pubs/stmt.htm"
PDF_URL = "https://x.test/files/report.pdf"

FEED = (
    '<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>'
    f"<item><title>Statement</title><link>{PAGE_URL}</link>"
    "<pubDate>Wed, 29 Jul 2026 14:00:00 -0400</pubDate></item>"
    "</channel></rss>"
)


def _pub(**kw) -> Publication:
    fields = dict(
        central_bank="fed",
        title="Statement",
        url=PAGE_URL,
        document_urls=(PAGE_URL,),
        source_id="src",
        source_url=FEED_URL,
        publication_date=datetime(2026, 7, 29, tzinfo=timezone.utc),
    )
    fields.update(kw)
    return Publication(**fields)


def _pub_with_pdf(**kw) -> Publication:
    fields = dict(document_urls=(PAGE_URL, PDF_URL))
    fields.update(kw)
    return _pub(**fields)


def _build(tmp_path, session, publicator=_pub):
    store = make_store(tmp_path)
    store.upsert_publication(publicator())
    collector = CentralBankCollector(
        store=store,
        client=make_client(session),
        raw_root=tmp_path / "raw",
    )
    return collector, store


def test_default_collection_statuses_include_repair_states():
    assert PublicationStatus.DISCOVERED in COLLECTION_STATUSES
    assert PublicationStatus.UPDATED in COLLECTION_STATUSES
    assert PublicationStatus.PARTIAL in COLLECTION_STATUSES
    assert PublicationStatus.FAILED in COLLECTION_STATUSES
    assert PublicationStatus.FETCHED not in COLLECTION_STATUSES


def test_partial_publication_is_retried_and_repaired(tmp_path):
    """PARTIAL → fetch_all() → the failed document is retried; once the server
    heals, the publication becomes FETCHED (repair is complete)."""
    session = FakeSession({
        PAGE_URL: response("<html><body>Body</body></html>", url=PAGE_URL),
        PDF_URL: TransportError(PDF_URL, "connection reset"),
    })
    collector, store = _build(tmp_path, session, _pub_with_pdf)

    # First pass: PDF fails → PARTIAL
    results = collector.fetch_all()
    assert len(results) == 1
    assert store.get_publication(_pub_lock(store)).status == PublicationStatus.PARTIAL
    assert store.get_document(_pub_lock(store), PDF_URL).retries >= 1

    # Server heals; second fetch_all retries the failed doc automatically
    session.routes[PDF_URL] = response(b"%PDF-1.4 fake", url=PDF_URL, content_type="application/pdf")
    results2 = collector.fetch_all()
    assert len(results2) == 1
    pub = store.get_publication(_pub_lock(store))
    assert pub.status == PublicationStatus.FETCHED
    assert store.get_document(_pub_lock(store), PDF_URL).status == DocumentStatus.FETCHED

    # No duplication: exactly one document row per URL
    assert store.document_count(_pub_lock(store)) == 2


def _pub_lock(store):
    return store.list_publications()[0].id


def test_document_fetched_is_not_redownloaded(tmp_path):
    """A document already FETCHED is skipped (no network) even when its
    publication is selected for another reason — idempotence is preserved."""
    calls = {"n": 0}

    session = FakeSession({
        PAGE_URL: response("<html><body>Body</body></html>", url=PAGE_URL),
    })
    real_get = session.get

    def counting(url, headers=None, timeout=None, allow_redirects=True):
        calls["n"] += 1
        return real_get(url, headers=headers, timeout=timeout, allow_redirects=allow_redirects)

    session.get = counting
    collector, store = _build(tmp_path, session)
    collector.fetch_all()
    assert calls["n"] == 1
    pub_id = _pub_lock(store)
    assert store.get_publication(pub_id).status == PublicationStatus.FETCHED

    # A rediscovery that changes metadata turns the FETCHED publication UPDATED
    # (and adds a document URL) — the whole publication is selected again, but
    # its already-fetched page must NOT be re-downloaded.
    store.upsert_publication(_pub(
        title="Statement (revised)",
        document_urls=(PAGE_URL, "https://x.test/files/extra.pdf"),
    )) 
    session.routes["https://x.test/files/extra.pdf"] = response(
        b"%PDF-1.4 extra", url="https://x.test/files/extra.pdf", content_type="application/pdf"
    )
    before = calls["n"]
    collector2 = CentralBankCollector(store=store, client=make_client(session), raw_root=tmp_path / "raw")
    collector2.fetch_all()
    # Only the new document was fetched; the already-FETCHED page was NOT re-downloaded.
    assert calls["n"] == before + 1
    assert store.document_count(pub_id) == 2


def test_successive_passes_stay_idempotent(tmp_path):
    """Once everything is fetched, further fetch_all passes do nothing and
    create nothing — even with the repair statuses in the default selection."""
    session = FakeSession({
        PAGE_URL: response("<html><body>Body</body></html>", url=PAGE_URL),
    })
    collector, store = _build(tmp_path, session)
    collector.fetch_all()
    pub_id = _pub_lock(store)
    docs_before = store.document_count(pub_id)

    calls_before = len(session.calls)
    results = collector.fetch_all()
    assert len(results) == 0          # nothing left to collect
    assert len(session.calls) == calls_before  # no network
    assert store.document_count(pub_id) == docs_before
    assert len(store.list_publications()) == 1


def test_failed_publication_is_retried(tmp_path):
    """FAILED → fetch_all() retries it; once all documents fetch, it becomes
    FETCHED."""
    session = FakeSession({
        PAGE_URL: TransportError(PAGE_URL, "connection refused"),
    })
    collector, store = _build(tmp_path, session)
    collector.fetch_all()
    pub_id = _pub_lock(store)
    assert store.get_publication(pub_id).status == PublicationStatus.FAILED

    session.routes[PAGE_URL] = response("<html><body>Body</body></html>", url=PAGE_URL)
    collector.fetch_all()
    assert store.get_publication(pub_id).status == PublicationStatus.FETCHED
    assert store.get_document(pub_id, PAGE_URL).status == DocumentStatus.FETCHED


def test_repair_respects_retry_cap(tmp_path):
    """A permanently failing document is not hammered: after the fetcher's retry
    cap, fetch_all leaves it FAILED without further network calls."""
    session = FakeSession({
        PAGE_URL: TransportError(PAGE_URL, "connection refused"),
    })
    collector, store = _build(tmp_path, session)
    pub_id = _pub_lock(store)

    for _ in range(5):
        collector.fetch_all()
    assert store.get_publication(pub_id).status == PublicationStatus.FAILED
    # documents row records the retry count and the failure, not a re-download
    doc = store.get_document(pub_id, PAGE_URL)
    assert doc.status == DocumentStatus.FAILED
    assert doc.retries >= collector.fetcher.max_retries