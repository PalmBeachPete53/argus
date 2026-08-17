#!/usr/bin/env python3
"""Real-engine smoke test of the Collection GUI chain (bridge-level).

Drives the exact command sequence the desktop GUI runs against a *disposable*
temp store (never `data/argus.db`):

    run_collection (mint + spawn detached)
        -> collection-status (poll the followed run)
        -> collection-control stop <run_id>
        -> cancelled + partial progression
        -> relaunch -> completed

Uses the real `CentralBankCollector` (real HTTP fetches, rate limiting,
validation) against a local HTTP server, so progression genuinely advances in
real time as the parallel workers finish.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
PY = sys.executable
PORT = 18765
N = 6

OUT = []


def log(msg: str) -> None:
    OUT.append(msg)
    print(msg, flush=True)


def _http_server():
    import functools
    import http.server

    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):  # noqa: D401
            pass

    handler = functools.partial(QuietHandler, directory=str(SRVDIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log(f"[server] local HTTP on 127.0.0.1:{PORT}")


def bridge(*args: str, detached: bool = False) -> dict:
    env = dict(
        os.environ,
        PYTHONPATH=str(REPO / "src"),
        ARGUS_ROOT=TMP,
    )
    if detached:
        env["ARGUS_COLLECTION_DETACHED"] = "1"
        subprocess.Popen(
            [PY, "-m", "argus.gui_bridge", *args],
            env=env,
            cwd=REPO,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {}
    p = subprocess.run(
        [PY, "-m", "argus.gui_bridge", *args],
        env=env, cwd=REPO, capture_output=True, text=True, timeout=120,
    )
    if p.returncode != 0:
        raise SystemExit(f"bridge {args} failed ({p.returncode}): {p.stderr} {p.stdout}")
    return json.loads(p.stdout)


def seed_publications() -> None:
    sys.path.insert(0, str(REPO / "src"))
    from argus.models import Publication, PublicationStatus
    from argus.store import Store

    store = Store(DATA / "argus.db")
    for i in range(N):
        store.upsert_publication(
            Publication(
                id=f"smoke-{i}",
                central_bank="fed",
                title=f"Smoke document {i}",
                url=f"http://127.0.0.1:{PORT}/doc{i}.html",
                source_id="fed_rss",
                source_url=f"http://127.0.0.1:{PORT}/feed.xml",
                status=PublicationStatus.DISCOVERED,
                publication_date=None,
            )
        )
    store.close()
    log(f"[setup] seeded {N} DISCOVERED publications")


def poll_until(run_id: str, terminal_statuses: set[str], *, max_s: float = 90.0):
    """Poll collection-status until the run reaches a terminal status."""
    deadline = time.monotonic() + max_s
    last = None
    while time.monotonic() < deadline:
        status = bridge("collection-status")
        if status.get("run_id") == run_id:
            last = status
            if status["status"] in terminal_statuses:
                return status
        time.sleep(0.4)
    raise SystemExit(f"timed out waiting for run {run_id}; last={last}")


def main() -> int:
    log("=== Argus Collection GUI smoke test (real engine, temp store) ===")
    log(f"[store] {DATA}")

    seed_publications()

    idle = bridge("collection-status")
    assert idle["status"] == "idle", idle
    log("[1] collection-status is idle ✓")

    # --- first campaign: run then cancel midway ---
    run_id = bridge("collection-run-id")["run_id"]
    log(f"[2] minted run-id {run_id}")
    bridge("collection-run", "--run-id", run_id, detached=True)
    log("[3] detached collection-run launched")

    # Watch the followed run appear and progress live (0/N → …), then cancel.
    deadline = time.monotonic() + 60
    appeared = None
    progressed = None
    samples = []
    while time.monotonic() < deadline:
        st = bridge("collection-status")
        if st.get("run_id") == run_id:
            appeared = st
            samples.append((st["publications_completed"], st["publications_total"]))
            if st["status"] == "running" and st["publications_completed"] >= 2:
                progressed = st
                break
        time.sleep(0.4)
    assert appeared is not None, "the launched run never appeared in collection-status"
    assert progressed, "campaign never showed live progression"
    log(f"[4] run appeared ({appeared['status']}) and progression advanced live: "
        f"{progressed['publications_completed']}/{progressed['publications_total']} · samples {samples}")

    time.sleep(0.5)
    cancelled = bridge("collection-control", "stop", run_id)
    assert cancelled["run_id"] == run_id, cancelled
    assert cancelled["status"] == "cancelled", cancelled
    assert cancelled["publications_completed"] < cancelled["publications_total"], cancelled
    log(f"[5] stop -> cancelled with partial progression "
        f"{cancelled['publications_completed']}/{cancelled['publications_total']} ✓")

    after = bridge("collection-status")
    assert after["status"] == "cancelled", after
    assert after["publications_completed"] == cancelled["publications_completed"]
    log("[6] status stays cancelled (no resurrection), partial kept ✓")

    # --- relaunch: must reach completed ---
    run2 = bridge("collection-run-id")["run_id"]
    log(f"[7] relaunch minted run-id {run2}")
    bridge("collection-run", "--run-id", run2, detached=True)

    done = poll_until(run2, {"completed"})
    assert done["status"] == "completed", done
    assert done["publications_completed"] == done["publications_total"], done
    # The Core's self-repair plan is re-scoped per campaign: the two publications
    # already fetched in campaign 1 need no re-collection, so the relaunch covers
    # the remaining DISCOVERED ones only (never a fabricated N/N).
    assert done["publications_total"] == N - 2, done
    log(f"[8] relaunch completed {done['publications_completed']}/{done['publications_total']} "
        f"(self-repair skipped the 2 already fetched) ✓")

    # Confirm real documents were fetched and atomically written (raw files).
    sys.path.insert(0, str(REPO / "src"))
    from argus.store import Store

    store = Store(DATA / "argus.db")
    fetched = int(
        store._conn.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE status='fetched'"
        ).fetchone()["n"]
    )
    raw_files = 0
    raw_root = DATA / "raw"
    if raw_root.is_dir():
        raw_files = len([p for p in raw_root.rglob("*") if p.is_file()])
    store.close()
    assert fetched >= N, f"expected >= {N} fetched documents, got {fetched}"
    assert raw_files >= N, f"expected >= {N} raw files, got {raw_files}"
    log(f"[9] store holds {fetched} fetched documents · {raw_files} raw files written ✓")

    log("=== SMOKE PASS ===")
    return 0


if __name__ == "__main__":
    TMP = tempfile.mkdtemp(prefix="argus-smoke-")
    DATA = pathlib.Path(TMP) / "data"
    SRVDIR = pathlib.Path(TMP) / "serve"
    DATA.mkdir()
    SRVDIR.mkdir()
    (SRVDIR / "robots.txt").write_text("User-agent: *\nAllow: /\n")
    for i in range(N):
        (SRVDIR / f"doc{i}.html").write_text(
            f"<html><head><title>Smoke {i}</title></head><body><p>Not a real central bank document {i}.</p></body></html>"
        )
    _http_server()
    time.sleep(0.5)
    try:
        raise SystemExit(main())
    finally:
        log(f"[cleanup] temp store left at {TMP}")
