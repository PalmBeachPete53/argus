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


def test_collection_detached_bootstrap_failure_is_observable_failed(tmp_path):
    """Subprocess bootstrap failure via the *real* detached launch path
    (``python -m argus.gui_bridge collection-run`` — the exact mechanism the
    Rust shell uses): the spawned campaign dies during ``plan_collection``, yet
    the pre-minted run must still appear in ``collection-status`` as ``failed``
    with an explicit error — never a silent disappearance that would leave the
    frontend waiting for 60s."""
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path

    src = str(Path(gui_bridge.__file__).resolve().parents[2] / "src")
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (tmp_path / "cache").mkdir(exist_ok=True)

    sitecustomize = (
        f"import sys\nsys.path.insert(0, {src!r})\n"
        "def _patch():\n"
        "    try:\n"
        "        import argus.collector as m\n"
        "        class FailingPlan:\n"
        "            def __init__(self, **kw):\n"
        "                self.store = kw['store']\n"
        "            def plan_collection(self, **kw):\n"
        "                raise RuntimeError('plan exploded in detached spawn')\n"
        "            def collect_campaign(self, **kw):\n"
        "                return []\n"
        "        m.CentralBankCollector = FailingPlan\n"
        "    except Exception:\n"
        "        pass\n"
        "_patch()\n"
    )
    (tmp_path / "sitecustomize.py").write_text(sitecustomize)

    env = dict(
        os.environ,
        PYTHONPATH=f"{tmp_path}:{src}",
        ARGUS_ROOT=str(tmp_path),
        ARGUS_COLLECTION_DETACHED="1",
        ARGUS_BANKS_CONFIG=str(tmp_path / "banks.json"),
    )
    campaign = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "argus.gui_bridge",
            "collection-run",
            "--run-id",
            "X-detached",
        ],
        cwd=Path(gui_bridge.__file__).resolve().parents[2],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # The run must become observable *and* reach `failed` on its own.
        final = None
        for _ in range(400):
            exitcode = campaign.poll()
            # drain any lingering process
            if exitcode is not None and campaign.wait(1) is not None and exitcode != 0:
                # The campaign may exit non-zero only if it could not record the
                # run (a real defect). We still require the row below.
                pass
            store = gui_bridge.Store(str(data / "argus.db"))
            run = store.get_collection_run("X-detached")
            store.close()
            if run is not None and run["status"] == "failed":
                final = run
                break
            time.sleep(0.05)
        assert final is not None, "the failed run never became observable"
        assert final["status"] == "failed"
        assert "plan exploded in detached spawn" in final["error"]
        assert final["finished_at"], "a failed bootstrap records finished_at"
    finally:
        if campaign.poll() is None:
            campaign.kill()
            campaign.wait()


def _run_main(root, argv):
    """Run ``gui_bridge.main`` against a given temp data root and return
    ``(code, parsed_stdout)`` without touching the ``patched`` fixture."""
    import contextlib
    import io

    import argus.gui_bridge as gb

    real_root = gb.ROOT
    gb.ROOT = root
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = gb.main(argv)
        out = buf.getvalue().strip()
        return code, json.loads(out) if out else None
    finally:
        gb.ROOT = real_root


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


def test_collection_run_begin_preregisters_observable_row(patched, tmp_path):
    """`collection-run-begin` makes the run observable *in the launcher*: the
    row exists (status running, the just-spawned subprocess's PID) the instant
    the GUI receives its id — before the detached campaign has booted."""
    import os

    _, minted = patched(["collection-run-id"])
    code, run = patched(["collection-run-begin", "--run-id", minted["run_id"], "--pid", str(os.getpid())])
    assert code == 0
    assert run["run_id"] == minted["run_id"]
    assert run["status"] == "running"
    assert run["pid"] == os.getpid()
    assert run["publications_total"] == 0

    _, status = patched(["collection-status"])
    assert status["run_id"] == minted["run_id"]
    assert status["status"] == "running"


def test_collection_run_begin_requires_run_id_and_pid(patched):
    code, data = patched(["collection-run-begin"])
    assert code == 2
    assert "requires --run-id" in data["error"]
    code, data = patched(["collection-run-begin", "--run-id", "x"])
    assert code == 2
    assert "requires --run-id" in data["error"]


def test_collection_run_begin_refused_when_different_campaign_active(patched, tmp_path):
    """Pre-registration is a claim on the single-active slot: a *different*
    active campaign is refused (the launcher then kills its child), exactly like
    a normal launch."""
    store = gui_bridge.Store(tmp_path / "data" / "argus.db")
    store.start_collection_run("active-other", ["fed"], pid=0)
    store.close()

    _, minted = patched(["collection-run-id"])
    code, data = patched(["collection-run-begin", "--run-id", minted["run_id"], "--pid", "4242"])
    assert code == 1
    assert "already active" in data["error"]


def test_collection_run_begin_then_run_self_adopts(monkeypatch, tmp_path, patched):
    """The campaign launched with a pre-registered id self-adopts the launcher's
    row (same run_id) instead of being refused as a competing campaign — and the
    status reflects the adopted run, not the pre-registration."""
    import os

    _seed_publication(tmp_path)
    _install_fake_collector(monkeypatch)
    _, minted = patched(["collection-run-id"])
    _, begun = patched(["collection-run-begin", "--run-id", minted["run_id"], "--pid", str(os.getpid())])
    assert begun["status"] == "running"

    code, data = patched(["collection-run", "--run-id", minted["run_id"]])
    assert code == 0
    assert data["status"] == "completed"
    assert data["run_id"] == minted["run_id"]

    _, status = patched(["collection-status"])
    assert status["run_id"] == minted["run_id"]
    assert status["status"] == "completed"
    assert "fed" in status["banks"]


def test_collection_run_begin_does_not_clobber_adopted_row(patched, tmp_path):
    """If the child booted fast and already adopted the row (with its real
    banks/pid), a late `collection-run-begin` returns the authoritative record
    instead of overwriting it with the empty pre-registration."""
    import os

    store = gui_bridge.Store(tmp_path / "data" / "argus.db")
    store.start_collection_run("fast-child", ["fed"], pid=os.getpid(), publications_total=3)
    store.close()

    code, run = patched(["collection-run-begin", "--run-id", "fast-child", "--pid", str(os.getpid())])
    assert code == 0
    assert run["run_id"] == "fast-child"
    assert run["status"] == "running"
    assert run["banks"] == ["fed"]
    assert run["publications_total"] == 3


def test_collection_status_explicit_run_id_never_substitutes_latest(patched, tmp_path):
    """`collection-status --run-id <id>` addresses exactly that campaign: an
    unknown id reports `idle` (never the latest run the poll loop could be
    confused into adopting), and a known id returns exactly that run even when a
    newer terminal run exists."""
    import os

    store = gui_bridge.Store(tmp_path / "data" / "argus.db")
    store.start_collection_run("old-a", ["fed"], pid=os.getpid(), publications_total=1)
    store.set_collection_progress("old-a", completed=1, total=1)
    store.finish_collection_run("old-a", status="completed")
    store.close()

    # Unknown id → idle, never the stale-but-present "old-a".
    code, data = patched(["collection-status", "--run-id", "never-minted"])
    assert code == 0
    assert data["status"] == "idle"
    assert data["run_id"] is None

    # Known id → exactly that run.
    code, data = patched(["collection-status", "--run-id", "old-a"])
    assert code == 0
    assert data["run_id"] == "old-a"
    assert data["status"] == "completed"

    # No id → latest (legacy convenience, unchanged).
    code, data = patched(["collection-status"])
    assert code == 0
    assert data["run_id"] == "old-a"


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


def test_collection_status_exposes_partial_progress_on_cancel(monkeypatch, tmp_path, patched):
    """A campaign cancelled mid-flight keeps its *real* partial publication
    progression in collection-status (never fabricated to total/total)."""
    from argus.models import Publication

    for index in range(3):
        store = gui_bridge.Store(tmp_path / "data" / "argus.db")
        store.upsert_publication(
            Publication(
                id=f"partial-{index}",
                central_bank="fed",
                title=f"Statement {index}",
                url=f"https://fed.gov/stmt-{index}.htm",
                source_id="fed_rss",
                source_url="https://fed.gov/feed.xml",
                status=PublicationStatus.DISCOVERED,
            )
        )
        store.close()

    class _PartialFake(_FakeCollector):
        def collect_campaign(self, *, run_id=None, should_stop=None, publications=None, **kwargs):
            from argus.collector import CollectionStopped
            from argus.models import FetchResult

            plan = list(publications) if publications is not None else list(self._plan)
            total = len(plan)
            for i, pub in enumerate(plan, start=1):
                if i >= 2:  # stop after the first publication finished
                    raise CollectionStopped("stop requested")
                if run_id:
                    self.store.set_collection_progress(run_id, completed=i, total=total)
            return [FetchResult(publication_id=pub.id or "", documents=[], ok=True)]

    _install_fake_collector(monkeypatch, _PartialFake)
    code, data = patched(["collection-run"])
    assert code == 0
    assert data["status"] == "cancelled"

    _, status = patched(["collection-status"])
    assert status["status"] == "cancelled"
    assert status["publications_total"] == 3
    assert status["publications_completed"] == 1
    assert status["publications_completed"] != status["publications_total"]


def test_collection_run_forwards_date_window(monkeypatch, tmp_path, patched):
    """collection-run forwards an optional publication-date window to the Core's
    plan (the same window the user just discovered) and persists it with the run."""
    _seed_publication(tmp_path)

    class _RecordingFake(_FakeCollector):
        instances = []

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            _RecordingFake.instances.append(self)

    _install_fake_collector(monkeypatch, _RecordingFake)
    code, data = patched(["collection-run", "--start-date", "2026-01-01", "--end-date", "2026-02-01"])
    assert code == 0
    assert data["status"] == "completed"

    from datetime import datetime, timezone

    recorded = _RecordingFake.instances[-1]
    start, end = recorded.passed[2], recorded.passed[3]
    assert start == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert end == datetime(2026, 2, 1, tzinfo=timezone.utc)

    _, status = patched(["collection-status"])
    assert status["date_start"].startswith("2026-01-01")
    assert status["date_end"].startswith("2026-02-01")


def test_collection_run_rejects_incomplete_window(patched):
    """A window with only one bound is refused (never silently ignored)."""
    code, data = patched(["collection-run", "--start-date", "2026-01-01"])
    assert code == 1
    assert "also requires --end-date" in data["error"]

    code, data = patched(["collection-run", "--end-date", "2026-02-01"])
    assert code == 1
    assert "also requires --start-date" in data["error"]


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


def test_collection_bootstrap_plan_failure_records_failed_run(monkeypatch, tmp_path, patched):
    """A failure *during plan_collection* (before the campaign exists) must not
    swallow the pre-minted run: the lifecycle was recorded first, so the run is
    observable as `failed` with an explicit error — never `did not start in
    time` on the frontend side."""
    _, minted = patched(["collection-run-id"])

    class _PlanFailingFake(_FakeCollector):
        def plan_collection(self, **kwargs):
            raise RuntimeError("plan exploded")

    _install_fake_collector(monkeypatch, _PlanFailingFake)
    code, data = patched(["collection-run", "--run-id", minted["run_id"]])
    assert code == 0
    assert data["status"] == "failed"
    assert data["run_id"] == minted["run_id"]
    assert "RuntimeError: plan exploded" in data["error"]

    _, status = patched(["collection-status"])
    assert status["status"] == "failed"
    assert status["run_id"] == minted["run_id"]
    assert status["finished_at"], "failed bootstrap must record finished_at"
    assert status["error"] and "plan exploded" in status["error"]
    # No bogus total is fabricated for a plan that was never computed.
    assert status["publications_total"] == 0
    assert status["publications_completed"] == 0


def test_collection_bootstrap_collector_construction_failure_records_failed(
    monkeypatch, tmp_path, patched
):
    """A failure building the collector itself is also a bootstrap failure: the
    run (already recorded) must surface as `failed`, not linger as `running`."""

    class _ConstructFailing:
        def __init__(self, **kwargs):
            raise RuntimeError("cannot build collector")

    _install_fake_collector(monkeypatch, _ConstructFailing)
    _, minted = patched(["collection-run-id"])
    code, data = patched(["collection-run", "--run-id", minted["run_id"]])
    assert code == 0
    assert data["status"] == "failed"
    assert "cannot build collector" in data["error"]
    _, status = patched(["collection-status"])
    assert status["status"] == "failed"
    assert status["run_id"] == minted["run_id"]


def test_collection_bootstrap_cancel_records_cancelled(monkeypatch, tmp_path, patched):
    """A stop request (SIGTERM flag) during bootstrap cancels the run cleanly —
    a deliberate stop is `cancelled`, never `failed`."""
    _seed_publication(tmp_path)

    class _BootstrapStopFake(_FakeCollector):
        def plan_collection(self, **kwargs):
            from argus.collector import CollectionStopped

            raise CollectionStopped("stop requested during campaign startup")

    _install_fake_collector(monkeypatch, _BootstrapStopFake)
    code, data = patched(["collection-run"])
    assert code == 0
    assert data["status"] == "cancelled"
    assert data["run_id"]
    _, status = patched(["collection-status"])
    assert status["status"] == "cancelled"
    assert status["error"] == "cancelled by user"
    assert status["finished_at"]


def test_collection_control_stop_terminal_run_adopts_state(monkeypatch, tmp_path, patched):
    """Stopping an already-terminal run (e.g. a bootstrap failure) reports the
    authoritative terminal state instead of `no active collection campaign to
    stop` — the frontend adopts it."""
    _seed_publication(tmp_path)

    class _FailingFake(_FakeCollector):
        def plan_collection(self, **kwargs):
            raise RuntimeError("boom")

    _install_fake_collector(monkeypatch, _FailingFake)
    _, minted = patched(["collection-run-id"])
    patched(["collection-run", "--run-id", minted["run_id"]])  # ends failed

    code, run = patched(["collection-control", "stop", minted["run_id"]])
    assert code == 0
    assert run["status"] == "failed"
    assert run["run_id"] == minted["run_id"]
    assert "boom" in run["error"]


def test_collection_control_stop_unknown_run_errors(monkeypatch, tmp_path, patched):
    """A stop targeting a run that never existed and never appears still errors
    (the bounded wait is for *late-arriving launches*, not a miracle lookup)."""
    monkeypatch.setattr(gui_bridge, "STOP_WAIT_STEP_S", 0.01)
    monkeypatch.setattr(gui_bridge, "STOP_WAIT_CYCLES", 5)
    code, data = patched(["collection-control", "stop", "never-minted"])
    assert code == 1
    assert "no collection campaign found" in data["error"]


def test_collection_control_stop_during_startup_waits_for_late_run(
    monkeypatch, tmp_path, patched
):
    """A Cancel issued right after launch (before the detached subprocess has
    recorded its row) waits a bounded time for the run to appear, then really
    terminates it and records `cancelled` — never `no active collection campaign
    to stop` for a run the GUI just launched."""
    import os
    import threading
    import time

    if os.name == "nt":
        pytest.skip("POSIX signals required")

    monkeypatch.setattr(gui_bridge, "STOP_WAIT_STEP_S", 0.01)
    monkeypatch.setattr(gui_bridge, "STOP_WAIT_CYCLES", 300)  # up to ~3s
    _, minted = patched(["collection-run-id"])
    campaign = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)", "argus.gui_bridge", "collection-run"]
    )
    recorded = {"done": False}

    def _record_late():
        time.sleep(0.3)  # the detached subprocess is still booting
        store = gui_bridge.Store(tmp_path / "data" / "argus.db")
        store.start_collection_run(minted["run_id"], ["fed"], pid=campaign.pid)
        store.close()
        recorded["done"] = True

    threading.Thread(target=_record_late).start()
    try:
        code, run = patched(["collection-control", "stop", minted["run_id"]])
        assert recorded["done"], "the wait loop must have let the run appear"
        assert code == 0
        assert run["run_id"] == minted["run_id"]
        assert run["status"] == "cancelled"
        assert campaign.wait(5) is not None  # really gone before `cancelled`
        assert not gui_bridge._process_alive(campaign.pid)
    finally:
        if campaign.poll() is None:
            campaign.kill()
            campaign.wait()


def test_collection_control_stop_during_startup_with_pre_registered_row(
    monkeypatch, tmp_path, patched
):
    """A Cancel issued while the detached campaign is cold-booting succeeds
    *immediately*: the launcher's `collection-run-begin` already recorded the
    row (status running, with the child's pid), so the stop addresses the real
    campaign — it never waits for a row that may never appear, and never errors
    with `no collection campaign found` for a run the GUI just launched."""
    import os

    if os.name == "nt":
        pytest.skip("POSIX signals required")

    monkeypatch.setattr(gui_bridge, "STOP_WAIT_STEP_S", 0.01)
    monkeypatch.setattr(gui_bridge, "STOP_WAIT_CYCLES", 60)
    campaign = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)", "argus.gui_bridge", "collection-run"]
    )
    try:
        # The launcher side pre-registers the row (with the child's pid) right
        # after spawning — the child itself is still booting.
        code, begun = patched(["collection-run-id"])
        assert code == 0
        run_id = begun["run_id"]
        code, _ = patched(
            ["collection-run-begin", "--run-id", run_id, "--pid", str(campaign.pid)]
        )
        assert code == 0

        # Cancel during cold boot: the row already exists with a live pid.
        code, run = patched(["collection-control", "stop", run_id])
        assert code == 0
        assert run["run_id"] == run_id
        assert run["status"] == "cancelled"
        assert campaign.wait(5) is not None  # really gone before `cancelled`
        assert not gui_bridge._process_alive(campaign.pid)
    finally:
        if campaign.poll() is None:
            campaign.kill()
            campaign.wait()


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
    campaign finalizes itself `failed` (the launching app died — an error
    condition, never a voluntary cancellation) instead of continuing as an
    orphan, and the pre-minted run stays observable in `collection-status`."""
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
    assert data["status"] == "failed"
    assert "launcher exited during campaign startup" in data["error"]
    _, status = patched(["collection-status"])
    assert status["status"] == "failed"


def test_collection_launch_parent_gone_before_record(monkeypatch, tmp_path, patched):
    """Parent is already gone before the run records itself: the campaign must
    never silently vanish — the pre-minted identity is recorded as `failed` so
    `collection-status` can still observe it (the Rust shell already returned it
    to the frontend)."""
    monkeypatch.setenv(gui_bridge._COLLECTION_DETACHED_ENV, "1")
    monkeypatch.setattr(gui_bridge.os, "getppid", lambda: 1)
    _install_fake_collector(monkeypatch)
    _, minted = patched(["collection-run-id"])
    code, data = patched(["collection-run", "--run-id", minted["run_id"]])
    assert code == 0
    assert data["status"] == "failed"
    assert data["run_id"] == minted["run_id"]
    assert "launcher exited during campaign startup" in data["error"]
    _, status = patched(["collection-status"])
    assert status["status"] == "failed"
    assert status["run_id"] == minted["run_id"]


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


def test_collection_cancel_during_bootstrap_terminates_and_records_cancelled(
    monkeypatch, tmp_path
):
    """A real Cancel issued while the campaign is still bootstrapping
    (plan_collection running) must terminate the process and record `cancelled`
    — the process is verified dead *before* the Store says `cancelled` (the
    Collection cancellation contract), and the run never lingers as `running`."""
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path

    if os.name == "nt":
        pytest.skip("POSIX signals required")

    src = str(Path(gui_bridge.__file__).resolve().parents[2] / "src")
    db = tmp_path / "data" / "argus.db"
    (tmp_path / "data").mkdir(exist_ok=True)

    monkeypatch.setattr(gui_bridge, "STOP_GRACE_S", 0.3)
    monkeypatch.setattr(gui_bridge, "STOP_KILL_S", 0.3)

    fake_collector = (
        "class SlowPlan:\n"
        "    def __init__(self, **kw):\n"
        "        self.store = kw['store']\n"
        "    def plan_collection(self, **kw):\n"
        "        import time as t\n"
        "        t.sleep(30)  # still bootstrapping while a Cancel arrives\n"
        "        return []\n"
        "    def collect_campaign(self, **kw):\n"
        "        return []\n"
    )
    campaign_code = (
        "import sys;\n"
        f"sys.path.insert(0, {src!r});\n"
        f"{fake_collector}\n"
        "from argus import gui_bridge;\n"
        f"gui_bridge.ROOT = __import__('pathlib').Path({str(tmp_path)!r});\n"
        "import argus.collector as collector_mod;\n"
        "collector_mod.CentralBankCollector = SlowPlan;\n"
        "gui_bridge.CentralBankCollector = SlowPlan;\n"
        "sys.exit(gui_bridge.main(['collection-run', '--run-id', 'bootstrap-cancel']))"
    )
    env = dict(os.environ, PYTHONPATH=src)
    campaign = subprocess.Popen(
        [sys.executable, "-c", campaign_code, "argus.gui_bridge", "collection-run"], env=env
    )
    try:
        pid = None
        for _ in range(500):
            store = gui_bridge.Store(db)
            run = store.get_collection_run("bootstrap-cancel")
            store.close()
            if run is not None and run["status"] == "running":
                pid = run["pid"]
                break
            time.sleep(0.02)
        assert pid is not None, "campaign never recorded itself"
        assert pid == campaign.pid, "the run must carry the campaign's real pid"

        code, run = _run_main(tmp_path, ["collection-control", "stop", "bootstrap-cancel"])
        assert code == 0, run
        assert run["run_id"] == "bootstrap-cancel"
        assert run["status"] == "cancelled", run
        assert campaign.wait(5) is not None, "process must really be gone"
        assert not gui_bridge._process_alive(campaign.pid)

        store = gui_bridge.Store(db)
        final = store.get_collection_run("bootstrap-cancel")
        store.close()
        assert final["status"] == "cancelled"
        assert final["finished_at"]
    finally:
        if campaign.poll() is None:
            campaign.kill()
            campaign.wait()