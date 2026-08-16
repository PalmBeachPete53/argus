"""Parallel discovery scheduler tests.

The scheduler must distribute *arbitrary* enabled sources (never a hardcoded
bank/source list) to a bounded worker pool, run them truly concurrently, keep
the logical results identical to a sequential run, isolate per-source errors,
and remain interruptible (Stop / app close) while workers are in flight.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

import pytest

from argus import collector as collector_mod
from argus.adapters.base import BankAdapter
from argus.collector import CentralBankCollector
from argus.discovery import STRATEGIES
from argus.discovery.base import DiscoveryStrategy
from argus.models import CentralBank, DiscoverySpec, Publication, Source as SourceModel
from conftest import FakeSession, make_client, make_store

ARBITRARY_SOURCE_IDS = ("source_a", "source_b", "source_c", "source_d")
ARBITRARY_BANK = "arbitrary_bank"  # not a real bank — the scheduler must not care


class _SlowDiscovery(DiscoveryStrategy):
    """One source == one blocking sleep; used to prove real concurrency."""

    kind = "slow"

    def discover(self):
        time.sleep(1.0)
        return [self._make(url=f"https://x/{self.source.id}", title=self.source.id)]


class _FixedDiscovery(DiscoveryStrategy):
    """Deterministic per-source publications; used for logical-equivalence."""

    kind = "fixed"

    def discover(self):
        return [
            self._make(url=f"https://x/{self.source.id}/1", title=f"{self.source.id} one"),
            self._make(url=f"https://x/{self.source.id}/2", title=f"{self.source.id} two"),
        ]


class _ExplodingDiscovery(DiscoveryStrategy):
    kind = "explode"

    def discover(self):
        raise RuntimeError("boom")


class _GatedDiscovery(DiscoveryStrategy):
    """One source == block on a per-source ``threading.Event``.

    Class-level ``gates`` / ``completion`` (create_strategy only passes
    source/client/now) let a test *deterministically* control, and observe,
    the real completion order of the workers.
    """

    kind = "gated"
    gates: dict[str, threading.Event] = {}
    completion: list[str] = []
    _deadline = 5.0

    def discover(self):
        gate = self.gates.get(self.source.id)
        if gate is not None:
            gate.wait(self._deadline)
        self.completion.append(self.source.id)
        return [self._make(url=f"https://x/{self.source.id}", title=self.source.id)]


class _GatedExplodingDiscovery(_GatedDiscovery):
    """Gated, like ``_GatedDiscovery``, but every source fails on return."""

    kind = "gated_explode"

    def discover(self):
        gate = self.gates.get(self.source.id)
        if gate is not None:
            gate.wait(self._deadline)
        self.completion.append(self.source.id)
        raise RuntimeError("boom")


class _ArbitraryAdapter(BankAdapter):
    """A dynamic, non-bank-catalog set of sources (kind + list given by the test).

    ``kinds`` (source_id → kind) lets a test give individual sources a different
    strategy, without touching the scheduler.
    """

    def __init__(
        self,
        kind: str,
        source_ids: tuple[str, ...] = ARBITRARY_SOURCE_IDS,
        kinds: dict[str, str] | None = None,
    ):
        self._kind = kind
        self._source_ids = source_ids
        self._kinds = kinds or {}
        super().__init__()

    def _build(self):
        bank = CentralBank(ARBITRARY_BANK, "Arbitrary Bank", "XXX", "arbitrary.example")
        sources = [
            SourceModel(
                id=source_id,
                central_bank=ARBITRARY_BANK,
                name=source_id,
                discovery=DiscoverySpec(kind=self._kinds.get(source_id, self._kind), url=f"https://x/{source_id}"),
            )
            for source_id in self._source_ids
        ]
        return bank, sources


def _register_strategy(kind: str, cls) -> None:
    original = STRATEGIES.get(kind)
    STRATEGIES[kind] = cls
    return original


def _build_collector(tmp_path, *, kind, source_ids=ARBITRARY_SOURCE_IDS, store_path="argus.db"):
    from argus.registry import SourceRegistry

    registry = SourceRegistry(adapters=[_ArbitraryAdapter(kind, source_ids)])
    store = make_store(tmp_path / store_path)
    collector = CentralBankCollector(
        store=store,
        registry=registry,
        client=make_client(FakeSession({})),
        raw_root=tmp_path / "raw",
    )
    return collector, store


def test_sources_discovered_concurrently(tmp_path, monkeypatch):
    """Four independent arbitrary sources running on four workers finish in
    ~one sleep, not four: real network-level concurrency."""
    import time as _time

    original = _register_strategy("slow", _SlowDiscovery)
    try:
        monkeypatch.setattr(collector_mod, "DISCOVERY_WORKERS", 4)
        collector, store = _build_collector(tmp_path, kind="slow")
        start = _time.monotonic()
        pubs = collector.discover_all()
        elapsed = _time.monotonic() - start

        # every arbitrary source was handed to a worker
        assert {p.source_id for p in pubs} == set(ARBITRARY_SOURCE_IDS)
        assert len(store.list_publications()) == 4
        # 4 x 1s sleeps in parallel ≈ 1s; 4x slower would be ~4s
        assert elapsed < 3.0, f"parallel run took {elapsed:.2f}s — workers not concurrent"
    finally:
        STRATEGIES["slow"] = original


def test_sequential_is_slower_than_parallel(tmp_path, monkeypatch):
    """Same sources, same window, same environment: sequential ≈ 4 sleeps,
    parallel ≈ 1 sleep."""
    import time as _time

    original = _register_strategy("slow", _SlowDiscovery)
    try:
        monkeypatch.setattr(collector_mod, "DISCOVERY_WORKERS", 1)
        collector_seq, _ = _build_collector(tmp_path, kind="slow", store_path="seq.db")
        start = _time.monotonic()
        collector_seq.discover_all()
        seq = _time.monotonic() - start

        monkeypatch.setattr(collector_mod, "DISCOVERY_WORKERS", 4)
        collector_par, _ = _build_collector(tmp_path, kind="slow", store_path="par.db")
        start = _time.monotonic()
        collector_par.discover_all()
        par = _time.monotonic() - start

        assert seq >= 3.5, f"sequential should take ~4s, took {seq:.2f}s"
        assert par < 2.5, f"parallel should take ~1s, took {par:.2f}s"
        assert par < seq / 2, f"parallel ({par:.2f}s) not faster than sequential ({seq:.2f}s)"
    finally:
        STRATEGIES["slow"] = original


def test_parallel_preserves_logical_results(tmp_path, monkeypatch):
    """A parallel run produces exactly the publications of a sequential run."""
    original = _register_strategy("fixed", _FixedDiscovery)
    try:
        monkeypatch.setattr(collector_mod, "DISCOVERY_WORKERS", 1)
        collector_seq, store_seq = _build_collector(tmp_path, kind="fixed", store_path="seq.db")
        collector_seq.discover_all()

        monkeypatch.setattr(collector_mod, "DISCOVERY_WORKERS", 4)
        collector_par, store_par = _build_collector(tmp_path, kind="fixed", store_path="par.db")
        collector_par.discover_all()

        seq = sorted(p.url for p in store_seq.list_publications())
        par = sorted(p.url for p in store_par.list_publications())
        assert seq == par
        assert len(seq) == 2 * len(ARBITRARY_SOURCE_IDS)
    finally:
        STRATEGIES["fixed"] = original


def test_source_error_does_not_fail_campaign(tmp_path, monkeypatch):
    """One failing source must not kill the other workers nor the campaign."""
    original = _register_strategy("fixed", _FixedDiscovery)
    original_explode = _register_strategy("explode", _ExplodingDiscovery)
    try:
        monkeypatch.setattr(collector_mod, "DISCOVERY_WORKERS", 4)
        # two good sources + one exploding
        source_ids = ("source_a", "source_b", "broken")
        from argus.registry import SourceRegistry

        registry = SourceRegistry(
            adapters=[_ArbitraryAdapter("fixed", source_ids, kinds={"broken": "explode"})]
        )
        store = make_store(tmp_path / "db")
        collector = CentralBankCollector(
            store=store,
            registry=registry,
            client=make_client(FakeSession({})),
            raw_root=tmp_path / "raw",
        )
        publications = collector.discover_all()
        assert {p.source_id for p in publications} == {"source_a", "source_b"}
        errors = store.list_errors()
        assert any(e.source_id == "broken" for e in errors)
        assert store.count_publications() == 4
    finally:
        STRATEGIES["fixed"] = original
        STRATEGIES["explode"] = original_explode


def test_progress_reports_completed_over_total(tmp_path, monkeypatch):
    """The scheduler exposes completed_sources / total_sources without knowing
    the candidate count in advance (the future progress-bar hook)."""
    original = _register_strategy("fixed", _FixedDiscovery)
    try:
        monkeypatch.setattr(collector_mod, "DISCOVERY_WORKERS", 2)
        collector, _ = _build_collector(tmp_path, kind="fixed")
        seen: list[tuple[int, int]] = []
        collector.discover_all(progress=lambda completed, total: seen.append((completed, total)))
        assert seen == [(1, 4), (2, 4), (3, 4), (4, 4)]
    finally:
        STRATEGIES["fixed"] = original


def _run_gated(collector, *source_ids):
    """Drive a gated discovery, releasing per-source gates in the given order.

    ``discover_all`` runs on the caller (main) thread — the Store is single
    connection and thread-affine, exactly like production — while a coordinator
    thread releases the gates, waiting on the observed progress after each one.
    This is deterministic (events, no sleep-ordering bets): the return value is
    the list of ``(completed, total, completion_order_tuple)`` snapshots seen
    by the progress callback, plus the discovered publications.
    """
    from threading import Condition

    seen: list[tuple[int, int, tuple]] = []
    condition = Condition()
    failures: list[BaseException] = []

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
            seen.append((completed, total, tuple(_GatedDiscovery.completion)))
            condition.notify_all()

    def coordinator():
        try:
            for index, source_id in enumerate(source_ids, start=1):
                _GatedDiscovery.gates[source_id].set()
                if index < len(source_ids):
                    wait_for(index)
        except BaseException as exc:  # pragma: no cover - timeout / unexpected
            failures.append(exc)
        finally:
            for source_id in source_ids:  # never leave workers blocked
                _GatedDiscovery.gates[source_id].set()

    thread = threading.Thread(target=coordinator, daemon=True)
    thread.start()
    try:
        pubs = collector.discover_all(progress=progress)
    finally:
        thread.join(timeout=5.0)
    assert not failures, failures
    return seen, pubs


def test_progress_follows_real_completion_order(tmp_path, monkeypatch):
    """The callback fires at the real completion instant, in completion order:
    a slow late source never stalls the progress of already-finished sources.

    All four sources run simultaneously behind per-source gates. B is released
    while A is still in flight: the callback already reports 1/4, proving the
    scheduler does not wait for A (the first submitted source).
    """
    original = _register_strategy("gated", _GatedDiscovery)
    try:
        monkeypatch.setattr(collector_mod, "DISCOVERY_WORKERS", 4)
        gates = {source_id: threading.Event() for source_id in ARBITRARY_SOURCE_IDS}
        completion: list[str] = []
        _GatedDiscovery.gates = gates
        _GatedDiscovery.completion = completion
        collector, _ = _build_collector(tmp_path, kind="gated")

        seen, pubs = _run_gated(collector, "source_b", "source_c", "source_d", "source_a")

        # progress in *completion* order, A (the slow one) last
        assert [snapshot[0:2] for snapshot in seen] == [(1, 4), (2, 4), (3, 4), (4, 4)]
        assert [snapshot[2] for snapshot in seen] == [
            ("source_b",),
            ("source_b", "source_c"),
            ("source_b", "source_c", "source_d"),
            ("source_b", "source_c", "source_d", "source_a"),
        ]
        # B reported done while A was still running (and before its gate).
        # The very first snapshot already contains B and not A.
        assert seen[0][2] == ("source_b",) and "source_a" not in seen[0][2]
        # logical results unchanged: every source, dedup intact
        assert {p.source_id for p in pubs} == set(ARBITRARY_SOURCE_IDS)
    finally:
        STRATEGIES["gated"] = original
        _GatedDiscovery.gates = {}
        _GatedDiscovery.completion = []


def test_progress_counts_failed_source_and_still_reaches_total(tmp_path, monkeypatch):
    """A source that fails fast is counted as a completed step as soon as it
    fails, is logged, does not stall slower in-flight sources, and the campaign
    still reaches 4/4. A (the slow source) must never delay the 1/3 ticks made
    possible by B/C/D."""
    original = _register_strategy("gated", _GatedDiscovery)
    original_explode = _register_strategy("gated_explode", _GatedExplodingDiscovery)
    try:
        monkeypatch.setattr(collector_mod, "DISCOVERY_WORKERS", 4)
        from argus.registry import SourceRegistry

        registry = SourceRegistry(
            adapters=[
                _ArbitraryAdapter(
                    "gated",
                    ARBITRARY_SOURCE_IDS,
                    kinds={"source_b": "gated_explode"},
                )
            ]
        )
        store = make_store(tmp_path / "db")
        collector = CentralBankCollector(
            store=store,
            registry=registry,
            client=make_client(FakeSession({})),
            raw_root=tmp_path / "raw",
        )
        gates = {source_id: threading.Event() for source_id in ARBITRARY_SOURCE_IDS}
        completion: list[str] = []
        _GatedDiscovery.gates = gates
        _GatedDiscovery.completion = completion
        _GatedExplodingDiscovery.gates = gates
        _GatedExplodingDiscovery.completion = completion

        seen, pubs = _run_gated(collector, "source_b", "source_c", "source_d", "source_a")

        # the failing B counts 1/4 before A (still running) is done
        assert seen[0][0:2] == (1, 4) and seen[0][2] == ("source_b",)
        assert [snapshot[0:2] for snapshot in seen] == [(1, 4), (2, 4), (3, 4), (4, 4)]
        # a failure never hides B's error from the store, and never produces pubs
        assert {p.source_id for p in pubs} == {"source_a", "source_c", "source_d"}
        assert store.count_publications() == 3
        assert any(e.source_id == "source_b" for e in store.list_errors())
    finally:
        STRATEGIES["gated"] = original
        STRATEGIES["gated_explode"] = original_explode
        _GatedDiscovery.gates = {}
        _GatedDiscovery.completion = []
        _GatedExplodingDiscovery.gates = {}
        _GatedExplodingDiscovery.completion = []


def test_window_applied_identically_to_each_worker(tmp_path, monkeypatch):
    """Every worker receives the same date_start/date_end and the window is
    enforced exactly once (start-inclusive, end-exclusive), as in sequential."""
    from argus.discovery.base import DiscoveryStrategy

    class _DatedDiscovery(DiscoveryStrategy):
        kind = "dated"

        def discover(self):
            return [
                self._make(
                    url=f"https://x/{self.source.id}/in",
                    title=f"{self.source.id} in",
                    publication_date=datetime(2026, 7, 20, tzinfo=timezone.utc),
                ),
                self._make(
                    url=f"https://x/{self.source.id}/out",
                    title=f"{self.source.id} out",
                    publication_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
                ),
            ]

    original = _register_strategy("dated", _DatedDiscovery)
    try:
        monkeypatch.setattr(collector_mod, "DISCOVERY_WORKERS", 4)
        collector, store = _build_collector(tmp_path, kind="dated")
        pubs = collector.discover_all(
            date_start=datetime(2026, 7, 1, tzinfo=timezone.utc),
            date_end=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        assert len(pubs) == 4  # only the /in publications
        assert all(p.publication_date.date() == datetime(2026, 7, 20).date() for p in pubs)
        assert store.count_publications() == 4
    finally:
        STRATEGIES["dated"] = original


def test_stop_interrupts_in_flight_workers_promptly(tmp_path):
    """A SIGTERM (Stop / app close) raised while workers are in flight
    propagates promptly — it must not wait for the slow workers to finish."""
    from pathlib import Path

    src = str(Path(__import__("argus").__path__[0]).parent)
    db = str(tmp_path / "stop.db")
    raw = str(tmp_path / "raw")
    script = f"""
import os, signal, sys, threading, time
sys.path.insert(0, {src!r})
from argus import collector as collector_mod
from argus.collector import CentralBankCollector
from argus.discovery import STRATEGIES
from argus.discovery.base import DiscoveryStrategy
from argus.gui_bridge import DiscoveryStopped
from argus.http import HttpClient
from argus.models import CentralBank, DiscoverySpec, Source
from argus.registry import SourceRegistry
from argus.store import Store

class Slow(DiscoveryStrategy):
    kind = "slow"
    def discover(self):
        time.sleep(10)
        return []

STRATEGIES["slow"] = Slow

class Adapter:
    def __init__(self):
        bank = CentralBank("arb", "Arb", "X", "x")
        self._bank = bank
        self._sources = [
            Source(id=f"source_{{i}}", central_bank="arb", name=str(i),
                   discovery=DiscoverySpec(kind="slow", url=f"https://x/{{i}}"))
            for i in range(4)
        ]
    @property
    def bank(self):
        return self._bank
    @property
    def sources(self):
        return self._sources

def _stop(signum, frame):
    raise DiscoveryStopped("stop requested")

signal.signal(signal.SIGTERM, _stop)
store = Store({db!r})
registry = SourceRegistry(adapters=[Adapter()])
collector = CentralBankCollector(store=store, registry=registry,
                                 client=HttpClient(), raw_root={raw!r})

def _sig():
    time.sleep(0.5)
    os.kill(os.getpid(), signal.SIGTERM)

threading.Thread(target=_sig, daemon=True).start()
start = time.monotonic()
try:
    collector.discover_all()
    print("NO-STOP")
except DiscoveryStopped:
    print(f"STOPPED {{time.monotonic() - start:.2f}}")
    sys.stdout.flush()
    os._exit(0)
"""
    env = dict(os.environ, PYTHONPATH=src)
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30, env=env
    )
    out = result.stdout.strip()
    assert out.startswith("STOPPED"), f"expected prompt stop, got: {out!r} {result.stderr!r}"
    elapsed = float(out.split()[1])
    assert elapsed < 5.0, f"stop took {elapsed:.2f}s — it waited for the slow workers"
