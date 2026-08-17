#!/usr/bin/env python3
"""Deterministic, offline Collection benchmark (no network, CI-friendly).

Builds a controlled synthetic workload (publications + documents spread over
several hosts, with artificial per-publication latency and one controlled
failure) in a temporary store, then runs the SAME workload with workers=1..N
and reports elapsed time, speedup, and persisted business state.

The workload is identical across worker counts; only the concurrency changes,
so the speedup is real and reproducible. The script also asserts *business
equivalence* across worker counts: same publications, documents, statuses,
errors, dedup keys and retries.

The production ``data/argus.db`` is never touched.

Usage:
    python scripts/benchmark_collection.py [--workers 1,4,8] [--latency-ms 100] [--publications 24]
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
import os
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from argus.collector import CentralBankCollector, _collection_worker_pool_size
from argus.http import HttpConfig
from argus.models import Document, DocumentStatus, FetchResult, Publication, PublicationStatus
from argus.store import Store


def _pub(index: int, *, bank: str, host: str, documents: int,
         latency_ms: float, fail: bool) -> Publication:
    pub = Publication(
        central_bank=bank,
        title=f"Statement {index}",
        url=f"https://{host}/pubs/{index}.htm",
        document_urls=tuple(f"https://{host}/pubs/{index}/doc{j}.pdf" for j in range(documents)),
        source_id="src",
        source_url=f"https://{host}/feed.xml",
        publication_date=datetime(2026, 7, (index % 28) + 1, tzinfo=timezone.utc),
        status=PublicationStatus.DISCOVERED,
    )
    # deterministic, injectable through the extra dict so the fake fetcher can
    # reproduce the exact workload
    pub.extra = {
        "bench_latency_ms": latency_ms,
        "bench_fail": fail,
        "bench_documents": documents,
    }
    return pub


class _BenchmarkFetcher:
    """Deterministic fetcher: sleeps `latency_ms` per publication, returns one
    document per URL (or a FAILED document when the publication is the
    controlled failure). No network, no Store access in collect()."""

    def __init__(self, store):
        self.store = store

    def collect(self, publication, existing_by_url, *, force=False):
        latency_ms = float(publication.extra.get("bench_latency_ms", 0.0))
        time.sleep(latency_ms / 1000.0)
        fail = bool(publication.extra.get("bench_fail", False))
        pub_id = publication.id or ""
        targets = list(publication.document_urls) or ([publication.url] if publication.url else [])
        documents = []
        for url in targets:
            if fail:
                documents.append(Document(publication_id=pub_id, url=url, kind="pdf",
                                          status=DocumentStatus.FAILED, error="benchmark failure",
                                          retries=1))
            else:
                documents.append(Document(publication_id=pub_id, url=url, kind="pdf",
                                          status=DocumentStatus.FETCHED, sha256="x" * 64, size=1))
        ok = bool(documents) and not fail
        status = PublicationStatus.FAILED if fail else PublicationStatus.FETCHED
        return (FetchResult(publication_id=pub_id, documents=documents, ok=ok,
                            failed_urls=[url for d in documents if d.status == DocumentStatus.FAILED]),
                status)

    def persist(self, publication_id, documents, status):
        for document in documents:
            self.store.upsert_document(document)
        if status is not None:
            self.store.set_publication_status(publication_id, status)


def _build_workload(tmp: Path, *, publications: int, documents: int,
                    latency_ms: float, hosts: int, failures: int, bank: str) -> Store:
    store = Store(tmp / "seed.db")
    for i in range(publications):
        host = f"bank-{(i % hosts)}.example"
        fail = i < failures
        store.upsert_publication(_pub(i, bank=bank, host=host, documents=documents,
                                       latency_ms=latency_ms, fail=fail))
    return store


def _business_state(store, bank) -> dict:
    """A sortable, comparable snapshot of the persisted business state."""
    pubs = sorted(
        (p.id, p.title, p.status.value, p.url, p.dedup_key or "") for p in store.list_publications(bank=bank)
    )
    docs = []
    for p in store.list_publications(bank=bank):
        for d in store.list_documents(p.id):
            docs.append((p.id, d.url, d.kind, d.status.value, d.retries))
    docs = sorted(docs)
    errors = sorted((e.source_id, e.error_type, e.message) for e in store.list_errors())
    return {"publications": pubs, "documents": docs, "errors": errors}


def _run(tmp: Path, run_name: str, workers: int, bank: str) -> dict:
    """Run the workload with a given worker count on a fresh store copy."""
    import shutil

    seed = tmp / "seed.db"
    db = tmp / f"{run_name}.db"
    shutil.copy2(seed, db)
    for suffix in ("-wal", "-shm"):
        src = str(seed) + suffix
        if Path(src).exists():
            shutil.copy2(src, str(db) + suffix)

    old = os.environ.get("ARGUS_COLLECTION_WORKERS")
    os.environ["ARGUS_COLLECTION_WORKERS"] = str(workers)
    try:
        store = Store(db)
        fetcher = _BenchmarkFetcher(store)
        collector = CentralBankCollector(
            store=store,
            http_config=HttpConfig(respect_robots=False, min_interval=0.0),
            raw_root=tmp / f"raw-{run_name}",
            fetcher=fetcher,
        )
        start = time.monotonic()
        results = collector.collect_campaign(banks=(bank,))
        elapsed = time.monotonic() - start

        state = _business_state(store, bank)
        statuses: dict[str, int] = {}
        for pub in store.list_publications(bank=bank):
            statuses[pub.status.value] = statuses.get(pub.status.value, 0) + 1
        store.close()
        return {
            "workers": workers,
            "elapsed": elapsed,
            "publications": len(state["publications"]),
            "documents": len(state["documents"]),
            "status": statuses,
            "errors": len(state["errors"]),
            "results": len(results),
            "state": state,
        }
    finally:
        if old is None:
            os.environ.pop("ARGUS_COLLECTION_WORKERS", None)
        else:
            os.environ["ARGUS_COLLECTION_WORKERS"] = old


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publications", type=int, default=24, help="publications in the workload")
    parser.add_argument("--documents", type=int, default=2, help="documents per publication")
    parser.add_argument("--latency-ms", type=float, default=100.0, help="artificial latency per publication (ms)")
    parser.add_argument("--hosts", type=int, default=3, help="distinct hosts to spread the workload across")
    parser.add_argument("--failures", type=int, default=1, help="number of publications that fail (controlled)")
    parser.add_argument("--bank", default="fed", help="bank id for the synthetic workload")
    parser.add_argument("--workers", default="1,4,8", help="comma-separated worker counts to benchmark")
    args = parser.parse_args()

    workers = [int(w.strip()) for w in args.workers.split(",") if w.strip()]
    assert args.failures <= args.publications

    tmp = Path(tempfile.mkdtemp(prefix="argus-bench-"))
    try:
        store = _build_workload(
            tmp, publications=args.publications, documents=args.documents,
            latency_ms=args.latency_ms, hosts=args.hosts, failures=args.failures,
            bank=args.bank,
        )
        store.close()

        print("Collection Parallel Benchmark")
        print()
        print("Workload:")
        print(f"  publications: {args.publications}")
        print(f"  documents:    {args.publications * args.documents}")
        print(f"  hosts:        {args.hosts}")
        print(f"  artificial latency: {args.latency_ms}ms/publication")
        print(f"  failures:     {args.failures}")
        print()

        runs = [_run(tmp, f"w{w}", w, args.bank) for w in workers]
        baseline = runs[0]  # the first listed worker count is the baseline

        print(f"{'workers':<8} {'elapsed':<10} {'speedup':<10} {'pubs':<6} {'docs':<6} {'errors':<6} {'status'}")
        for run in runs:
            speedup = baseline["elapsed"] / run["elapsed"] if run["elapsed"] > 0 else float("inf")
            print(f"{run['workers']:<8} {run['elapsed']:<10.2f} {speedup:<8.2f}x "
                  f"{run['publications']:<6} {run['documents']:<6} {run['errors']:<6} {run['status']}")

        # Business equivalence across worker counts: identical state, and a
        # meaningful speedup over the baseline.
        for run in runs[1:]:
            assert run["state"] == baseline["state"], (
                f"workers={run['workers']} business state differs from baseline"
            )
            assert run["status"] == baseline["status"]
        print("\nBusiness equivalence: identical publications/documents/statuses/errors/dedup/retries "
              "across all worker counts")

        if len(runs) > 1:
            best = min(runs[1:], key=lambda r: r["elapsed"])
            speedup = baseline["elapsed"] / best["elapsed"] if best["elapsed"] > 0 else float("inf")
            if speedup < 1.5:
                print(f"\nWARNING: speedup only {speedup:.2f}x — the workload/latency may be too small "
                      "to demonstrate parallelism.")
        return 0
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
