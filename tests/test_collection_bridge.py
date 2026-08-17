"""Collection commands exposed through the bridge (findings 🔴 #2, #3, #4).

Covers the lifecycle contract without touching the real data store: every test
runs against the temp repo's `data/argus.db` (the `patched` fixture) and drives
the campaign through the real `CentralBankCollector` stand-ins used by the
discovery tests.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from argus import gui_bridge
from argus.models import PublicationStatus

from conftest import make_store  # noqa: F401  (re-exported for parity)


@pytest.fixture
def patched(monkeypatch, tmp_path, capsys):
    """Point the bridge at a temp repo with a temp `data/` and capture stdout."""
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "cache").mkdir(exist_ok=True)
    monkeypatch.setattr(gui_bridge, "ROOT", tmp_path)

    def run(argv):
        capsys.readouterr()  # drain previous output
        code = gui_bridge.main(argv)
        out = capsys.readouterr().out
        return code, json.loads(out)

    return run


def _seed_publication(tmp_path, **kw):
    from argus.models import Publication

    fields = dict(
        central_bank="fed",
        title="Statement",
        url="https://fed.gov/stmt.htm",
        source_id="fed_rss",
        source_url="https://fed.gov/feed.xml",
        publication_date=None,
        status=PublicationStatus.DISCOVERED,
    )
    fields.update(kw)
    store = gui_bridge.Store(tmp_path / "data" / "argus.db")
    store.upsert_publication(Publication(**fields))
    store.close()


class _FakeFetcher:
    """Stand-in for the collector's `fetcher` that records calls and produces a
    controllable FetchResult — no network is ever touched."""

    def __init__(self, results=None):
        self.calls = []
        self._results = list(results or [])

    def fetch(self, publication, *, force=False):
        self.calls.append((publication, force))
        from argus.models import FetchResult

        if self._results:
            return self._results.pop(0)
        return FetchResult(publication_id=publication.id or "", documents=[], ok=True)


class _FakeCollector:
    def __init__(self, *, store, registry=None, http_config=None, raw_root=None,
                 search_provider=None, **kwargs):
        self.store = store
        self._plan = []
        self._fetcher = None
        self.calls = []
        self.force = None

    def plan_collection(self, *, banks=None, force=False, date_start=None, date_end=None):
        self.passed = (banks, force, date_start, date_end)
        pubs = self.store.list_publications(
            bank=banks,
            statuses=(
                PublicationStatus.DISCOVERED,
                PublicationStatus.UPDATED,
                PublicationStatus.PARTIAL,
                PublicationStatus.FAILED,
            ),
            date_start=date_start,
            date_end=date_end,
        )
        self._plan = pubs
        return list(pubs)

    def collect_campaign(self, *, banks=None, force=False, date_start=None, date_end=None,
                         run_id=None, progress=None, should_stop=None, publications=None):
        self.force = force
        self.calls.append((banks, force, run_id))
        from argus.models import FetchResult

        plan = list(publications) if publications is not None else list(self._plan)
        total = len(plan)
        results = []
        for i, pub in enumerate(plan, start=1):
            if should_stop is not None and should_stop():
                from argus.collector import CollectionStopped

                raise CollectionStopped("stop requested")
            results.append(FetchResult(publication_id=pub.id or "", documents=[], ok=True))
            if run_id:
                self.store.set_collection_progress(run_id, completed=i, total=total)
            if progress is not None:
                progress(i, total)
        return results


def _install_fake_collector(monkeypatch, collector=None):
    monkeypatch.setattr(gui_bridge, "CentralBankCollector", collector or _FakeCollector)
    return collector


def test_collection_run_id_mints_unique_stamp(patched):
    import re

    code, first = patched(["collection-run-id"])
    assert code == 0
    _, second = patched(["collection-run-id"])
    for stamp in (first["run_id"], second["run_id"]):
        assert re.fullmatch(r"\d{8}T\d{6}-\d+", stamp), stamp


def test_collection_run_records_completed_campaign(monkeypatch, tmp_path, patched):
    _seed_publication(tmp_path)
    _install_fake_collector(monkeypatch)

    code, data = patched(["collection-run"])
    assert code == 0
    assert data["status"] == "completed"
    assert data["run_id"]

    _, status = patched(["collection-status"])
    assert status["status"] == "completed"
    assert status["publications_total"] >= 1
    assert status["publications_completed"] >= 1
    assert status["pid"] == __import__("os").getpid()


def test_collection_run_records_own_pid(monkeypatch, patched):
    _install_fake_collector(monkeypatch)
    code, data = patched(["collection-run"])
    assert code == 0 and data["status"] == "completed"
    _, status = patched(["collection-status"])
    assert status["pid"] == __import__("os").getpid()


def test_collection_run_idle_when_no_runs(patched):
    code, data = patched(["collection-status"])
    assert code == 0
    assert data["status"] == "idle"
    assert data["run_id"] is None
    assert data["publications_total"] == 0
    assert data["publications_completed"] == 0


def test_collection_run_uses_preminted_run_id(monkeypatch, tmp_path, patched):
    _seed_publication(tmp_path)
    _install_fake_collector(monkeypatch)
    _, minted = patched(["collection-run-id"])
    code, data = patched(["collection-run", "--run-id", minted["run_id"]])
    assert code == 0 and data["status"] == "completed"
    assert data["run_id"] == minted["run_id"]
    _, status = patched(["collection-status"])
    assert status["run_id"] == minted["run_id"]


def test_collection_run_refused_when_already_active(patched, tmp_path, monkeypatch, capsys):
    store = gui_bridge.Store(tmp_path / "data" / "argus.db")
    store.start_collection_run("active-1", ["fed"], pid=0)
    store.close()
    _install_fake_collector(monkeypatch)
    code, data = patched(["collection-run"])
    assert code == 1
    assert "already active" in data["error"]


def test_collection_run_cancelled_on_collection_stopped(monkeypatch, tmp_path, patched):
    _seed_publication(tmp_path)

    class _StoppingFake(_FakeCollector):
        def collect_campaign(self, **kwargs):
            from argus.collector import CollectionStopped

            raise CollectionStopped("stop requested")

    _install_fake_collector(monkeypatch, _StoppingFake)
    code, data = patched(["collection-run"])
    assert code == 0
    assert data["status"] == "cancelled"
    _, status = patched(["collection-status"])
    assert status["status"] == "cancelled"
    assert status["error"]


def test_collection_run_failure_records_failed_status(monkeypatch, tmp_path, patched):
    _seed_publication(tmp_path)

    class _FailingFake(_FakeCollector):
        def collect_campaign(self, **kwargs):
            raise RuntimeError("boom")

    _install_fake_collector(monkeypatch, _FailingFake)
    code, data = patched(["collection-run"])
    assert code == 0
    assert data["status"] == "failed"
    assert "RuntimeError: boom" in data["error"]
    _, status = patched(["collection-status"])
    assert status["status"] == "failed"


def test_collection_status_reconciles_dead_pid(monkeypatch, tmp_path, patched):
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    store = gui_bridge.Store(tmp_path / "data" / "argus.db")
    store.start_collection_run("dead-col", ["fed"], pid=gone.pid)
    store.close()

    code, status = patched(["collection-status"])
    assert code == 0
    assert status["status"] == "failed"
    assert "exited unexpectedly" in status["error"]


def test_collection_control_requires_known_action(patched):
    assert gui_bridge.main(["collection-control", "explode"]) == 2


def test_collection_control_no_active_campaign(patched):
    code, data = patched(["collection-control", "stop"])
    assert code == 1
    assert "no active collection campaign" in data["error"]


def test_collection_control_stop_terminates_process(monkeypatch, tmp_path, patched):
    import os
    import signal
    import time

    if os.name == "nt":
        pytest.skip("POSIX signals required")

    code = "import time; time.sleep(120)"
    campaign = subprocess.Popen(
        [sys.executable, "-c", code, "argus.gui_bridge", "collection-run"]
    )
    try:
        store = gui_bridge.Store(tmp_path / "data" / "argus.db")
        store.start_collection_run("ctrl-col", ["fed"], pid=campaign.pid)
        store.close()

        code, run = patched(["collection-control", "stop", "ctrl-col"])
        assert code == 0
        assert run["status"] == "cancelled"
        assert campaign.wait(5) is not None  # really gone
        assert not gui_bridge._process_alive(campaign.pid)
    finally:
        if campaign.poll() is None:
            campaign.kill()
            campaign.wait()


def test_collection_control_stop_dead_pid_records_cancelled(monkeypatch, tmp_path, patched):
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    store = gui_bridge.Store(tmp_path / "data" / "argus.db")
    store.start_collection_run("dead-col-stop", ["fed"], pid=gone.pid)
    store.close()

    code, run = patched(["collection-control", "stop", "dead-col-stop"])
    assert code == 0
    assert run["status"] == "cancelled"


def test_collection_control_refuses_foreign_live_pid(monkeypatch, tmp_path, patched):
    import os

    if os.name == "nt":
        pytest.skip("POSIX signals required")

    foreign = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        store = gui_bridge.Store(tmp_path / "data" / "argus.db")
        store.start_collection_run("foreign-col", ["fed"], pid=foreign.pid)
        store.close()

        code, data = patched(["collection-control", "stop", "foreign-col"])
        assert code == 1
        assert "no longer the running campaign" in data["error"]
        assert foreign.poll() is None  # untouched
        _, status = patched(["collection-status"])
        assert status["status"] == "failed"
    finally:
        if foreign.poll() is None:
            foreign.kill()
            foreign.wait()


def test_collection_launch_race_parent_gone_after_record(monkeypatch, tmp_path, patched):
    """Parent disappears right after the run is recorded: the collection
    campaign finalizes itself `cancelled` instead of continuing as an orphan."""
    monkeypatch.setenv(gui_bridge._COLLECTION_DETACHED_ENV, "1")
    real_getppid = gui_bridge.os.getppid
    calls = {"n": 0}

    def _getppid():
        calls["n"] += 1
        return 1 if calls["n"] > 1 else real_getppid()

    monkeypatch.setattr(gui_bridge.os, "getppid", _getppid)
    _install_fake_collector(monkeypatch)
    code, data = patched(["collection-run"])
    assert code == 0
    assert data["status"] == "cancelled"
    assert "launcher exited during campaign startup" in data["error"]
    _, status = patched(["collection-status"])
    assert status["status"] == "cancelled"


def test_stats_exposes_last_collection(monkeypatch, tmp_path, patched):
    _seed_publication(tmp_path)
    _install_fake_collector(monkeypatch)
    patched(["collection-run"])
    _, stats = patched(["stats"])
    assert stats["last_collection"]["status"] == "completed"


def test_collection_sigterm_mid_campaign_records_cancelled(tmp_path):
    """A real SIGTERM to a running collection campaign is polled between
    publications and finalizes the run as `cancelled` — never `failed` and
    never `completed`. Exercises the full chain: SIGTERM → flag → should_stop
    poll → CollectionStopped → cancelled."""
    import os
    import signal
    import subprocess
    import sys
    import time
    from pathlib import Path

    if os.name == "nt":
        pytest.skip("POSIX signals required")

    src = str(Path(gui_bridge.__file__).resolve().parents[2] / "src")
    db = tmp_path / "data" / "argus.db"
    (tmp_path / "data").mkdir(exist_ok=True)

    fake_collector = (
        "class SlowFake:\n"
        "    def __init__(self, **kw):\n"
        "        self.store = kw['store']\n"
        "    def plan_collection(self, **kw):\n"
        "        return [object()]  # one pending publication\n"
        "    def collect_campaign(self, *, run_id=None, should_stop=None, **kw):\n"
        "        from argus.models import FetchResult\n"
        "        import time as t\n"
        "        for i in range(200):\n"
        "            if should_stop is not None and should_stop():\n"
        "                from argus.collector import CollectionStopped\n"
        "                raise CollectionStopped('requested')\n"
        "            t.sleep(0.02)\n"
        "        return []\n"
    )
    campaign_code = (
        "import sys;\n"
        f"sys.path.insert(0, {src!r});\n"
        f"{fake_collector}\n"
        "from argus import gui_bridge;\n"
        f"gui_bridge.ROOT = __import__('pathlib').Path({str(tmp_path)!r});\n"
        "import argus.collector as collector_mod;\n"
        "collector_mod.CentralBankCollector = SlowFake;\n"
        "gui_bridge.CentralBankCollector = SlowFake;\n"
        "sys.exit(gui_bridge.main(['collection-run', '--run-id', 'sig-col']))"
    )
    env = dict(os.environ, PYTHONPATH=src)
    campaign = subprocess.Popen([sys.executable, "-c", campaign_code], env=env)
    try:
        # Wait until the store records the campaign (so we have its real PID).
        pid = None
        for _ in range(500):
            store = gui_bridge.Store(db)
            run = store.get_collection_run("sig-col")
            store.close()
            if run is not None and run["status"] == "running":
                pid = run["pid"]
                break
            time.sleep(0.02)
        assert pid is not None, "campaign never started running"

        os.kill(pid, signal.SIGTERM)
        assert campaign.wait(10) is not None

        store = gui_bridge.Store(db)
        run = store.get_collection_run("sig-col")
        store.close()
        assert run is not None
        assert run["status"] == "cancelled", run["status"]
        assert run["error"]
    finally:
        if campaign.poll() is None:
            campaign.kill()
            campaign.wait()