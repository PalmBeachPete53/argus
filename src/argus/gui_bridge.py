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
  ``discovery-status`` / ``discovery-results``). ``--start-date`` /
  ``--end-date`` are **required** and bound the campaign to a
  publication-date window, applied by the Core (start-inclusive,
  end-exclusive; ``start_date <= end_date``).
- ``discovery-control <pause|resume|stop>`` → the real lifecycle controls:
  the campaign subprocess is frozen (SIGSTOP), resumed (SIGCONT) or asked to
  stop (SIGTERM, the campaign records itself as ``cancelled``). The PID is read
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
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .cli import _search_provider_from_env
from .collector import CentralBankCollector
from .config import enabled_banks
from .http import HttpConfig
from .normalize import iso, now_utc
from .registry import SourceRegistry
from .store import ActiveDiscoveryError, Store, make_run_stamp

USAGE = (
    "usage: python -m argus.gui_bridge "
    "banks|banks-set <id> on|off|data-root|list-dir <relative-path>|"
    "open-file <relative-path>|sources|"
    "discovery-run [--bank <id>]... --start-date YYYY-MM-DD --end-date YYYY-MM-DD|"
    "discovery-control <pause|resume|stop> [<run-id>]|discovery-clear|"
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
    campaign finalizes itself as ``cancelled`` instead of ``failed`` so the GUI
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


# Lifecycle-control timings (kept as module constants so tests can shorten
# them): how long a Stop waits for SIGTERM to be honoured before escalating to
# SIGKILL, and how long it waits for the hard kill to take effect.
STOP_GRACE_S = 2.5
STOP_KILL_S = 2.0

_ACTIVE_STATUSES = ("running", "paused")
_TERMINAL_STATUSES = ("completed", "failed", "cancelled", "stopped")


def _process_state(pid: int) -> str:
    """The process state char (``ps -o stat=``), or ``""`` when the process is
    gone. Distinguishes a live process from a dead-but-unreaped zombie: a
    zombie reports ``Z``, which ``_process_alive`` treats as not alive."""
    try:
        out = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip()


def _process_alive(pid: int) -> bool:
    """True only for a *runnable* process (running or stopped, never a zombie).

    ``os.kill(pid, 0)`` alone is not enough: an unreaped zombie still answers
    it, which would let the Store keep lying about a dead campaign. The state
    comes from ``ps``, the only portable zombie-aware oracle.
    """
    state = _process_state(pid)
    return bool(state) and not state.startswith("Z")


def _process_command_line(pid: int) -> str:
    """The full command line of ``pid`` (``ps -o command=``), or ``""``."""
    try:
        out = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip()


def _process_identity(pid: int) -> str:
    """Classify the recorded PID for signalling decisions.

    - ``"missing"``   → no runnable process exists for that PID;
    - ``"discovery"`` → a live process whose command line is recognizably an
      Argus discovery campaign (``argus.gui_bridge discovery-run``);
    - ``"foreign"``   → a *live* process that is not the recorded campaign —
      typically a recycled PID. It is never signalled.
    """
    if not _process_alive(pid):
        return "missing"
    cmdline = _process_command_line(pid)
    if "argus.gui_bridge" in cmdline and "discovery-run" in cmdline:
        return "discovery"
    return "foreign"


def _process_group_id(pid: int) -> int | None:
    """The process-group id of ``pid`` (``ps -o pgid=``), or ``None``."""
    try:
        out = subprocess.run(
            ["ps", "-o", "pgid=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = out.stdout.strip()
    if not value.isdigit():
        return None
    return int(value)


def _process_group_members(pgid: int) -> list[int]:
    """PIDs of every process currently in the process group ``pgid``.

    A GUI-launched campaign is its own group leader (pgid == pid), so this
    covers any descendants it may have spawned. An empty list means the whole
    group is gone.
    """
    try:
        out = subprocess.run(
            ["ps", "-g", str(pgid), "-o", "pid="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    pids: list[int] = []
    for token in out.stdout.split():
        if token.isdigit():
            pids.append(int(token))
    return pids


def _process_group_alive(pgid: int) -> bool:
    """True while any *runnable* member of the group remains.

    Zombies are excluded: a killed-but-unreaped member still answers ``ps``,
    but it does no work — counting it would make the shutdown wait forever on a
    process that is already gone.
    """
    for member in _process_group_members(pgid):
        state = _process_state(member)
        if state and not state.startswith("Z"):
            return True
    return False


# Set by the Rust shell when it spawns a campaign detached (as opposed to a
# campaign driven in-process by the test harness or a terminal). Enables the
# launcher-liveness guards: a detached campaign must never outlive Argus.
_DETACHED_ENV = "ARGUS_DISCOVERY_DETACHED"


def _launched_detached() -> bool:
    return os.environ.get(_DETACHED_ENV) == "1"


def _reconcile_run(store: Store, run: dict | None) -> dict | None:
    """Bring the Store in line with reality: a campaign whose recorded PID is
    no longer a live process can never be ``running``/``paused`` again.

    The cause is not recoverable from the Store (the process vanished without
    finalizing), so it is the honest non-explicit outcome: ``failed``. An
    explicit user Stop records ``cancelled`` at the moment it *verifies* the
    process is gone, and is never downgraded afterwards.
    """
    if run is None or run["status"] not in _ACTIVE_STATUSES:
        return run
    pid = run.get("pid")
    if pid and not _process_alive(pid):
        store.finish_discovery_run(
            run["run_id"],
            status="failed",
            error=f"campaign process {pid} exited unexpectedly",
        )
        return store.get_discovery_run(run["run_id"])
    return run


def _terminate_campaign(store: Store, run: dict, pid: int) -> dict:
    """Stop a campaign and guarantee the whole process tree is really gone
    before the Store says ``cancelled``.

    1. un-freeze a paused campaign (SIGCONT) so its signals can be delivered;
    2. verify the recorded PID is the real discovery campaign — a live but
       foreign process (e.g. a recycled PID) is never signalled;
    3. SIGTERM the campaign's process group (a detached campaign is its own
       group leader, so any descendants are covered) — graceful stop;
    4. wait for real termination of the whole group (``STOP_GRACE_S``);
    5. escalate to SIGKILL on the same target if anything still lives
       (``STOP_KILL_S``);
    6. only when every member is verified dead, write ``cancelled``.

    The Store is never edited to hide a still-living process: if the process
    cannot be killed, the error propagates and the campaign stays active.
    """
    if run["status"] == "paused":
        try:
            os.kill(pid, signal.SIGCONT)
        except ProcessLookupError:
            pass
        except OSError:
            pass

    identity = _process_identity(pid)
    if identity == "foreign":
        # A live process that is not the recorded campaign: killing it could
        # take down an unrelated process. Never signal it; the run is
        # finalized as failed (the expected campaign is gone) and the stop is
        # reported as an error.
        store.finish_discovery_run(
            run["run_id"],
            status="failed",
            error=f"recorded pid {pid} is no longer the discovery campaign; not terminating",
        )
        raise RuntimeError(
            f"recorded pid {pid} is no longer the discovery campaign; not terminating"
        )
    if identity == "missing":
        # Already gone (or never existed): there is nothing to signal.
        current = store.get_discovery_run(run["run_id"])
        if current and current["status"] in _ACTIVE_STATUSES:
            store.finish_discovery_run(run["run_id"], status="cancelled", error="cancelled by user")
        return store.get_discovery_run(run["run_id"])

    pgid = _process_group_id(pid)
    group_target = pgid if (pgid is not None and pgid == pid) else None
    # A negative kill targets the whole process group (leader + descendants).
    target = -group_target if group_target is not None else pid

    def _tree_alive() -> bool:
        if _process_alive(pid):
            return True
        if group_target is not None:
            return _process_group_alive(group_target)
        return False

    def _signal(sig: int) -> None:
        try:
            os.kill(target, sig)
        except ProcessLookupError:
            pass
        except OSError:
            pass

    _signal(signal.SIGTERM)
    deadline = time.monotonic() + STOP_GRACE_S
    while time.monotonic() < deadline:
        if not _tree_alive():
            break
        time.sleep(0.05)

    if _tree_alive():
        _signal(signal.SIGKILL)
        kill_deadline = time.monotonic() + STOP_KILL_S
        while time.monotonic() < kill_deadline:
            if not _tree_alive():
                break
            time.sleep(0.05)

    if _tree_alive():
        raise RuntimeError(f"could not terminate campaign process {pid}")

    current = store.get_discovery_run(run["run_id"])
    if current and current["status"] in _ACTIVE_STATUSES:
        store.finish_discovery_run(run["run_id"], status="cancelled", error="cancelled by user")
    return store.get_discovery_run(run["run_id"])


def _start_parent_watchdog() -> None:
    """Stop the campaign if its launching parent disappears.

    A campaign launched *detached* by the desktop GUI must never outlive Argus.
    If the parent (the Rust shell) dies for any reason — normal close, system
    shutdown, even a force-quit that skips ``ExitRequested`` — the campaign is
    reparented to the init process; the watchdog then asks the campaign to stop
    itself (SIGTERM → the campaign records ``cancelled``). Daemon thread, only
    active while this process lives.
    """
    if not _launched_detached():
        return

    def _watch() -> None:
        try:
            while os.getppid() != 1:
                time.sleep(0.5)
        except Exception:
            return
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass

    threading.Thread(target=_watch, daemon=True, name="argus-parent-watchdog").start()


def _run_discovery_campaign(
    store_path: Path,
    raw_root: Path,
    banks: tuple[str, ...],
    date_start: datetime | None = None,
    date_end: datetime | None = None,
    run_id: str | None = None,
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

    A campaign launched *detached* by the desktop GUI must never outlive Argus:
    launcher-liveness guards run before recording (refuse to start an orphan),
    right after recording, and for the whole run via a parent watchdog (a
    launcher that dies — even by force-quit — asks the campaign to stop itself).
    """
    signal.signal(signal.SIGTERM, _raise_discovery_stopped)
    detached = _launched_detached()
    if detached and os.getppid() == 1:
        # The desktop app that spawned this campaign is already gone (reparented
        # to the init process). Never become an orphan doing work after Argus
        # closed — refuse to start.
        return {
            "run_id": None,
            "status": "failed",
            "error": "launcher exited during campaign startup",
            "candidates": 0,
        }
    store = Store(store_path)
    registry = SourceRegistry()
    run_id = run_id or make_run_stamp()
    bank_names = {b.id: b.name for b in registry.banks}
    # The number of sources the campaign will discover, fixed at launch so the
    # GUI reads 0 / N immediately; the Core advances `sources_completed` via the
    # same registry-driven selection (the exact set `discover_all` schedules).
    sources_total = len(registry.enabled_sources(banks=tuple(banks) if banks else None))
    store.start_discovery_run(
        run_id,
        banks,
        pid=os.getpid(),
        date_start=iso(date_start) if date_start else None,
        date_end=iso(date_end) if date_end else None,
        sources_total=sources_total,
    )
    if detached and os.getppid() == 1:
        # The launcher vanished in the instant between the first check and the
        # run being recorded — close the window by finalizing immediately.
        store.finish_discovery_run(
            run_id,
            status="cancelled",
            error="launcher exited during campaign startup",
        )
        return {
            "run_id": run_id,
            "status": "cancelled",
            "error": "launcher exited during campaign startup",
            "candidates": 0,
        }
    _start_parent_watchdog()
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
        store.finish_discovery_run(run_id, status="cancelled", error="cancelled by user")
        return {"run_id": run_id, "status": "cancelled", "error": "cancelled by user", "candidates": 0}
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
    ``--start-date`` / ``--end-date`` (ISO dates) are **required**: a campaign
    may only be launched with a complete, ordered window
    (``start_date <= end_date``). The window is applied by the Core and
    persisted with the run. Exactly one campaign may be active; the Store
    claims the run in a locked transaction, so a concurrent or second launch
    (even racing) is refused here too.

    ``--run-id <id>`` (optional) lets the desktop launcher pre-mint the run's
    identity (via ``discovery-run-id``) so it can be returned to the frontend
    *before* the detached subprocess records it — the campaign then starts
    under that exact id instead of minting its own.
    """
    banks = enabled_banks()
    selected: list[str] = []
    date_start = None
    date_end = None
    run_id = None
    index = 0
    while index < len(argv):
        if argv[index] == "--bank" and index + 1 < len(argv):
            selected.append(argv[index + 1])
            index += 2
        elif argv[index] == "--run-id" and index + 1 < len(argv):
            run_id = argv[index + 1].strip() or None
            index += 2
        elif argv[index] == "--start-date" and index + 1 < len(argv):
            value = argv[index + 1].strip()
            if value:
                try:
                    date_start = _parse_date_arg(value, "start-date")
                except ValueError as exc:
                    print(str(exc), file=sys.stderr)
                    return 2
            index += 2
        elif argv[index] == "--end-date" and index + 1 < len(argv):
            value = argv[index + 1].strip()
            if value:
                try:
                    date_end = _parse_date_arg(value, "end-date")
                except ValueError as exc:
                    print(str(exc), file=sys.stderr)
                    return 2
            index += 2
        else:
            print(f"unexpected argument: {argv[index]}", file=sys.stderr)
            return 2
    if date_start is None or date_end is None:
        print(
            json.dumps(
                {"error": "Discovery requires both start_date and end_date."},
                indent=2,
            )
        )
        return 1
    if date_start > date_end:
        print(
            json.dumps(
                {"error": "start_date must be <= end_date"},
                indent=2,
            )
        )
        return 1
    if selected:
        from .config import filter_enabled

        banks = filter_enabled(selected) or ()

    store = Store(_store_path())
    # Reconcile a stale "active" record (dead PID) before deciding whether a
    # new campaign may start.
    _reconcile_run(store, store.latest_discovery_run())
    active = store.latest_discovery_run()
    if active is not None and active["status"] in _ACTIVE_STATUSES:
        print(
            json.dumps(
                {"error": f"a discovery campaign is already active: {active['run_id']}"},
                indent=2,
            )
        )
        return 1

    try:
        result = _run_discovery_campaign(
            _store_path(), _raw_root(), banks, date_start, date_end, run_id=run_id
        )
    except ActiveDiscoveryError as exc:
        # Lost the claim race — another campaign started first. This is the
        # backend's single-active invariant, never a client-side guess.
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0


def _cmd_discovery_run_id(argv: list[str]) -> int:
    """Mint a fresh discovery-run identifier (no store access, no side effect).

    The desktop launcher calls this synchronously before spawning the detached
    campaign, so ``run_discovery`` can return the run's identity immediately —
    the frontend then waits for *that* id to appear instead of racing with the
    previous terminal run.
    """
    print(json.dumps({"run_id": make_run_stamp()}, indent=2))
    return 0


def _cmd_discovery_control(argv: list[str]) -> int:
    """Real lifecycle control of a campaign, targeting its ``run_id``.

    ``discovery-control <action> [run_id]`` — the ``run_id`` is that of the
    campaign actually controlled (its PID is read from the Store, never
    invented). Without a ``run_id`` the most recent campaign is used (legacy
    convenience; the GUI always passes the explicit id).

    The status is flipped in the Store only after the signal demonstrably took
    effect:
    - ``pause``  → SIGSTOP, then ``paused``;
    - ``resume`` → SIGCONT, then ``running``;
    - ``stop``   → SIGTERM with a real termination wait and SIGKILL escalation
      (see :func:`_terminate_campaign`) — the Store says ``cancelled`` only once
      the process is verified gone.
    A dead PID is never hidden: the campaign is reconciled and the command
    errors out.
    """
    if len(argv) < 1:
        print(USAGE, file=sys.stderr)
        return 2
    action = argv[0].strip().lower()
    if action not in ("pause", "resume", "stop"):
        print(f"unknown discovery-control action: {action} (pause|resume|stop)", file=sys.stderr)
        return 2
    run_id = argv[1] if len(argv) > 1 else None
    store = Store(_store_path())
    run = store.get_discovery_run(run_id) if run_id else store.latest_discovery_run()
    if run is None or run["status"] in ("idle", *_TERMINAL_STATUSES):
        print(json.dumps({"error": f"no active campaign to {action}"}, indent=2))
        return 1
    pid = run.get("pid")
    if not pid:
        print(
            json.dumps({"error": f"campaign {run['run_id']} has no recorded pid (started outside the desktop GUI)"}, indent=2)
        )
        return 1
    try:
        if action == "stop":
            run = _terminate_campaign(store, run, pid)
        else:
            identity = _process_identity(pid)
            if identity == "missing":
                _reconcile_run(store, run)
                print(json.dumps({"error": f"campaign process {pid} is no longer running"}, indent=2))
                return 1
            if identity == "foreign":
                store.finish_discovery_run(
                    run["run_id"],
                    status="failed",
                    error=f"recorded pid {pid} is no longer the discovery campaign",
                )
                print(
                    json.dumps(
                        {"error": f"recorded pid {pid} is no longer the discovery campaign; not {action}ing"}
                    )
                )
                return 1
            os.kill(pid, signal.SIGSTOP if action == "pause" else signal.SIGCONT)
            # The signal is only taken as applied once the process is still
            # demonstrably around (and, for pause, has transitioned under the
            # signal's effect, seen as a T state by `ps`).
            if not _process_alive(pid):
                _reconcile_run(store, run)
                print(json.dumps({"error": f"campaign process {pid} vanished during {action}"}, indent=2))
                return 1
            store.set_discovery_run_control(run["run_id"], "paused" if action == "pause" else "running")
            run = store.get_discovery_run(run["run_id"])
    except ProcessLookupError:
        _reconcile_run(store, run)
        print(json.dumps({"error": f"campaign process {pid} is no longer running"}, indent=2))
        return 1
    except RuntimeError as exc:
        # Stop could not guarantee the process is gone — the Store must NOT
        # claim `cancelled` while the process lives.
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1
    except OSError as exc:
        print(json.dumps({"error": f"cannot {action} campaign process {pid}: {exc}"}, indent=2))
        return 1
    print(json.dumps(run, indent=2))
    return 0


def _cmd_discovery_clear(argv: list[str]) -> int:
    """Drop the discovery candidate cache (never during an active campaign).

    Refused while a campaign is ``running`` or ``paused`` (after reconciling a
    stale dead-PID active record). Clears the candidate snapshots only; the
    run/ campaign history is preserved (see ``Store.clear_discovery_cache``).
    """
    store = Store(_store_path())
    _reconcile_run(store, store.latest_discovery_run())
    active = store.latest_discovery_run()
    if active is not None and active["status"] in _ACTIVE_STATUSES:
        print(
            json.dumps({"error": "cannot clear the discovery cache while a campaign is active"}, indent=2)
        )
        return 1
    runs, candidates = store.clear_discovery_cache()
    print(json.dumps({"runs_preserved": runs, "candidates_cleared": candidates}, indent=2))
    return 0


def _cmd_discovery_status(argv: list[str]) -> int:
    """JSON summary of the most recent discovery campaign (or ``idle``).

    Runs the dead-PID reconciliation first, so an active-looking campaign whose
    process is gone is surfaced as ``failed`` rather than left ``running``
    forever (the GUI polls this command).
    """
    store = Store(_store_path())
    run = _reconcile_run(store, store.latest_discovery_run())
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
                    "pid": None,
                    "date_start": None,
                    "date_end": None,
                    "sources_total": 0,
                    "sources_completed": 0,
                    "new": 0,
                    "known": 0,
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
    if command == "discovery-run-id":
        return _cmd_discovery_run_id(argv[1:])
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
