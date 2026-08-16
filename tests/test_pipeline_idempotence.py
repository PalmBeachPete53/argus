"""Phase D — end-to-end idempotence of the L4 pipeline.

Two complete and successive executions of the full L4 pipeline on the same
publication must produce exactly the same persisted business identity with
zero duplication:

- run #1 (discovery -> publication -> classification -> fetch -> normalization
  -> gated dispatch -> extraction -> persistence) -> snapshot;
- run #2 (same inputs, same Store) -> snapshot;
- the two snapshots are strictly identical and no new publication / document /
  normalized document / classification / fact row was created.

The second run is a *complete* pipeline re-execution (``run_l4_end_to_end_twice``
reuses the exact single pipeline path ``_pipeline_once`` of the Phase B/C
harness), not a mere re-invocation of the extractor.

A dedicated BoC MPR test demonstrates the flow explicitly; the parametrized test
extends it to the 10 slices of Phase C.
"""

from __future__ import annotations

import pytest

from argus.adapters.boc import BoCAdapter
from argus.reports import extract_report
from argus.reports.boc import BocReportExtractor
from l4_harness import run_l4_end_to_end_twice
from test_pipeline_end_to_end import CASES, BOC_MPR_URL, require_enabled

_BOC = dict(
    adapter=BoCAdapter(),
    source_id="boc_mpr_feed",
    discovery_fixture="boc_mpr_feed.xml",
    document_fixture="documents/boc_report.html",
    target_url=BOC_MPR_URL,
    expected_type="monetary_policy_report",
    extract=extract_report,
    expected_extractor=BocReportExtractor,
    qualifier_prefix="report:",
    expected_facts={
        ("inflation", "value", "percentage", 2.1, "year:2026"),
        ("gdp", "value", "percentage", 1.8, "year:2027"),
        ("unemployment", "value", "percentage", 5.8, "year:2026"),
        ("inflation_risk", "assessment", "categorical", "balanced", None),
    },
)


def test_boc_mpr_end_to_end_idempotent(tmp_path, fixture_bytes):
    """Explicit BoC MPR demonstration: two full runs, strict snapshot equality."""
    store, run1, run2, snapshot1, snapshot2 = run_l4_end_to_end_twice(
        **_BOC, fixture_bytes=fixture_bytes, tmp_path=tmp_path,
    )
    stored1, result1, facts1 = run1
    stored2, result2, facts2 = run2

    # no new objects: counts are strictly identical across the two runs
    assert len(snapshot1["publications"]) == len(snapshot2["publications"]) == 1
    assert len(snapshot1["documents"]) == len(snapshot2["documents"])
    assert len(snapshot1["normalized_documents"]) == len(snapshot2["normalized_documents"])
    assert len(snapshot1["facts"]) == len(snapshot2["facts"])
    assert len(snapshot1["classifications"]) == len(snapshot2["classifications"])

    # identical persisted business identity after the second complete run
    assert snapshot1 == snapshot2

    # same publication and document identities
    assert stored2.id == stored1.id
    assert {d["id"] for d in snapshot1["documents"]} == {d["id"] for d in snapshot2["documents"]}
    assert {n["document_id"] for n in snapshot1["normalized_documents"]} == {
        n["document_id"] for n in snapshot2["normalized_documents"]
    }

    # same Fact identities, no duplicates, same relations
    assert set(f.resolve_id() for f in facts1) == set(f.resolve_id() for f in facts2)
    assert len({f.resolve_id() for f in facts2}) == len(facts2)
    assert set(f.resolve_id() for f in result1.facts) == set(f.resolve_id() for f in facts1)
    assert all(f.publication_id == stored1.id for f in facts2)
    assert all(f.document_id in {n["document_id"] for n in snapshot1["normalized_documents"]} for f in facts2)

    # the extraction stage is idempotent too (result of run #2 == run #1)
    assert set(f.resolve_id() for f in result2.facts) == set(f.resolve_id() for f in result1.facts)
    assert len(store.get_facts(publication_id=stored1.id)) == len(facts1)


@pytest.mark.parametrize("case", CASES)
def test_l4_end_to_end_idempotent_all_banks(tmp_path, fixture_bytes, case):
    """End-to-end idempotence for the Phase-C slices."""
    require_enabled(case["bank"])
    store, run1, run2, snapshot1, snapshot2 = run_l4_end_to_end_twice(
        **{k: v for k, v in case.items() if k not in ("scenario_assert", "bank")},
        fixture_bytes=fixture_bytes,
        tmp_path=tmp_path,
    )
    stored1, result1, facts1 = run1
    stored2, result2, facts2 = run2

    # scenario-specific canonical Facts hold on both runs
    case["scenario_assert"](facts1)
    case["scenario_assert"](facts2)

    # strict business-identity equality, zero duplication
    assert snapshot1 == snapshot2
    assert stored2.id == stored1.id
    assert set(f.resolve_id() for f in facts1) == set(f.resolve_id() for f in facts2)
    assert len({f.resolve_id() for f in facts2}) == len(facts2)
    assert all(f.publication_id == stored1.id for f in facts2)
    assert set(f.resolve_id() for f in result1.facts) == set(f.resolve_id() for f in facts1)
    assert set(f.resolve_id() for f in result2.facts) == set(f.resolve_id() for f in facts2)
