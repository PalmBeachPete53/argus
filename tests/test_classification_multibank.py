"""Bank-specific classification regression tests — RBA, RBNZ, BoC, Riksbank.

Closes the Phase 4 audit classification gaps for the publication families that
previously relied on injected publication types in dispatch tests only:

- RBA Monetary Policy Decision        -> monetary_policy_decision
- RBA Statement on Monetary Policy    -> monetary_policy_report
- RBNZ Monetary Policy Decision       -> monetary_policy_decision
- RBNZ Monetary Policy Statement      -> monetary_policy_report
- BoC FAD Press Release               -> monetary_policy_decision
- Riksbank Policy Rate Decision       -> monetary_policy_decision
- Riksbank Minutes                    -> minutes

Each test drives the real ``PublicationClassifier`` with representative
publication metadata (URL + title, and where relevant a source declaration /
stale hint) and asserts the canonical ``publication_type`` — never a manually
injected type.
"""

from __future__ import annotations

from argus.classification import Confidence, METHOD_URL_PATTERN, PublicationClassifier
from argus.models import Publication
from argus.registry import SourceRegistry


def publication(bank: str, url: str, title: str, *, source_id: str | None = None, type_hint=None) -> Publication:
    extra = {"type_hint": list(type_hint)} if type_hint else {}
    return Publication(
        central_bank=bank,
        title=title,
        url=url,
        source_id=source_id or f"{bank}-x",
        source_url=url,
        extra=extra,
    )


def classify(pub: Publication):
    return PublicationClassifier().classify(pub)


def classify_with_registry(pub: Publication):
    return PublicationClassifier(registry=SourceRegistry()).classify(pub)


# ---------------------------------------------------------------------------
# Reserve Bank of Australia (rba)
# ---------------------------------------------------------------------------

_RBA_DECISION_URL = "https://www.rba.gov.au/monetary-policy/int-rate-decisions/2026/mr-26-07.html"
_RBA_DECISION_TITLE = "Reserve Bank of Australia — Monetary Policy Decision"
_RBA_SMP_URL = "https://www.rba.gov.au/publications/smp/2026/may/html/overview.html"
_RBA_SMP_TITLE = "Statement on Monetary Policy – May 2026"
_RBA_SMP_ALT_URL = "https://www.rba.gov.au/monetary-policy/statement-on-monetary-policy/2026/may/"


def test_rba_decision_is_monetary_policy_decision():
    result = classify(publication("rba", _RBA_DECISION_URL, _RBA_DECISION_TITLE))
    assert result.publication_type == "monetary_policy_decision"
    assert result.method == METHOD_URL_PATTERN
    assert result.confidence == Confidence.MEDIUM


def test_rba_decision_is_not_a_report():
    result = classify(publication("rba", _RBA_DECISION_URL, _RBA_DECISION_TITLE))
    assert result.publication_type == "monetary_policy_decision"
    assert not evidence_contains(result, "monetary_policy_report")


def test_rba_smp_is_monetary_policy_report():
    result = classify(publication("rba", _RBA_SMP_URL, _RBA_SMP_TITLE))
    assert result.publication_type == "monetary_policy_report"


def test_rba_smp_statement_on_monetary_policy_url_is_report():
    result = classify(publication("rba", _RBA_SMP_ALT_URL, _RBA_SMP_TITLE))
    assert result.publication_type == "monetary_policy_report"


def test_rba_smp_is_not_a_decision():
    result = classify(publication("rba", _RBA_SMP_URL, _RBA_SMP_TITLE))
    assert result.publication_type == "monetary_policy_report"
    assert not evidence_contains(result, "monetary_policy_decision")


def test_rba_decision_via_decision_archive_hint():
    # Real discovery: the int-rate-decisions archive source stamps a decision
    # type hint, so even a decision titled with statement wording stays a
    # decision (the source is decision-specific).
    result = classify_with_registry(publication(
        "rba", _RBA_DECISION_URL, "Statement on Monetary Policy",
        source_id="rba_int_rate_archive",
    ))
    assert result.publication_type == "monetary_policy_decision"
    assert result.confidence == Confidence.HIGH


def test_rba_smp_via_smp_rss_hint():
    # Real discovery: the SMP RSS stamps a report type hint.
    result = classify_with_registry(publication(
        "rba", _RBA_SMP_URL, _RBA_SMP_TITLE, source_id="rba_smp_rss",
    ))
    assert result.publication_type == "monetary_policy_report"
    assert result.confidence == Confidence.HIGH


# ---------------------------------------------------------------------------
# Reserve Bank of New Zealand (rbnz)
# ---------------------------------------------------------------------------

_RBNZ_DECISION_URL = "https://www.rbnz.govt.nz/news/2026/05/ocr-media-release-22-may-2026"
_RBNZ_DECISION_TITLE = "OCR media release - 22 May 2026"
_RBNZ_DECISION_URL2 = "https://www.rbnz.govt.nz/monetary-policy/monetary-policy-decisions/2026/05/22/"
_RBNZ_MPS_URL = "https://www.rbnz.govt.nz/hub/publications/monetary-policy-statement/2026/may/"
_RBNZ_MPS_TITLE = "Monetary Policy Statement May 2026"
_RBNZ_MPS_TIMELINE_URL = "https://www.rbnz.govt.nz/monetary-policy/ocr-decisions/2026/07/monetary-policy-statement-july-2026"


def test_rbnz_ocr_decision_is_monetary_policy_decision():
    result = classify(publication("rbnz", _RBNZ_DECISION_URL, _RBNZ_DECISION_TITLE))
    assert result.publication_type == "monetary_policy_decision"


def test_rbnz_ocr_decision_timeline_page_is_monetary_policy_decision():
    result = classify(publication("rbnz", _RBNZ_DECISION_URL2, "OCR Decision 22 May 2026"))
    assert result.publication_type == "monetary_policy_decision"


def test_rbnz_mps_is_monetary_policy_report():
    result = classify(publication("rbnz", _RBNZ_MPS_URL, _RBNZ_MPS_TITLE))
    assert result.publication_type == "monetary_policy_report"


def test_rbnz_mps_on_ocr_timeline_path_is_report_not_decision():
    # The MPS leaf linked from the OCR timeline carries no decision signal of
    # its own: title resolves it to the report family (never a decision).
    result = classify(publication("rbnz", _RBNZ_MPS_TIMELINE_URL, "Monetary Policy Statement - July 2026"))
    assert result.publication_type == "monetary_policy_report"


def test_rbnz_mps_is_not_a_decision():
    result = classify(publication("rbnz", _RBNZ_MPS_URL, _RBNZ_MPS_TITLE))
    assert result.publication_type == "monetary_policy_report"
    assert not evidence_contains(result, "monetary_policy_decision")


def test_rbnz_decision_is_not_a_report():
    result = classify(publication("rbnz", _RBNZ_DECISION_URL, _RBNZ_DECISION_TITLE))
    assert result.publication_type == "monetary_policy_decision"
    assert not evidence_contains(result, "monetary_policy_report")


def test_rbnz_mps_via_ocr_timeline_source_is_report():
    # Regression: the OCR timeline source is mixed-family ("media releases +
    # MPS") and must NOT stamp a decision hint on MPS items. Discovery stamps no
    # type, so the MPS resolves to the report family by URL/title.
    result = classify_with_registry(publication(
        "rbnz", _RBNZ_MPS_TIMELINE_URL, "Monetary Policy Statement - July 2026",
        source_id="rbnz_ocr_decisions",
    ))
    assert result.publication_type == "monetary_policy_report"


def test_rbnz_ocr_decision_via_ocr_timeline_source_is_decision():
    result = classify_with_registry(publication(
        "rbnz", _RBNZ_DECISION_URL, _RBNZ_DECISION_TITLE, source_id="rbnz_ocr_decisions",
    ))
    assert result.publication_type == "monetary_policy_decision"


def test_rbnz_mps_stale_decision_hint_does_not_override_live_untyped_source():
    # A publication stored with a stale decision hint from the old (typed)
    # adapter must not be forced into a HIGH decision: the live source now
    # declares no types, so URL/title resolve it to the report family.
    result = classify_with_registry(publication(
        "rbnz", _RBNZ_MPS_URL, _RBNZ_MPS_TITLE,
        source_id="rbnz_ocr_decisions", type_hint=["monetary_policy_decision"],
    ))
    assert result.publication_type == "monetary_policy_report"


# ---------------------------------------------------------------------------
# Bank of Canada (boc)
# ---------------------------------------------------------------------------

_BOC_FAD_URL = "https://www.bankofcanada.ca/2026/07/fad-press-release-2026-07-15/"
_BOC_FAD_TITLE = "FAD Press Release – July 15, 2026"
_BOC_FAD_RATE_URL = "https://www.bankofcanada.ca/2026/07/policy-interest-rate-2026-07-15/"
_BOC_MPR_URL = "https://www.bankofcanada.ca/publications/mpr/mpr-2026-07-15/"
_BOC_MPR_TITLE = "Monetary Policy Report – July 2026"


def test_boc_fad_press_release_is_monetary_policy_decision():
    result = classify(publication("boc", _BOC_FAD_URL, _BOC_FAD_TITLE))
    assert result.publication_type == "monetary_policy_decision"


def test_boc_fad_policy_interest_rate_url_is_monetary_policy_decision():
    result = classify(publication("boc", _BOC_FAD_RATE_URL, "Bank of Canada lowers policy interest rate"))
    assert result.publication_type == "monetary_policy_decision"


def test_boc_fad_via_fad_archive_hint():
    result = classify_with_registry(publication(
        "boc", _BOC_FAD_URL, _BOC_FAD_TITLE, source_id="boc_fad_archive",
    ))
    assert result.publication_type == "monetary_policy_decision"
    assert result.confidence == Confidence.HIGH


def test_boc_fad_is_not_a_report():
    result = classify(publication("boc", _BOC_FAD_URL, _BOC_FAD_TITLE))
    assert result.publication_type == "monetary_policy_decision"
    assert not evidence_contains(result, "monetary_policy_report")


def test_boc_mpr_is_not_a_decision():
    result = classify(publication("boc", _BOC_MPR_URL, _BOC_MPR_TITLE))
    assert result.publication_type == "monetary_policy_report"
    assert not evidence_contains(result, "monetary_policy_decision")


def test_boc_mpr_via_mpr_feed_hint():
    result = classify_with_registry(publication(
        "boc", _BOC_MPR_URL, _BOC_MPR_TITLE, source_id="boc_mpr_feed",
    ))
    assert result.publication_type == "monetary_policy_report"
    assert result.confidence == Confidence.HIGH


# ---------------------------------------------------------------------------
# Sveriges Riksbank (riksbank)
# ---------------------------------------------------------------------------

_RIKSBANK_DECISION_URL = "https://www.riksbank.se/en-gb/monetary-policy/monetary-policy-decision/2026/june/"
_RIKSBANK_DECISION_TITLE = "Monetary policy decision June 2026"
_RIKSBANK_DECISION_MPR_URL = "https://www.riksbank.se/en-gb/monetary-policy/monetary-policy-report/2026/monetary-policy-decision-june-2026/"
_RIKSBANK_MINUTES_URL = "https://www.riksbank.se/en-gb/monetary-policy/minutes/2026/minutes-of-the-monetary-policy-meeting-june-2026/"
_RIKSBANK_MINUTES_TITLE = "Minutes of the monetary policy meeting June 2026"
_RIKSBANK_MINUTES_EB_URL = "https://www.riksbank.se/en-gb/monetary-policy/minutes/2026/minutes-of-the-executive-board-meeting-june-2026/"


def test_riksbank_policy_rate_decision_is_monetary_policy_decision():
    result = classify(publication("riksbank", _RIKSBANK_DECISION_URL, _RIKSBANK_DECISION_TITLE))
    assert result.publication_type == "monetary_policy_decision"


def test_riksbank_decision_aggregate_page_is_monetary_policy_decision():
    result = classify(publication("riksbank", _RIKSBANK_DECISION_MPR_URL, _RIKSBANK_DECISION_TITLE))
    assert result.publication_type == "monetary_policy_decision"


def test_riksbank_minutes_are_minutes():
    result = classify(publication("riksbank", _RIKSBANK_MINUTES_URL, _RIKSBANK_MINUTES_TITLE))
    assert result.publication_type == "minutes"


def test_riksbank_executive_board_minutes_are_minutes():
    result = classify(publication(
        "riksbank", _RIKSBANK_MINUTES_EB_URL, "Minutes of the Executive Board meeting June 2026",
    ))
    assert result.publication_type == "minutes"


def test_riksbank_decision_is_not_minutes():
    result = classify(publication("riksbank", _RIKSBANK_DECISION_URL, _RIKSBANK_DECISION_TITLE))
    assert result.publication_type == "monetary_policy_decision"
    assert not evidence_contains(result, "minutes")


def test_riksbank_minutes_are_not_a_decision():
    result = classify(publication("riksbank", _RIKSBANK_MINUTES_URL, _RIKSBANK_MINUTES_TITLE))
    assert result.publication_type == "minutes"
    assert not evidence_contains(result, "monetary_policy_decision")


def test_riksbank_minutes_are_not_a_report():
    result = classify(publication("riksbank", _RIKSBANK_MINUTES_URL, _RIKSBANK_MINUTES_TITLE))
    assert result.publication_type == "minutes"
    assert not evidence_contains(result, "monetary_policy_report")


def test_riksbank_minutes_via_minutes_rss_hint():
    result = classify_with_registry(publication(
        "riksbank", _RIKSBANK_MINUTES_URL, _RIKSBANK_MINUTES_TITLE, source_id="riksbank_minutes_rss",
    ))
    assert result.publication_type == "minutes"
    assert result.confidence == Confidence.HIGH


def test_riksbank_decision_via_press_releases_feed_is_decision():
    # The press-releases feed declares no type: the decision resolves by URL.
    result = classify_with_registry(publication(
        "riksbank", _RIKSBANK_DECISION_URL, _RIKSBANK_DECISION_TITLE,
        source_id="riksbank_press_releases_rss",
    ))
    assert result.publication_type == "monetary_policy_decision"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def evidence_contains(result, publication_type: str) -> bool:
    return any(publication_type in item for item in result.evidence)
