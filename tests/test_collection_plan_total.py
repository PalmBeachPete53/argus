"""Plan/total coherence for Collection under a concurrent Discovery.

A Collection campaign works on a **frozen logical snapshot** of its plan:
``publications_total`` is fixed to the number of publications selected when the
plan was established, and a concurrent Discovery adding or modifying
publications afterwards never appears in the total nor in the workers of that
campaign. These tests drive the concurrency deterministically with
``threading.Event`` barriers (no sleeps).
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from argus.collector import CentralBankCollector
from argus.models import Document, DocumentStatus, FetchResult, Publication, PublicationStatus
from conftest import FakeSession, make_client, make_store, response

_PAGE = "https://x.test/pubs/stmt.htm"


def _pub(i: int, *, status=PublicationStatus.DISCOVERED, url=None, bank="fed") -> Publication:
    return Publication(
        central_bank=bank,
        title=f"Statement {i}",
        url=url or f"{_PAGE}?i={i}",
        source_id="src",
        source_url="https://x.test/feed.xml",
        publication_date=datetime(2026, 7, i + 1, tzinfo=timezone.utc),
        status=status,
    )


def _seed(tmp_path, pubs) -> object:
    from argus.store import Store

    store = Store(tmp_path / "s.db")
    for pub in pubs:
        store.upsert_publication(pub)
    return store


class _GatedFetcher:
    """A fetcher whose per-publication work can be gated (deterministic races)."""

    def __init__(self, store, *, gate=None, blocked=None):
        self.store = store
        self.gate = gate  # optional threading.Event workers wait on before finishing
        self.blocked = set(blocked or ())  # publications that must wait on the gate
        self.completed: list[str] = []
        self._lock = threading.Lock()

    def collect(self, publication, existing_by_url, *, force=False):
        if publication.url in self.blocked and self.gate is not None:
            self.gate.wait(5.0)
        with self._lock:
            self.completed.append(publication.url)
        pub_id = publication.id or ""
        doc = Document(publication_id=pub_id, url=publication.url, kind="html",
                       status=DocumentStatus.FETCHED)
        return (FetchResult(publication_id=pub_id, documents=[doc], ok=True),
                PublicationStatus.FETCHED)

    def persist(self, publication_id, documents, status):
        for document in documents:
            self.store.upsert_document(document)
        if status is not None:
            self.store.set_publication_status(publication_id, status)


def _collector(store, fetcher, tmp_path):
    return CentralBankCollector(
        store=store,
        client=make_client(FakeSession({})),
        raw_root=Path(tmp_path) / "raw",
        fetcher=fetcher,
    )


def _by_url(store, url):
    for p in store.list_publications():
        if p.url == url:
            return p
    raise AssertionError(f"publication not found: {url}")


# ---------------------------------------------------------------------------
# Case A — Discovery before plan
# ---------------------------------------------------------------------------

def test_case_a_discovery_before_plan(tmp_path):
    """Discovery → publication A; Collection plan → A. Total = 1."""
    store = _seed(tmp_path, [_pub(0)])
    pub_a = _by_url(store, f"{_PAGE}?i=0")
    fetcher = _GatedFetcher(store)
    collector = _collector(store, fetcher, tmp_path)
    store.start_collection_run("case-a", ["fed"], publications_total=0)

    results = collector.collect_campaign(run_id="case-a", publications=[pub_a])

    assert len(results) == 1
    run = store.get_collection_run("case-a")
    assert run["publications_total"] == 1
    assert run["publications_completed"] == 1


# ---------------------------------------------------------------------------
# Case B — Discovery after plan
# ---------------------------------------------------------------------------

def test_case_b_discovery_after_plan(tmp_path):
    """Collection plan → A; Discovery adds B afterwards. Total stays 1 and B is
    not collected by this campaign (its workers only see the frozen plan)."""
    store = _seed(tmp_path, [_pub(0)])
    pub_a = _by_url(store, f"{_PAGE}?i=0")

    # Discovery adds B after the plan was frozen.
    store.upsert_publication(_pub(1))
    pub_b = _by_url(store, f"{_PAGE}?i=1")

    fetcher = _GatedFetcher(store)
    collector = _collector(store, fetcher, tmp_path)
    store.start_collection_run("case-b", ["fed"], publications_total=1)

    results = collector.collect_campaign(run_id="case-b", publications=[pub_a])

    assert len(results) == 1
    assert {r.publication_id for r in results} == {pub_a.id}
    assert pub_b.id not in {r.publication_id for r in results}
    run = store.get_collection_run("case-b")
    assert run["publications_total"] == 1
    # B was never collected by this campaign
    assert store.get_publication(pub_b.id).status == PublicationStatus.DISCOVERED
    assert store.document_count(pub_b.id) == 0


# ---------------------------------------------------------------------------
# Case C — Discovery modifies A after plan
# ---------------------------------------------------------------------------

def test_case_c_discovery_modifies_a_after_plan(tmp_path):
    """Discovery modifies A (adds a document URL) after the plan was frozen: the
    campaign collects the frozen snapshot — the new URL is not fetched."""
    page = "https://x.test/pubs/stmt.htm"
    store = _seed(tmp_path, [_pub(0, url=page)])
    pub_a = _by_url(store, page)

    session = FakeSession({
        page: response("<html><body>stmt</body></html>", url=page),
    })
    # the new URL must never be requested (it is outside the frozen plan)
    extra = "https://x.test/pubs/extra.pdf"

    calls = {"n": 0}
    real_get = session.get

    def counting(url, headers=None, timeout=None, allow_redirects=True):
        calls["n"] += 1
        assert url != extra, "a URL added after the plan was frozen must not be fetched"
        return real_get(url, headers=headers, timeout=timeout, allow_redirects=allow_redirects)

    session.get = counting
    # Use the real Fetcher (no injected fake) so the network path is exercised.
    collector = CentralBankCollector(store=store, client=make_client(session),
                                     raw_root=Path(tmp_path) / "raw")

    # Discovery modifies A after the plan was frozen (adds a document URL).
    modified = _pub(0, url=page)
    modified.document_urls = (page, extra)
    store.upsert_publication(modified)

    results = collector.collect_campaign(run_id="case-c", publications=[pub_a])
    assert len(results) == 1
    assert calls["n"] == 1  # only the frozen page fetched
    assert extra not in calls  # never requested


# ---------------------------------------------------------------------------
# Case D — Discovery and Collection simultaneous
# ---------------------------------------------------------------------------

def test_case_d_simultaneous_discovery_and_collection(tmp_path):
    """Discovery adds B while Collection is mid-flight on the frozen plan [A]:
    total == len(snapshot) == number of results processed, and B is untouched.

    The campaign and the concurrent Discovery use *separate* Store connections
    (like the real detached processes): the Store is single-connection /
    thread-affine, so a concurrent Discovery writes through its own connection.
    """
    from argus.store import Store

    db = Path(tmp_path) / "s.db"
    seed = Store(db)
    seed.upsert_publication(_pub(0))
    seed.close()

    started = threading.Event()
    gate = threading.Event()
    errors: list = []
    results: list = []

    def run_campaign():
        # The campaign owns its own Store connection (thread-affine).
        store = Store(db)
        pub_a = _by_url(store, f"{_PAGE}?i=0")
        fetcher = _GatedFetcher(store, gate=gate, blocked={pub_a.url})
        real_collect = fetcher.collect

        def gated_collect(publication, existing_by_url, *, force=False):
            started.set()
            return real_collect(publication, existing_by_url, force=force)

        fetcher.collect = gated_collect
        collector = CentralBankCollector(
            store=store,
            client=make_client(FakeSession({})),
            raw_root=Path(tmp_path) / "raw",
            fetcher=fetcher,
        )
        store.start_collection_run("case-d", ["fed"], publications_total=1)
        try:
            results.extend(collector.collect_campaign(run_id="case-d", publications=[pub_a]))
        except BaseException as exc:  # pragma: no cover - propagate to test
            errors.append(exc)
        finally:
            store.close()

    thread = threading.Thread(target=run_campaign, daemon=True)
    thread.start()

    # Wait until A's worker is blocked mid-flight (deterministic barrier).
    assert started.wait(5.0), "collection worker never started"
    # Discovery (separate connection) adds B while A is still blocked.
    discovery = Store(db)
    discovery.upsert_publication(_pub(1))
    discovery.close()
    gate.set()
    thread.join(timeout=5.0)

    assert not errors, errors
    assert len(results) == 1

    store = Store(db)
    try:
        run = store.get_collection_run("case-d")
        assert run["publications_total"] == 1
        assert run["publications_completed"] == 1
        pub_a = _by_url(store, f"{_PAGE}?i=0")
        pub_b = _by_url(store, f"{_PAGE}?i=1")
        assert store.document_count(pub_a.id) == 1
        # B was added during the run but never collected by it
        assert store.get_publication(pub_b.id).status == PublicationStatus.DISCOVERED
        assert store.document_count(pub_b.id) == 0
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Case E — two Collections are isolated
# ---------------------------------------------------------------------------

def test_case_e_two_collections_isolated(tmp_path):
    """Two campaigns each keep their own frozen snapshot and counters."""
    store = _seed(tmp_path, [_pub(0), _pub(1)])
    pub_a = _by_url(store, f"{_PAGE}?i=0")
    pub_b = _by_url(store, f"{_PAGE}?i=1")

    fetcher = _GatedFetcher(store)
    collector = _collector(store, fetcher, tmp_path)

    store.start_collection_run("e-1", ["fed"], publications_total=1)
    results1 = collector.collect_campaign(run_id="e-1", publications=[pub_a])
    store.finish_collection_run("e-1", status="completed")

    store.start_collection_run("e-2", ["fed"], publications_total=1)
    results2 = collector.collect_campaign(run_id="e-2", publications=[pub_b])
    store.finish_collection_run("e-2", status="completed")

    run1 = store.get_collection_run("e-1")
    run2 = store.get_collection_run("e-2")
    assert run1["publications_total"] == 1 and run1["publications_completed"] == 1
    assert run2["publications_total"] == 1 and run2["publications_completed"] == 1
    assert {r.publication_id for r in results1} == {pub_a.id}
    assert {r.publication_id for r in results2} == {pub_b.id}
    # no cross-campaign leakage: each snapshot was distinct
    assert store.document_count(pub_a.id) == 1
    assert store.document_count(pub_b.id) == 1
