"""Phase E — golden corpus from captured real official sources.

Each golden case replays a **captured** real official central-bank source
(stored under ``tests/golden/<bank>/`` with provenance in
``tests/golden/manifest.json``) through the shared L4 harness
(``l4_harness.run_l4_end_to_end``) — discovery -> publication -> classification
-> fetch -> normalization -> gated dispatch -> extraction -> persistence.

The captures are versioned and deterministic: the tests never touch the
network. A change to a live official page therefore cannot silently alter the
corpus output.

Assertions are contractual, not blind snapshots: the canonical
``publication_type``, the extractor version, provenance fields, identity
relations, and a representative subset of canonical Facts. The corpus exists to
detect regressions that clean synthetic fixtures can miss.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.adapters.boe import BoEAdapter
from argus.adapters.boj import BoJAdapter
from argus.adapters.boc import BoCAdapter
from argus.adapters.ecb import ECBAdapter
from argus.adapters.fed import FedAdapter
from argus.adapters.norges import NorgesBankAdapter
from argus.adapters.rba import RBAAdapter
from argus.adapters.rbnz import RBNZAdapter
from argus.adapters.riksbank import RiksbankAdapter
from argus.adapters.snb import SNBAdapter
from argus.config import is_bank_enabled
from argus.decisions import extract_decision
from argus.decisions.boe import BoeDecisionExtractor
from argus.decisions.boc import BocDecisionExtractor
from argus.decisions.ecb import EcbDecisionExtractor
from argus.decisions.fed import FedDecisionExtractor
from argus.decisions.norges import NorgesDecisionExtractor
from argus.decisions.rba import RbaDecisionExtractor
from argus.decisions.riksbank import RiksbankDecisionExtractor
from argus.decisions.snb import SnbDecisionExtractor
from argus.reports import extract_report
from argus.reports.rbnz import RbnzReportExtractor
from argus.statements import extract_statement
from argus.statements.boj import BojStatementExtractor
from l4_harness import run_l4_end_to_end, run_l4_end_to_end_twice

GOLDEN = Path(__file__).parent / "golden"
MANIFEST = json.loads((GOLDEN / "manifest.json").read_text(encoding="utf-8"))

ADAPTERS = {
    "fed": FedAdapter(),
    "ecb": ECBAdapter(),
    "boe": BoEAdapter(),
    "boj": BoJAdapter(),
    "snb": SNBAdapter(),
    "boc": BoCAdapter(),
    "rba": RBAAdapter(),
    "rbnz": RBNZAdapter(),
    "norges": NorgesBankAdapter(),
    "riksbank": RiksbankAdapter(),
}

EXTRACTORS = {
    "fed": FedDecisionExtractor,
    "ecb": EcbDecisionExtractor,
    "boe": BoeDecisionExtractor,
    "boj": BojStatementExtractor,
    "snb": SnbDecisionExtractor,
    "boc": BocDecisionExtractor,
    "rba": RbaDecisionExtractor,
    "rbnz": RbnzReportExtractor,
    "norges": NorgesDecisionExtractor,
    "riksbank": RiksbankDecisionExtractor,
}

ENTRY_POINTS = {
    "extract_decision": extract_decision,
    "extract_statement": extract_statement,
    "extract_report": extract_report,
}

# Representative stable canonical Facts observed on the captured documents.
# Dates and policy-rate values are stable, directly observable, and would catch
# a wrong document / wrong extractor / broken field extraction.
EXPECTED_FACTS = {
    "boc": {
        ("monetary_policy_decision", "date", "date", "2026-07-15", None),
        ("policy_rate", "value", "percentage", 2.25, None),
    },
    "boe": {
        ("monetary_policy_decision", "date", "date", "2026-07-29", None),
        ("bank_rate", "value", "percentage", 3.75, None),
        # Regression: an "increase Bank Rate by 0.25" (dissenting vote) must be
        # a positive change (+25 bps) — not a negative one from matching "ease"
        # inside "increase" — and must persist without a fact_id collision.
        ("bank_rate", "change", "basis_points", 25.0, None),
    },
    "ecb": {
        ("monetary_policy_decision", "date", "date", "2026-07-23", None),
    },
    "norges": {
        ("policy_rate", "value", "percentage", 4.25, None),
    },
    "riksbank": {
        ("monetary_policy_decision", "date", "date", "2026-06-24", None),
        ("policy_rate", "value", "percentage", 1.75, None),
    },
    "snb": {
        ("monetary_policy_decision", "date", "date", "2026-06-18", None),
        ("policy_rate", "value", "percentage", 0.0, None),
    },
    "boj": {
        ("monetary_policy_decision", "date", "date", "2026-07-31", None),
        ("policy_rate", "value", "percentage", 1.0, None),
    },
    "rba": {
        ("monetary_policy_decision", "date", "date", "2026-08-11", None),
        ("cash_rate", "value", "percentage", 4.35, None),
    },
}

# Subject/predicate presence checks for banks whose real document yields only
# text facts (Fed) or whose date is not reliably anchored (Norges).
SCENARIO_ASSERTS = {
    "fed": lambda facts: any(
        f.subject == "monetary_policy_decision" and f.predicate == "statement" for f in facts
    ),
    "ecb": lambda facts: (
        any(f.subject == "monetary_policy_decision" and f.predicate == "statement" for f in facts)
        and any(f.subject == "asset_purchase" for f in facts)
    ),
    "norges": lambda facts: any(
        f.subject == "monetary_policy_decision" and f.predicate == "statement" for f in facts
    ),
    "boj": lambda facts: any(
        f.subject == "monetary_policy_decision" and f.predicate == "statement" for f in facts
    ),
    "rba": lambda facts: any(
        f.subject == "monetary_policy_decision" and f.predicate == "statement" for f in facts
    ),
}


def golden_bytes(name: str) -> bytes:
    return (GOLDEN / name).read_bytes()


def _harness_kwargs(bank: str) -> dict:
    entry = MANIFEST[bank]
    return {
        "adapter": ADAPTERS[bank],
        "source_id": entry["source_id"],
        "discovery_fixture": entry["discovery_fixture"],
        "document_fixture": entry["document_fixture"],
        "target_url": entry["publication_url"],
        "expected_type": entry["expected_type"],
        "extract": ENTRY_POINTS[entry["entry_point"]],
        "expected_extractor": EXTRACTORS[bank],
        # The captured publication page is the mined document; the real pages
        # also link supplementary PDFs (e.g. the press-conference statement).
        # Those are not captured here, so linked-document fetching is disabled:
        # the corpus validates the page through the full L4 path deterministically.
        "page_doc_extraction": False,
        # RBA is a Search Discovery golden case: its discovery fixture is the
        # captured SearXNG JSON response replayed through SearchDiscovery.
        "search_discovery": entry.get("discovery_mode") == "search",
    }


def _assert_case(bank: str, facts) -> None:
    assert all(f.source_text for f in facts)
    assert all(f.document_id for f in facts)
    assert all(f.publication_id for f in facts)
    if bank in SCENARIO_ASSERTS:
        assert SCENARIO_ASSERTS[bank](facts), f"scenario facts missing for {bank}"


@pytest.mark.parametrize("bank", list(MANIFEST))
def test_golden_corpus_l4(tmp_path, bank):
    """The captured real source traverses the full L4 pipeline and persists
    canonical Facts with provenance, deterministically and offline."""
    if not is_bank_enabled(bank):
        pytest.skip(f"{bank} disabled by configuration")
    kwargs = _harness_kwargs(bank)
    store, stored, result, facts, again = run_l4_end_to_end(
        **kwargs,
        expected_facts=EXPECTED_FACTS.get(bank),
        fixture_bytes=golden_bytes,
        tmp_path=tmp_path,
    )
    _assert_case(bank, facts)

    # persisted Facts are coherent with the extraction result and the Store
    assert {f.resolve_id() for f in result.facts} == {f.resolve_id() for f in facts}
    assert store.get_publication(stored.id).id == stored.id
    assert {f.resolve_id() for f in facts} == {f.resolve_id() for f in again}


@pytest.mark.parametrize("bank", list(MANIFEST))
def test_golden_corpus_idempotent(tmp_path, bank):
    """Two complete pipeline runs on the captured source are idempotent."""
    if not is_bank_enabled(bank):
        pytest.skip(f"{bank} disabled by configuration")
    kwargs = _harness_kwargs(bank)
    store, run1, run2, snapshot1, snapshot2 = run_l4_end_to_end_twice(
        **kwargs,
        expected_facts=EXPECTED_FACTS.get(bank),
        fixture_bytes=golden_bytes,
        tmp_path=tmp_path,
    )
    _, _, facts1 = run1
    _, _, facts2 = run2
    _assert_case(bank, facts1)
    _assert_case(bank, facts2)
    assert snapshot1 == snapshot2
    assert set(f.resolve_id() for f in facts1) == set(f.resolve_id() for f in facts2)
