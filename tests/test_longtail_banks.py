"""Long-tail central bank extractors — golden facts over the local fixtures.

Each fixture for the additional banks (fed: decision / statement / minutes /
SEP projections; boe, boc, snb, rba, rbnz, norges, riksbank: decision and
statement / report; boj: statement) is verified end-to-end through the
Normalizer and the type-specific extractor. Assertions cover the exact golden
fact set (subject / predicate / value kind / value / period), provenance
(source text inside the owning section or table row), determinism and the
cross-phase separation guarantees.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from argus.classification.base import Confidence
from argus.decisions.boe import BoeDecisionExtractor
from argus.decisions.boc import BocDecisionExtractor
from argus.decisions.fed import FedDecisionExtractor
from argus.decisions.norges import NorgesDecisionExtractor
from argus.decisions.rba import RbaDecisionExtractor
from argus.decisions.rbnz import RbnzDecisionExtractor
from argus.decisions.riksbank import RiksbankDecisionExtractor
from argus.decisions.snb import SnbDecisionExtractor
from argus.documents import Normalizer
from argus.facts import LocationKind, ValueKind
from argus.minutes import FedMinutesExtractor
from argus.models import Document, DocumentStatus
from argus.projections import FedSepExtractor
from argus.reports import NorgesReportExtractor
from argus.statements import (
    BoeStatementExtractor,
    BojStatementExtractor,
    BocStatementExtractor,
    FedStatementExtractor,
    RbaStatementExtractor,
    RbnzStatementExtractor,
    RiksbankStatementExtractor,
    SnbStatementExtractor,
)

FIXTURES = Path(__file__).parent / "fixtures" / "documents"

CASES = [
    ("fed_decision.html", FedDecisionExtractor),
    ("fed_statement_econ.html", FedStatementExtractor),
    ("fed_minutes.html", FedMinutesExtractor),
    ("fed_sep.html", FedSepExtractor),
    ("boe_decision.html", BoeDecisionExtractor),
    ("boe_statement_econ.html", BoeStatementExtractor),
    ("boc_decision.html", BocDecisionExtractor),
    ("boc_statement.html", BocStatementExtractor),
    ("snb_decision.html", SnbDecisionExtractor),
    ("snb_statement.html", SnbStatementExtractor),
    ("rba_decision.html", RbaDecisionExtractor),
    ("rba_statement.html", RbaStatementExtractor),
    ("rbnz_decision.html", RbnzDecisionExtractor),
    ("rbnz_statement.html", RbnzStatementExtractor),
    ("norges_decision.html", NorgesDecisionExtractor),
    ("norges_mpr.html", NorgesReportExtractor),
    ("riksbank_decision.html", RiksbankDecisionExtractor),
    ("riksbank_statement.html", RiksbankStatementExtractor),
    ("boj_statement.html", BojStatementExtractor),
]

# Generic decision instrument subjects used by the decision extractors.
DECISION_INSTRUMENTS = {"policy_rate", "bank_rate", "cash_rate", "official_cash_rate"}


def _publication(name: str) -> SimpleNamespace:
    return SimpleNamespace(id=f"pub-{name}", dedup_key=None)


def _normalized(name: str):
    document = Document(
        publication_id=f"pub-{name}",
        url=f"https://example.org/{name}",
        kind="html",
        status=DocumentStatus.FETCHED,
        local_path=str(FIXTURES / name),
    )
    return Normalizer().parse(document)


def _extract(name: str, cls):
    return cls().extract(_publication(name), _normalized(name))


def _signature(fact) -> tuple:
    kind = fact.value.kind.value if hasattr(fact.value.kind, "value") else fact.value.kind
    return (
        fact.subject,
        fact.predicate,
        kind,
        fact.value.value,
        fact.value.min,
        fact.value.max,
        fact.period.canonical() if fact.period else None,
    )


# ---------------------------------------------------------------------------
# golden facts, transcribed from the verified extractor output
# ---------------------------------------------------------------------------

GOLDEN: dict[str, list[tuple]] = {
    "fed_decision.html": [
        ("monetary_policy_decision", "date", "date", "2026-09-18", None, None, None),
        ("policy_rate", "value", "range", None, 4.5, 4.75, None),
        ("policy_rate", "change", "basis_points", -25.0, None, None, None),
        ("monetary_policy_decision", "statement", "text", "In light of these developments, the Federal Open Market Committee decided to lower the target range for the federal funds rate by 25 basis points to 4.50 to 4.75 percent.", None, None, None),
        ("policy_guidance", "statement", "text", "In assessing the appropriate stance of monetary policy, the Committee will carefully assess incoming data, the evolving outlook, and the balance of risks.", None, None, None),
        ("policy_guidance", "statement", "text", "The Committee's decisions will be data-dependent.", None, None, None),
    ],
    "fed_statement_econ.html": [
        ("monetary_policy", "date", "date", "2026-09-18", None, None, None),
        ("growth", "assessment", "text", "Recent indicators suggest that economic activity continued to expand at a solid pace.", None, None, None),
        ("growth", "assessment", "text", "Job gains have remained strong, and the unemployment rate has stayed low.", None, None, None),
        ("gdp", "value", "percentage", 1.8, None, None, "year:2026"),
        ("inflation", "assessment", "text", "Inflation has eased over the past year but remains somewhat elevated.", None, None, None),
        ("inflation", "value", "percentage", 2.4, None, None, "year:2026"),
        ("inflation_expectations", "assessment", "text", "Inflation expectations remain well anchored.", None, None, None),
        ("risk", "assessment", "text", "The Committee remains attentive to the risks to both sides of its dual mandate.", None, None, None),
        ("policy_guidance", "statement", "text", "In assessing the appropriate stance of monetary policy, the Committee will carefully assess incoming data, the evolving outlook, and the balance of risks.", None, None, None),
        ("policy_guidance", "statement", "text", "The Committee's decisions will be data-dependent.", None, None, None),
    ],
    "fed_minutes.html": [
        ("growth", "assessment", "text", "The staff forecast that real GDP increased at a moderate pace through the third quarter.", None, None, None),
        ("inflation", "value", "percentage", 2.4, None, None, None),
        ("labour_market", "assessment", "text", "Conditions in the labor market remained tight, with job gains solid.", None, None, None),
        ("inflation", "value", "percentage", 2.2, None, None, "year:2027"),
        ("risk", "assessment", "text", "Most participants agreed that the risks to the outlook were moving into better balance.", None, None, None),
        ("monetary_policy", "statement", "text", "One participant preferred to keep the target range unchanged for a longer period.", None, None, None),
        ("monetary_policy", "statement", "text", "The Committee judged that it would not be appropriate to lower the target range until it had gained greater confidence that inflation is moving sustainably toward 2 percent.", None, None, None),
        ("policy_guidance", "statement", "text", "Participants agreed that future determinations of monetary policy would depend on the incoming data.", None, None, None),
        ("inflation", "assessment", "text", "All participants affirmed their strong commitment to returning inflation to the Committee's 2 percent objective.", None, None, None),
        ("financial_conditions", "assessment", "text", "Financial conditions eased somewhat over the intermeeting period, and spreads in credit markets remained narrow.", None, None, None),
    ],
    "fed_sep.html": [
        ("gdp", "projection", "percentage", 1.8, None, None, "year:2026"),
        ("gdp", "projection", "percentage", 2.0, None, None, "year:2027"),
        ("gdp", "projection", "percentage", 2.5, None, None, "year:2028"),
        ("unemployment", "projection", "percentage", 4.1, None, None, "year:2026"),
        ("unemployment", "projection", "percentage", 4.0, None, None, "year:2027"),
        ("unemployment", "projection", "percentage", 3.9, None, None, "year:2028"),
        ("inflation", "projection", "percentage", 2.4, None, None, "year:2026"),
        ("inflation", "projection", "percentage", 2.2, None, None, "year:2027"),
        ("inflation", "projection", "percentage", 2.0, None, None, "year:2028"),
        ("core_inflation", "projection", "percentage", 2.6, None, None, "year:2026"),
        ("core_inflation", "projection", "percentage", 2.3, None, None, "year:2027"),
        ("core_inflation", "projection", "percentage", 2.0, None, None, "year:2028"),
        ("policy_rate", "projection", "percentage", 4.6, None, None, "year:2026"),
        ("policy_rate", "projection", "percentage", 3.9, None, None, "year:2027"),
        ("policy_rate", "projection", "percentage", 3.5, None, None, "year:2028"),
    ],
    "boe_decision.html": [
        ("monetary_policy_decision", "date", "date", "2026-08-20", None, None, None),
        ("bank_rate", "value", "percentage", 4.75, None, None, None),
        ("bank_rate", "change", "basis_points", -25.0, None, None, None),
        ("monetary_policy_decision", "statement", "text", "20 August 2026\nAt its meeting ending on 20 August 2026, the Committee voted by a majority of 7 to 2 to reduce Bank Rate by 0.25 percentage points to 4.75%.", None, None, None),
        ("policy_guidance", "statement", "text", "The Committee will continue to monitor the impact of its policy on inflation.", None, None, None),
        ("policy_guidance", "statement", "text", "The Committee's approach will be data-dependent.", None, None, None),
    ],
    "boe_statement_econ.html": [
        ("monetary_policy", "date", "date", "2026-08-20", None, None, None),
        ("inflation", "assessment", "text", "CPI inflation has fallen sharply from its peak.", None, None, None),
        ("inflation", "value", "percentage", 2.0, None, None, "year:2027"),
        ("inflation_expectations", "assessment", "text", "Inflation expectations remain anchored.", None, None, None),
        ("gdp", "value", "percentage", 1.5, None, None, "year:2027"),
        ("growth", "assessment", "text", "Domestic demand remains resilient.", None, None, None),
        ("wages", "value", "percentage", 3.2, None, None, "year:2026"),
        ("unemployment", "assessment", "text", "Unemployment remains low.", None, None, None),
        ("inflation_risk", "assessment", "categorical", "balanced", None, None, None),
        ("growth_risk", "assessment", "text", "There remains uncertainty surrounding global activity.", None, None, None),
        ("policy_guidance", "statement", "text", "The Committee will keep under review the future path of monetary policy.", None, None, None),
        ("policy_guidance", "statement", "text", "Policy will be set accordingly.", None, None, None),
    ],
    "boc_decision.html": [
        ("monetary_policy_decision", "date", "date", "2026-07-24", None, None, None),
        ("policy_rate", "value", "percentage", 4.75, None, None, None),
        ("policy_rate", "change", "basis_points", -25.0, None, None, None),
        ("monetary_policy_decision", "statement", "text", "Ottawa, Ontario — 24 July 2026\nThe Bank of Canada is lowering its policy interest rate by 25 basis points to 4.75 per cent.", None, None, None),
        ("policy_guidance", "statement", "text", "The Bank will continue to assess the evolution of inflation and the economic outlook.", None, None, None),
        ("policy_guidance", "statement", "text", "The Bank stands ready to adjust policy if needed.", None, None, None),
    ],
    "boc_statement.html": [
        ("monetary_policy", "date", "date", "2026-07-24", None, None, None),
        ("inflation", "value", "percentage", 2.2, None, None, "year:2026"),
        ("inflation", "assessment", "text", "Inflation is well anchored around the 2 per cent control range.", None, None, None),
        ("gdp", "value", "percentage", 1.5, None, None, "year:2026"),
        ("growth", "assessment", "text", "Economic activity is expected to strengthen in the second half of the year.", None, None, None),
        ("inflation_risk", "assessment", "categorical", "upside", None, None, None),
        ("risk", "assessment", "text", "Geopolitical tensions remain a key source of uncertainty.", None, None, None),
        ("policy_guidance", "statement", "text", "The Bank will continue to assess the data.", None, None, None),
        ("policy_guidance", "statement", "text", "The Bank will adjust policy as needed.", None, None, None),
    ],
    "snb_decision.html": [
        ("monetary_policy_decision", "date", "date", "2026-06-19", None, None, None),
        ("policy_rate", "value", "percentage", 1.25, None, None, None),
        ("policy_rate", "change", "basis_points", -25.0, None, None, None),
        ("monetary_policy_decision", "statement", "text", "Assessments as of 19 June 2026\nThe Swiss National Bank (SNB) is lowering the SNB policy rate by 25 basis points to 1.25%.", None, None, None),
        ("policy_guidance", "statement", "text", "The SNB will continue to monitor price developments closely.", None, None, None),
        ("policy_guidance", "statement", "text", "The SNB stands ready to adjust policy if necessary.", None, None, None),
    ],
    "snb_statement.html": [
        ("monetary_policy", "date", "date", "2026-06-19", None, None, None),
        ("gdp", "value", "percentage", 1.2, None, None, "year:2026"),
        ("growth", "assessment", "text", "Economic activity remains moderate.", None, None, None),
        ("inflation", "value", "percentage", 1.1, None, None, "year:2026"),
        ("core_inflation", "assessment", "text", "Underlying inflation remains moderate.", None, None, None),
        ("policy_guidance", "statement", "text", "The SNB is continuing to ensure appropriate monetary conditions over the medium term.", None, None, None),
        ("policy_guidance", "statement", "text", "The SNB will continue to monitor the situation closely.", None, None, None),
        ("growth_risk", "assessment", "categorical", "downside", None, None, None),
    ],
    "rba_decision.html": [
        ("monetary_policy_decision", "date", "date", "2026-08-04", None, None, None),
        ("cash_rate", "value", "percentage", 4.1, None, None, None),
        ("cash_rate", "change", "basis_points", -25.0, None, None, None),
        ("monetary_policy_decision", "statement", "text", "Date: 4 August 2026\nAt its meeting today, the Board decided to lower the cash rate target by 25 basis points to 4.10 per cent.", None, None, None),
        ("policy_guidance", "statement", "text", "The Board will continue to monitor emerging data.", None, None, None),
        ("policy_guidance", "statement", "text", "The future path of interest rates will depend on incoming data.", None, None, None),
    ],
    "rba_statement.html": [
        ("monetary_policy", "date", "date", "2026-08-04", None, None, None),
        ("risk", "assessment", "categorical", "balanced", None, None, None),
        ("risk", "assessment", "text", "International developments remain a key uncertainty.", None, None, None),
        ("gdp", "value", "percentage", 1.6, None, None, "year:2026"),
        ("growth", "assessment", "text", "The Australian economy continues to expand at a modest pace.", None, None, None),
        ("inflation", "value", "percentage", 2.8, None, None, "year:2026"),
        ("inflation", "assessment", "text", "Services price inflation remains elevated.", None, None, None),
        ("labour_market", "assessment", "text", "The labour market has remained tight.", None, None, None),
        ("wages", "value", "percentage", 3.5, None, None, "year:2026"),
        ("policy_guidance", "statement", "text", "The Board will continue to monitor global economic developments.", None, None, None),
        ("policy_guidance", "statement", "text", "The Board is not ruling anything in or out regarding the future path of interest rates.", None, None, None),
    ],
    "rbnz_decision.html": [
        ("monetary_policy_decision", "date", "date", "2026-11-19", None, None, None),
        ("official_cash_rate", "value", "percentage", 4.25, None, None, None),
        ("official_cash_rate", "change", "basis_points", -50.0, None, None, None),
        ("monetary_policy_decision", "statement", "text", "19 November 2026\nAt its meeting today, the Monetary Policy Committee agreed to reduce the Official Cash Rate (OCR) by 50 basis points to 4.25 per cent.", None, None, None),
        ("policy_guidance", "statement", "text", "The Committee's future OCR decisions will be data-dependent.", None, None, None),
        ("policy_guidance", "statement", "text", "The Committee will continue to assess inflation and employment prospects.", None, None, None),
    ],
    "rbnz_statement.html": [
        ("monetary_policy", "date", "date", "2026-11-19", None, None, None),
        ("growth", "assessment", "text", "New Zealand's economy remains subdued.", None, None, None),
        ("gdp", "value", "percentage", 0.8, None, None, "year:2026"),
        ("inflation", "value", "percentage", 2.1, None, None, "year:2027"),
        ("core_inflation", "assessment", "text", "Underlying inflation continues to ease.", None, None, None),
        ("unemployment", "value", "percentage", 4.8, None, None, "year:2027"),
        ("risk", "assessment", "categorical", "balanced", None, None, None),
        ("policy_guidance", "statement", "text", "The Committee agreed that monetary policy will need to remain restrictive for some time.", None, None, None),
        ("policy_guidance", "statement", "text", "The Committee will continue to assess incoming data.", None, None, None),
    ],
    "norges_decision.html": [
        ("monetary_policy_decision", "date", "date", "2026-05-03", None, None, None),
        ("policy_rate", "value", "percentage", 3.5, None, None, None),
        ("policy_rate", "change", "basis_points", -25.0, None, None, None),
        ("monetary_policy_decision", "statement", "text", "3 May 2026\nAt its meeting today, the Monetary Policy and Financial Stability Committee decided to cut the policy rate by 0.25 percentage points to 3.50 per cent.", None, None, None),
        ("policy_guidance", "statement", "text", "The Committee will continue to assess the economic outlook.", None, None, None),
        ("policy_guidance", "statement", "text", "The Committee stands ready to adjust the policy rate if necessary.", None, None, None),
    ],
    "norges_mpr.html": [
        ("monetary_policy", "date", "date", "2026-05-05", None, None, None),
        ("gdp", "value", "percentage", 1.5, None, None, "year:2026"),
        ("gdp", "value", "percentage", 1.1, None, None, "year:2026"),
        ("inflation", "value", "percentage", 2.6, None, None, "year:2027"),
        ("core_inflation", "value", "percentage", 3.4, None, None, "year:2026"),
        ("unemployment", "value", "percentage", 3.1, None, None, "year:2026"),
        ("wages", "value", "percentage", 3.9, None, None, "year:2026"),
        ("policy_rate_projection", "value", "percentage", 1.75, None, None, "year:2028"),
        ("risk", "assessment", "categorical", "upside", None, None, None),
        ("growth_risk", "assessment", "text", "Uncertainties surrounding international growth are considerable.", None, None, None),
        ("policy_guidance", "statement", "text", "The policy rate will be kept at a tight level for an extended period.", None, None, None),
        ("policy_guidance", "statement", "text", "The Committee is prepared to adjust the policy rate if the outlook changes.", None, None, None),
    ],
    "riksbank_decision.html": [
        ("monetary_policy_decision", "date", "date", "2026-06-25", None, None, None),
        ("policy_rate", "value", "percentage", 4.0, None, None, None),
        ("policy_rate", "change", "basis_points", -25.0, None, None, None),
        ("monetary_policy_decision", "statement", "text", "25 June 2026\nAt its meeting today, the Executive Board of the Riksbank decided to lower the policy rate by 0.25 percentage points to 4.00 per cent.", None, None, None),
        ("policy_guidance", "statement", "text", "The Executive Board will continue to monitor the inflation outlook.", None, None, None),
        ("policy_guidance", "statement", "text", "The Riksbank stands ready to adjust policy if necessary.", None, None, None),
    ],
    "riksbank_statement.html": [
        ("monetary_policy", "date", "date", "2026-06-25", None, None, None),
        ("gdp", "value", "percentage", 1.3, None, None, "year:2026"),
        ("growth", "assessment", "text", "Economic activity is gradually strengthening.", None, None, None),
        ("inflation", "value", "percentage", 2.0, None, None, "year:2027"),
        ("inflation", "assessment", "text", "Inflation has fallen markedly from the peak.", None, None, None),
        ("unemployment", "value", "percentage", 7.2, None, None, "year:2026"),
        ("wages", "value", "percentage", 3.3, None, None, "year:2026"),
        ("growth_risk", "assessment", "categorical", "downside", None, None, None),
        ("policy_guidance", "statement", "text", "Monetary policy needs to be tightened by a small amount in the coming year.", None, None, None),
        ("policy_guidance", "statement", "text", "The Executive Board will make its decisions on the basis of the assessment of the inflation outlook.", None, None, None),
    ],
    "boj_statement.html": [
        ("monetary_policy_decision", "date", "date", "2026-06-16", None, None, None),
        ("policy_rate", "value", "percentage", 0.5, None, None, None),
        ("monetary_policy_decision", "statement", "text", "At the Monetary Policy Meeting held today, the Policy Board decided to conduct market operations so that the uncollateralized overnight call rate will be formed at around 0.5%.", None, None, None),
        ("monetary_policy_decision", "statement", "text", "The vote was 8 to 1.", None, None, None),
        ("policy_guidance", "statement", "text", "The Bank will continue with monetary easing as long as necessary.", None, None, None),
        ("policy_guidance", "statement", "text", "The Bank will not hesitate to take additional easing measures if needed.", None, None, None),
        ("gdp", "value", "percentage", 1.2, None, None, "year:2026"),
        ("inflation", "value", "percentage", 2.4, None, None, "year:2026"),
        ("growth_risk", "assessment", "categorical", "downside", None, None, None),
        ("inflation_risk", "assessment", "categorical", "upside", None, None, None),
    ],
}

# Forecast/statement sections use numeric instruments under different subject
# names; decisions are exempt (their instruments are DECISION_INSTRUMENTS above).
MACRO_SUBJECTS = {
    "gdp", "growth", "inflation", "core_inflation", "inflation_expectations",
    "unemployment", "wages", "labour_market", "financial_conditions",
    "risk", "inflation_risk", "growth_risk", "policy_rate_projection",
}


def test_golden_facts_across_all_longtail_fixtures():
    for name, cls in CASES:
        result = _extract(name, cls)
        assert result.warnings == [], (name, result.warnings)
        got = [_signature(f) for f in result.facts]
        assert got == GOLDEN[name], name
        assert len(result.facts) == len(GOLDEN[name]), name


def test_forward_guidance_is_verbatim_and_never_interpreted():
    for name, cls in CASES:
        result = _extract(name, cls)
        for f in result.facts:
            if f.subject != "policy_guidance":
                continue
            assert f.value.kind is ValueKind.TEXT, name
            assert f.value.value == f.source_text, name  # verbatim
            assert f.identity_qualifier, name


def test_decision_facts_are_quantitative_and_high_confidence():
    for name, cls in CASES:
        if not name.endswith("_decision.html"):
            continue
        result = _extract(name, cls)
        for f in result.facts:
            assert f.confidence is Confidence.HIGH, (name, f.subject)
        for f in result.facts:
            if f.subject in DECISION_INSTRUMENTS:
                assert f.predicate in ("value", "change")
                assert f.value.kind in (ValueKind.PERCENTAGE, ValueKind.BASIS_POINTS, ValueKind.RANGE)


def test_provenance_is_traceable():
    for name, cls in CASES:
        document = _normalized(name)
        result = _extract(name, cls)
        for fact in result.facts:
            assert fact.extraction_version == cls.extraction_version, name
            assert fact.extraction_method, name
            assert fact.source_location is not None, name
            assert fact.source_text, name
            assert fact.publication_id == f"pub-{name}", name
            assert fact.document_id == document.document_id, name
            if fact.predicate != "date":
                assert fact.effective_date is None, (name, fact.subject)
            location = fact.source_location
            if location.kind is LocationKind.SECTION:
                section_text = document.sections[location.section].text or ""
                assert fact.source_text in section_text, (name, fact.subject, fact.predicate)
                assert fact.value.source_text in section_text, (name, fact.subject, fact.predicate)
            elif location.kind is LocationKind.TABLE:
                table = document.tables[location.table]
                row = " | ".join(str(cell or "") for cell in table.rows[location.row])
                assert fact.source_text == row, (name, fact.subject, fact.predicate)
                cell = table.rows[location.row][location.column]
                assert fact.value.source_text == str(cell or "").strip(), (name, fact.subject, fact.predicate)


def test_extraction_is_deterministic():
    for name, cls in CASES:
        first = _extract(name, cls)
        second = _extract(name, cls)
        assert [(f.resolve_id(), _signature(f)) for f in first.facts] == [
            (f.resolve_id(), _signature(f)) for f in second.facts
        ], name
        ids = [f.resolve_id() for f in first.facts]
        assert len(ids) == len(set(ids)), name  # no fact_id collisions


def test_no_cross_phase_facts():
    for name, cls in CASES:
        result = _extract(name, cls)
        subjects = {f.subject for f in result.facts}
        predicates = {f.predicate for f in result.facts}
        if name.endswith("_decision.html"):
            # decisions: decision / instrument / guidance only — never macro data
            allowed = DECISION_INSTRUMENTS | {"monetary_policy_decision", "policy_guidance"}
            assert subjects <= allowed, (name, subjects)
            assert "monetary_policy" not in subjects, name
            assert "vote" not in subjects, name
            if "fed_decision.html" not in name:
                assert not MACRO_SUBJECTS & subjects, (name, subjects)
        elif name == "boj_statement.html":
            # BoJ statement combines decision-language and statement facts
            assert "monetary_policy_decision" in subjects
            assert all(f.predicate != "change" for f in result.facts), name
        else:
            # statements / minutes / projections / reports: no decision-window
            assert "monetary_policy_decision" not in subjects, name
            assert "change" not in predicates, name
            # decision instruments are never emitted as *value* facts from
            # statements/minutes/reports (fed_sep projects policy_rate under
            # the "projection" predicate, which is Phase 4.5, not Phase 4.1)
            instrument_values = {f.subject for f in result.facts if f.predicate == "value"}
            assert not DECISION_INSTRUMENTS & instrument_values, (name, instrument_values)


def test_no_hawkish_or_dovish_labels():
    for name, cls in CASES:
        result = _extract(name, cls)
        for f in result.facts:
            assert "hawkish" not in str(f.value.value or "").lower(), name
            assert "dovish" not in str(f.value.value or "").lower(), name
            assert "stance" not in f.predicate, name