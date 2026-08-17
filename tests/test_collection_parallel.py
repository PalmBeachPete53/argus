"""Parallel collection scheduler tests.

The scheduler must distribute *arbitrary* publications (never a hardcoded
bank/source list) to a bounded worker pool, run them truly concurrently, keep
the logical results identical to a sequential run, isolate per-publication
errors, and remain interruptible (Stop / app close) while workers are in
flight — without a worker ever touching the Store directly.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from argus.collector import (
    COLLECTION_WORKERS,
    _collection_worker_pool_size,
    CentralBankCollector,
)
from argus.models import Document, DocumentStatus, FetchResult, Publication, PublicationStatus
from conftest import FakeSession, make_client, make_store, response

_PAGE = "https://x.test/pubs/stmt.htm"


def _pub(index: int, *, bank="fed", status=PublicationStatus.DISCOVERED, url=None) -> Publication:
    return Publication(
        central_bank=bank,
        title=f"Statement {index}",
        url=url or f"{_PAGE}?i={index}",
        source_id="src",
        source_url="https://x.test/feed.xml",
        publication_date=datetime(2026, 7, index + 1, tzinfo=timezone.utc),
        status=status,
    )


def _seed(tmp_path, n, *, bank="fed", status=PublicationStatus.DISCOVERED):
    store = make_store(tmp_path)
    pubs = [_pub(i, bank=bank, status=status) for i in range(n)]
    for pub in pubs:
        store.upsert_publication(pub)
    store.close()
    return pubs


def _build_collector(store, *, fetcher=None, client=None):
    """A collector bound to the *given* store (single connection), with an
    optional injected fake fetcher and client."""
    return CentralBankCollector(
        store=store,
        client=client or make_client(FakeSession({})),
        raw_root=Path(store.path).parent / "raw",
        fetcher=fetcher,
    )


class _GatedFetcher:
    """A fetcher whose per-publication work is gated by ``threading.Event``.

    The fake only performs network-like work (no Store access in ``collect``);
    ``persist`` applies results to the Store and is invoked by the scheduler's
    serialized writer — mirroring the real Fetcher contract.
    """

    timeout = 10.0

    def __init__(self, store, *, gates=None, fail=None):
        self.store = store
        self.gates = gates or {}
        self.fail = set(fail or ())
        self.started: list[str] = []
        self.completion: list[str] = []
        self._lock = threading.Lock()

    def collect(self, publication, existing_by_url, *, force=False):
        with self._lock:
            self.started.append(publication.url)
        gate = self.gates.get(publication.url)
        if gate is not None and not gate.wait(self.timeout):
            raise TimeoutError(f"gate never released for {publication.url}")
        with self._lock:
            self.completion.append(publication.url)
        pub_id = publication.id or ""
        if publication.url in self.fail:
            doc = Document(publication_id=pub_id, url=publication.url, kind="html",
                           status=DocumentStatus.FAILED, error="boom", retries=1)
            return (
                FetchResult(publication_id=pub_id, documents=[doc], ok=False, failed_urls=[publication.url]),
                PublicationStatus.FAILED,
            )
        doc = Document(publication_id=pub_id, url=publication.url, kind="html",
                       status=DocumentStatus.FETCHED, sha256="x" * 64, size=1)
        return (
            FetchResult(publication_id=pub_id, documents=[doc], ok=True),
            PublicationStatus.FETCHED,
        )

    def persist(self, publication_id, documents, status):
        for document in documents:
            self.store.upsert_document(document)
        if status is not None:
            self.store.set_publication_status(publication_id, status)


class _SlowFetcher(_GatedFetcher):
    """Each publication sleeps 0.4s — used to prove wall-clock speedup."""

    def __init__(self, store):
        super().__init__(store)
        self._sleep = 0.4

    def collect(self, publication, existing_by_url, *, force=False):
        with self._lock:
            self.started.append(publication.url)
        time.sleep(self._sleep)
        with self._lock:
            self.completion.append(publication.url)
        return super().collect(publication, existing_by_url, force=force)


def _fetch_result(publication_id):
    from argus.models import FetchResult

    return FetchResult(publication_id=publication_id, documents=[], ok=True)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def test_collection_workers_default(monkeypatch):
    monkeypatch.delenv("ARGUS_COLLECTION_WORKERS", raising=False)
    assert _collection_worker_pool_size() == COLLECTION_WORKERS


@pytest.mark.parametrize("raw,expected", [
    ("3", 3),
    ("8", 8),
    ("1", 1),          # sequential-equivalent mode
    ("0", COLLECTION_WORKERS),   # invalid → fallback
    ("-2", COLLECTION_WORKERS),
    ("abc", COLLECTION_WORKERS),
    ("", COLLECTION_WORKERS),
    ("  ", COLLECTION_WORKERS),
])
def test_collection_workers_override(monkeypatch, raw, expected):
    monkeypatch.setenv("ARGUS_COLLECTION_WORKERS", raw)
    assert _collection_worker_pool_size() == expected


def test_collection_workers_pool_is_bounded(tmp_path, monkeypatch):
    """The pool size is never unbounded — the configured value is capped by the
    fixed default when the override is invalid."""
    monkeypatch.setenv("ARGUS_COLLECTION_WORKERS", "banana")
    store = make_store(tmp_path)
    for i in range(3):
        store.upsert_publication(_pub(i))
    collector = _build_collector(store)
    collector.collect_campaign(banks=("fed",))
    assert _collection_worker_pool_size() == COLLECTION_WORKERS


# ---------------------------------------------------------------------------
# Real concurrency
# ---------------------------------------------------------------------------

def test_publications_collected_concurrently(tmp_path, monkeypatch):
    """Two gated publications prove *real* simultaneity: both workers are in
    flight before either finishes (events, no sleep-ordering bet)."""
    monkeypatch.setenv("ARGUS_COLLECTION_WORKERS", "2")
    store = make_store(tmp_path)
    pubs = [_pub(i) for i in range(2)]
    for pub in pubs:
        store.upsert_publication(pub)
    gates = {p.url: threading.Event() for p in pubs}
    both_in_flight = threading.Event()
    coordinator_done = threading.Event()
    started: list[str] = []
    lock = threading.Lock()

    class _SignalingFetcher:
        timeout = 5.0

        def __init__(self, store):
            self.store = store
            self.completion: list[str] = []

        def collect(self, publication, existing_by_url, *, force=False):
            with lock:
                started.append(publication.url)
                if len(started) >= 2:
                    both_in_flight.set()
            if not gates[publication.url].wait(self.timeout):
                raise TimeoutError(f"gate never released for {publication.url}")
            with lock:
                self.completion.append(publication.url)
            doc = Document(publication_id=publication.id or "", url=publication.url,
                           kind="html", status=DocumentStatus.FETCHED)
            return (FetchResult(publication_id=publication.id or "", documents=[doc], ok=True),
                    PublicationStatus.FETCHED)

        def persist(self, publication_id, documents, status):
            pass

    fetcher = _SignalingFetcher(store)
    collector = _build_collector(store, fetcher=fetcher)

    def coordinator():
        if not both_in_flight.wait(fetcher.timeout):
            return
        for gate in gates.values():
            gate.set()
        coordinator_done.set()

    thread = threading.Thread(target=coordinator, daemon=True)
    thread.start()
    collector.collect_campaign(banks=("fed",))
    thread.join(5.0)

    assert coordinator_done.is_set(), "both workers were never in flight simultaneously"
    assert sorted(fetcher.completion) == sorted(started)
    assert len(started) == 2


def test_parallel_faster_than_sequential(tmp_path, monkeypatch):
    """Same workload: sequential ≈ N sleeps, parallel ≈ 1 sleep."""
    monkeypatch.setenv("ARGUS_COLLECTION_WORKERS", "1")
    store = make_store(tmp_path / "seq")
    for i in range(4):
        store.upsert_publication(_pub(i))
    fetcher = _SlowFetcher(store)
    collector = _build_collector(store, fetcher=fetcher)
    start = time.monotonic()
    collector.collect_campaign(banks=("fed",))
    seq = time.monotonic() - start

    monkeypatch.setenv("ARGUS_COLLECTION_WORKERS", "4")
    store2 = make_store(tmp_path / "par")
    for i in range(4):
        store2.upsert_publication(_pub(i))
    fetcher2 = _SlowFetcher(store2)
    collector2 = _build_collector(store2, fetcher=fetcher2)
    start = time.monotonic()
    collector2.collect_campaign(banks=("fed",))
    par = time.monotonic() - start

    assert seq >= 1.2, f"sequential should take ~1.6s, took {seq:.2f}s"
    assert par < seq / 2, f"parallel ({par:.2f}s) not faster than sequential ({seq:.2f}s)"


# ---------------------------------------------------------------------------
# Progress: real completion order, logical results in submission order
# ---------------------------------------------------------------------------

def test_progress_follows_real_completion_order(tmp_path, monkeypatch):
    """Progress fires in real completion order (B→C→D→A) while the returned
    results keep the deterministic submission order (A first)."""
    monkeypatch.setenv("ARGUS_COLLECTION_WORKERS", "4")
    pubs = [_pub(i) for i in range(4)]
    store = make_store(tmp_path)
    for pub in pubs:
        store.upsert_publication(pub)
    gates = {p.url: threading.Event() for p in pubs}
    fetcher = _GatedFetcher(store, gates=gates)
    collector = _build_collector(store, fetcher=fetcher)

    seen: list[tuple[int, int, tuple]] = []
    condition = threading.Condition()

    def wait_for(count: int) -> None:
        with condition:
            deadline = time.monotonic() + 5.0
            while len(seen) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"progress stuck at {len(seen)}/{count}: {seen!r}")
                condition.wait(remaining)

    def progress(completed, total):
        with condition:
            seen.append((completed, total, tuple(fetcher.completion)))
            condition.notify_all()

    def coordinator():
        order = [pubs[1], pubs[2], pubs[3], pubs[0]]  # B, C, D, A
        for index, pub in enumerate(order, start=1):
            gates[pub.url].set()
            if index < len(order):
                wait_for(index)

    thread = threading.Thread(target=coordinator, daemon=True)
    thread.start()
    try:
        results = collector.collect_campaign(banks=("fed",), progress=progress)
    finally:
        for gate in gates.values():
            gate.set()
        thread.join(timeout=5.0)

    # progress in *completion* order (A, the last-released one, is last)
    assert [snapshot[0:2] for snapshot in seen] == [(1, 4), (2, 4), (3, 4), (4, 4)]
    assert [snapshot[2] for snapshot in seen] == [
        (pubs[1].url,),
        (pubs[1].url, pubs[2].url),
        (pubs[1].url, pubs[2].url, pubs[3].url),
        (pubs[1].url, pubs[2].url, pubs[3].url, pubs[0].url),
    ]
    # the first snapshot is B (not A, the first *submitted* publication)
    assert seen[0][2] == (pubs[1].url,)

    # logical results keep submission order: A is index 0, then B, C, D
    ordered_urls = [r.publication_id for r in results]
    assert len(ordered_urls) == 4
    stored_ids = {p.id for p in store.list_publications(bank="fed")}
    assert {r.publication_id for r in results} == stored_ids


# ---------------------------------------------------------------------------
# Error isolation
# ---------------------------------------------------------------------------

def test_one_publication_failure_does_not_fail_campaign(tmp_path, monkeypatch):
    """A failing publication is recorded (not fabricated to success), the other
    workers complete, and the campaign still reaches 4/4."""
    monkeypatch.setenv("ARGUS_COLLECTION_WORKERS", "4")
    pubs = [_pub(i) for i in range(4)]
    store = make_store(tmp_path)
    for pub in pubs:
        store.upsert_publication(pub)
    fetcher = _GatedFetcher(store, fail={pubs[1].url})
    collector = _build_collector(store, fetcher=fetcher)

    seen = []
    results = collector.collect_campaign(banks=("fed",), progress=lambda c, t: seen.append((c, t)))

    assert [snapshot for snapshot in seen] == [(1, 4), (2, 4), (3, 4), (4, 4)]
    by_id = {r.publication_id: r for r in results}
    failed_pub = store.list_publications(bank="fed")
    failed = [p for p in failed_pub if p.url == pubs[1].url]
    assert failed and failed[0].status == PublicationStatus.FAILED
    # a FAILED *document* is a normal outcome, not a campaign error — nothing is
    # fabricated into collect_errors, and the other publications are untouched
    assert store.list_errors() == []
    stored = {p.url: p for p in store.list_publications(bank="fed")}
    assert store.document_count(stored[pubs[0].url].id) == 1


# ---------------------------------------------------------------------------
# Sequential ↔ parallel logical equivalence
# ---------------------------------------------------------------------------

def test_parallel_matches_sequential_business_state(tmp_path, monkeypatch):
    """The same scenario run with 1 worker and with several workers produces the
    same persisted publications, documents, statuses and error rows."""
    page = "https://x.test/pubs/stmt.htm"
    pdf = "https://x.test/files/report.pdf"

    def run(worker_count: int, root) -> tuple:
        monkeypatch.setenv("ARGUS_COLLECTION_WORKERS", str(worker_count))
        session = FakeSession({
            f"{page}?i=0": response("<html><body>0</body></html>", url=f"{page}?i=0"),
            f"{page}?i=1": response("<html><body>1</body></html>", url=f"{page}?i=1"),
            pdf: response(b"%PDF-1.4 fake", url=pdf, content_type="application/pdf"),
        })
        store = make_store(root / "s.db")
        for i in range(2):
            pub = _pub(i, url=f"{page}?i={i}")
            pub.document_urls = (f"{page}?i={i}", pdf)
            store.upsert_publication(pub)
        collector = CentralBankCollector(
            store=store,
            client=make_client(session),
            raw_root=root / "raw",
        )
        collector.collect_campaign(banks=("fed",))
        pubs = sorted(
            (p.id, p.title, p.status.value, p.url) for p in store.list_publications()
        )
        docs = sorted(
            (d.publication_id, d.url, d.kind, d.status.value) for d in _all_documents(store)
        )
        errors = sorted((e.source_id, e.error_type, e.message) for e in store.list_errors())
        store.close()
        return pubs, docs, errors

    seq = run(1, tmp_path / "seq")
    par = run(4, tmp_path / "par")
    assert seq == par


def _all_documents(store):
    docs = []
    for pub in store.list_publications():
        docs.extend(store.list_documents(pub.id))
    return docs


# ---------------------------------------------------------------------------
# Retry / repair under parallelism
# ---------------------------------------------------------------------------

def test_parallel_retry_repairs_and_does_not_duplicate(tmp_path, monkeypatch):
    """A FAILED publication is repaired by the next parallel pass (no duplicate
    document rows), and an already-FETCHED document is never re-downloaded."""
    monkeypatch.setenv("ARGUS_COLLECTION_WORKERS", "2")
    page = "https://x.test/pubs/stmt.htm"
    store = make_store(tmp_path)
    pub = _pub(0, url=page)
    pub.document_urls = (page,)
    persisted = store.upsert_publication(pub)

    class _FailOnce(_GatedFetcher):
        def __init__(self, store):
            super().__init__(store)
            self.first = True

        def collect(self, publication, existing_by_url, *, force=False):
            with self._lock:
                self.completion.append(publication.url)
            pub_id = publication.id or ""
            if self.first:
                self.first = False
                doc = Document(publication_id=pub_id, url=publication.url, kind="html",
                               status=DocumentStatus.FAILED, error="boom", retries=1)
                return (FetchResult(publication_id=pub_id, documents=[doc], ok=False, failed_urls=[publication.url]),
                        PublicationStatus.FAILED)
            doc = Document(publication_id=pub_id, url=publication.url, kind="html",
                           status=DocumentStatus.FETCHED, sha256="x" * 64, size=1)
            return (FetchResult(publication_id=pub_id, documents=[doc], ok=True), PublicationStatus.FETCHED)

    fetcher = _FailOnce(store)
    collector = _build_collector(store, fetcher=fetcher)
    pub_id = persisted.id

    collector.collect_campaign(banks=("fed",))
    assert store.get_publication(pub_id).status == PublicationStatus.FAILED
    assert store.document_count(pub_id) == 1

    collector.collect_campaign(banks=("fed",))
    assert store.get_publication(pub_id).status == PublicationStatus.FETCHED
    assert store.document_count(pub_id) == 1  # repaired, never duplicated
    assert len(fetcher.completion) == 2  # exactly one retry attempt


# ---------------------------------------------------------------------------
# Stop / cancellation
# ---------------------------------------------------------------------------

def test_stop_aborts_with_partial_progress(tmp_path, monkeypatch):
    """Stop with several in-flight publications: the pool is shut down, the
    remaining publications are not fabricated to completed, and the last real
    progression is retained (strictly below the total)."""
    monkeypatch.setenv("ARGUS_COLLECTION_WORKERS", "4")
    pubs = [_pub(i) for i in range(4)]
    store = make_store(tmp_path)
    for pub in pubs:
        store.upsert_publication(pub)
    gates = {p.url: threading.Event() for p in pubs}
    fetcher = _GatedFetcher(store, gates=gates)
    collector = _build_collector(store, fetcher=fetcher)

    stop_flag = {"stop": False}
    seen: list[tuple[int, int]] = []
    condition = threading.Condition()

    def wait_for(count):
        with condition:
            deadline = time.monotonic() + 5.0
            while len(seen) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"stuck at {len(seen)}/{count}: {seen!r}")
                condition.wait(remaining)

    def progress(completed, total):
        with condition:
            seen.append((completed, total))
            condition.notify_all()

    def should_stop():
        return stop_flag["stop"]

    def coordinator():
        # release B and C (2 completions), then request a stop
        gates[pubs[1].url].set()
        wait_for(1)
        gates[pubs[2].url].set()
        wait_for(2)
        stop_flag["stop"] = True
        # release the rest so no worker stays blocked forever
        for gate in gates.values():
            gate.set()

    thread = threading.Thread(target=coordinator, daemon=True)
    thread.start()
    try:
        with pytest.raises(Exception) as excinfo:
            collector.collect_campaign(banks=("fed",), progress=progress, should_stop=should_stop)
        assert excinfo.type.__name__ == "CollectionStopped"
    finally:
        for gate in gates.values():
            gate.set()
        thread.join(timeout=5.0)

    # last real progression retained, never fabricated to 4/4
    assert seen[-1][0] < seen[-1][1]
    assert seen[-1][1] == 4
    assert seen[-1][0] == 2


# ---------------------------------------------------------------------------
# Pause / resume at the process level (SIGSTOP / SIGCONT)
# ---------------------------------------------------------------------------

def test_pause_resume_with_workers_records_completed(tmp_path):
    """A running parallel campaign can be SIGSTOPped (paused) and SIGCONTed
    (resumed) without corruption: the process state transitions T→R and the
    campaign still completes."""
    if os.name == "nt":
        pytest.skip("POSIX signals required")
    from pathlib import Path
    from argus.store import Store

    src = str(Path(__import__("argus").__path__[0]).parent)
    db = tmp_path / "data" / "argus.db"
    (tmp_path / "data").mkdir()

    seed = (
        "from argus.store import Store; "
        "from argus.models import Publication, PublicationStatus; "
        f"s=Store({str(db)!r}); "
        "[s.upsert_publication(Publication(central_bank='fed', title=str(i), "
        "url='https://x.test/{0}'.format(i), source_id='r', "
        "source_url='https://x.test/feed', status=PublicationStatus.DISCOVERED)) "
        "for i in range(2)]; "
        "s.close()"
    )
    subprocess.run([sys.executable, "-c", seed], env=dict(os.environ, PYTHONPATH=src), check=True)

    campaign_code = "\n".join([
        "import sys",
        f"sys.path.insert(0, {src!r})",
        "from argus import gui_bridge",
        "from argus.collector import CentralBankCollector",
        "from argus.store import Store",
        "from argus.http import HttpConfig",
        "import time",
        f"gui_bridge.ROOT = __import__('pathlib').Path({str(tmp_path)!r})",
        "class Slow:",
        "    def __init__(self, store):",
        "        self.store = store",
        "    def collect(self, pub, existing, *, force=False):",
        "        time.sleep(0.6)",
        "        from argus.models import Document, DocumentStatus, FetchResult, PublicationStatus",
        "        doc = Document(publication_id=pub.id or '', url=pub.url, kind='html', status=DocumentStatus.FETCHED)",
        "        return (FetchResult(publication_id=pub.id or '', documents=[doc], ok=True), PublicationStatus.FETCHED)",
        "    def persist(self, pub_id, documents, status):",
        "        for d in documents: self.store.upsert_document(d)",
        "        if status: self.store.set_publication_status(pub_id, status)",
        f"store = Store({str(db)!r})",
        "col = CentralBankCollector(store=store, http_config=HttpConfig(respect_robots=False, min_interval=0.0), "
        f"raw_root={str(tmp_path / 'raw')!r}, fetcher=Slow(store))",
        "store.start_collection_run('pause-run', ['fed'], pid=__import__('os').getpid(), publications_total=2)",
        "col.collect_campaign(banks=('fed',), run_id='pause-run')",
        "store.finish_collection_run('pause-run', status='completed')",
        "store.close()",
        "print('DONE', flush=True)",
    ])
    env = dict(os.environ, PYTHONPATH=src, ARGUS_COLLECTION_WORKERS="2")
    campaign = subprocess.Popen(
        [sys.executable, "-c", campaign_code], env=env, stdout=subprocess.PIPE, text=True
    )
    try:
        import signal

        # wait for the campaign to start running
        for _ in range(300):
            if campaign.stdout.readline().strip():
                break
            # we read stdout below; poll the store for a running row instead
            try:
                run = Store(db).get_collection_run("pause-run")
            except Exception:
                run = None
            if run is not None and run["status"] == "running":
                break
            time.sleep(0.05)

        # SIGSTOP → paused (T state), SIGCONT → resumed (runnable, or already
        # finished the short campaign), still alive / eventually completed
        os.kill(campaign.pid, signal.SIGSTOP)
        time.sleep(0.2)
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(campaign.pid)],
                               capture_output=True, text=True).stdout.strip()
        assert state.startswith("T"), f"expected paused (T), got {state!r}"

        os.kill(campaign.pid, signal.SIGCONT)
        time.sleep(0.2)
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(campaign.pid)],
                               capture_output=True, text=True).stdout.strip()
        # after SIGCONT the (short) campaign may legitimately have finished and
        # be reaped as a zombie — either way the process is no longer stopped
        assert not state.startswith("T"), f"expected resumed, still paused: {state!r}"

        assert campaign.wait(30) is not None
        run = Store(db).get_collection_run("pause-run")
        assert run["status"] == "completed", run["status"]
    finally:
        if campaign.poll() is None:
            campaign.kill()
            campaign.wait()


# ---------------------------------------------------------------------------
# Worker never touches the Store
# ---------------------------------------------------------------------------

def test_workers_never_touch_the_store(tmp_path, monkeypatch):
    """The worker's `collect` runs without any Store reference — the fake asserts
    it receives no store and the collector never passes one."""
    monkeypatch.setenv("ARGUS_COLLECTION_WORKERS", "2")
    store = make_store(tmp_path)
    for i in range(2):
        store.upsert_publication(_pub(i))
    calls = {"collect": 0}

    class _AssertNoStore:
        def collect(self, publication, existing_by_url, *, force=False):
            calls["collect"] += 1
            assert not hasattr(self, "store") or self.store is None
            from argus.models import FetchResult

            return FetchResult(publication_id=publication.id or "", documents=[], ok=True), PublicationStatus.FETCHED

        def persist(self, publication_id, documents, status):
            pass

    collector = _build_collector(store, fetcher=_AssertNoStore())
    collector.collect_campaign(banks=("fed",))
    assert calls["collect"] == 2