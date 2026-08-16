"""Command-line bridge for the Argus desktop GUI (Tauri 2).

The GUI never re-implements Argus logic: the Rust layer shells out to this
module and reads JSON on stdout. All bank-state and data-path knowledge stays in
the Python Core, which remains the single source of truth.

Commands (``python -m argus.gui_bridge <command>``):

- ``banks``          → JSON ``{"banks": [{id, name, currency, enabled}, …]}``
- ``banks-set <id> on|off`` → persists the toggle, returns the updated list
- ``data-root``      → JSON ``{"root": "<absolute path of data/>"}``
- ``list-dir <rel>`` → JSON listing of ``<data-root>/<rel>``, confined to ``data/``
  (``rel`` is relative; ``..`` / absolute paths are rejected)
- ``open-file <rel>``→ open a ``.html`` / ``.htm`` / ``.pdf`` file inside ``data/``
  with the OS-default application (never a hardcoded app)
- ``sources``        → JSON view of the real ``SourceRegistry`` (read-only)
- ``discovery-run``  → run a discovery campaign over the enabled banks (long;
  designed to be spawned detached by the Rust shell and observed via
  ``discovery-status`` / ``discovery-results``). Optional ``--start-date`` /
  ``--end-date`` limit the campaign to a publication-date window, applied by
  the Core (start-inclusive, end-exclusive).
- ``discovery-control <pause|resume|stop>`` → the real lifecycle controls:
  the campaign subprocess is frozen (SIGSTOP), resumed (SIGCONT) or asked to
  stop (SIGTERM, the campaign records itself as ``stopped``). The PID is read
  from the store's `discovery_runs.pid`, never invented.
- ``discovery-clear``  → drop the discovery report cache (the ``discovery_runs``
  and ``discovery_candidates`` tables only — pipeline data is untouched).
- ``discovery-status``→ JSON summary of the latest discovery run (or ``idle``)
- ``discovery-results``→ JSON candidates produced by the latest (or a given) run
- ``stats``          → read-only store aggregates for the Overview (publications,
  documents, facts, last discovery)
- ``open-url <url>`` → open an http(s) URL with the OS-default application
- ``help``           → usage

Every command exits 0 and prints one JSON object on stdout; errors exit
non-zero with a message on stderr (or a ``{"error": …}`` object for `list-dir`).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .cli import _search_provider_from_env
from .collector import CentralBankCollector
from .config import enabled_banks
from .http import HttpConfig
from .normalize import iso, now_utc
from .registry import SourceRegistry
from .store import Store

USAGE = (
    "usage: python -m argus.gui_bridge "
    "banks|banks-set <id> on|off|data-root|list-dir <relative-path>|"
    "open-file <relative-path>|sources|"
    "discovery-run [--bank <id>]... [--start-date YYYY-MM-DD|--end-date YYYY-MM-DD]|"
    "discovery-control <pause|resume|stop>|discovery-clear|"
    "discovery-status|discovery-results [<run-id>]|stats|open-url <url>"
)

# Repository root, resolved from this module's own location
# (`<root>/src/argus/gui_bridge.py`), never from the process working directory —
# so the bridge behaves identically whether spawned from a shell, `tauri dev` or
# a Finder-launched `.app`.
ROOT = Path(__file__).resolve().parents[2]

# File types the Data browser can open externally (case-insensitive).
OPENABLE_EXTENSIONS = (".html", ".htm", ".pdf")


def _system_open(path: Path) -> None:
    """Open a file with the OS-default application.

    Uses the platform's native launcher (``open`` / ``os.startfile`` /
    ``xdg-open``) — Argus never chooses the application.
    """
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=True)
    elif os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", str(path)], check=True)


def _data_root() -> Path:
    """The Argus data directory (resolved per call, CWD-independent)."""
    return ROOT / "data"


def _resolve_data_path(rel: str) -> Path:
    """Resolve a GUI-supplied *relative* path against the data root, rejecting
    anything that would escape it.

    Absolute paths and ``..``-style escapes are refused after real-path
    resolution — never a raw string concatenation.
    """
    rel = (rel or "").strip()
    if rel in ("", ".", "/"):
        return _data_root()
    candidate = Path(rel)
    if candidate.is_absolute():
        raise ValueError("absolute paths are not allowed")
    root_resolved = _data_root().resolve()
    target = (_data_root() / rel).resolve()
    if not target.is_relative_to(root_resolved):
        raise ValueError("path escapes the Argus data directory")
    return target


def _bank_list() -> list[dict]:
    from .config import is_bank_enabled
    from .registry import SourceRegistry

    registry = SourceRegistry()
    return [
        {
            "id": bank.id,
            "name": bank.name,
            "currency": bank.currency,
            "enabled": is_bank_enabled(bank.id),
        }
        for bank in registry.banks
    ]


def _cmd_banks() -> int:
    print(json.dumps({"banks": _bank_list()}, indent=2))
    return 0


def _cmd_banks_set(argv: list[str]) -> int:
    if len(argv) < 2:
        print(USAGE, file=sys.stderr)
        return 2
    bank_id = argv[0]
    state = argv[1].strip().lower()
    if state not in ("on", "off"):
        print(f"invalid state: {state!r} (expected on|off)", file=sys.stderr)
        return 2
    from .config import set_bank_enabled

    set_bank_enabled(bank_id, state == "on")
    print(json.dumps({"banks": _bank_list()}, indent=2))
    return 0


def _cmd_data_root() -> int:
    # Explicit, working-directory-independent resolution of the Argus data dir.
    print(json.dumps({"root": str(_data_root().resolve())}, indent=2))
    return 0


def _cmd_list_dir(argv: list[str]) -> int:
    """List ``<data-root>/<rel>``. ``rel`` is relative; the bridge controls the
    final path resolution and refuses anything outside ``data/``."""
    rel = (argv[0] if argv else "").strip().rstrip("/")
    try:
        target = _resolve_data_path(rel)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1
    if not target.exists():
        print(json.dumps({"error": f"directory not found: {rel or '/'}"}, indent=2))
        return 1
    if not target.is_dir():
        print(json.dumps({"error": f"not a directory: {rel or '/'}"}, indent=2))
        return 1
    try:
        entries: list[dict] = []
        for item in target.iterdir():
            try:
                is_dir = item.is_dir()
            except OSError:
                is_dir = False
            name = item.name
            entries.append(
                {
                    "name": name,
                    "path": f"{rel}/{name}" if rel else name,
                    "is_dir": is_dir,
                }
            )
        entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    except OSError as exc:
        print(json.dumps({"error": f"cannot read directory: {exc}"}, indent=2))
        return 1
    segments = rel.split("/") if rel else []
    parent = "/".join(segments[:-1]) if segments else None
    print(
        json.dumps(
            {
                "root": str(_data_root().resolve()),
                "path": rel,
                "segments": segments,
                "parent": parent,
                "entries": entries,
            },
            indent=2,
        )
    )
    return 0


def _cmd_open_file(argv: list[str]) -> int:
    """Open a supported file inside ``data/`` with the OS-default application.

    The bridge resolves the relative path, confines it to ``data/``, checks
    existence / file-ness / extension, and only then asks the system to open it.
    """
    if len(argv) < 1:
        print(USAGE, file=sys.stderr)
        return 2
    rel = argv[0].strip().rstrip("/")
    try:
        target = _resolve_data_path(rel)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1
    if not target.exists():
        print(json.dumps({"error": f"file not found: {rel}"}, indent=2))
        return 1
    if not target.is_file():
        print(json.dumps({"error": f"not a file: {rel}"}, indent=2))
        return 1
    if target.suffix.lower() not in OPENABLE_EXTENSIONS:
        print(
            json.dumps({"error": f"unsupported file type: {target.suffix or '(none)'}"}, indent=2)
        )
        return 1
    try:
        _system_open(target)
    except Exception as exc:
        print(json.dumps({"error": f"cannot open file: {exc}"}, indent=2))
        return 1
    print(json.dumps({"opened": rel, "path": str(target)}, indent=2))
    return 0


def _cmd_sources() -> int:
    """Read-only view of the real ``SourceRegistry`` (never duplicated in the
    frontend): each bank's known sources with their discovery configuration."""
    from .registry import SourceRegistry

    registry = SourceRegistry()
    banks: dict[str, dict] = {}
    for bank in registry.banks:
        sources = []
        for source in registry.sources_for_bank(bank.id):
            sources.append(
                {
                    "id": source.id,
                    "name": source.name,
                    "kind": source.discovery.kind,
                    "url": source.discovery.url,
                    "enabled": source.enabled,
                    "publication_types": list(source.publication_types),
                    "search_fallback": bool(source.discovery.search_query)
                    and source.discovery.search_fallback_on_empty,
                }
            )
        banks[bank.id] = {"bank": bank.name, "sources": sources}
    print(json.dumps({"banks": banks}, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Discovery campaign commands
# ---------------------------------------------------------------------------

def _store_path() -> Path:
    """The Argus SQLite store path (``data/argus.db``), CWD-independent."""
    return _data_root() / "argus.db"


def _raw_root() -> Path:
    return _data_root() / "raw"


class DiscoveryStopped(Exception):
    """Raised inside the campaign process when a stop (SIGTERM) is requested.

    SIGTERM is a normal lifecycle control, never a "failed" signal — the
    campaign finalizes itself as ``stopped`` instead of ``failed`` so the GUI
    shows exactly what happened.
    """


def _raise_discovery_stopped(_signum, _frame):
    raise DiscoveryStopped("stop requested")


def _parse_date_arg(value: str, name: str) -> datetime:
    """Parse an optional discovery ``--start-date`` / ``--end-date`` argument.

    Accepts an ISO date (``YYYY-MM-DD``) or a full ISO datetime; the result is
    always timezone-aware (UTC) so the Core's bounds comparison never mixes
    naive and aware datetimes.
    """
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=timezone.utc)
    raise ValueError(f"{name} must be a date (YYYY-MM-DD or ISO datetime): {value!r}")


def _run_discovery_campaign(
    store_path: Path,
    raw_root: Path,
    banks: tuple[str, ...],
    date_start: datetime | None = None,
    date_end: datetime | None = None,
) -> dict:
    """Run one discovery campaign and record its lifecycle in the store.

    Orchestration only — every piece of discovery logic (strategy selection,
    native/search fallback, deduplication, upsert, provenance) runs inside the
    existing ``CentralBankCollector.discover_all``. ``banks`` is the enabled
    selection resolved by the caller (the GUI always passes the toggle-respecting
    ``enabled_banks()``), so an OFF bank can never be launched.

    ``date_start`` / ``date_end`` bound the campaign's publication-date window
    (start-inclusive, end-exclusive) and are handed to the Core, which is the
    single place a window is applied. The campaign process records its own PID
    with the run so the GUI's pause/resume/stop can signal it.
    """
    signal.signal(signal.SIGTERM, _raise_discovery_stopped)
    store = Store(store_path)
    registry = SourceRegistry()
    run_id = store.run_stamp()
    bank_names = {b.id: b.name for b in registry.banks}
    store.start_discovery_run(run_id, banks, pid=os.getpid())
    known = {p.id for p in store.list_publications()}
    try:
        config = HttpConfig(respect_robots=True, min_interval=1.0)
        collector = CentralBankCollector(
            store=store,
            registry=registry,
            http_config=config,
            raw_root=raw_root,
            search_provider=_search_provider_from_env(),
        )
        publications = collector.discover_all(
            banks=tuple(banks) if banks else None,
            run_id=run_id,
            date_start=date_start,
            date_end=date_end,
        )
    except DiscoveryStopped:
        store.finish_discovery_run(run_id, status="stopped", error="stopped by user")
        return {"run_id": run_id, "status": "stopped", "error": "stopped by user", "candidates": 0}
    except Exception as exc:  # pragma: no cover - defensive (Core raises are logged)
        message = f"{exc.__class__.__name__}: {exc}"
        store.finish_discovery_run(run_id, status="failed", error=message)
        return {"run_id": run_id, "status": "failed", "error": message, "candidates": 0}

    candidates = []
    for pub in publications:
        candidates.append(
            {
                "publication_id": pub.id,
                "bank_id": pub.central_bank,
                "bank_name": bank_names.get(pub.central_bank, pub.central_bank),
                "title": pub.title,
                "url": pub.url,
                "source_id": pub.source_id,
                # Search candidates carry discovery provenance in their stored
                # `extra`; every other candidate was produced by a native
                # strategy. No value is invented here.
                "method": "search" if pub.extra.get("discovery_method") == "search" else "native",
                "is_new": pub.id not in known,
                "discovered_at": iso(pub.last_seen_at) if pub.last_seen_at else iso(now_utc()),
                "publication_date": iso(pub.publication_date) if pub.publication_date else None,
            }
        )
    store.finish_discovery_run(run_id, status="completed", candidates=candidates)
    return {"run_id": run_id, "status": "completed", "error": None, "candidates": len(candidates)}


def _cmd_discovery_run(argv: list[str]) -> int:
    """Run a discovery campaign over the enabled banks.

    Optional ``--bank <id>`` (repeatable) selects a subset; without one, the
    run uses every currently enabled bank. Either way the selection is the
    Core's toggle-respecting enabled set — never a GUI-side bank list.
    Optional ``--start-date`` / ``--end-date`` (ISO dates) bound the campaign
    to a publication-date window, applied by the Core.
    """
    banks = enabled_banks()
    selected: list[str] = []
    date_start = None
    date_end = None
    index = 0
    while index < len(argv):
        if argv[index] == "--bank" and index + 1 < len(argv):
            selected.append(argv[index + 1])
            index += 2
        elif argv[index] == "--start-date" and index + 1 < len(argv):
            try:
                date_start = _parse_date_arg(argv[index + 1], "start-date")
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            index += 2
        elif argv[index] == "--end-date" and index + 1 < len(argv):
            try:
                date_end = _parse_date_arg(argv[index + 1], "end-date")
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            index += 2
        else:
            print(f"unexpected argument: {argv[index]}", file=sys.stderr)
            return 2
    if selected:
        from .config import filter_enabled

        banks = filter_enabled(selected) or ()
    result = _run_discovery_campaign(_store_path(), _raw_root(), banks, date_start, date_end)
    print(json.dumps(result, indent=2))
    return 0


def _await_run_status(store: Store, run_id: str, terminal: tuple[str, ...], timeout: float) -> dict | None:
    """Poll a run until it reaches a terminal status (used after a stop signal
    so the campaign's own ``stopped`` finalization wins over a forced one)."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = store.get_discovery_run(run_id)
        if run and run["status"] in terminal:
            return run
        time.sleep(0.2)
    return None


def _cmd_discovery_control(argv: list[str]) -> int:
    """Real lifecycle control of the active campaign subprocess.

    The campaign records its PID in the store; the controlling command reads it
    and signals the process directly (SIGSTOP / SIGCONT / SIGTERM) — the GUI
    never fabricates a state. The status is flipped in the store by the
    controller, because a SIGSTOPped process cannot write to the store itself,
    except for ``stop`` where the campaign is asked to finalize itself as
    ``stopped`` (a SIGTERMped process records an honest status).
    """
    if len(argv) < 1:
        print(USAGE, file=sys.stderr)
        return 2
    action = argv[0].strip().lower()
    if action not in ("pause", "resume", "stop"):
        print(f"unknown discovery-control action: {action} (pause|resume|stop)", file=sys.stderr)
        return 2
    store = Store(_store_path())
    run = store.latest_discovery_run()
    if run is None or run["status"] in ("idle", "completed", "failed", "stopped"):
        print(json.dumps({"error": f"no active campaign to {action}"}, indent=2))
        return 1
    run_id = run["run_id"]
    pid = run.get("pid")
    if not pid:
        print(json.dumps({"error": "campaign has no recorded pid (started outside the desktop GUI)"}, indent=2))
        return 1
    try:
        if action == "pause":
            os.kill(pid, signal.SIGSTOP)
        elif action == "resume":
            os.kill(pid, signal.SIGCONT)
        else:  # stop: deliver after un-freezing a paused campaign, so the
            # process' own stop handler can actually run and record `stopped`.
            os.kill(pid, signal.SIGCONT)
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        if action != "stop":
            print(json.dumps({"error": f"campaign process {pid} is no longer running"}, indent=2))
            return 1
        # The campaign is already gone → record an honest stopped lifecycle.
    except OSError as exc:
        print(json.dumps({"error": f"cannot {action} campaign process {pid}: {exc}"}, indent=2))
        return 1

    if action == "stop":
        # Give the campaign a moment to finalize itself as `stopped`; if it
        # cannot (already dead, stuck frozen), the controller records it.
        run = _await_run_status(store, run_id, ("stopped", "failed", "completed"), timeout=2.0)
        if run is None:
            store.finish_discovery_run(run_id, status="stopped", error="stopped by user (process did not finalize)")
            run = store.get_discovery_run(run_id)
    else:
        store.set_discovery_run_control(run_id, "paused" if action == "pause" else "running")
        run = store.get_discovery_run(run_id)
    print(json.dumps(run, indent=2))
    return 0


def _cmd_discovery_clear(argv: list[str]) -> int:
    """Drop the discovery report cache (runs + candidate snapshots only)."""
    store = Store(_store_path())
    runs, candidates = store.clear_discovery_cache()
    print(json.dumps({"runs_cleared": runs, "candidates_cleared": candidates}, indent=2))
    return 0


def _cmd_discovery_status(argv: list[str]) -> int:
    """JSON summary of the most recent discovery campaign (or ``idle``)."""
    store = Store(_store_path())
    run = store.latest_discovery_run()
    if run is None:
        print(
            json.dumps(
                {
                    "run_id": None,
                    "status": "idle",
                    "started_at": None,
                    "finished_at": None,
                    "error": None,
                    "candidates": 0,
                    "banks": [],
                },
                indent=2,
            )
        )
        return 0
    print(json.dumps(run, indent=2))
    return 0


def _cmd_discovery_results(argv: list[str]) -> int:
    """JSON candidates of a discovery campaign (latest run by default)."""
    store = Store(_store_path())
    run = store.get_discovery_run(argv[0]) if argv else store.latest_discovery_run()
    if run is None:
        print(
            json.dumps(
                {
                    "run_id": None,
                    "status": "idle",
                    "started_at": None,
                    "finished_at": None,
                    "candidates": [],
                    "total": 0,
                },
                indent=2,
            )
        )
        return 0
    candidates = store.list_discovery_candidates(run["run_id"])
    run["candidates"] = candidates
    run["total"] = len(candidates)
    print(json.dumps(run, indent=2))
    return 0


def _cmd_stats(argv: list[str]) -> int:
    """Read-only store aggregates for the Overview (no derived magic)."""
    store = Store(_store_path())
    print(
        json.dumps(
            {
                "publications": store.count_publications(),
                "documents": store.count_documents(),
                "normalized_documents": store.count_normalized_documents(),
                "facts": store.count_facts(),
                "last_discovery": store.latest_discovery_run(),
            },
            indent=2,
        )
    )
    return 0


def _cmd_open_url(argv: list[str]) -> int:
    """Open an http(s) URL with the OS-default application (no app hardcoded)."""
    if len(argv) < 1:
        print(USAGE, file=sys.stderr)
        return 2
    url = (argv[0] or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        print(json.dumps({"error": "only http(s) URLs can be opened"}, indent=2))
        return 1
    try:
        _system_open(url)
    except Exception as exc:
        print(json.dumps({"error": f"cannot open URL: {exc}"}, indent=2))
        return 1
    print(json.dumps({"opened": url}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(USAGE, file=sys.stderr)
        return 2
    command = argv[0]
    if command == "banks":
        return _cmd_banks()
    if command == "banks-set":
        return _cmd_banks_set(argv[1:])
    if command == "data-root":
        return _cmd_data_root()
    if command == "list-dir":
        return _cmd_list_dir(argv[1:])
    if command == "open-file":
        return _cmd_open_file(argv[1:])
    if command == "sources":
        return _cmd_sources()
    if command == "discovery-run":
        return _cmd_discovery_run(argv[1:])
    if command == "discovery-control":
        return _cmd_discovery_control(argv[1:])
    if command == "discovery-clear":
        return _cmd_discovery_clear(argv[1:])
    if command == "discovery-status":
        return _cmd_discovery_status(argv[1:])
    if command == "discovery-results":
        return _cmd_discovery_results(argv[1:])
    if command == "stats":
        return _cmd_stats(argv[1:])
    if command == "open-url":
        return _cmd_open_url(argv[1:])
    if command in ("help", "--help", "-h"):
        print(USAGE)
        return 0
    print(f"unknown command: {command}", file=sys.stderr)
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
