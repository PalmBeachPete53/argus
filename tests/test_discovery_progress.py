"""Core-driven discovery progression tests.

The Core is the single source of truth for campaign progression:
``discover_all`` fixes ``sources_total`` at launch (so ``0 / N`` is readable
immediately) and advances ``sources_completed`` as each source *actually*
finishes — through the same serialized writer as every other Store mutation,
never from a worker thread. A failing or empty source still counts as one
completed step; a stopped campaign keeps its last known value and is never
fabricated into ``total / total``.

Covered here:

* in process, the exact ``set_discovery_progress`` call sequence
  (initialisation ``0 / N``, then one tick per real completion, in completion
  order) and the error-counting rule;
* cross-process (a real detached campaign, like the GUI's), the persistence
  a *separate* process polls: live progression for a normal run, the partial
  value kept by a Stop, error counting, and a progress that freezes during
  Pause and continues after Resume.

All stores are per-test temp files — ``data/argus.db`` is never opened.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from argus import collector as collector_mod
from argus.collector import CentralBankCollector
from argus.discovery import STRATEGIES
from argus.discovery.base import DiscoveryStrategy
from argus.models import CentralBank, DiscoverySpec, Source as SourceModel
from argus.registry import SourceRegistry
from argus.store import Store
from conftest import FakeSession, make_client

ARBITRARY_BANK = "arbitrary_bank"
ARBITRARY_SOURCE_IDS = ("source_a", "source_b", "source_c", "source_d")

_SRC_ROOT = str(Path(__import__("argus").__path__[0]).parent)


class _RecordingStore(Store):
    """A real Store that also records every ``set_discovery_progress`` call."""

    def __init__(self, path):
        super().__init__(path)
        self.progress_calls: list[tuple[int, int]] = []

    def set_discovery_progress(self, run_id, *, completed, total):
        self.progress_calls.append((completed, total))
        super().set_discovery_progress(run_id, completed=completed, total=total)


class _GatedDiscovery(DiscoveryStrategy):
    """One source == block on a per-source ``threading.Event``; class-level
    scratch lets a test deterministically control real completion order."""

    kind = "gated"
    gates: dict[str, threading.Event] = {}

    def discover(self):
        gate = self.gates.get(self.source.id)
        if gate is not None:
            gate.wait(5.0)
        return [self._make(url=f"https://x/{self.source.id}", title=self.source.id)]


class _ExplodingDiscovery(_GatedDiscovery):
    kind = "gated_explode"

    def discover(self):
        super().discover()
        raise RuntimeError("boom")


def _register_strategy(kind: str, cls):
    original = STRATEGIES.get(kind)
    STRATEGIES[kind] = cls
    return original


def _build_collector(store, *, kinds=None):
    """A collector over four arbitrary (non-catalog) sources. ``kinds`` maps a
    source id to a registered strategy kind, so one source can fail."""
    class _Adapter:
        def __init__(self):
            self._bank = CentralBank(ARBITRARY_BANK, "Arbitrary Bank", "XXX", "arbitrary.example")
            self._sources = [
                SourceModel(
                    id=source_id,
                    central_bank=ARBITRARY_BANK,
                    name=source_id,
                    discovery=DiscoverySpec(
                        kind=(kinds or {}).get(source_id, "gated"),
                        url=f"https://x/{source_id}",
                    ),
                )
                for source_id in ARBITRARY_SOURCE_IDS
            ]

        @property
        def bank(self):
            return self._bank

        @property
        def sources(self):
            return self._sources

    registry = SourceRegistry(adapters=[_Adapter()])
    return CentralBankCollector(
        store=store,
        registry=registry,
        client=make_client(FakeSession({})),
        raw_root="/tmp",
    )


def test_init_persists_zero_over_total_then_advances_in_completion_order(tmp_path):
    """discover_all writes 0 / N at launch, then one tick per real completion,
    in completion order (the slow/held source never blocks the others)."""
    original = _register_strategy("gated", _GatedDiscovery)
    try:
        collector_mod.DISCOVERY_WORKERS = 4
        store = _RecordingStore(tmp_path / "prog.db")
        store.start_discovery_run("prog-1", [ARBITRARY_BANK], sources_total=4)
        collector = _build_collector(store)
        gates = {source_id: threading.Event() for source_id in ARBITRARY_SOURCE_IDS}
        _GatedDiscovery.gates = gates

        failure = {}

        def coordinator():
            # Release B → wait for 1/4, C → 2/4, D → 3/4, then A last. Waiting
            # after each release proves the tick arrived before granting the
            # next source, so A (still held) can never inflate an earlier tick.
            try:
                for index, source_id in enumerate(("source_b", "source_c", "source_d", "source_a"), start=1):
                    gates[source_id].set()
                    if index < 4:
                        deadline = time.monotonic() + 5.0
                        # progress_calls starts at 1 (the init 0/N); each
                        # released gate must add exactly one more tick.
                        while len(store.progress_calls) <= index:
                            if time.monotonic() > deadline:
                                raise TimeoutError(
                                    f"progress stalled after releasing {source_id}"
                                )
                            time.sleep(0.005)
            except BaseException as exc:  # pragma: no cover - unexpected
                failure["error"] = exc
            finally:
                for source_id in ARBITRARY_SOURCE_IDS:
                    gates[source_id].set()

        thread = threading.Thread(target=coordinator, daemon=True)
        thread.start()
        try:
            collector.discover_all(run_id="prog-1")
        finally:
            thread.join(timeout=6.0)

        assert "error" not in failure, failure["error"]
        # init 0/4, then one tick per real completion — A finishes last.
        assert store.progress_calls == [(0, 4), (1, 4), (2, 4), (3, 4), (4, 4)]
        run = store.get_discovery_run("prog-1")
        assert run["sources_total"] == 4
        assert run["sources_completed"] == 4
    finally:
        STRATEGIES["gated"] = original
        _GatedDiscovery.gates = {}


def test_failing_source_counts_as_completed_and_error_kept(tmp_path):
    """A source that fails is a finished source for the progression (the tick
    still fires) while the failure stays logged for the operator."""
    original = _register_strategy("gated", _GatedDiscovery)
    original_explode = _register_strategy("gated_explode", _ExplodingDiscovery)
    try:
        collector_mod.DISCOVERY_WORKERS = 4
        store = _RecordingStore(tmp_path / "error.db")
        store.start_discovery_run("err-1", [ARBITRARY_BANK], sources_total=4)
        collector = _build_collector(store, kinds={"source_b": "gated_explode"})
        collector.discover_all(run_id="err-1")

        # source_b errored but still advanced the progression to 4/4; the
        # failure is recorded (never hidden to move the bar forward).
        assert store.progress_calls == [(0, 4), (1, 4), (2, 4), (3, 4), (4, 4)]
        run = store.get_discovery_run("err-1")
        assert run["sources_completed"] == 4
        assert run["sources_total"] == 4
        assert any(e.source_id == "source_b" for e in store.list_errors())
    finally:
        STRATEGIES["gated"] = original
        STRATEGIES["gated_explode"] = original_explode
        _GatedDiscovery.gates = {}


def test_empty_campaign_never_writes_zero_over_zero(tmp_path):
    """With no enabled sources the campaign returns early: no invalid 0/0
    progression is ever persisted."""
    original = _register_strategy("gated", _GatedDiscovery)
    try:
        store = _RecordingStore(tmp_path / "empty.db")
        store.start_discovery_run("empty-1", [ARBITRARY_BANK], sources_total=0)

        class _EmptyAdapter:
            @property
            def bank(self):
                return CentralBank(ARBITRARY_BANK, "Arbitrary Bank", "XXX", "arbitrary.example")

            @property
            def sources(self):
                return []

        registry = SourceRegistry(adapters=[_EmptyAdapter()])
        collector = CentralBankCollector(
            store=store,
            registry=registry,
            client=make_client(FakeSession({})),
            raw_root="/tmp",
        )
        assert collector.discover_all(run_id="empty-1") == []
        assert store.progress_calls == []
    finally:
        STRATEGIES["gated"] = original


# ---------------------------------------------------------------------------
# Real cross-process campaign (the GUI poll model): a detached process writes
# the progression to the shared store while the test process polls it.
# ---------------------------------------------------------------------------

_SUB_PROCESS_SCRIPT = r"""
import json, os, signal, sys, threading, time

db = sys.argv[1]
scenario = sys.argv[2]

from argus.discovery import STRATEGIES
from argus.discovery.base import DiscoveryStrategy
from argus.gui_bridge import DiscoveryStopped
from argus.models import CentralBank, DiscoverySpec, Source
from argus.registry import SourceRegistry
from argus.store import Store

BANK = "arbitrary_bank"
SIDS = ("source_a", "source_b", "source_c", "source_d")
GATE_GAP = float(sys.argv[3]) if len(sys.argv) > 3 else 0.35


def _handler(signum, frame):
    raise DiscoveryStopped("stop requested")


class Gated(DiscoveryStrategy):
    kind = "gated"
    gates = {}

    def discover(self):
        gate = self.gates.get(self.source.id)
        if gate is not None:
            gate.wait(60.0)
        return [self._make(url=f"https://x/{self.source.id}", title=self.source.id)]


class Explode(Gated):
    kind = "gated_explode"

    def discover(self):
        super().discover()
        raise RuntimeError("boom")


STRATEGIES["gated"] = Gated
STRATEGIES["gated_explode"] = Explode


class Adapter:
    def __init__(self, kinds):
        self._bank = CentralBank(BANK, "Arbitrary Bank", "XXX", "arbitrary.example")
        self._sources = [
            Source(id=sid, central_bank=BANK, name=sid,
                   discovery=DiscoverySpec(kind=kinds.get(sid, "gated"),
                                           url="https://x/%s" % sid))
            for sid in SIDS
        ]

    @property
    def bank(self):
        return self._bank

    @property
    def sources(self):
        return self._sources


kinds = {"source_b": "gated_explode"} if scenario == "error" else {}
signal.signal(signal.SIGTERM, _handler)
store = Store(db)
registry = SourceRegistry(adapters=[Adapter(kinds)])
run_id = store.run_stamp()
store.start_discovery_run(run_id, [BANK], pid=os.getpid(),
                          date_start="2026-01-01", date_end="2026-02-01",
                          sources_total=len(SIDS))
gates = {s: threading.Event() for s in SIDS}
Gated.gates = gates


def coordinator():
    if scenario == "stop":
        gates[SIDS[0]].set()
        time.sleep(GATE_GAP)
        gates[SIDS[1]].set()
        time.sleep(1.5)
        os.kill(os.getpid(), signal.SIGTERM)
    else:
        for sid in SIDS:  # complete / error / pause
            time.sleep(GATE_GAP)
            gates[sid].set()


threading.Thread(target=coordinator, daemon=True).start()
print("READY", flush=True)

from argus.collector import CentralBankCollector
collector = CentralBankCollector(store=store, registry=registry, raw_root="/tmp")
try:
    collector.discover_all(banks=(BANK,), run_id=run_id)
    store.finish_discovery_run(run_id, status="completed")
    status = "completed"
except DiscoveryStopped:
    store.finish_discovery_run(run_id, status="stopped", error="stopped by user")
    status = "stopped"
final = store.get_discovery_run(run_id)
print(json.dumps({"run_id": run_id, "status": status,
                  "sources_completed": final["sources_completed"],
                  "sources_total": final["sources_total"]}), flush=True)
"""


def _spawn_child(db: Path, scenario: str, *, gate_gap: float = 0.35):
    env = dict(os.environ, PYTHONPATH=_SRC_ROOT)
    return subprocess.Popen(
        [sys.executable, "-c", _SUB_PROCESS_SCRIPT, str(db), scenario, str(gate_gap)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def _ready_run_id(child, db: Path) -> str:
    """Wait for the child to create its run and read its run_id from the store."""
    line = child.stdout.readline()  # the child's "READY"; blocks until printed
    assert line.strip() == "READY", f"child failed to start: {line!r}"
    store = Store(db)
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        run = store.latest_discovery_run()
        if run is not None:
            return run["run_id"]
        time.sleep(0.02)
    raise AssertionError("campaign run never appeared")


def _poll_until(store, run_id, predicate, *, deadline_s=20.0, ceiling=None):
    deadline = time.monotonic() + deadline_s
    last = None
    while time.monotonic() < deadline:
        run = store.get_discovery_run(run_id)
        if run is not None and predicate(run):
            return run
        last = run
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} never satisfied the predicate; last={ceiling or last}")


def test_real_campaign_advances_live_progression(tmp_path):
    """A real detached campaign: another process that only polls the store
    observes multiple progression transitions *while* the campaign is running,
    then a final completed 4 / 4."""
    db = tmp_path / "live.db"
    child = _spawn_child(db, "complete")
    try:
        run_id = _ready_run_id(child, db)
    except Exception:
        child.kill()
        raise

    store = Store(db)
    samples: list[tuple[str, int]] = []
    try:
        while True:
            run = store.get_discovery_run(run_id)
            samples.append((run["status"], run["sources_completed"]))
            if run["status"] in ("completed", "stopped", "failed"):
                break
            time.sleep(0.02)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()

    assert samples[0][1] == 0, f"must start at 0/4, saw {samples}"
    completed_values = [c for status, c in samples if status in ("running", "paused")]
    # strictly monotonic (never a regression), and several real levels observed
    assert completed_values == sorted(completed_values), samples
    assert len(set(completed_values)) >= 3, f"expected several live levels, saw {samples}"
    final = store.get_discovery_run(run_id)
    assert final["status"] == "completed"
    assert final["sources_completed"] == 4
    assert final["sources_total"] == 4


def test_stop_keeps_last_known_partial_progression(tmp_path):
    """Stop after two sources: the store keeps 2 / 4 with status `stopped` —
    the GUI can never display a fabricated 100%."""
    db = tmp_path / "stop.db"
    child = _spawn_child(db, "stop")
    try:
        run_id = _ready_run_id(child, db)
    except Exception:
        child.kill()
        raise

    store = Store(db)
    # while it runs, 2/4 must be observable before the stop lands
    saw_running_2of4 = False
    try:
        while True:
            run = store.get_discovery_run(run_id)
            if run["status"] == "running" and run["sources_completed"] == 2:
                saw_running_2of4 = True
            if run["status"] in ("completed", "stopped", "failed"):
                break
            time.sleep(0.02)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()

    final = store.get_discovery_run(run_id)
    assert final["status"] == "stopped"
    assert final["sources_total"] == 4
    assert final["sources_completed"] == 2
    assert final["sources_completed"] != final["sources_total"]  # never 4 / 4
    assert saw_running_2of4, "2/4 was never observed as running"


def test_failing_source_persisted_as_completed_in_real_campaign(tmp_path):
    """In a real campaign a failing source still advances the persisted
    progression, and the failure ends up in the store's errors."""
    db = tmp_path / "error.db"
    child = _spawn_child(db, "error")
    try:
        run_id = _ready_run_id(child, db)
    except Exception:
        child.kill()
        raise

    store = Store(db)
    try:
        final = _poll_until(store, run_id, lambda r: r["status"] == "completed")
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()

    assert final["sources_total"] == 4
    assert final["sources_completed"] == 4  # the failed source counted
    assert any(e.source_id == "source_b" for e in store.list_errors())


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals required")
def test_pause_freezes_progression_then_resume_continues(tmp_path):
    """Pause (SIGSTOP) freezes the persisted progression; Resume (SIGCONT)
    lets it advance again — the GUI's paused state keeps the last value."""
    db = tmp_path / "pause.db"
    child = _spawn_child(db, "pause", gate_gap=0.6)
    try:
        run_id = _ready_run_id(child, db)
    except Exception:
        child.kill()
        raise

    store = Store(db)
    try:
        run = _poll_until(
            store, run_id,
            lambda r: r["status"] == "running" and (r["sources_completed"] or 0) >= 1,
        )
        pid = run["pid"]
        assert pid, "campaign must record its pid"

        os.kill(pid, signal.SIGSTOP)
        try:
            time.sleep(0.3)
            frozen1 = store.get_discovery_run(run_id)["sources_completed"]
            time.sleep(0.6)
            frozen2 = store.get_discovery_run(run_id)["sources_completed"]
            assert frozen1 == frozen2 >= 1, "progression must freeze during pause"
        finally:
            os.kill(pid, signal.SIGCONT)

        final = _poll_until(store, run_id, lambda r: r["status"] == "completed")
        assert final["sources_total"] == 4
        assert final["sources_completed"] == 4
        assert final["sources_completed"] > frozen1, "resume must continue the progression"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()