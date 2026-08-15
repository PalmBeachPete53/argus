"""Phase 10 — ECB Monetary Policy Report / Reports extractor: end-to-end tests
using the local HTML fixtures and the existing Store (vertical slice).

Covers: classification gating (``monetary_policy_report``), conservative
section routing (known economic section → mined, non-economic / unknown
heading / analytical box → IGNORED, ``UNKNOWN ≠ ECONOMIC``), content-first
sentence classification (guidance > policy > risk > financial > inflation >
labour > growth > fiscal), explicit value claims only (percentage + explicit
reference period; forecast without a period is ignored; "% of GDP" shares are
never percentages), categorical risk orientations (upside / downside /
balanced) only when explicit, table extraction (variable × year × value × unit
integrity, unit from the table's own caption), provenance (verbatim
source_text, source_location, extraction version/method), ``speaker`` always
``None``, within-run deduplication, deterministic extraction, idempotent and
empty-result persistence, and Phase 5/6/7/8/9 coexistence.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from argus.classification.base import Confidence
from argus.decisions import extract_decision
from argus.documents import Normalizer
from argus.documents.base import DocumentSection, DocumentTable, NormalizedDocument
from argus.facts import ExtractionResult, LocationKind, ValueKind
from argus.minutes import extract_minutes
from argus.models import Document, DocumentStatus, Publication
from argus.press_conferences import extract_press_conference
from argus.projections import extract_projections
from argus.reports import (
    REPORT_PUBLICATION_TYPES,
    SUBJECT_CORE_INFLATION,
    SUBJECT_FINANCIAL_CONDITIONS,
    SUBJECT_FISCAL_POLICY,
    SUBJECT_GDP,
    SUBJECT_GROWTH,
    SUBJECT_GROWTH_RISK,
    SUBJECT_INFLATION,
    SUBJECT_INFLATION_EXPECTATIONS,
    SUBJECT_INFLATION_RISK,
    SUBJECT_LABOUR_MARKET,
    SUBJECT_MONETARY_POLICY,
    SUBJECT_POLICY_GUIDANCE,
    SUBJECT_RISK,
    SUBJECT_UNEMPLOYMENT,
    SUBJECT_WAGES,
    EcbReportsExtractor,
    ReportsExtractor,
    extract_report,
    extract_report_batch,
)
from argus.reports.ecb import CAT_IGNORE, CAT_GROWTH, CAT_POLICY, CAT_UNKNOWN, _section_category
from argus.statements import extract_statement
from argus.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "documents"
ECB_URL = "https://www.ecb.europa.eu/press/economic-bulletin/html/ecb.eb202603.en.html"


def reports_publication(**kw) -> Publication:
    fields = dict(
        central_bank="ecb",
        title="Economic Bulletin Issue 3/2026",
        url=ECB_URL,
        source_id="ecb-report",
        source_url="https://www.ecb.europa.eu/press/economic-bulletin/html/index.en.html",
        id="pub-ecb-report",
    )
    fields.update(kw)
    return Publication(**fields)


def normalized_fixture(name: str) -> object:
    doc = Document(
        publication_id="pub-ecb-report",
        url=ECB_URL,
        kind="html",
        status=DocumentStatus.FETCHED,
        local_path=str(FIXTURES / name),
    )
    return Normalizer().parse(doc)


def extract_fixture(name: str):
    return EcbReportsExtractor().extract(reports_publication(), normalized_fixture(name))


def facts_by(result, subject: str, predicate: str):
    return [f for f in result.facts if f.subject == subject and f.predicate == predicate]


def period_of(fact) -> str | None:
    if fact.period is None:
        return None
    kind = fact.period.kind
    kind_str = kind.value if hasattr(kind, "value") else kind  # persisted rows keep a plain string
    return f"{kind_str}:{fact.period.value}"


def _doc_with_sections(sections: list[DocumentSection]) -> NormalizedDocument:
    return NormalizedDocument(
        publication_id="pub-ecb-report",
        document_id="sha-sections",
        source_url=ECB_URL,
        local_path=None,
        document_kind="html",
        sections=sections,
    )


def _doc_with_tables(tables: list[DocumentTable]) -> NormalizedDocument:
    return NormalizedDocument(
        publication_id="pub-ecb-report",
        document_id="sha-tables",
        source_url=ECB_URL,
        local_path=None,
        document_kind="html",
        tables=tables,
    )


def _section(heading: str, text: str) -> DocumentSection:
    return DocumentSection(order=0, heading=heading, level=2, text=text)


def _table(name: str, rows: list[list[str]], headers: list[str] | None = None) -> DocumentTable:
    return DocumentTable(
        order=0,
        name=name,
        headers=headers or ["Variable", "2024", "2025", "2026"],
        rows=rows,
    )


def _numeric_values(result, subject: str) -> dict:
    return {
        (period_of(f), f.value.value)
        for f in facts_by(result, subject, "value")
        if f.value.value is not None
    }


# ---------------------------------------------------------------------------
# golden facts across all ECB report fixtures
# ---------------------------------------------------------------------------

GOLDEN = {
    "ecb_report.html": {
        "warnings": [],
        "count": 17,
        "triples": [
            (SUBJECT_GROWTH, "assessment", None),
            (SUBJECT_GDP, "value", "quarter:2026-Q1"),
            (SUBJECT_GDP, "value", "year:2026"),
            (SUBJECT_GDP, "value", "year:2027"),
            (SUBJECT_GROWTH, "assessment", None),
            (SUBJECT_INFLATION, "value", "year:2025"),
            (SUBJECT_INFLATION, "value", "year:2026"),
            (SUBJECT_INFLATION, "value", "year:2027"),
            (SUBJECT_CORE_INFLATION, "value", "month:2026-06"),
            (SUBJECT_INFLATION_EXPECTATIONS, "assessment", None),
            (SUBJECT_FINANCIAL_CONDITIONS, "value", "month:2026-06"),
            (SUBJECT_FINANCIAL_CONDITIONS, "assessment", None),
            (SUBJECT_FISCAL_POLICY, "assessment", None),
            (SUBJECT_MONETARY_POLICY, "statement", None),
            (SUBJECT_POLICY_GUIDANCE, "statement", None),
            (SUBJECT_GROWTH_RISK, "assessment", None),
            (SUBJECT_INFLATION_RISK, "assessment", None),
        ],
        "values": {
            (SUBJECT_GDP, "quarter:2026-Q1"): 0.4,
            (SUBJECT_GDP, "year:2026"): 1.2,
            (SUBJECT_GDP, "year:2027"): 1.4,
            (SUBJECT_INFLATION, "year:2025"): 2.4,
            (SUBJECT_INFLATION, "year:2026"): 2.0,
            (SUBJECT_INFLATION, "year:2027"): 1.9,
            (SUBJECT_CORE_INFLATION, "month:2026-06"): 2.3,
            (SUBJECT_FINANCIAL_CONDITIONS, "month:2026-06"): 4.2,
        },
        "risk_orientations": {
            (SUBJECT_GROWTH_RISK, None): "balanced",
            (SUBJECT_INFLATION_RISK, None): "upside",
        },
    },
    "ecb_report_tables.html": {
        "warnings": ["no_risk_assessment", "no_forward_guidance"],
        "count": 16,
        "triples": [
            (SUBJECT_INFLATION, "value", "year:2024"),
            (SUBJECT_INFLATION, "value", "year:2027"),
            (SUBJECT_INFLATION, "value", "year:2025"),
            (SUBJECT_INFLATION, "value", "year:2026"),
            (SUBJECT_CORE_INFLATION, "value", "year:2024"),
            (SUBJECT_CORE_INFLATION, "value", "year:2025"),
            (SUBJECT_CORE_INFLATION, "value", "year:2026"),
            (SUBJECT_GDP, "value", "year:2024"),
            (SUBJECT_GDP, "value", "year:2025"),
            (SUBJECT_GDP, "value", "year:2026"),
            (SUBJECT_UNEMPLOYMENT, "value", "year:2024"),
            (SUBJECT_UNEMPLOYMENT, "value", "year:2025"),
            (SUBJECT_UNEMPLOYMENT, "value", "year:2026"),
            (SUBJECT_WAGES, "value", "year:2024"),
            (SUBJECT_WAGES, "value", "year:2025"),
            (SUBJECT_WAGES, "value", "year:2026"),
        ],
        "values": {
            (SUBJECT_INFLATION, "year:2024"): 2.4,
            (SUBJECT_INFLATION, "year:2025"): 2.0,
            (SUBJECT_INFLATION, "year:2026"): 1.9,
            (SUBJECT_INFLATION, "year:2027"): 2.0,
            (SUBJECT_CORE_INFLATION, "year:2024"): 2.8,
            (SUBJECT_CORE_INFLATION, "year:2025"): 2.5,
            (SUBJECT_CORE_INFLATION, "year:2026"): 2.3,
            (SUBJECT_GDP, "year:2024"): 0.8,
            (SUBJECT_GDP, "year:2025"): 1.2,
            (SUBJECT_GDP, "year:2026"): 1.4,
            (SUBJECT_UNEMPLOYMENT, "year:2024"): 6.5,
            (SUBJECT_UNEMPLOYMENT, "year:2025"): 6.2,
            (SUBJECT_UNEMPLOYMENT, "year:2026"): 6.0,
            (SUBJECT_WAGES, "year:2024"): 4.2,
            (SUBJECT_WAGES, "year:2025"): 3.8,
            (SUBJECT_WAGES, "year:2026"): 3.4,
        },
        "risk_orientations": {},
    },
    "ecb_report_risks.html": {
        "warnings": ["no_forward_guidance"],
        "count": 5,
        "triples": [
            (SUBJECT_INFLATION, "value", "year:2026"),
            (SUBJECT_GROWTH_RISK, "assessment", None),
            (SUBJECT_INFLATION_RISK, "assessment", None),
            (SUBJECT_GROWTH_RISK, "assessment", None),
            (SUBJECT_RISK, "assessment", None),
        ],
        "values": {(SUBJECT_INFLATION, "year:2026"): 2.0},
        "risk_orientations": {
            (SUBJECT_GROWTH_RISK, None): "balanced",
            (SUBJECT_INFLATION_RISK, None): "upside",
        },
    },
    "ecb_report_unknown.html": {
        "warnings": ["no_economic_sections", "no_risk_assessment", "no_forward_guidance"],
        "count": 0,
        "triples": [],
        "values": {},
        "risk_orientations": {},
    },
    "ecb_report_minimal.html": {
        "warnings": ["no_risk_assessment", "no_forward_guidance"],
        "count": 1,
        "triples": [(SUBJECT_INFLATION, "value", "year:2026")],
        "values": {(SUBJECT_INFLATION, "year:2026"): 2.0},
        "risk_orientations": {},
    },
}


def test_golden_facts_across_all_fixtures():
    for name, expected in GOLDEN.items():
        result = extract_fixture(name)
        assert result.warnings == expected["warnings"], (name, result.warnings)
        assert len(result.facts) == expected["count"], name
        got_triples = [(f.subject, f.predicate, period_of(f)) for f in result.facts]
        assert got_triples == expected["triples"], (name, got_triples)
        for (subject, period), value in expected["values"].items():
            facts = [f for f in facts_by(result, subject, "value") if period_of(f) == period]
            assert facts and facts[0].value.value == value, (name, subject, period)
            assert facts[0].value.kind is ValueKind.PERCENTAGE, (name, subject, period)
            assert facts[0].confidence is Confidence.HIGH, (name, subject, period)
        for (subject, period), orientation in expected["risk_orientations"].items():
            facts = [f for f in facts_by(result, subject, "assessment") if period_of(f) == period]
            assert facts and facts[0].value.kind is ValueKind.CATEGORICAL, (name, subject)
            assert facts[0].value.value == orientation, (name, subject)
            assert facts[0].confidence is Confidence.HIGH, (name, subject)


def test_golden_risks_fixture_has_both_orientations():
    result = extract_fixture("ecb_report_risks.html")
    growth_risks = facts_by(result, SUBJECT_GROWTH_RISK, "assessment")
    assert {f.value.value for f in growth_risks} == {"balanced", "downside"}
    text_risks = facts_by(result, SUBJECT_RISK, "assessment")
    assert len(text_risks) == 1
    assert text_risks[0].value.kind is ValueKind.TEXT  # "elevated" without an orientation is verbatim, never forced
    assert text_risks[0].confidence is Confidence.MEDIUM


# ---------------------------------------------------------------------------
# conservative section routing
# ---------------------------------------------------------------------------


def test_known_economic_sections_are_mined():
    result = extract_fixture("ecb_report.html")
    assert len(result.facts) == 17


def test_non_economic_sections_are_ignored():
    result = extract_fixture("ecb_report.html")
    assert not any(f.source_text == "This document is provided for information purposes only." for f in result.facts)
    assert not any("Foreword" in (f.source_text or "") for f in result.facts)
    assert not any("Legal notice" in (f.source_text or "") for f in result.facts)


def test_unknown_heading_ignores_economic_content():
    # an unknown section full of economic-looking sentences yields nothing:
    # UNKNOWN ≠ ECONOMIC
    result = extract_fixture("ecb_report_unknown.html")
    assert result.facts == []
    assert result.warnings == ["no_economic_sections", "no_risk_assessment", "no_forward_guidance"]


def test_unknown_section_with_economic_content_is_never_mined():
    sections = [
        _section("Economic activity", "Inflation is projected to average 2.4% in 2026."),
        _section("Some future section", "Inflation is projected to average 2.4% in 2026."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert len(result.facts) == 1  # only the known section is mined
    assert result.facts[0].source_location.section == 0


def test_analytical_boxes_are_never_mined():
    sections = [
        _section("Box 1 — The global economy", "Inflation is projected to average 2.4% in 2026."),
        _section("Box 2 — Fiscal developments", "Inflation is projected to average 2.0% in 2027."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert result.facts == []
    assert "no_economic_sections" in result.warnings


def test_report_title_and_legal_heading_are_ignored():
    sections = [
        _section("Economic Bulletin Issue 3/2026", "Inflation is projected to average 2.4% in 2026."),
        _section("Monetary policy report", "Inflation is projected to average 2.0% in 2027."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert result.facts == []


def test_heading_numbering_and_footnotes_are_normalized():
    sections = [
        _section("2 Economic activity 1)", "Inflation is projected to average 2.4% in 2026."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert len(result.facts) == 1
    assert result.facts[0].subject == SUBJECT_INFLATION


def test_known_general_overview_heading_is_mined():
    sections = [_section("1 Overview", "Inflation averaged 2.4% in 2024.")]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert len(result.facts) == 1


# ---------------------------------------------------------------------------
# Phase 10 hardening — exact heading routing (near-miss + identity)
# ---------------------------------------------------------------------------

NEAR_MISS_HEADINGS = [
    "Non-financial developments",
    "Non-economic developments",
    "Financial institutions",
    "Core developments",
    "Output developments",
    "Risk management",
    "Fiscal institutions",
    "Employment policy",
    "Economic history",
]

NEAR_MISS_TEXT = (
    "Inflation is projected to average 2.4% in 2026. "
    "Risks to growth were tilted to the downside."
)


def test_near_miss_headings_are_never_mined():
    # substring routing is gone: a marker inside a near-miss heading ("risk"
    # in "Risk management", "economic" in "Non-economic developments",
    # "fiscal" in "Fiscal institutions", "employment" in "Employment policy",
    # "output" in "Output developments", "financial" in "Financial
    # institutions") must never route the section.
    for heading in NEAR_MISS_HEADINGS:
        result = EcbReportsExtractor().extract(
            reports_publication(), _doc_with_sections([_section(heading, NEAR_MISS_TEXT)])
        )
        assert result.facts == [], heading
        assert result.warnings == ["no_economic_sections", "no_risk_assessment", "no_forward_guidance"], heading


LEGIT_HEADING_ROUTING = {
    "Financial developments": ("Financing conditions remained tight.", SUBJECT_FINANCIAL_CONDITIONS),
    "Economic activity": ("Real GDP is projected to grow by 1.2% in 2026.", SUBJECT_GDP),
    "Prices and costs": ("Inflation is projected to average 2.0% in 2026.", SUBJECT_INFLATION),
    "Risk assessment": ("Risks to growth were broadly balanced.", SUBJECT_GROWTH_RISK),
    "Fiscal developments": ("Fiscal policy is expected to remain neutral.", SUBJECT_FISCAL_POLICY),
    "Monetary policy developments": ("The Governing Council decided to keep its key interest rates unchanged.", SUBJECT_MONETARY_POLICY),
    "Labour market": ("The labour market remained resilient.", SUBJECT_LABOUR_MARKET),
    "Overview": ("Inflation averaged 2.4% in 2024.", SUBJECT_INFLATION),
    "External environment": ("Global growth remained moderate.", SUBJECT_GROWTH),
}


def test_legitimate_heading_variants_are_still_mined():
    for heading, (text, subject) in LEGIT_HEADING_ROUTING.items():
        result = EcbReportsExtractor().extract(
            reports_publication(), _doc_with_sections([_section(heading, text)])
        )
        assert result.facts, heading
        assert {f.subject for f in result.facts} == {subject}, heading


def test_heading_normalization_controls_case_numbering_punctuation_and_the():
    for heading in (
        "ECONOMIC ACTIVITY",
        "3.2 Economic activity",
        "Economic activity:",
        "Economic activity.",
        "The economic activity",
        "Economic activity 2)",
    ):
        result = EcbReportsExtractor().extract(
            reports_publication(),
            _doc_with_sections([_section(heading, "Real GDP is projected to grow by 1.2% in 2026.")]),
        )
        assert result.facts, heading
        assert result.facts[0].subject == SUBJECT_GDP, heading


def test_heading_routing_is_exact_identity_not_substring():
    mined = EcbReportsExtractor().extract(
        reports_publication(), _doc_with_sections([_section("Risk", "Downside risks increased.")])
    )
    assert mined.facts
    ignored = EcbReportsExtractor().extract(
        reports_publication(), _doc_with_sections([_section("Risk management", "Downside risks increased.")])
    )
    assert ignored.facts == []
    assert ignored.warnings == ["no_economic_sections", "no_risk_assessment", "no_forward_guidance"]


# ---------------------------------------------------------------------------
# Phase 10 final hardening — exact IGNORE heading matching (controlled
# vocabulary, never substring coincidence)
# ---------------------------------------------------------------------------


def test_known_ignored_headings_yield_zero_facts():
    # every controlled non-economic heading is a known IGNORE section: 0 facts
    # and the no_economic_sections warning, even under economic-looking content
    for heading in (
        "Legal notice", "Foreword", "Editorial", "Disclaimer", "Copyright",
        "Imprint", "Statistical annex", "Statistics", "Annex", "Appendix",
        "Technical appendix", "Glossary", "References", "Bibliography",
        "Abbreviations", "Acknowledgements", "Contents", "Methodology",
        "Economic bulletin", "Monetary policy report", "Note",
    ):
        result = EcbReportsExtractor().extract(
            reports_publication(),
            _doc_with_sections([_section(heading, "Inflation is projected to average 2.4% in 2026.")]),
        )
        assert result.facts == [], heading
        assert "no_economic_sections" in result.warnings, heading


def test_normalized_ignored_headings_yield_zero_facts():
    # the existing normalization (case, numbering, leading "the", trailing
    # punctuation) is preserved before the exact membership check
    for heading in ("3. LEGAL NOTICE.", "Foreword.", "The Disclaimer", "Statistical Annex"):
        result = EcbReportsExtractor().extract(
            reports_publication(),
            _doc_with_sections([_section(heading, "Inflation is projected to average 2.4% in 2026.")]),
        )
        assert result.facts == [], heading
        assert "no_economic_sections" in result.warnings, heading


def test_legitimate_ignored_headings_remain_ignored():
    for heading in ("Legal notice", "Statistical annex", "Copyright", "Imprint", "Disclaimer"):
        result = EcbReportsExtractor().extract(
            reports_publication(),
            _doc_with_sections([_section(heading, "Inflation is projected to average 2.4% in 2026.")]),
        )
        assert result.facts == [], heading


def test_ignore_identity_is_exact_never_substring():
    # "Legal framework for monetary policy" merely shares words with the
    # controlled ignore heading "legal notice"; substring coincidence must
    # never determine identity — it routes through the normal exact rules
    # (UNKNOWN), never as a known non-economic heading.
    assert _section_category("Legal framework for monetary policy") == CAT_UNKNOWN
    assert _section_category("Legal framework for monetary policy") != CAT_IGNORE
    # "monetary policy developments" shares "monetary policy" with the ignore
    # heading "monetary policy report"; exact identity keeps it a policy
    # section, never an ignored one.
    assert _section_category("Monetary policy developments") == CAT_POLICY
    # "economic activity" shares "economic" with the ignore heading "economic
    # bulletin"; exact identity keeps it a growth section.
    assert _section_category("Economic activity") == CAT_GROWTH


def test_ignore_routing_never_uses_substring_for_annex_variants():
    # the exact heading "Statistical annex" (and "Annex" itself) are ignored …
    assert _section_category("Statistical annex") == CAT_IGNORE
    assert _section_category("Annex") == CAT_IGNORE
    # … but headings that merely contain "statistical" or "annex" are never
    # those headings — they fall through to the normal exact rules (UNKNOWN).
    assert _section_category("Statistical outlook") == CAT_UNKNOWN
    assert _section_category("Annexation of financial conditions") == CAT_UNKNOWN
    result = EcbReportsExtractor().extract(
        reports_publication(),
        _doc_with_sections(
            [_section("Annexation of financial conditions", "Inflation is projected to average 2.4% in 2026.")]
        ),
    )
    assert result.facts == []
    assert "no_economic_sections" in result.warnings


def test_unknown_heading_with_economic_content_yields_zero_facts():
    for heading in ("Some future section", "Additional information", "Legal framework for monetary policy"):
        for text in ("Inflation is expected to remain elevated.", "GDP growth increased."):
            result = EcbReportsExtractor().extract(
                reports_publication(), _doc_with_sections([_section(heading, text)])
            )
            assert result.facts == [], (heading, text)
            assert "no_economic_sections" in result.warnings, (heading, text)


# ---------------------------------------------------------------------------
# Phase 10 hardening — content anchors require context, never a bare token
# ---------------------------------------------------------------------------


def test_inflation_near_misses_require_inflation_context():
    result = EcbReportsExtractor().extract(
        reports_publication(),
        _doc_with_sections([_section("Prices and costs", "Core developments remained broadly based.")]),
    )
    assert result.facts == []


def test_inflation_context_variants_are_mined():
    sections = [
        _section("Prices and costs", "Inflation expectations increased."),
        _section("Prices and costs", "Core inflation increased."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert {f.subject for f in result.facts} == {SUBJECT_INFLATION_EXPECTATIONS, SUBJECT_CORE_INFLATION}


def test_growth_near_misses_require_growth_context():
    result = EcbReportsExtractor().extract(
        reports_publication(),
        _doc_with_sections([_section("Economic activity", "Output of financial institutions remained stable.")]),
    )
    assert result.facts == []


def test_growth_context_variants_are_mined():
    sections = [
        _section("Economic activity", "Economic activity remained broadly stable."),
        _section("Economic activity", "Real GDP growth increased."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert {f.subject for f in result.facts} == {SUBJECT_GROWTH}
    assert len(result.facts) == 2
    assert all(f.predicate == "assessment" for f in result.facts)


def test_gdp_near_misses_never_anchor_growth():
    sections = [
        _section("Economic activity", "The GDP deflator rose by 2.1% in 2026."),
        _section("Economic activity", "GDP per capita increased by 1.1% in 2026."),
        _section("Economic activity", "Per capita GDP increased by 1.1% in 2026."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert result.facts == []


def test_gdp_near_miss_never_leaks_into_a_growth_value():
    sections = [
        _section("Economic activity", "Real GDP growth held steady while the GDP deflator rose by 2.1%."),
        _section("Economic activity", "Growth remained solid and GDP per capita rose by 1.1%."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert result.facts == []


def test_gdp_value_facts_still_mined_when_not_a_near_miss():
    sections = [
        _section("Economic activity", "GDP growth is projected to reach 1.4% in 2027."),
        _section("Economic activity", "GDP increased by 0.4% in the first quarter of 2026."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    values = facts_by(result, SUBJECT_GDP, "value")
    assert {(f.value.value, period_of(f)) for f in values} == {(1.4, "year:2027"), (0.4, "quarter:2026-Q1")}


def test_financial_near_misses_require_financial_context():
    result = EcbReportsExtractor().extract(
        reports_publication(),
        _doc_with_sections([_section("Financial developments", "Non-financial corporations remained resilient.")]),
    )
    assert result.facts == []


def test_financial_context_variants_are_mined():
    sections = [
        _section("Financial developments", "Financial conditions tightened."),
        _section("Financial developments", "Bank lending weakened."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert {f.subject for f in result.facts} == {SUBJECT_FINANCIAL_CONDITIONS}
    assert len(result.facts) == 2
    assert all(f.predicate == "assessment" for f in result.facts)


def test_risk_near_misses_require_risk_context():
    result = EcbReportsExtractor().extract(
        reports_publication(),
        _doc_with_sections([_section("Risk assessment", "Risk management framework was strengthened.")]),
    )
    assert result.facts == []


def test_risk_context_variants_are_mined():
    sections = [
        _section("Risk assessment", "Downside risks increased."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert len(result.facts) == 1
    assert result.facts[0].subject == SUBJECT_RISK
    assert result.facts[0].predicate == "assessment"
    assert result.facts[0].value.value == "downside"
    assert result.facts[0].value.kind is ValueKind.CATEGORICAL


def test_policy_near_misses_require_policy_context():
    result = EcbReportsExtractor().extract(
        reports_publication(),
        _doc_with_sections([_section("Monetary policy developments", "Policy implementation continued at a steady pace.")]),
    )
    assert result.facts == []


def test_policy_context_variants_are_mined():
    sections = [
        _section("Monetary policy developments", "The monetary policy stance remained appropriately calibrated."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert len(result.facts) == 1
    assert result.facts[0].subject == SUBJECT_MONETARY_POLICY
    assert result.facts[0].predicate == "statement"
    assert result.facts[0].source_text == "The monetary policy stance remained appropriately calibrated."


def test_labour_near_misses_require_labour_context():
    result = EcbReportsExtractor().extract(
        reports_publication(),
        _doc_with_sections([_section("Labour market", "Employment policy remained stable.")]),
    )
    assert result.facts == []


def test_labour_context_variants_are_mined():
    sections = [
        _section("Labour market", "Employment increased further."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert len(result.facts) == 1
    assert result.facts[0].subject == SUBJECT_LABOUR_MARKET
    assert result.facts[0].predicate == "assessment"
    assert result.facts[0].value.kind is ValueKind.TEXT


# ---------------------------------------------------------------------------
# Phase 10 hardening — content-first precedence is fixed and deterministic
# ---------------------------------------------------------------------------


def test_content_precedence_is_fixed_and_unchanged():
    cases = [
        ("The Council stood ready to adjust its instruments within its mandate.", SUBJECT_POLICY_GUIDANCE),
        ("The monetary policy stance remained appropriately calibrated.", SUBJECT_MONETARY_POLICY),
        ("Downside risks increased.", SUBJECT_RISK),
        ("Financial conditions tightened.", SUBJECT_FINANCIAL_CONDITIONS),
        ("Inflation increased.", SUBJECT_INFLATION),
        ("Employment increased.", SUBJECT_LABOUR_MARKET),
        ("Economic activity remained stable.", SUBJECT_GROWTH),
        ("Fiscal policy is expected to remain neutral.", SUBJECT_FISCAL_POLICY),
    ]
    for sentence, subject in cases:
        result = EcbReportsExtractor().extract(
            reports_publication(), _doc_with_sections([_section("Overview", sentence)])
        )
        assert {f.subject for f in result.facts} == {subject}, sentence


def test_precedence_guidance_over_policy_risk_is_deterministic():
    sentence = (
        "The Council stood ready to adjust its instruments within its mandate, "
        "and downside risks to growth remained."
    )
    doc = _doc_with_sections([_section("Risk assessment", sentence)])
    first = EcbReportsExtractor().extract(reports_publication(), doc)
    second = EcbReportsExtractor().extract(reports_publication(), doc)
    assert [f.subject for f in first.facts] == [SUBJECT_POLICY_GUIDANCE]
    assert [f.subject for f in second.facts] == [SUBJECT_POLICY_GUIDANCE]


def test_content_near_miss_positives_carry_full_fact_fields():
    sections = [
        _section("Prices and costs", "Inflation expectations increased."),
        _section("Economic activity", "Real GDP growth increased."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    expectations = facts_by(result, SUBJECT_INFLATION_EXPECTATIONS, "assessment")[0]
    assert expectations.value.kind is ValueKind.TEXT
    assert expectations.value.source_text == "Inflation expectations increased."
    assert expectations.source_text == "Inflation expectations increased."
    assert expectations.period is None
    growth = facts_by(result, SUBJECT_GROWTH, "assessment")[0]
    assert growth.predicate == "assessment"
    assert growth.period is None
    assert growth.source_text == "Real GDP growth increased."


# ---------------------------------------------------------------------------
# content-first classification precedence
# ---------------------------------------------------------------------------


def test_guidance_outranks_policy_risk_and_growth():
    sections = [
        _section("Economic activity", "The Council stood ready to adjust its instruments within its mandate."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert len(result.facts) == 1
    assert result.facts[0].subject == SUBJECT_POLICY_GUIDANCE
    assert result.facts[0].predicate == "statement"
    assert "no_forward_guidance" not in result.warnings  # guidance was found


def test_policy_stance_requires_a_policy_term():
    sections = [
        _section("Economic activity", "The stance of the economy remained fragile."),
        _section("Economic activity", "The monetary policy stance remained appropriately calibrated."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    subjects = [f.subject for f in result.facts]
    assert SUBJECT_MONETARY_POLICY in subjects
    assert SUBJECT_GROWTH in subjects  # the "economy" sentence is growth, not policy


def test_risk_outranks_inflation_financial_and_growth():
    sections = [
        _section("Prices and costs", "Risks to inflation were tilted to the upside."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert len(result.facts) == 1
    assert result.facts[0].subject == SUBJECT_INFLATION_RISK


def test_financial_outranks_inflation():
    sections = [
        _section("Prices and costs", "Financial conditions are projected to ease gradually."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert len(result.facts) == 1
    assert result.facts[0].subject == SUBJECT_FINANCIAL_CONDITIONS
    assert result.facts[0].predicate == "assessment"


def test_fiscal_policy_is_never_misread_as_monetary_policy():
    sections = [
        _section("Fiscal developments", "Fiscal policy is expected to remain neutral over the projection horizon."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert len(result.facts) == 1
    assert result.facts[0].subject == SUBJECT_FISCAL_POLICY
    assert result.facts[0].predicate == "assessment"


def test_no_fact_from_neutral_sentences():
    sections = [
        _section("Economic activity", "The Governing Council will assess the outlook at its next meeting."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert result.facts == []


# ---------------------------------------------------------------------------
# inflation / growth / labour market subject resolution
# ---------------------------------------------------------------------------


def test_inflation_subjects_are_distinct():
    sections = [
        _section("Prices and costs", "Inflation is projected to average 2.0% in 2026."),
        _section("Prices and costs", "Core inflation is projected to average 2.3% in June 2026."),
        _section("Prices and costs", "Inflation expectations remained well anchored."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert {f.subject for f in result.facts} == {
        SUBJECT_INFLATION, SUBJECT_CORE_INFLATION, SUBJECT_INFLATION_EXPECTATIONS,
    }
    assert facts_by(result, SUBJECT_CORE_INFLATION, "value")[0].period.canonical() == "month:2026-06"


def test_plain_growth_sentence_is_qualitative():
    sections = [
        _section("Economic activity", "Economic activity continued to expand at a moderate pace."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert len(result.facts) == 1
    assert result.facts[0].subject == SUBJECT_GROWTH
    assert result.facts[0].predicate == "assessment"
    assert result.facts[0].value.kind is ValueKind.TEXT


def test_quantitative_growth_is_gdp_value():
    sections = [
        _section("Economic activity", "Real GDP is projected to grow by 1.2% in 2026."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert len(result.facts) == 1
    assert result.facts[0].subject == SUBJECT_GDP
    assert result.facts[0].predicate == "value"
    assert result.facts[0].value.value == 1.2


def test_labour_market_subjects():
    sections = [
        _section("Labour market", "The unemployment rate is projected to average 6.2% in 2026."),
        _section("Labour market", "Wage growth is projected to average 3.8% in 2026."),
        _section("Labour market", "The labour market remained resilient."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert {f.subject for f in result.facts} == {
        SUBJECT_UNEMPLOYMENT, SUBJECT_WAGES, SUBJECT_LABOUR_MARKET,
    }


# ---------------------------------------------------------------------------
# value gate: explicit value claims with explicit units and periods
# ---------------------------------------------------------------------------


def test_value_requires_an_explicit_value_claim_verb():
    sections = [
        _section("Prices and costs", "Inflation stood at 2.4% in 2025."),
        _section("Prices and costs", "Inflation is 2.4% in 2025."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    value_facts = facts_by(result, SUBJECT_INFLATION, "value")
    assert len(value_facts) == 1  # only "stood at" is a value claim
    assert value_facts[0].value.value == 2.4


def test_periods_come_from_the_sentence_wording():
    sections = [
        _section("Prices and costs", "Inflation is projected to average 2.4% in 2025."),
        _section("Prices and costs", "Inflation is projected to average 2.4% in June 2025."),
        _section("Prices and costs", "Inflation is projected to average 2.4% in the first quarter of 2025."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    periods = {period_of(f) for f in facts_by(result, SUBJECT_INFLATION, "value")}
    assert periods == {"year:2025", "month:2025-06", "quarter:2025-Q1"}


def test_forecast_without_explicit_period_is_ignored():
    sections = [
        _section("Prices and costs", "Inflation is projected to average 2.4%."),
        _section("Prices and costs", "Inflation averaged 2.4% in 2025."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    value_facts = facts_by(result, SUBJECT_INFLATION, "value")
    assert len(value_facts) == 1  # the period-less forecast is under-determined and ignored
    assert value_facts[0].period.canonical() == "year:2025"


def test_share_units_are_never_percentages():
    sections = [
        _section("Fiscal developments", "The general government deficit stood at 3.0% of GDP in 2026."),
        _section("Fiscal developments", "Public debt is projected to decline to 2.0% of total debt."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert not any(f.value.kind is ValueKind.PERCENTAGE for f in result.facts)


def test_basis_points_are_not_extracted_as_percentages():
    sections = [
        _section("Financial developments", "Market rates rose by 25 basis points."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert not any(f.value.kind in (ValueKind.PERCENTAGE, ValueKind.BASIS_POINTS) for f in result.facts)


def test_value_facts_carry_percentage_kind_and_verbatim_token():
    result = extract_fixture("ecb_report.html")
    gdp = next(f for f in facts_by(result, SUBJECT_GDP, "value") if period_of(f) == "quarter:2026-Q1")
    assert gdp.value.kind is ValueKind.PERCENTAGE
    assert gdp.value.value == 0.4
    assert gdp.value.source_text == "0.4%"
    assert gdp.period.label == "in the first quarter of 2026"


# ---------------------------------------------------------------------------
# risk facts
# ---------------------------------------------------------------------------


def test_risk_orientation_is_categorical_high_confidence():
    result = extract_fixture("ecb_report.html")
    growth_risk = facts_by(result, SUBJECT_GROWTH_RISK, "assessment")[0]
    assert growth_risk.value.kind is ValueKind.CATEGORICAL
    assert growth_risk.value.value == "balanced"
    assert growth_risk.confidence is Confidence.HIGH


def test_risk_target_read_from_wording():
    sections = [
        _section("Risk assessment", "Risks to inflation were tilted to the upside."),
        _section("Risk assessment", "Risks to the growth outlook were tilted to the downside."),
        _section("Risk assessment", "Risks were broadly balanced."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert {f.subject for f in result.facts} == {SUBJECT_INFLATION_RISK, SUBJECT_GROWTH_RISK, SUBJECT_RISK}
    orientations = {f.subject: f.value.value for f in result.facts}
    assert orientations == {
        SUBJECT_INFLATION_RISK: "upside",
        SUBJECT_GROWTH_RISK: "downside",
        SUBJECT_RISK: "balanced",
    }


# ---------------------------------------------------------------------------
# tables: variable × year × value × unit integrity
# ---------------------------------------------------------------------------


def test_table_facts_pin_row_column_and_year():
    result = extract_fixture("ecb_report_tables.html")
    hicp_2025 = next(f for f in facts_by(result, SUBJECT_INFLATION, "value") if period_of(f) == "year:2025")
    assert hicp_2025.source_location.kind is LocationKind.TABLE
    assert hicp_2025.source_location.table == 0
    assert hicp_2025.source_location.row == 0
    assert hicp_2025.source_location.column == 2  # Variable=0, 2024=1, 2025=2
    assert hicp_2025.period.label == "2025"
    assert hicp_2025.value.source_text == "2.0"
    assert hicp_2025.value.kind is ValueKind.PERCENTAGE


def test_table_unit_missing_is_ignored():
    table = _table("Table 1 — Key macroeconomic variables", [["HICP", "2.4", "2.0", "1.9"]])
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_tables([table]))
    assert result.facts == []


def test_table_unit_share_or_incompatible_is_ignored_never_converted():
    for caption in (
        "Table 1 — General government deficit (% of GDP)",
        "Table 1 — Government debt (% of total)",
        "Table 1 — HICP index (index 2015 = 100)",
        "Table 1 — Assumptions (USD/barrel)",
    ):
        table = _table(caption, [["HICP", "2.4", "2.0", "1.9"]])
        result = EcbReportsExtractor().extract(reports_publication(), _doc_with_tables([table]))
        assert result.facts == [], caption


def test_table_unit_variants_are_accepted():
    for caption in (
        "Table 1 — Projections (annual percentage changes)",
        "Table 1 — Projections (percentage changes)",
        "Table 1 — Projections (percent)",
        "Table 1 — Projections (per cent)",
        "Table 1 — Projections (%)",
        "Table 1 — Projections, % growth",
    ):
        table = _table(caption, [["HICP", "2.4", "2.0", "1.9"]])
        result = EcbReportsExtractor().extract(reports_publication(), _doc_with_tables([table]))
        assert len(result.facts) == 3, caption
        assert all(f.value.kind is ValueKind.PERCENTAGE for f in result.facts), caption


def test_table_columns_without_years_are_not_value_columns():
    table = DocumentTable(
        order=0,
        name="Scenario assumptions",
        headers=["Variable", "Baseline", "Adverse", "Severe"],
        rows=[["HICP", "2.0", "2.2", "2.4"], ["Real GDP", "1.2", "0.8", "0.4"]],
    )
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_tables([table]))
    assert result.facts == []


def test_table_unit_does_not_leak_across_tables():
    authorised = _table("Table 1 — Projections (annual percentage changes)", [["HICP", "2.0", "1.9", "1.8"]])
    unitless = _table("Table 2 — Projections", [["HICP", "3.0", "2.9", "2.8"]])
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_tables([authorised, unitless]))
    assert len(result.facts) == 3
    assert {(f.value.value, period_of(f)) for f in result.facts} == {
        (2.0, "year:2024"), (1.9, "year:2025"), (1.8, "year:2026")
    }
    assert all(f.source_location.table == 0 for f in result.facts)


def test_table_unrecognised_variable_rows_are_ignored():
    table = _table(
        "Table 1 — Projections (annual percentage changes)",
        [
            ["HICP", "2.0", "1.9", "1.8"],
            ["Private consumption", "1.0", "1.1", "1.2"],
            ["Oil price (USD/barrel)", "60", "62", "63"],
        ],
    )
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_tables([table]))
    assert {f.subject for f in result.facts} == {SUBJECT_INFLATION}
    assert len(result.facts) == 3


def test_table_exact_match_survives_footnote_markers():
    table = _table("Table 1 — Projections (annual percentage changes)", [["HICP 1)", "2.0", "1.9", "1.8"]])
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_tables([table]))
    assert len(result.facts) == 3
    assert all(f.subject == SUBJECT_INFLATION for f in result.facts)


def test_table_placeholder_cells_are_ignored():
    table = _table(
        "Table 1 — Projections (annual percentage changes)",
        [["HICP", "", "1.9", "…"], ["Real GDP", "–", "1.2", "n.a."]],
    )
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_tables([table]))
    assert {period_of(f) for f in result.facts} == {"year:2025"}
    assert {(f.subject, f.value.value) for f in result.facts} == {
        (SUBJECT_INFLATION, 1.9), (SUBJECT_GDP, 1.2),
    }


def test_table_share_gated_fixture_ignores_extra_tables():
    result = extract_fixture("ecb_report_tables.html")
    table_facts = [f for f in result.facts if f.source_location.kind is LocationKind.TABLE]
    assert len(table_facts) == 14  # Table 2 (no unit) and Table 3 (% of GDP) contribute nothing
    assert {f.source_location.table for f in table_facts} == {0}
    assert len(result.facts) == 16


# ---------------------------------------------------------------------------
# within-run deduplication
# ---------------------------------------------------------------------------


def test_identical_assertion_across_sections_is_emitted_once():
    sections = [
        _section("1 Overview", "Inflation is projected to average 2.0% in 2026."),
        _section("Prices and costs", "Inflation is projected to average 2.0% in 2026."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert len(result.facts) == 1


def test_prose_value_dedups_with_table_value():
    result = extract_fixture("ecb_report_tables.html")
    assert len(facts_by(result, SUBJECT_INFLATION, "value")) == 4  # 2.4/2024 prose, 2.0/2027 prose, 2.0/2025, 1.9/2026
    assert (SUBJECT_INFLATION, "year:2024") not in {
        (f.subject, period_of(f)) for f in facts_by(result, SUBJECT_INFLATION, "value") if f.source_location.kind is LocationKind.TABLE
    }
    # the prose source (section) survives as the provenance of the deduped value
    prose_2024 = next(f for f in facts_by(result, SUBJECT_INFLATION, "value") if period_of(f) == "year:2024")
    assert prose_2024.source_location.kind is LocationKind.SECTION
    assert prose_2024.source_location.section == 2


def test_distinct_assertions_with_same_value_are_kept():
    sections = [
        _section("Prices and costs", "Inflation is projected to average 2.0% in 2026."),
        _section("Prices and costs", "Inflation expectations are projected to average 2.0% in 2026."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    subjects = {f.subject for f in result.facts}
    assert SUBJECT_INFLATION in subjects and SUBJECT_INFLATION_EXPECTATIONS in subjects
    assert len(result.facts) == 2


def test_risk_sentences_with_same_orientation_but_different_text_both_kept():
    sections = [
        _section("Risk assessment", "Risks to the growth outlook were tilted to the downside."),
        _section("Risk assessment", "The risks to growth were assessed as being on the downside."),
    ]
    result = EcbReportsExtractor().extract(reports_publication(), _doc_with_sections(sections))
    assert len(facts_by(result, SUBJECT_GROWTH_RISK, "assessment")) == 2


# ---------------------------------------------------------------------------
# provenance + no interpretation
# ---------------------------------------------------------------------------


def test_provenance_is_traceable():
    for name in GOLDEN:
        document = normalized_fixture(name)
        result = extract_fixture(name)
        for fact in result.facts:
            assert fact.extraction_version == EcbReportsExtractor.extraction_version
            assert fact.publication_id == "pub-ecb-report"
            assert fact.document_id
            assert fact.effective_date is None
            assert fact.speaker is None
            assert fact.identity_qualifier.startswith("report:")
            if fact.source_location.kind is LocationKind.SECTION:
                assert fact.source_text in document.sections[fact.source_location.section].text
            else:
                table = document.tables[fact.source_location.table]
                assert fact.source_text == " | ".join(str(cell or "") for cell in table.rows[fact.source_location.row])
                assert fact.value.source_text in fact.source_text


def test_speaker_never_invented():
    for name in GOLDEN:
        result = extract_fixture(name)
        assert all(f.speaker is None for f in result.facts), name


def test_no_hawkish_dovish_or_forex_interpretation():
    for name in GOLDEN:
        result = extract_fixture(name)
        for fact in result.facts:
            raw = str(fact.value.value or "").lower()
            assert "hawkish" not in raw and "dovish" not in raw
            assert "bullish" not in raw and "bearish" not in raw
            assert "forex" not in raw and "eur/usd" not in raw
            assert fact.predicate not in ("sentiment", "market_reaction", "decision")


def test_policy_narrative_is_verbatim_never_priced():
    result = extract_fixture("ecb_report.html")
    policy = facts_by(result, SUBJECT_MONETARY_POLICY, "statement")
    assert len(policy) == 1
    assert policy[0].value.kind is ValueKind.TEXT
    assert policy[0].value.value == "The Governing Council decided to keep its key interest rates unchanged."
    assert policy[0].predicate != "decision"
    assert policy[0].value.value != "unchanged"  # never turned into a categorical outcome


# ---------------------------------------------------------------------------
# warnings + empty documents
# ---------------------------------------------------------------------------


def test_empty_document_warns_no_sections():
    doc = NormalizedDocument(
        publication_id="pub-ecb-report",
        document_id="sha-empty",
        source_url=ECB_URL,
        local_path=None,
        document_kind="html",
        sections=[],
        tables=[],
    )
    result = EcbReportsExtractor().extract(reports_publication(), doc)
    assert result.warnings == ["no_sections"]
    assert result.facts == []


def test_unknown_fixture_warns_all_three():
    result = extract_fixture("ecb_report_unknown.html")
    assert result.warnings == ["no_economic_sections", "no_risk_assessment", "no_forward_guidance"]


def test_tables_fixture_warns_missing_risk_and_guidance():
    result = extract_fixture("ecb_report_tables.html")
    assert result.warnings == ["no_risk_assessment", "no_forward_guidance"]


# ---------------------------------------------------------------------------
# determinism + idempotent persistence (vertical slice)
# ---------------------------------------------------------------------------


def _store_report(tmp_path, name: str = "ecb_report.html") -> Store:
    store = Store(tmp_path / f"{name}.db")
    store.upsert_publication(reports_publication())
    store.upsert_normalized_document(normalized_fixture(name))
    return store


def classify_report(store: Store, *, publication_type: str = "monetary_policy_report") -> None:
    store.set_classification(
        "pub-ecb-report",
        central_bank="ecb",
        publication_type=publication_type,
        confidence=Confidence.HIGH.value,
        method="url_pattern",
        evidence=[],
    )


class _ZeroFactReportsExtractor(ReportsExtractor):
    """Stub report extractor that yields no facts — used to simulate a
    re-extraction of an already-persisted document that now produces nothing."""

    bank = "ecb"
    extraction_version = "test-zero"

    def extract(self, publication, document):
        return ExtractionResult(
            publication_id=publication.id or publication.dedup_key or "",
            document_id=document.document_id,
        )


def test_extract_report_persists_facts(tmp_path):
    store = _store_report(tmp_path)
    classify_report(store)
    results = extract_report(store, reports_publication())
    assert len(results) == 1
    persisted = store.get_facts(publication_id="pub-ecb-report")
    assert len(persisted) == 17
    assert all(f.central_bank == "ecb" for f in persisted)
    assert all(f.extraction_version == EcbReportsExtractor.extraction_version for f in persisted)
    gdp = next(f for f in persisted if f.subject == SUBJECT_GDP and period_of(f) == "quarter:2026-Q1")
    assert gdp.value.kind.value == "percentage"
    assert gdp.value.value == 0.4


def test_extract_report_is_idempotent(tmp_path):
    store = _store_report(tmp_path)
    classify_report(store)
    pub = reports_publication()
    extract_report(store, pub)
    first = sorted((f.fact_id, f.subject, f.predicate, period_of(f), f.value.value) for f in store.get_facts(publication_id="pub-ecb-report"))
    extract_report(store, pub)  # re-run: same deterministic fact_ids
    second = sorted((f.fact_id, f.subject, f.predicate, period_of(f), f.value.value) for f in store.get_facts(publication_id="pub-ecb-report"))
    assert first == second
    assert len(second) == 17


def test_extraction_is_deterministic(tmp_path):
    for name in GOLDEN:
        store = _store_report(tmp_path, name)
        result = extract_fixture(name)
        store.rebuild_facts_for_document(result.document_id, result)
        first = sorted((f.fact_id, f.subject, f.predicate, period_of(f), f.value.value) for f in store.get_facts(publication_id="pub-ecb-report"))
        store.rebuild_facts_for_document(result.document_id, result)
        second = sorted((f.fact_id, f.subject, f.predicate, period_of(f), f.value.value) for f in store.get_facts(publication_id="pub-ecb-report"))
        assert first == second, name
        assert len(first) == len(result.facts), name
        ids = [f.fact_id for f in result.facts]
        assert len(ids) == len(set(ids)), name  # no fact_id collisions


def test_identity_qualifiers_are_ordinal_and_deterministic():
    result = extract_fixture("ecb_report.html")
    growth = facts_by(result, SUBJECT_GROWTH, "assessment")
    assert [f.identity_qualifier for f in growth] == ["report:growth:0", "report:growth:1"]
    assert all(f.identity_qualifier.startswith("report:") for f in result.facts)


# ---------------------------------------------------------------------------
# classification gating (single source of truth = classifications table)
# ---------------------------------------------------------------------------


def test_gating_report_classification_allows_extraction(tmp_path):
    store = _store_report(tmp_path)
    classify_report(store, publication_type="monetary_policy_report")
    results = extract_report(store, reports_publication())
    assert len(results) == 1
    assert len(store.get_facts(publication_id="pub-ecb-report")) == 17


def test_gating_other_classification_refuses_extraction(tmp_path):
    store = _store_report(tmp_path)
    classify_report(store, publication_type="press_conference")
    assert extract_report(store, reports_publication()) == []
    assert store.get_facts(publication_id="pub-ecb-report") == []


def test_gating_absent_classification_refuses_extraction(tmp_path):
    store = _store_report(tmp_path)
    assert extract_report(store, reports_publication()) == []
    assert store.get_facts(publication_id="pub-ecb-report") == []


def test_gating_publication_type_cache_alone_never_authorizes(tmp_path):
    store = _store_report(tmp_path)
    pub = reports_publication(publication_type="monetary_policy_report")
    # the denormalized cache says monetary_policy_report, but there is no
    # authoritative classification record -> extraction must be refused
    assert extract_report(store, pub) == []
    assert store.get_facts(publication_id="pub-ecb-report") == []


def test_gating_batch_respects_classification(tmp_path):
    store = _store_report(tmp_path)
    assert extract_report_batch(store) == []  # unclassified -> nothing extracted
    assert store.get_facts(publication_id="pub-ecb-report") == []
    classify_report(store)
    results = extract_report_batch(store)
    assert len(results) == 1
    assert len(store.get_facts(bank="ecb")) == 17


def test_gating_never_persists_facts_when_not_authorized(tmp_path):
    store = _store_report(tmp_path)
    classify_report(store, publication_type="economic_projections")
    assert extract_report(store, reports_publication()) == []
    assert extract_report_batch(store) == []
    assert store.get_facts(publication_id="pub-ecb-report") == []


def test_gating_refusal_never_deletes_existing_facts(tmp_path):
    """A classification that refuses extraction must NOT delete facts that an
    earlier authorized extraction persisted (X-1)."""
    store = _store_report(tmp_path)
    classify_report(store)
    assert len(extract_report(store, reports_publication())) == 1
    assert len(store.get_facts(publication_id="pub-ecb-report")) == 17
    classify_report(store, publication_type="economic_projections")
    assert extract_report(store, reports_publication()) == []
    assert len(store.get_facts(publication_id="pub-ecb-report")) == 17


def test_report_publication_types_are_recognized():
    assert REPORT_PUBLICATION_TYPES == ("monetary_policy_report",)


# ---------------------------------------------------------------------------
# empty-result persistence: the current extraction result is the source of truth
# ---------------------------------------------------------------------------


def test_empty_result_persistence_clears_stale_facts(tmp_path):
    store = _store_report(tmp_path)
    classify_report(store)
    pub = reports_publication()
    extract_report(store, pub)
    assert len(store.get_facts(publication_id="pub-ecb-report")) == 17
    results = extract_report(store, pub, extractor=_ZeroFactReportsExtractor())
    assert len(results) == 1
    assert results[0].facts == []
    assert store.get_facts(publication_id="pub-ecb-report") == []


def test_empty_result_persistence_preserves_other_documents(tmp_path):
    store = _store_report(tmp_path)
    classify_report(store)
    pub = reports_publication()
    extract_report(store, pub)
    extract_report(store, pub, document=normalized_fixture("ecb_report_minimal.html"))
    assert len(store.get_facts(publication_id="pub-ecb-report")) == 18
    # zero-out only the nominal document; the other document's facts must stay
    extract_report(
        store, pub, document=normalized_fixture("ecb_report.html"),
        extractor=_ZeroFactReportsExtractor(),
    )
    persisted = store.get_facts(publication_id="pub-ecb-report")
    assert len(persisted) == 1
    assert persisted[0].document_id == normalized_fixture("ecb_report_minimal.html").document_id
    assert persisted[0].subject == SUBJECT_INFLATION


def test_empty_result_persistence_is_idempotent(tmp_path):
    store = _store_report(tmp_path)
    classify_report(store)
    pub = reports_publication()
    extract_report(store, pub)
    assert len(store.get_facts(publication_id="pub-ecb-report")) == 17
    zero = _ZeroFactReportsExtractor()
    extract_report(store, pub, extractor=zero)
    extract_report(store, pub, extractor=zero)
    assert store.get_facts(publication_id="pub-ecb-report") == []


# ---------------------------------------------------------------------------
# Phase 5 / 6 / 7 / 8 / 9 coexistence
# ---------------------------------------------------------------------------


def test_other_extractors_do_not_overlap_with_reports(tmp_path):
    """A monetary policy report publication never feeds the decision, statement,
    press conference, minutes or projections extractors (gating on
    classification), and Phase 10 never emits Phase 5/6/7/8/9 fact subjects."""
    store = _store_report(tmp_path)
    pub = reports_publication()
    classify_report(store)
    # store-level helpers are gated on classification
    assert extract_decision(store, pub) == []
    assert extract_statement(store, pub) == []
    assert extract_press_conference(store, pub) == []
    assert extract_minutes(store, pub) == []
    assert extract_projections(store, pub) == []
    # Phase 10 extraction produces its own facts only
    extract_report(store, pub)
    persisted = store.get_facts(publication_id="pub-ecb-report")
    phase_subjects = {
        "monetary_policy_decision", "main_refinancing_rate", "marginal_lending_rate",
        "deposit_facility_rate", "asset_purchase", "vote",
    }
    assert not phase_subjects & {f.subject for f in persisted}
    assert all(f.predicate in ("assessment", "statement", "value") for f in persisted)
    assert all(f.extraction_version == EcbReportsExtractor.extraction_version for f in persisted)
    assert all(f.identity_qualifier.startswith("report:") for f in persisted)


def test_reports_extractor_refuses_other_publication_types(tmp_path):
    store = _store_report(tmp_path)
    classify_report(store, publication_type="meeting_account")
    assert extract_report(store, reports_publication()) == []
    assert store.get_facts(publication_id="pub-ecb-report") == []


# ---------------------------------------------------------------------------
# generic dispatch integration tests (Phase 4 hardening)
# ---------------------------------------------------------------------------


def test_get_reports_extractor_resolves_registered_banks():
    """Verify the generic registry resolves the correct extractor for each bank."""
    from argus.reports import get_extractor

    expected = {
        "ecb": "EcbReportsExtractor",
        "norges": "NorgesReportExtractor",
        "boe": "BoeReportExtractor",
        "boc": "BocReportExtractor",
        "rba": "RbaReportExtractor",
        "rbnz": "RbnzReportExtractor",
    }
    for bank, class_name in expected.items():
        ext = get_extractor(bank)
        assert ext is not None, f"{bank}: extractor not registered"
        assert ext.__class__.__name__ == class_name, f"{bank}: wrong extractor {ext.__class__.__name__}"

    # Banks with no monetary_policy_report publication type (per taxonomy)
    for bank in ("fed", "boj", "snb", "riksbank"):
        assert get_extractor(bank) is None, f"{bank}: should not have report extractor"


REPORTS_FIXTURE_MAP = {
    "ecb": "ecb_report.html",
    "norges": "norges_mpr.html",
    "boe": "boe_report.html",
    "boc": "boc_report.html",
    "rba": "rba_report.html",
    "rbnz": "rbnz_report.html",
}


def _normalized_reports_fixture(bank: str, name: str):
    from argus.documents import Normalizer
    from argus.models import Document, DocumentStatus
    return Normalizer().parse(
        Document(
            publication_id=f"pub-{bank}-report",
            url=f"https://example.com/{bank}/{name}",
            kind="html",
            status=DocumentStatus.FETCHED,
            local_path=str(FIXTURES / name),
        )
    )


def _reports_publication(bank: str, pub_id: str = None) -> Publication:
    return Publication(
        central_bank=bank,
        title="Monetary policy report",
        url=f"https://example.com/{bank}/report",
        source_id=f"{bank}-report",
        source_url=f"https://example.com/{bank}/feed.xml",
        id=pub_id or f"pub-{bank}-report",
    )


def _classify_report(store: Store, pub_id: str, bank: str) -> None:
    store.set_classification(
        pub_id,
        central_bank=bank,
        publication_type="monetary_policy_report",
        confidence=Confidence.HIGH.value,
        method="url_pattern",
        evidence=[],
    )


@pytest.mark.parametrize("bank", list(REPORTS_FIXTURE_MAP.keys()))
def test_extract_report_generic_dispatch(tmp_path, bank):
    """Test the generic extract_report dispatch for each registered bank."""
    store = Store(tmp_path / f"{bank}_report.db")
    pub = _reports_publication(bank)
    store.upsert_publication(pub)
    doc = _normalized_reports_fixture(bank, REPORTS_FIXTURE_MAP[bank])
    store.upsert_normalized_document(doc)
    _classify_report(store, pub.id, bank)

    results = extract_report(store, pub)
    assert len(results) == 1, f"{bank}: expected 1 result"
    result = results[0]
    assert result.publication_id == pub.id
    assert result.document_id == doc.document_id

    # Verify some facts were produced
    assert len(result.facts) > 0, f"{bank}: no facts extracted"

    # Verify provenance is preserved
    for fact in result.facts:
        assert fact.extraction_version
        assert fact.extraction_method
        assert fact.source_location is not None
        assert fact.source_text
        assert fact.confidence is not None
        # ECB uses "report:<subject>:<n>", Norges uses "<subject>:<n>"
        # Just verify identity_qualifier is well-formed when present
        if fact.identity_qualifier:
            assert ":" in fact.identity_qualifier, f"malformed identity_qualifier: {fact.identity_qualifier}"


def test_extract_report_batch_generic_dispatch(tmp_path):
    """Test extract_report_batch runs all classified reports via generic dispatch."""
    store = Store(tmp_path / "batch_reports.db")
    for bank in REPORTS_FIXTURE_MAP:
        pub = _reports_publication(bank, pub_id=f"pub-{bank}-report")
        store.upsert_publication(pub)
        doc = _normalized_reports_fixture(bank, REPORTS_FIXTURE_MAP[bank])
        store.upsert_normalized_document(doc)
        _classify_report(store, pub.id, bank)

    results = extract_report_batch(store)
    assert len(results) == len(REPORTS_FIXTURE_MAP)

    for bank in REPORTS_FIXTURE_MAP:
        facts = store.get_facts(publication_id=f"pub-{bank}-report")
        assert facts, f"{bank}: no facts persisted"
        # ECB uses "report:<subject>:<n>", Norges uses "<subject>:<n>"
        for f in facts:
            if f.identity_qualifier:
                assert ":" in f.identity_qualifier, f"malformed identity_qualifier: {f.identity_qualifier}"
