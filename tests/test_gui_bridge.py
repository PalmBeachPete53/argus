"""Tests for the `argus.gui_bridge` filesystem commands (data listing).

The Data browser accesses the filesystem *only* through the bridge, which
resolves every path relative to the Argus ``data/`` directory and refuses any
navigation that escapes it (``..``, absolute paths).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from argus import gui_bridge


def _make_data(tmp_path) -> None:
    data = tmp_path / "data"
    (data / "cache").mkdir(parents=True)
    (data / "cache" / "sub").mkdir()
    (data / "raw_2025").mkdir()
    (data / "argus_2025.db").write_text("x", encoding="utf-8")


@pytest.fixture
def patched(monkeypatch, tmp_path, capsys):
    """Point the bridge at a temp repo with a temp `data/` and capture stdout."""
    _make_data(tmp_path)
    monkeypatch.setattr(gui_bridge, "ROOT", tmp_path)

    def run(argv):
        capsys.readouterr()  # drain previous output
        code = gui_bridge.main(argv)
        out = capsys.readouterr().out
        return code, json.loads(out)

    return run


def test_data_root(monkeypatch, tmp_path, capsys):
    _make_data(tmp_path)
    monkeypatch.setattr(gui_bridge, "ROOT", tmp_path)
    assert gui_bridge.main(["data-root"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["root"] == str((tmp_path / "data").resolve())


def test_list_dir_root(patched):
    code, data = patched(["list-dir"])
    assert code == 0
    assert data["path"] == ""
    assert data["segments"] == []
    assert data["parent"] is None
    names = {e["name"]: e["is_dir"] for e in data["entries"]}
    assert names["cache"] is True
    assert names["raw_2025"] is True
    assert names["argus_2025.db"] is False


def test_list_dir_subdir(patched):
    code, data = patched(["list-dir", "cache"])
    assert code == 0
    assert data["path"] == "cache"
    assert data["segments"] == ["cache"]
    assert data["parent"] == ""  # Up from cache → data root
    assert [e["name"] for e in data["entries"]] == ["sub"]


def test_list_dir_nested_subdir(patched):
    code, data = patched(["list-dir", "cache/sub"])
    assert code == 0
    assert data["path"] == "cache/sub"
    assert data["segments"] == ["cache", "sub"]
    assert data["parent"] == "cache"


def test_list_dir_dirs_sorted_before_files(patched):
    code, data = patched(["list-dir", ""])
    assert code == 0
    kinds = [e["is_dir"] for e in data["entries"]]
    assert kinds == sorted(kinds, reverse=True)  # all dirs first


def test_list_dir_missing_directory(patched):
    code, data = patched(["list-dir", "does-not-exist"])
    assert code == 1
    assert "not found" in data["error"]


def test_list_dir_not_a_directory(patched):
    code, data = patched(["list-dir", "argus_2025.db"])
    assert code == 1
    assert "not a directory" in data["error"]


def test_list_dir_dotdot_escape_rejected(patched):
    for attempt in ("..", "../..", "../../.."):
        code, data = patched(["list-dir", attempt])
        assert code == 1, attempt
        assert "escapes" in data["error"], attempt


def test_list_dir_dotdot_from_subdir_rejected(patched):
    code, data = patched(["list-dir", "cache/../.."])
    assert code == 1
    assert "escapes" in data["error"]


def test_list_dir_absolute_path_rejected(patched, tmp_path):
    # absolute outside data/
    code, data = patched(["list-dir", str(tmp_path / "elsewhere")])
    assert code == 1
    assert "absolute" in data["error"]
    # absolute inside data/ is also refused (only relative paths are accepted)
    code2, data2 = patched(["list-dir", str(tmp_path / "data" / "cache")])
    assert code2 == 1
    assert "absolute" in data2["error"]


def test_list_dir_dotdot_traversal_safe(patched):
    # "cache/.." stays inside data/ → resolves back to the data root, allowed
    code, data = patched(["list-dir", "cache/.."])
    assert code == 0
    assert {e["name"] for e in data["entries"]} == {"cache", "raw_2025", "argus_2025.db"}


@pytest.fixture
def open_patched(monkeypatch, tmp_path, capsys):
    """Like `patched` but with a fake `_system_open` that records the paths
    handed to the OS launcher (no real application is launched in unit tests)."""
    _make_data(tmp_path)
    data = tmp_path / "data"
    (data / "report.html").write_text("<h1>test</h1>", encoding="utf-8")
    (data / "REPORT.HTML").write_text("x", encoding="utf-8")
    (data / "doc.htm").write_text("x", encoding="utf-8")
    (data / "REPORT.PDF").write_text("%PDF-1.4", encoding="utf-8")
    (data / "reports").mkdir()
    (data / "reports" / "report.pdf").write_text("%PDF-1.4", encoding="utf-8")
    (data / "note.txt").write_text("x", encoding="utf-8")
    (data / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(gui_bridge, "ROOT", tmp_path)
    opened: list[str] = []
    monkeypatch.setattr(gui_bridge, "_system_open", lambda p: opened.append(str(p)))

    def run(argv):
        capsys.readouterr()
        code = gui_bridge.main(argv)
        out = capsys.readouterr().out
        return code, json.loads(out)

    run.opened = opened
    return run


def test_open_html(open_patched):
    code, data = open_patched(["open-file", "report.html"])
    assert code == 0
    assert data["opened"] == "report.html"
    assert _first_open(open_patched).endswith("report.html")


def _first_open(run) -> str:
    return run.opened[0] if run.opened else ""


def test_open_html_uppercase(open_patched):
    code, data = open_patched(["open-file", "REPORT.HTML"])
    assert code == 0
    assert data["opened"] == "REPORT.HTML"
    assert _first_open(open_patched).endswith("REPORT.HTML")


def test_open_htm(open_patched):
    code, data = open_patched(["open-file", "doc.htm"])
    assert code == 0
    assert _first_open(open_patched).endswith("doc.htm")


def test_open_pdf(open_patched):
    code, data = open_patched(["open-file", "REPORT.PDF"])
    assert code == 0
    assert _first_open(open_patched).endswith("REPORT.PDF")


def test_open_nested_pdf(open_patched):
    code, data = open_patched(["open-file", "reports/report.pdf"])
    assert code == 0
    assert _first_open(open_patched).endswith("reports/report.pdf")


def test_open_traversal_safe_stays_inside_data(open_patched):
    # cache/../report.html resolves to data/report.html → inside data/, allowed
    code, data = open_patched(["open-file", "cache/../report.html"])
    assert code == 0
    assert _first_open(open_patched).endswith("report.html")


def test_open_unsupported_txt(open_patched):
    code, data = open_patched(["open-file", "note.txt"])
    assert code == 1
    assert "unsupported" in data["error"]


def test_open_unsupported_json(open_patched):
    code, data = open_patched(["open-file", "config.json"])
    assert code == 1
    assert "unsupported" in data["error"]


def test_open_unsupported_db(open_patched):
    code, data = open_patched(["open-file", "argus_2025.db"])
    assert code == 1
    assert "unsupported" in data["error"]


def test_open_missing_file(open_patched):
    code, data = open_patched(["open-file", "nonexistent.pdf"])
    assert code == 1
    assert "not found" in data["error"]


def test_open_directory_not_a_file(open_patched):
    code, data = open_patched(["open-file", "reports"])
    assert code == 1
    assert "not a file" in data["error"]


def test_open_dotdot_escapes_rejected(open_patched):
    for attempt in ("../report.html", "../../report.pdf", "../../../etc/passwd"):
        code, data = open_patched(["open-file", attempt])
        assert code == 1, attempt
        assert "escapes" in data["error"], attempt


def test_open_absolute_paths_rejected(open_patched):
    for attempt in ("/etc/passwd", "/tmp/test.pdf"):
        code, data = open_patched(["open-file", attempt])
        assert code == 1, attempt
        assert "absolute" in data["error"], attempt


def test_sources_returns_real_registry(capsys):
    """The `sources` command reflects the real SourceRegistry (10 banks, each
    with its configured sources) — nothing is invented or duplicated."""
    assert gui_bridge.main(["sources"]) == 0
    data = json.loads(capsys.readouterr().out)
    banks = data["banks"]
    assert len(banks) == 10
    assert "fed" in banks
    assert "rbnz" in banks  # known bank, even though the toggle is OFF
    fed = banks["fed"]
    assert fed["bank"] == "Federal Reserve"
    assert fed["sources"], "fed must have at least one configured source"
    for source in fed["sources"]:
        assert source["id"]
        assert source["name"]
        assert source["kind"] in ("rss", "sitemap", "html", "search")
        assert source["url"].startswith("http")
        assert "enabled" in source
        assert "search_fallback" in source
        assert isinstance(source["publication_types"], list)


def test_bridge_subprocess_data_root():
    # real subprocess invocation, as the GUI does
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "argus.gui_bridge", "data-root"],
        capture_output=True,
        text=True,
        cwd=str(gui_bridge.ROOT),
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(gui_bridge.ROOT / "src")},
    )
    assert result.returncode == 0
    root = json.loads(result.stdout)["root"]
    assert root.endswith("data")


# ---------------------------------------------------------------------------
# Discovery campaign commands (run lifecycle + results + toggle respect)
# ---------------------------------------------------------------------------


def _publication(*, id_, bank, title, url, source_id, extra=None):
    from argus.models import Publication, PublicationStatus

    return Publication(
        id=id_,
        central_bank=bank,
        title=title,
        url=url,
        source_id=source_id,
        source_url="https://bank.example/feed.xml",
        extra=extra or {},
        status=PublicationStatus.DISCOVERED,
        last_seen_at=None,
        publication_date=None,
    )


def _fake_collector_factory(calls, pubs_fn):
    """A CentralBankCollector stand-in recording the requested banks and
    date bounds and returning the campaign's publications — no network is ever
    touched."""
    class _FakeCollector:
        def __init__(self, **kwargs):
            self.called_banks = None
            self.called_bounds = (None, None)

        def discover_all(self, *, banks=None, run_id=None, date_start=None, date_end=None):
            self.called_banks = banks
            self.called_bounds = (date_start, date_end)
            calls.append((banks, (date_start, date_end)))
            return pubs_fn(run_id)

    return _FakeCollector


def _seed_store(tmp_path, pubs):
    store = gui_bridge.Store(tmp_path / "data" / "argus.db")
    for pub in pubs:
        store.upsert_publication(pub)
    store.close()


def test_discovery_run_records_completed_campaign(monkeypatch, tmp_path, capsys, patched):
    """discovery-run records a completed campaign whose candidates carry the
    Core-derived provenance (Native/Search, New/Known) and the enabled banks."""
    known_pub = _publication(
        id_="src1", bank="fed", title="FOMC Statement",
        url="https://www.federalreserve.gov/newsevents/pressreleases/statement.htm",
        source_id="fed_press_releases_rss", extra={"feed_guid": "1"},
    )
    _seed_store(tmp_path, [known_pub])

    fresh_native = _publication(
        id_="src2", bank="ecb", title="Monetary Policy Statement",
        url="https://www.ecb.europa.eu/press/govcdec/mopo/statement.html",
        source_id="ecb_mopo", extra={"feed_guid": "2"},
    )
    fresh_search = _publication(
        id_="src3", bank="rba", title="Search-discovered release",
        url="https://www.rba.gov.au/speech/2026/sp-cc.html",
        source_id="rba_search", extra={"discovery_method": "search"},
    )
    calls: list = []

    def pubs_fn(run_id):
        return [fresh_native, fresh_search, known_pub]

    monkeypatch.setattr(gui_bridge, "CentralBankCollector", _fake_collector_factory(calls, pubs_fn))

    code, data = patched(["discovery-run"])
    assert code == 0
    assert data["status"] == "completed"
    assert data["candidates"] == 3
    assert "rbnz" not in (calls[0][0] or ()), "an OFF bank must never run"

    # status
    code2, status = patched(["discovery-status"])
    assert code2 == 0
    assert status["status"] == "completed"
    assert status["candidates"] == 3
    assert "fed" in status["banks"]
    assert "rbnz" not in status["banks"]

    # results
    code3, results = patched(["discovery-results"])
    assert code3 == 0
    assert results["total"] == 3
    by_id = {c["publication_id"]: c for c in results["candidates"]}
    # ecb: native strategy, new candidate
    assert by_id["src2"]["bank_name"] == "European Central Bank"
    assert by_id["src2"]["method"] == "native"
    assert by_id["src2"]["is_new"] is True
    # rba: search provenance carried by the Core in extra.discovery_method
    assert by_id["src3"]["method"] == "search"
    assert by_id["src3"]["is_new"] is True
    # fed: known before the run → Known, native (rss has no search marker)
    assert by_id["src1"]["method"] == "native"
    assert by_id["src1"]["is_new"] is False


def test_discovery_run_failure_records_failed_status(monkeypatch, tmp_path, capsys, patched):
    class _FailingCollector:
        def __init__(self, **kwargs):
            pass

        def discover_all(self, *, banks=None, run_id=None, date_start=None, date_end=None):
            raise RuntimeError("boom")

    monkeypatch.setattr(gui_bridge, "CentralBankCollector", _FailingCollector)
    code, data = patched(["discovery-run"])
    assert code == 0
    assert data["status"] == "failed"
    assert "RuntimeError: boom" in data["error"]

    _, status = patched(["discovery-status"])
    assert status["status"] == "failed"
    assert status["error"]


def test_discovery_status_idle_when_no_runs(patched):
    code, data = patched(["discovery-status"])
    assert code == 0
    assert data["status"] == "idle"
    assert data["run_id"] is None


def test_discovery_respects_bank_toggle(monkeypatch, tmp_path, capsys, patched):
    """Fed ON + RBNZ OFF → the campaign never selects RBNZ; after re-enabling
    RBNZ it participates normally. The toggle is the Core's single truth."""
    from argus.config import is_bank_enabled

    assert is_bank_enabled("fed") is True
    assert is_bank_enabled("rbnz") is False

    calls: list = []

    def pubs_fn(run_id):
        return [
            _publication(
                id_="p1", bank="fed", title="FOMC", url="https://fed.gov/1",
                source_id="fed_rss",
            )
        ]

    monkeypatch.setattr(gui_bridge, "CentralBankCollector", _fake_collector_factory(calls, pubs_fn))

    code, data = patched(["discovery-run"])
    assert code == 0 and data["status"] == "completed"
    assert calls and calls[-1][0] is not None
    assert "rbnz" not in calls[-1][0]
    assert "fed" in calls[-1][0]

    # re-enable RBNZ through the Core toggle → participates
    from argus.config import set_bank_enabled

    set_bank_enabled("rbnz", True)
    assert is_bank_enabled("rbnz") is True
    code2, data2 = patched(["discovery-run"])
    assert code2 == 0 and data2["status"] == "completed"
    assert "rbnz" in calls[-1][0]


def test_stats_reflect_core_store(patched, tmp_path, monkeypatch):
    from argus.models import Document, DocumentStatus

    _seed_store(
        tmp_path,
        [_publication(id_="p1", bank="fed", title="FOMC", url="https://fed.gov/1", source_id="fed_rss")],
    )
    store = gui_bridge.Store(tmp_path / "data" / "argus.db")
    store.upsert_document(
        Document(publication_id="p1", url="https://fed.gov/1.html", kind="html", status=DocumentStatus.FETCHED)
    )
    store.close()

    code, stats = patched(["stats"])
    assert code == 0
    assert stats["publications"] == 1
    assert stats["documents"] == 1
    assert stats["normalized_documents"] == 0
    assert stats["facts"] == 0
    assert stats["last_discovery"] is None  # no campaign recorded yet

    # after a discovery campaign, last_discovery reflects the real run
    calls: list = []

    def pubs_fn(run_id):
        return [_publication(id_="p9", bank="fed", title="New", url="https://fed.gov/9", source_id="fed_rss")]

    monkeypatch.setattr(gui_bridge, "CentralBankCollector", _fake_collector_factory(calls, pubs_fn))
    patched(["discovery-run"])

    _, stats2 = patched(["stats"])
    assert stats2["last_discovery"]["status"] == "completed"
    assert stats2["last_discovery"]["candidates"] == 1


def test_open_url_opens_http_with_system_default(monkeypatch, capsys):
    opened: list[str] = []
    monkeypatch.setattr(gui_bridge, "_system_open", lambda url: opened.append(str(url)))
    assert gui_bridge.main(["open-url", "https://www.federalreserve.gov/x"]) == 0
    assert opened and opened[0] == "https://www.federalreserve.gov/x"
    data = json.loads(capsys.readouterr().out)
    assert data["opened"].startswith("https://")


def test_open_url_rejects_non_http(monkeypatch, capsys):
    opened: list[str] = []
    monkeypatch.setattr(gui_bridge, "_system_open", lambda url: opened.append(str(url)))
    for bad in ("ftp://example.org", "file:///etc/passwd", "javascript:alert(1)"):
        assert gui_bridge.main(["open-url", bad]) == 1
        assert not opened, f"must not open {bad!r}"
        data = json.loads(capsys.readouterr().out)
        assert "http" in data["error"]


# ---------------------------------------------------------------------------
# Discovery lifecycle controls (pause / resume / stop) and date window
# ---------------------------------------------------------------------------

def _completed_campaign(monkeypatch, patched):
    """Run a discovery campaign through the fake collector (no network)."""
    def pubs_fn(run_id):
        return [_publication(
            id_="c1", bank="fed", title="Candidate", url="https://fed.gov/c1", source_id="fed_rss"
        )]

    monkeypatch.setattr(gui_bridge, "CentralBankCollector", _fake_collector_factory([], pubs_fn))
    patched(["discovery-run"])


def test_discovery_run_records_own_pid(monkeypatch, patched):
    _completed_campaign(monkeypatch, patched)
    _, status = patched(["discovery-status"])
    assert status["pid"] == __import__("os").getpid()


def test_discovery_run_passes_date_bounds(monkeypatch, patched):
    from datetime import datetime, timezone

    calls: list = []
    monkeypatch.setattr(
        gui_bridge, "CentralBankCollector",
        _fake_collector_factory(calls, lambda run_id: []),
    )
    code, data = patched(["discovery-run", "--start-date", "2026-01-01", "--end-date", "2026-02-01"])
    assert code == 0 and data["status"] == "completed"
    start, end = calls[-1][1]
    assert start == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert end == datetime(2026, 2, 1, tzinfo=timezone.utc)
    assert start is not None and end is not None


def test_discovery_run_rejects_invalid_dates(monkeypatch, tmp_path, capsys):
    _make_data(tmp_path)
    monkeypatch.setattr(gui_bridge, "ROOT", tmp_path)
    assert gui_bridge.main(["discovery-run", "--start-date", "not-a-date"]) == 2
    assert "start-date must be a date" in capsys.readouterr().err


def test_discovery_run_stop_records_stopped_status(monkeypatch, patched):
    """A SIGTERMped campaign records itself as ``stopped`` (never ``failed``)."""

    class _StoppedCollector:
        def __init__(self, **kwargs):
            pass

        def discover_all(self, *, banks=None, run_id=None, date_start=None, date_end=None):
            raise gui_bridge.DiscoveryStopped("stop requested")

    monkeypatch.setattr(gui_bridge, "CentralBankCollector", _StoppedCollector)
    code, data = patched(["discovery-run"])
    assert code == 0
    assert data["status"] == "stopped"
    _, status = patched(["discovery-status"])
    assert status["status"] == "stopped"
    assert status["error"]


def test_discovery_control_pause_resume_stop(monkeypatch, tmp_path, patched):
    """Real signals to the campaign subprocess: SIGSTOP freezes it, SIGCONT
    resumes it, SIGTERM ends it — the store reflects each transition."""
    import os
    import subprocess
    import sys

    if os.name == "nt":
        import pytest

        pytest.skip("POSIX signals required")

    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        store = gui_bridge.Store(tmp_path / "data" / "argus.db")
        store.start_discovery_run("ctrl-1", ["fed"], pid=sleeper.pid)
        store.close()

        code, run = patched(["discovery-control", "pause"])
        assert code == 0
        assert run["status"] == "paused"
        assert run["pid"] == sleeper.pid
        assert sleeper.poll() is None  # frozen, not dead

        code, run = patched(["discovery-control", "resume"])
        assert code == 0
        assert run["status"] == "running"

        code, run = patched(["discovery-control", "stop"])
        assert code == 0
        assert run["status"] == "stopped"
        assert sleeper.wait(5) is not None  # terminated
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
            sleeper.wait()


def test_discovery_control_stop_of_dead_campaign_records_stopped(monkeypatch, tmp_path, patched):
    import subprocess
    import sys

    if sys.platform == "win32":
        import pytest

        pytest.skip("POSIX signals required")
    # a pid whose process has already exited (safe: never an out-of-range value)
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    store = gui_bridge.Store(tmp_path / "data" / "argus.db")
    store.start_discovery_run("dead-1", ["fed"], pid=gone.pid)
    store.close()
    code, run = patched(["discovery-control", "stop"])
    assert code == 0
    assert run["status"] == "stopped"


def test_discovery_control_requires_active_campaign(monkeypatch, patched):
    code, data = patched(["discovery-control", "pause"])
    assert code == 1
    assert "no active campaign" in data["error"]

    _completed_campaign(monkeypatch, patched)
    code2, data2 = patched(["discovery-control", "stop"])
    assert code2 == 1
    assert "no active campaign to stop" in data2["error"]


def test_discovery_control_unknown_action(patched):
    assert gui_bridge.main(["discovery-control", "explode"]) == 2


def test_discovery_clear_preserves_history_clears_candidates(monkeypatch, tmp_path, patched):
    from argus.models import Document, DocumentStatus

    _seed_store(
        tmp_path,
        [_publication(id_="p1", bank="fed", title="FOMC", url="https://fed.gov/1", source_id="fed_rss")],
    )
    store = gui_bridge.Store(tmp_path / "data" / "argus.db")
    store.upsert_document(
        Document(publication_id="p1", url="https://fed.gov/1.html", kind="html", status=DocumentStatus.FETCHED)
    )
    store.close()

    _completed_campaign(monkeypatch, patched)
    _, status = patched(["discovery-status"])
    assert status["status"] == "completed"
    assert status["candidates"] == 1
    assert status["new"] == 1

    code, cleared = patched(["discovery-clear"])
    assert code == 0
    assert cleared["candidates_cleared"] == 1
    assert cleared["runs_preserved"] == 1  # campaign history survives the cache

    # the candidate cache is gone, the run history is preserved (candidates 0)
    _, status = patched(["discovery-status"])
    assert status["status"] == "completed"  # history: the run still exists
    assert status["candidates"] == 0
    assert status["new"] == 0 and status["known"] == 0
    # results: the snapshot list is empty
    _, results = patched(["discovery-results"])
    assert results["total"] == 0
    # …but pipeline data (publications, documents) is untouched
    store = gui_bridge.Store(tmp_path / "data" / "argus.db")
    assert store.count_publications() == 1
    assert store.count_documents() == 1
    store.close()


def test_discovery_clear_refused_while_running(patched, tmp_path):
    store = gui_bridge.Store(tmp_path / "data" / "argus.db")
    store.start_discovery_run("run-1", ["fed"], pid=0)
    store.close()
    code, data = patched(["discovery-clear"])
    assert code == 1
    assert "while a campaign is active" in data["error"]


def test_discovery_clear_refused_while_paused(patched, tmp_path):
    store = gui_bridge.Store(tmp_path / "data" / "argus.db")
    store.start_discovery_run("run-1", ["fed"], pid=0)
    store.set_discovery_run_control("run-1", "paused")
    store.close()
    code, data = patched(["discovery-clear"])
    assert code == 1
    assert "while a campaign is active" in data["error"]


def test_discovery_run_refused_when_already_active(patched, tmp_path, monkeypatch):
    """A second launch is refused by the backend — React is not responsible
    for the single-active invariant."""
    store = gui_bridge.Store(tmp_path / "data" / "argus.db")
    store.start_discovery_run("run-1", ["fed"], pid=0)
    store.close()

    calls: list = []

    def pubs_fn(run_id):
        return []

    monkeypatch.setattr(gui_bridge, "CentralBankCollector", _fake_collector_factory(calls, pubs_fn))
    code, data = patched(["discovery-run"])
    assert code == 1
    assert "already active" in data["error"]
    assert calls == []  # no campaign ever started


def test_start_discovery_run_enforces_single_active(tmp_path):
    store = gui_bridge.Store(tmp_path / "data" / "argus.db")
    store.start_discovery_run("run-a", ["fed"], pid=1)
    try:
        store.start_discovery_run("run-b", ["ecb"], pid=2)
    except gui_bridge.ActiveDiscoveryError as exc:
        assert "run-a" in str(exc)
    else:
        raise AssertionError("a second active campaign must be refused")
    # the winner is untouched, the loser never recorded
    assert store.latest_discovery_run()["run_id"] == "run-a"
    assert store.get_discovery_run("run-b") is None


def test_discovery_run_reconciles_dead_active_before_launch(patched, tmp_path, monkeypatch, capsys):
    """A stale 'running' row whose PID is gone is reconciled (failed) first, so
    a fresh campaign can start."""
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    store = gui_bridge.Store(tmp_path / "data" / "argus.db")
    store.start_discovery_run("stale-1", ["fed"], pid=gone.pid)
    store.close()

    calls: list = []

    def pubs_fn(run_id):
        return [_publication(id_="p2", bank="fed", title="F2", url="https://fed.gov/2", source_id="fed_rss")]

    monkeypatch.setattr(gui_bridge, "CentralBankCollector", _fake_collector_factory(calls, pubs_fn))
    code, data = patched(["discovery-run"])
    assert code == 0
    assert data["status"] == "completed"
    # the stale record was reconciled before the guard ran
    store = gui_bridge.Store(tmp_path / "data" / "argus.db")
    stale = store.get_discovery_run("stale-1")
    assert stale["status"] == "failed"
    store.close()


def test_discovery_control_targets_explicit_run_id(patched, tmp_path, monkeypatch):
    """The control commands operate on the given run_id — never an implicit
    'latest' that could be a different campaign."""
    store = gui_bridge.Store(tmp_path / "data" / "argus.db")
    store.start_discovery_run("old-1", ["fed"], pid=999999)
    store.finish_discovery_run("old-1", status="completed")
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    store.start_discovery_run("live-1", ["ecb"], pid=sleeper.pid)
    store.close()
    try:
        # a completed run_id cannot be controlled
        code, data = patched(["discovery-control", "pause", "old-1"])
        assert code == 1
        assert "no active campaign" in data["error"]

        # the active run_id is targeted precisely (its own PID is signalled)
        code, run = patched(["discovery-control", "pause", "live-1"])
        assert code == 0
        assert run["run_id"] == "live-1"
        assert run["pid"] == sleeper.pid
        assert run["status"] == "paused"
        assert gui_bridge._process_state(sleeper.pid).startswith("T"), "must actually be stopped"

        code, run = patched(["discovery-control", "resume", "live-1"])
        assert code == 0
        assert run["run_id"] == "live-1"
        assert run["status"] == "running"
        assert not gui_bridge._process_state(sleeper.pid).startswith("T")

        # explicit run_id that does not exist
        code, data = patched(["discovery-control", "stop", "nope-1"])
        assert code == 1
        assert "no active campaign" in data["error"]
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
            sleeper.wait()


def test_discovery_control_pause_dead_pid_reconciles(patched, tmp_path):
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    for index, action in enumerate(("pause", "resume")):
        run_id = f"dead-{index}"
        store = gui_bridge.Store(tmp_path / "data" / "argus.db")
        store.start_discovery_run(run_id, ["fed"], pid=gone.pid)
        store.close()

        code, data = patched(["discovery-control", action, run_id])
        assert code == 1
        assert "no longer running" in data["error"]
        _, status = patched(["discovery-status"])
        assert status["status"] == "failed", action
        assert "exited unexpectedly" in status["error"]


def test_discovery_status_reconciles_dead_pid(tmp_path, monkeypatch, capsys):
    for seeded_status in ("running", "paused"):
        root = tmp_path / seeded_status
        root.mkdir()
        monkeypatch.setattr(gui_bridge, "ROOT", root)
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        store = gui_bridge.Store(root / "data" / "argus.db")
        run_id = f"dead-{seeded_status}"
        store.start_discovery_run(run_id, ["fed"], pid=gone.pid)
        if seeded_status == "paused":
            store.set_discovery_run_control(run_id, "paused")
        store.close()

        code = gui_bridge.main(["discovery-status"])
        assert code == 0
        status = json.loads(capsys.readouterr().out)
        assert status["status"] == "failed", f"stored {seeded_status}"
        assert status["run_id"] == run_id
        assert "exited unexpectedly" in status["error"]


def test_discovery_stop_kills_uncooperative_process(patched, tmp_path, monkeypatch):
    """SIGTERM alone is not enough → escalate to SIGKILL, verify real exit, and
    only then record `stopped`. A live process must never hide behind a
    `stopped` Store row."""
    monkeypatch.setattr(gui_bridge, "STOP_GRACE_S", 0.3)
    monkeypatch.setattr(gui_bridge, "STOP_KILL_S", 0.5)

    stubborn = subprocess.Popen(
        [sys.executable, "-c", "import signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); import time; time.sleep(120)"]
    )
    store = gui_bridge.Store(tmp_path / "data" / "argus.db")
    store.start_discovery_run("stub-1", ["fed"], pid=stubborn.pid)
    store.close()
    try:
        code, run = patched(["discovery-control", "stop", "stub-1"])
        assert code == 0
        assert run["status"] == "stopped"
        assert stubborn.wait(5) is not None
        assert run["pid"] == stubborn.pid
        # no active (running/paused) campaign remains
        _, status = patched(["discovery-status"])
        assert status["status"] == "stopped"
        assert not gui_bridge._process_alive(stubborn.pid)
    finally:
        if stubborn.poll() is None:
            stubborn.kill()
            stubborn.wait()
