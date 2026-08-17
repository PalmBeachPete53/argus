#!/usr/bin/env python3
"""Benchmark: sequential vs parallel Collection on real sources with a temp store.

Runs a real Discovery pass over the enabled banks (bounded window) to populate
a temporary store, then runs Collection twice on *separate copies* of that store
(workers=1 vs workers=N) and reports the wall-clock and the persisted outcome.

The production ``data/argus.db`` is never touched: everything lives under a
temporary directory.

Usage:
    python scripts/benchmark_collection.py [--banks fed,ecb,boe] [--start 2026-07-01] [--end 2026-08-01]
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from argus.collector import COLLECTION_WORKERS, CentralBankCollector
from argus.http import HttpConfig
from argus.store import Store


def _bank_list(values: str | None) -> tuple[str, ...] | None:
    if not values:
        return None
    return tuple(b.strip() for b in values.split(",") if b.strip())


def _discover(tmp: Path, banks, date_start, date_end) -> int:
    store = Store(tmp / "seed.db")
    collector = CentralBankCollector(
        store=store,
        http_config=HttpConfig(respect_robots=True, min_interval=0.2),
        raw_root=tmp / "raw-seed",
    )
    start = time.monotonic()
    pubs = collector.discover_all(
        banks=banks, date_start=date_start, date_end=date_end
    )
    elapsed = time.monotonic() - start
    print(f"  discovery: {len(pubs)} publications in {elapsed:.2f}s")
    store.close()
    return len(pubs)


def _run_collection(tmp: Path, db_name: str, workers: int, banks, date_start, date_end,
                    min_interval: float) -> dict:
    """Copy the seeded store (and raw tree) and collect it with a given pool."""
    import os

    seed_db = tmp / "seed.db"
    run_dir = tmp / db_name
    run_dir.mkdir(exist_ok=True)
    db = run_dir / "argus.db"
    shutil.copy2(seed_db, db)
    if os.path.exists(str(seed_db) + "-wal"):
        shutil.copy2(str(seed_db) + "-wal", str(db) + "-wal")
    if os.path.exists(str(seed_db) + "-shm"):
        shutil.copy2(str(seed_db) + "-shm", str(db) + "-shm")
    raw = run_dir / "raw"
    raw.mkdir(exist_ok=True)

    store = Store(db)
    collector = CentralBankCollector(
        store=store,
        http_config=HttpConfig(respect_robots=True, min_interval=min_interval),
        raw_root=raw,
    )
    start = time.monotonic()
    results = collector.collect_campaign(
        banks=banks, date_start=date_start, date_end=date_end,
    )
    elapsed = time.monotonic() - start

    pubs = store.list_publications(bank=banks)
    by_status: dict[str, int] = {}
    docs = 0
    for pub in pubs:
        by_status[pub.status.value] = by_status.get(pub.status.value, 0) + 1
        docs += store.document_count(pub.id)
    fetched = sum(1 for r in results if r.ok)
    failed = sum(1 for r in results if not r.ok)
    errors = len(store.list_errors())
    store.close()

    return {
        "workers": workers,
        "elapsed": elapsed,
        "publications": len(pubs),
        "documents": docs,
        "status": by_status,
        "fetched": fetched,
        "failed": failed,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--banks", help="comma-separated bank ids (default: all enabled)")
    parser.add_argument("--start", default="2026-06-01", help="publication date window start")
    parser.add_argument("--end", default="2026-08-01", help="publication date window end (exclusive)")
    parser.add_argument("--workers", type=int, default=COLLECTION_WORKERS,
                        help="worker count for the parallel leg (default: %(default)s)")
    parser.add_argument("--min-interval", type=float, default=0.2,
                        help="per-host request interval in seconds. NOTE: this is the "
                             "dominant serializing factor when many documents share a host "
                             "(central banks serve most pages from one host); use 0.0 to "
                             "observe the raw pool speedup, or a polite value for the "
                             "realistic politeness-constrained gain.")
    args = parser.parse_args()

    banks = _bank_list(args.banks)
    date_start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    date_end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)

    tmp = Path(tempfile.mkdtemp(prefix="argus-bench-"))
    try:
        print("seeding store via real discovery…")
        n = _discover(tmp, banks, date_start, date_end)
        if n == 0:
            print("no publications discovered in the window — nothing to benchmark")
            return 1

        seq = _run_collection(tmp, "seq", 1, banks, date_start, date_end, args.min_interval)
        par = _run_collection(tmp, "par", args.workers, banks, date_start, date_end, args.min_interval)

        print("\n=== Collection benchmark ===")
        for row in (seq, par):
            print(
                f"workers={row['workers']}: {row['elapsed']:.2f}s "
                f"| {row['publications']} pubs | {row['documents']} docs "
                f"| {row['status']} | ok={row['fetched']} failed={row['failed']} "
                f"errors={row['errors']}"
            )
        speedup = seq["elapsed"] / par["elapsed"] if par["elapsed"] > 0 else float("inf")
        print(f"\nspeedup: {speedup:.2f}x")
        # coverage must not be lost by parallelism
        assert seq["publications"] == par["publications"]
        assert seq["documents"] == par["documents"]
        assert seq["status"] == par["status"]
        assert seq["errors"] == par["errors"]
        print("coverage: sequential == parallel (publications/documents/status/errors)")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
