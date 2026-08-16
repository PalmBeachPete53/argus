"""Tests for the `argus.gui_bridge` filesystem commands (data listing).

The Data browser accesses the filesystem *only* through the bridge, which
resolves every path relative to the Argus ``data/`` directory and refuses any
navigation that escapes it (``..``, absolute paths).
"""

from __future__ import annotations

import json

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
    returning the campaign's publications — no network is ever touched."""
    class _FakeCollector:
        def __init__(self, **kwargs):
            self.called_banks = None

        def discover_all(self, *, banks=None, run_id=None):
            self.called_banks = banks
            calls.append(banks)
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
    assert "rbnz" not in (calls[0] or ()), "an OFF bank must never run"

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

        def discover_all(self, *, banks=None, run_id=None):
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
    assert calls and calls[-1] is not None
    assert "rbnz" not in calls[-1]
    assert "fed" in calls[-1]

    # re-enable RBNZ through the Core toggle → participates
    from argus.config import set_bank_enabled

    set_bank_enabled("rbnz", True)
    assert is_bank_enabled("rbnz") is True
    code2, data2 = patched(["discovery-run"])
    assert code2 == 0 and data2["status"] == "completed"
    assert "rbnz" in calls[-1]


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
