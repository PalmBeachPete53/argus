"""Phase 6 — ECB Monetary Policy Statement extractor: end-to-end tests using the
local HTML fixtures and the existing Store (vertical slice)."""

from __future__ import annotations

from pathlib import Path

from argus.classification.base import Confidence
from argus.decisions import EcbDecisionExtractor, extract_decision
from argus.documents import Normalizer
from argus.documents.base import DocumentSection, NormalizedDocument
from argus.facts import ValueKind
from argus.models import Document, DocumentStatus, Publication
from argus.statements import (
    SUBJECT_CORE_INFLATION,
    SUBJECT_FINANCIAL_CONDITIONS,
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
    EcbMonetaryPolicyStatementExtractor,
    extract_statement,
    extract_statement_batch,
)
from argus.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "documents"
ECB_URL = "https://www.ecb.europa.eu/press/press_conf/2026/html/ecb.mp260723.en.html"


def statement_publication(**kw) -> Publication:
    fields = dict(
        central_bank="ecb",
        title="Monetary policy statement",
        url=ECB_URL,
        source_id="ecb-govcdec",
        source_url="https://www.ecb.europa.eu/press/govcdec/html/feed.xml",
        id="pub-ecb-stmt",
    )
    fields.update(kw)
    return Publication(**fields)


def normalized_fixture(name: str) -> object:
    doc = Document(
        publication_id="pub-ecb-stmt",
        url=ECB_URL,
        kind="html",
        status=DocumentStatus.FETCHED,
        local_path=str(FIXTURES / name),
    )
    return Normalizer().parse(doc)


def extract_fixture(name: str):
    return EcbMonetaryPolicyStatementExtractor().extract(statement_publication(), normalized_fixture(name))


def fact_by(result, subject: str, predicate: str):
    matches = [f for f in result.facts if f.subject == subject and f.predicate == predicate]
    assert len(matches) == 1, f"{subject}/{predicate}: {len(matches)} facts"
    return matches[0]


def period_of(fact) -> str | None:
    if fact.period is None:
        return None
    kind = fact.period.kind
    kind_str = kind.value if hasattr(kind, "value") else kind  # persisted rows keep a plain string
    return f"{kind_str}:{fact.period.value}"


# ---------------------------------------------------------------------------
# golden facts across all ECB statement fixtures
# ---------------------------------------------------------------------------

GOLDEN = {
    "ecb_statement.html": {
        "warnings": [],
        "count": 18,
        "rationale": [
            "The decisions are based on the assessment that inflation will return to the 2% target over the coming quarters.",
        ],
        "guidance": [
            "The Governing Council stands ready to adjust all of its instruments within its mandate to ensure that inflation returns to its 2% target.",
            "The Governing Council will keep the key ECB interest rates at restrictive levels for as long as necessary.",
            "The Governing Council stands ready to adjust all of its instruments within its mandate.",
        ],
        "growth": ["The euro area economy has slowed, reflecting weaker external demand."],
        "gdp": {1.4: "year:2027", 1.6: "year:2028"},
        "inflation": {2.4: None, 2.2: "year:2027"},
        "core_inflation": ["Core inflation remains elevated but is expected to decline gradually."],
        "inflation_expectations": ["Inflation expectations remain well anchored."],
        "labour_market": ["The labour market remains resilient."],
        "unemployment": {6.4: "month:2026-06"},
        "wages": {3.0: "year:2027"},
        "financial_conditions": ["Financing conditions remain tight, and monetary policy transmission is functioning smoothly."],
        "risk": ["balanced"],
        "inflation_risk": ["upside"],
        "growth_risk": ["downside"],
    },
    "ecb_statement_infl_growth_emp.html": {
        "warnings": ["no_risk_assessment", "no_forward_guidance"],
        "count": 6,
        "rationale": [],
        "guidance": [],
        "growth": ["The economy remains resilient."],
        "gdp": {1.2: "year:2027"},
        "inflation": {2.1: "year:2027"},
        "core_inflation": {2.3: "year:2028"},
        "labour_market": ["The labour market is tight."],
        "unemployment": {6.3: None},
    },
    "ecb_statement_risks_guidance.html": {
        "warnings": [],
        "count": 6,
        "rationale": [],
        "guidance": [
            "The Governing Council will keep the key ECB interest rates at restrictive levels for as long as necessary.",
            "The Governing Council stands ready to adjust all of its instruments within its mandate.",
        ],
        "risk": ["balanced", "Uncertainty surrounding the outlook remains elevated."],
        "inflation_risk": ["upside"],
        "growth_risk": ["downside"],
    },
    "ecb_statement_minimal.html": {
        "warnings": ["no_risk_assessment", "no_forward_guidance"],
        "count": 1,
        "rationale": [
            "The decisions are based on the Governing Council's assessment of the inflation outlook.",
        ],
        "guidance": [],
    },
    "ecb_statement_wording.html": {
        "warnings": [],
        "count": 7,
        "rationale": [],
        "guidance": ["The Governing Council will be guided by the incoming data."],
        "financial_conditions": [
            "Financing conditions remain tight and continue to transmit to the real economy.",
            "Monetary policy transmission is functioning smoothly.",
        ],
        "inflation": {2.0: "year:2027"},
        "inflation_expectations": ["Inflation expectations remain firmly anchored."],
        "risk": ["balanced"],
        "inflation_risk": ["balanced"],
    },
}

GOLDEN_KEY_SUBJECT = {
    "rationale": SUBJECT_MONETARY_POLICY,
    "guidance": SUBJECT_POLICY_GUIDANCE,
    "growth": SUBJECT_GROWTH,
    "gdp": SUBJECT_GDP,
    "inflation": SUBJECT_INFLATION,
    "core_inflation": SUBJECT_CORE_INFLATION,
    "inflation_expectations": SUBJECT_INFLATION_EXPECTATIONS,
    "labour_market": SUBJECT_LABOUR_MARKET,
    "unemployment": SUBJECT_UNEMPLOYMENT,
    "wages": SUBJECT_WAGES,
    "financial_conditions": SUBJECT_FINANCIAL_CONDITIONS,
    "risk": SUBJECT_RISK,
    "inflation_risk": SUBJECT_INFLATION_RISK,
    "growth_risk": SUBJECT_GROWTH_RISK,
}


def test_golden_facts_across_all_fixtures():
    for name, expected in GOLDEN.items():
        result = extract_fixture(name)
        assert result.warnings == expected["warnings"], (name, result.warnings)
        assert len(result.facts) == expected["count"], name

        for key, wanted in expected.items():
            if key in ("warnings", "count"):
                continue
            subject = GOLDEN_KEY_SUBJECT[key]
            facts = [f for f in result.facts if f.subject == subject]
            if isinstance(wanted, dict):
                # quantitative: value ↔ reference period
                got = {(f.value.value, period_of(f)) for f in facts if f.predicate == "value"}
                assert got == set(wanted.items()), (name, subject, got, wanted)
            else:
                # verbatim text / categorical values
                assert sorted(f.value.value for f in facts) == sorted(wanted), (name, subject)

        # forward guidance facts are verbatim text with an ordinal qualifier
        for f in result.facts:
            if f.subject == SUBJECT_POLICY_GUIDANCE:
                assert f.value.kind is ValueKind.TEXT
                assert f.value.source_text == f.value.value
                assert f.identity_qualifier
                assert f.identity_qualifier.startswith("policy_guidance:")


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------


def test_rationale_is_verbatim_and_located():
    result = extract_fixture("ecb_statement.html")
    f = fact_by(result, SUBJECT_MONETARY_POLICY, "rationale")
    assert f.value.kind is ValueKind.TEXT
    assert f.source_location.section == 1
    assert f.confidence is Confidence.MEDIUM


def test_quantitative_values_carry_periods():
    result = extract_fixture("ecb_statement.html")
    gdp = next(f for f in result.facts if f.subject == SUBJECT_GDP and period_of(f) == "year:2027")
    assert gdp.value.kind is ValueKind.PERCENTAGE
    assert gdp.value.value == 1.4
    assert gdp.value.source_text == "1.4%"
    assert gdp.confidence is Confidence.HIGH
    unemployment = next(f for f in result.facts if f.subject == SUBJECT_UNEMPLOYMENT and f.predicate == "value")
    assert unemployment.value.value == 6.4
    assert period_of(unemployment) == "month:2026-06"


def test_risk_orientations_are_categorical():
    result = extract_fixture("ecb_statement.html")
    balanced = fact_by(result, SUBJECT_RISK, "assessment")
    assert balanced.value.kind is ValueKind.CATEGORICAL
    assert balanced.value.value == "balanced"
    assert balanced.confidence is Confidence.HIGH
    assert balanced.value.source_text == "Risks to the economic outlook are broadly balanced."
    assert fact_by(result, SUBJECT_INFLATION_RISK, "assessment").value.value == "upside"
    assert fact_by(result, SUBJECT_GROWTH_RISK, "assessment").value.value == "downside"


def test_risk_without_orientation_is_verbatim_text():
    result = extract_fixture("ecb_statement_risks_guidance.html")
    texts = [f for f in result.facts if f.subject == SUBJECT_RISK and f.value.kind is ValueKind.TEXT]
    assert len(texts) == 1
    assert texts[0].value.value == "Uncertainty surrounding the outlook remains elevated."
    assert texts[0].confidence is Confidence.MEDIUM


def test_forward_guidance_is_verbatim_and_never_interpreted():
    result = extract_fixture("ecb_statement.html")
    guidance = [f for f in result.facts if f.subject == SUBJECT_POLICY_GUIDANCE]
    assert len(guidance) == 3
    for f in guidance:
        assert f.value.kind is ValueKind.TEXT
        assert f.value.value == f.source_text  # verbatim, no stance interpretation
        assert f.identity_qualifier
        assert f.identity_qualifier.startswith("policy_guidance:")
    # the decision wording is Phase 5 territory and is NOT mined here
    assert not any(f.subject == "monetary_policy_decision" for f in result.facts)


def test_wording_variants_are_normalized():
    """'two-sided'/'will be guided by' map onto the canonical vocabulary without
    inventing anything the source did not state."""
    result = extract_fixture("ecb_statement_wording.html")
    assert fact_by(result, SUBJECT_RISK, "assessment").value.value == "balanced"
    assert fact_by(result, SUBJECT_INFLATION_RISK, "assessment").value.value == "balanced"
    guidance = fact_by(result, SUBJECT_POLICY_GUIDANCE, "statement")
    assert guidance.value.value == "The Governing Council will be guided by the incoming data."
    assert guidance.source_location.section == 5  # unheaded fallback section


def test_provenance_is_traceable():
    for name in GOLDEN:
        document = normalized_fixture(name)
        result = extract_fixture(name)
        for fact in result.facts:
            assert fact.extraction_version == EcbMonetaryPolicyStatementExtractor.extraction_version
            assert fact.extraction_method
            assert fact.source_location is not None
            assert fact.source_text
            assert fact.publication_id == "pub-ecb-stmt"
            assert fact.document_id
            assert fact.effective_date is None
            section_text = document.sections[fact.source_location.section].text or ""
            assert fact.source_text in section_text, (name, fact.subject, fact.predicate)
            assert fact.value.source_text in section_text, (name, fact.subject, fact.predicate)


# ---------------------------------------------------------------------------
# no invented facts
# ---------------------------------------------------------------------------


def test_no_decision_facts_from_statement():
    """The decision (wording, rates, changes, effective date) is Phase 5
    territory and never surfaces from a statement publication."""
    result = extract_fixture("ecb_statement.html")
    phase5_subjects = {
        "monetary_policy_decision", "main_refinancing_rate", "marginal_lending_rate",
        "deposit_facility_rate", "asset_purchase", "vote", "risk_assessment",
    }
    assert not phase5_subjects & {f.subject for f in result.facts}
    assert not any(f.predicate == "change" for f in result.facts)
    assert not any(f.predicate == "date" for f in result.facts)


def test_no_hawkish_dovish_interpretation():
    """Facts carry verbatim text / percentages / orientations — never a
    hawkish/dovish, bullish/bearish or stance label the source did not state."""
    for name in GOLDEN:
        result = extract_fixture(name)
        for fact in result.facts:
            raw = str(fact.value.value or "")
            assert "hawkish" not in raw.lower()
            assert "dovish" not in raw.lower()
            assert "stance" not in fact.predicate


def test_absence_of_risk_and_guidance_is_never_invented():
    result = extract_fixture("ecb_statement_infl_growth_emp.html")
    assert result.warnings == ["no_risk_assessment", "no_forward_guidance"]
    assert not any(f.subject in (SUBJECT_RISK, SUBJECT_INFLATION_RISK, SUBJECT_GROWTH_RISK) for f in result.facts)
    assert not any(f.subject == SUBJECT_POLICY_GUIDANCE for f in result.facts)


def test_minimal_document_extracts_only_what_is_stated():
    result = extract_fixture("ecb_statement_minimal.html")
    assert result.warnings == ["no_risk_assessment", "no_forward_guidance"]
    assert len(result.facts) == 1
    assert result.facts[0].subject == SUBJECT_MONETARY_POLICY


def test_empty_document_warns_no_sections():
    doc = NormalizedDocument(
        publication_id="pub-ecb-stmt",
        document_id="sha-empty",
        source_url=ECB_URL,
        local_path=None,
        document_kind="html",
        sections=[],
    )
    result = EcbMonetaryPolicyStatementExtractor().extract(statement_publication(), doc)
    assert result.warnings == ["no_sections"]
    assert result.facts == []


# ---------------------------------------------------------------------------
# determinism + idempotent persistence (vertical slice)
# ---------------------------------------------------------------------------


def _store_statement(tmp_path, name: str = "ecb_statement.html") -> Store:
    store = Store(tmp_path / f"{name}.db")
    store.upsert_publication(statement_publication())
    store.upsert_normalized_document(normalized_fixture(name))
    return store


def test_extract_statement_persists_facts(tmp_path):
    store = _store_statement(tmp_path)
    results = extract_statement(store, statement_publication())
    assert len(results) == 1
    persisted = store.get_facts(publication_id="pub-ecb-stmt")
    assert len(persisted) == 18
    by = {(f.subject, f.predicate) for f in persisted}
    assert (SUBJECT_MONETARY_POLICY, "rationale") in by
    assert (SUBJECT_POLICY_GUIDANCE, "statement") in by
    assert (SUBJECT_INFLATION, "value") in by
    assert (SUBJECT_GDP, "value") in by
    assert (SUBJECT_RISK, "assessment") in by
    inflation = next(f for f in persisted if (f.subject, f.predicate) == (SUBJECT_INFLATION, "value") and period_of(f) == "year:2027")
    assert inflation.value.value == 2.2
    assert inflation.central_bank == "ecb"  # filled from the publication
    assert inflation.period.label == "in 2027"


def test_extract_statement_is_idempotent(tmp_path):
    store = _store_statement(tmp_path)
    pub = statement_publication()
    extract_statement(store, pub)
    extract_statement(store, pub)  # re-run: same deterministic fact_ids
    assert len(store.get_facts(publication_id="pub-ecb-stmt")) == 18


def test_extraction_is_deterministic(tmp_path):
    for name in GOLDEN:
        store = _store_statement(tmp_path, name)
        result = extract_fixture(name)
        store.rebuild_facts_for_document(result.document_id, result)
        first = sorted((f.fact_id, f.subject, f.predicate, f.value.value) for f in store.get_facts(publication_id="pub-ecb-stmt"))
        store.rebuild_facts_for_document(result.document_id, result)
        second = sorted((f.fact_id, f.subject, f.predicate, f.value.value) for f in store.get_facts(publication_id="pub-ecb-stmt"))
        assert first == second, name
        assert len(first) == len(result.facts), name
        ids = [f.fact_id for f in result.facts]
        assert len(ids) == len(set(ids)), name  # no fact_id collisions


def test_extract_statement_skips_non_statement_publications(tmp_path):
    store = _store_statement(tmp_path)
    pub = statement_publication(publication_type="monetary_policy_decision")  # stale cache disagrees
    assert extract_statement(store, pub) == []
    assert store.get_facts(publication_id="pub-ecb-stmt") == []
    # authoritative classification record also gates extraction
    store.set_classification(
        "pub-ecb-stmt",
        central_bank="ecb",
        publication_type="minutes",
        confidence=Confidence.HIGH.value,
        method="title_pattern",
        evidence=[],
    )
    assert extract_statement(store, statement_publication()) == []


def test_extract_statement_batch_runs_all_statements(tmp_path):
    store = _store_statement(tmp_path)
    results = extract_statement_batch(store)
    assert len(results) == 1
    assert len(store.get_facts(bank="ecb")) == 18


# ---------------------------------------------------------------------------
# Phase 5 / Phase 6 coexistence
# ---------------------------------------------------------------------------


def test_phase5_and_phase6_do_not_overlap(tmp_path):
    """A statement document must never feed the decision extractor and vice
    versa: gating is on classification, and the fact vocabularies are disjoint."""
    store = _store_statement(tmp_path)
    pub = statement_publication()
    store.set_classification(
        "pub-ecb-stmt",
        central_bank="ecb",
        publication_type="monetary_policy_statement",
        confidence=Confidence.HIGH.value,
        method="url_pattern",
        evidence=[],
    )
    # the decision extractor, even called directly, never mines the statement's
    # own section: the rationale and its forward guidance sit under the
    # "Monetary policy statement" heading (the Phase 6 boundary)
    decision_result = EcbDecisionExtractor().extract(pub, normalized_fixture("ecb_statement.html"))
    assert not any(f.subject == SUBJECT_MONETARY_POLICY for f in decision_result.facts)
    assert not any(
        f.subject == SUBJECT_POLICY_GUIDANCE and f.source_location.section == 1 for f in decision_result.facts
    )
    # the store-level helpers are gated: extract_decision produces nothing for a
    # statement publication, extract_statement produces only statement facts
    assert extract_decision(store, pub) == []
    extract_statement(store, pub)
    persisted = store.get_facts(publication_id="pub-ecb-stmt")
    assert all(f.subject != "monetary_policy_decision" for f in persisted)
    assert any(f.subject == SUBJECT_POLICY_GUIDANCE for f in persisted)