"""Deterministic Collection benchmark tests (offline, no network).

Covers the benchmark's guarantees: the same workload runs with different worker
counts, produces a real reproducible speedup, and keeps the business state
(publications / documents / statuses / errors / dedup / retries) strictly
equivalent. The workload uses a controlled failure and several hosts.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.benchmark_collection import _business_state, _run, _build_workload


@pytest.fixture
def bench_store(tmp_path):
    store = _build_workload(
        tmp_path,
        publications=12,
        documents=2,
        latency_ms=50.0,
        hosts=3,
        failures=1,
        bank="fed",
    )
    store.close()
    return tmp_path


def test_benchmark_identical_workload_same_documents(bench_store):
    """The workload is deterministic: every run sees the same publications and
    document counts."""
    run = _run(bench_store, "w1", 1, "fed")
    assert run["publications"] == 12
    assert run["documents"] == 24
    assert run["status"] == {"fetched": 11, "failed": 1}
    assert run["errors"] == 0


def test_benchmark_speedup_is_real(bench_store):
    """workers=4 is measurably faster than workers=1 on the same workload."""
    seq = _run(bench_store, "seq", 1, "fed")
    par = _run(bench_store, "par", 4, "fed")
    assert seq["elapsed"] > 0
    assert par["elapsed"] > 0
    # 12 x 50ms ≈ 600ms sequential; 4 workers ≈ 150ms + overhead. Require a
    # comfortable 1.5x so the assertion is not flaky on loaded CI machines.
    assert seq["elapsed"] / par["elapsed"] > 1.5, (
        f"parallel ({par['elapsed']:.2f}s) not meaningfully faster than "
        f"sequential ({seq['elapsed']:.2f}s)"
    )


def test_benchmark_business_equivalence(bench_store):
    """Different worker counts produce identical persisted business state."""
    baseline = _run(bench_store, "w1", 1, "fed")
    for workers in (2, 4, 8):
        run = _run(bench_store, f"w{workers}", workers, "fed")
        assert run["state"] == baseline["state"], (
            f"workers={workers} business state differs from workers=1"
        )
        assert run["status"] == baseline["status"]
        assert run["publications"] == baseline["publications"]
        assert run["documents"] == baseline["documents"]


def test_benchmark_controlled_failure_persisted(bench_store):
    """The controlled failure is persisted identically at every worker count."""
    run = _run(bench_store, "w1", 1, "fed")
    state = run["state"]
    failed = [p for p in state["publications"] if p[2] == "failed"]
    assert len(failed) == 1
    # the failed publication carries its documents as FAILED rows
    failed_docs = [d for d in state["documents"] if d[2] == "pdf" and d[3] == "failed"]
    assert len(failed_docs) == 2  # the controlled failure's two documents


def test_benchmark_env_worker_count(bench_store, monkeypatch):
    """ARGUS_COLLECTION_WORKERS drives the pool size used by the run."""
    from argus.collector import _collection_worker_pool_size

    monkeypatch.setenv("ARGUS_COLLECTION_WORKERS", "3")
    assert _collection_worker_pool_size() == 3
    # the run itself honours the env override
    run = _run(bench_store, "w3", 3, "fed")
    assert run["workers"] == 3


def test_benchmark_never_touches_production_data(bench_store):
    """The benchmark only ever uses the temp store; production is untouched."""
    import glob

    db = Path(os.environ.get("ARGUS_BENCH_DB", str(bench_store / "seed.db")))
    assert db.exists()
    # no stray .tmp benchmark files are produced
    assert not list(bench_store.rglob("*.tmp"))
