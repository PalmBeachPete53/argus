"""Parametrizable L4 end-to-end harness — one representative slice per bank.

Drives the shared L4 harness (``l4_harness.run_l4_end_to_end``) for each of the
10 initial central banks so the complete pipeline (discovery -> publication ->
classification -> fetch -> normalization -> gated dispatch -> extraction ->
persistence -> idempotent re-extraction) is proven bank by bank.

Slices are deliberately representative, not exhaustive:

- Fed / ECB / BoE / SNB / RBA / Norges / Riksbank  -> decision family
  (Fed Decision+Statement, SNB Monetary Policy Assessment, BoE Summary &
  Minutes are fused communications; the decision entry point is the
  representative one).
- BoJ -> statement family (Decision+Statement fused into the Statement on
  Monetary Policy; no decision extractor by design).
- RBNZ / BoC -> report family (Monetary Policy Statement / Monetary Policy
  Report).

The L4 invariants (persistence, provenance, stable ``resolve_id()`` on
re-extraction) are asserted by the harness itself; these tests supply the
scenario parameters and assert scenario-specific canonical Facts.
"""

from __future__ import annotations

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
from argus.decisions.ecb import EcbDecisionExtractor
from argus.decisions.fed import FedDecisionExtractor
from argus.decisions.norges import NorgesDecisionExtractor
from argus.decisions.rba import RbaDecisionExtractor
from argus.decisions.riksbank import RiksbankDecisionExtractor
from argus.decisions.snb import SnbDecisionExtractor
from argus.reports import extract_report
from argus.reports.boc import BocReportExtractor
from argus.reports.rbnz import RbnzReportExtractor
from argus.statements import extract_statement
from argus.statements.boj import BojStatementExtractor
from l4_harness import fact_signature, run_l4_end_to_end

FED_DECISION_URL = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm"
ECB_DECISION_URL = "https://www.ecb.europa.eu/press/pr/date/2026/html/ecb.mp260703~d1a2b3c4d5.en.html"
BOE_DECISION_URL = "https://www.bankofengland.co.uk/monetary-policy-summary-and-minutes/2026/july-2026"
BOJ_STATEMENT_URL = "https://www.boj.or.jp/en/mopo/mpmdeci/statement_20260731/index.htm"
SNB_DECISION_URL = "https://www.snb.ch/en/publications/communication/press-releases-restricted/pre_20260619"
RBA_DECISION_URL = "https://www.rba.gov.au/media-releases/2026/mr-26-19.html"
NORGES_DECISION_URL = "https://www.norges-bank.no/en/topics/monetary-policy/Monetary-policy-meetings/2026/august-2026/"
RIKSBANK_DECISION_URL = (
    "https://www.riksbank.se/en-gb/monetary-policy/monetary-policy-report/2026/monetary-policy-decision-june-2026/"
)
RBNZ_MPS_URL = "https://www.rbnz.govt.nz/monetary-policy/ocr-decisions/2026/07/monetary-policy-statement-july-2026"
BOC_MPR_URL = "https://www.bankofcanada.ca/publications/mpr/mpr-2026-07-15/"


def require_enabled(bank: str) -> None:
    """Skip a parametrized bank scenario when the bank is disabled by config."""
    if not is_bank_enabled(bank):
        pytest.skip(f"{bank} disabled by configuration")


def _decision_family(facts) -> bool:
    """No report/minutes/projections/speech facts leak into a decision slice."""
    forbidden = {"monetary_policy_report", "minutes", "meeting_account", "speech", "economic_projections"}
    return all(f.subject not in forbidden for f in facts)


def _report_family(facts) -> bool:
    """A report slice never emits decision instruments."""
    return all(f.subject not in ("policy_rate", "monetary_policy_decision") for f in facts)


CASES = [
    pytest.param(
        dict(
            adapter=FedAdapter(),
            bank="fed",
            source_id="fed_monetary_press_rss",
            discovery_fixture="fed_press_monetary.xml",
            document_fixture="documents/fed_decision.html",
            target_url=FED_DECISION_URL,
            expected_type="monetary_policy_decision",
            extract=extract_decision,
            expected_extractor=FedDecisionExtractor,
            qualifier_prefix=None,
            expected_facts={
                ("monetary_policy_decision", "date", "date", "2026-09-18", None),
                ("policy_rate", "change", "basis_points", -25.0, None),
                ("policy_rate", "value", "range", None, None),
            },
            scenario_assert=_decision_family,
        ),
        id="fed-decision-statement-fused",
    ),
    pytest.param(
        dict(
            adapter=ECBAdapter(),
            bank="ecb",
            source_id="ecb_press_rss",
            discovery_fixture="ecb_press.xml",
            document_fixture="documents/ecb_decision.html",
            target_url=ECB_DECISION_URL,
            expected_type="monetary_policy_decision",
            extract=extract_decision,
            expected_extractor=EcbDecisionExtractor,
            qualifier_prefix=None,
            expected_facts={
                ("monetary_policy_decision", "date", "date", "2026-07-23", None),
                ("main_refinancing_rate", "value", "percentage", 2.0, None),
                ("deposit_facility_rate", "change", "basis_points", -25.0, None),
            },
            scenario_assert=_decision_family,
        ),
        id="ecb-decision",
    ),
    pytest.param(
        dict(
            adapter=BoEAdapter(),
            bank="boe",
            source_id="boe_news_rss",
            discovery_fixture="boe_news.xml",
            document_fixture="documents/boe_decision.html",
            target_url=BOE_DECISION_URL,
            expected_type="monetary_policy_decision",
            extract=extract_decision,
            expected_extractor=BoeDecisionExtractor,
            qualifier_prefix=None,
            expected_facts={
                ("monetary_policy_decision", "date", "date", "2026-08-20", None),
                ("bank_rate", "value", "percentage", 4.75, None),
                ("bank_rate", "change", "basis_points", -25.0, None),
            },
            scenario_assert=_decision_family,
        ),
        id="boe-summary-and-minutes",
    ),
    pytest.param(
        dict(
            adapter=BoJAdapter(),
            bank="boj",
            source_id="boj_whatsnew_rss",
            discovery_fixture="boj_whatsnew.xml",
            document_fixture="documents/boj_statement.html",
            target_url=BOJ_STATEMENT_URL,
            expected_type="monetary_policy_statement",
            extract=extract_statement,
            expected_extractor=BojStatementExtractor,
            qualifier_prefix=None,
            expected_facts={
                ("monetary_policy_decision", "date", "date", "2026-06-16", None),
                ("policy_rate", "value", "percentage", 0.5, None),
                ("inflation", "value", "percentage", 2.4, "year:2026"),
            },
            scenario_assert=_decision_family,
        ),
        id="boj-statement-decision-fused",
    ),
    pytest.param(
        dict(
            adapter=SNBAdapter(),
            bank="snb",
            source_id="snb_decision_archive",
            discovery_fixture="snb_decision_archive.html",
            document_fixture="documents/snb_decision.html",
            target_url=SNB_DECISION_URL,
            expected_type="monetary_policy_decision",
            extract=extract_decision,
            expected_extractor=SnbDecisionExtractor,
            qualifier_prefix=None,
            expected_facts={
                ("monetary_policy_decision", "date", "date", "2026-06-19", None),
                ("policy_rate", "value", "percentage", 1.25, None),
                ("policy_rate", "change", "basis_points", -25.0, None),
            },
            scenario_assert=_decision_family,
        ),
        id="snb-monetary-policy-assessment",
    ),
    pytest.param(
        dict(
            adapter=RBAAdapter(),
            bank="rba",
            source_id="rba_media_releases_rss",
            discovery_fixture="rba_media.xml",
            document_fixture="documents/rba_decision.html",
            target_url=RBA_DECISION_URL,
            expected_type="monetary_policy_decision",
            extract=extract_decision,
            expected_extractor=RbaDecisionExtractor,
            qualifier_prefix=None,
            expected_facts={
                ("monetary_policy_decision", "date", "date", "2026-08-04", None),
                ("cash_rate", "value", "percentage", 4.1, None),
                ("cash_rate", "change", "basis_points", -25.0, None),
            },
            scenario_assert=_decision_family,
        ),
        id="rba-decision",
    ),
    pytest.param(
        dict(
            adapter=NorgesBankAdapter(),
            bank="norges",
            source_id="norges_press_releases_rss",
            discovery_fixture="norges_press.xml",
            document_fixture="documents/norges_decision.html",
            target_url=NORGES_DECISION_URL,
            expected_type="monetary_policy_decision",
            extract=extract_decision,
            expected_extractor=NorgesDecisionExtractor,
            qualifier_prefix=None,
            expected_facts={
                ("monetary_policy_decision", "date", "date", "2026-05-03", None),
                ("policy_rate", "value", "percentage", 3.5, None),
                ("policy_rate", "change", "basis_points", -25.0, None),
            },
            scenario_assert=_decision_family,
        ),
        id="norges-decision",
    ),
    pytest.param(
        dict(
            adapter=RiksbankAdapter(),
            bank="riksbank",
            source_id="riksbank_press_releases_rss",
            discovery_fixture="riksbank_press.xml",
            document_fixture="documents/riksbank_decision.html",
            target_url=RIKSBANK_DECISION_URL,
            expected_type="monetary_policy_decision",
            extract=extract_decision,
            expected_extractor=RiksbankDecisionExtractor,
            qualifier_prefix=None,
            expected_facts={
                ("policy_rate", "value", "percentage", 4.0, None),
                ("policy_rate", "change", "basis_points", -25.0, None),
                ("monetary_policy_decision", "date", "date", "2026-06-25", None),
            },
            scenario_assert=_decision_family,
        ),
        id="riksbank-policy-rate-decision",
    ),
    pytest.param(
        dict(
            adapter=RBNZAdapter(),
            bank="rbnz",
            source_id="rbnz_ocr_decisions",
            discovery_fixture="rbnz_decisions.html",
            document_fixture="documents/rbnz_report.html",
            target_url=RBNZ_MPS_URL,
            expected_type="monetary_policy_report",
            extract=extract_report,
            expected_extractor=RbnzReportExtractor,
            qualifier_prefix="report:",
            expected_facts={
                ("inflation", "value", "percentage", 2.0, "year:2026"),
                ("gdp", "value", "percentage", 1.2, "year:2026"),
                ("inflation_risk", "assessment", "categorical", "balanced", None),
            },
            scenario_assert=_report_family,
        ),
        id="rbnz-monetary-policy-statement",
    ),
    pytest.param(
        dict(
            adapter=BoCAdapter(),
            bank="boc",
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
            scenario_assert=_report_family,
        ),
        id="boc-monetary-policy-report",
    ),
]


@pytest.mark.parametrize("case", CASES)
def test_l4_end_to_end_harness(tmp_path, fixture_bytes, case):
    require_enabled(case["bank"])
    store, stored, result, facts, again = run_l4_end_to_end(
        **{k: v for k, v in case.items() if k not in ("scenario_assert", "bank")},
        fixture_bytes=fixture_bytes,
        tmp_path=tmp_path,
    )

    # the persisted publication, document and Facts are coherent
    assert store.get_publication(stored.id).id == stored.id
    assert store.normalized_documents_for_publication(stored.id)
    assert result.publication_id == stored.id
    # no fact identity drift between the extraction result and the store
    assert {f.resolve_id() for f in result.facts} == {f.resolve_id() for f in facts}
    # scenario-specific canonical Facts were extracted
    case["scenario_assert"](facts)
    # deterministic signatures are stable across the idempotent re-extraction
    assert {fact_signature(f) for f in facts} == {fact_signature(f) for f in again}
