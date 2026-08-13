"""Phase 5 — ECB Monetary Policy Decision extractor: end-to-end tests using the
local HTML fixture and the existing Store (vertical slice)."""

from __future__ import annotations

from pathlib import Path

from argus.classification.base import Confidence
from argus.decisions import (
    SUBJECT_ASSET_PURCHASE,
    SUBJECT_DECISION,
    SUBJECT_DEPOSIT_FACILITY,
    SUBJECT_MAIN_REFINANCING,
    SUBJECT_MARGINAL_LENDING,
    SUBJECT_POLICY_GUIDANCE,
    EcbDecisionExtractor,
    extract_decision,
    extract_decision_batch,
)
from argus.documents import Normalizer
from argus.facts import ValueKind
from argus.models import Document, DocumentStatus, Publication
from argus.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "documents"
ECB_URL = "https://www.ecb.europa.eu/press/govcdec/mopo/html/ecb.mp260723.en.html"


def normalized_ecb_document() -> object:
    doc = Document(
        publication_id="pub-ecb-1",
        url=ECB_URL,
        kind="html",
        status=DocumentStatus.FETCHED,
        local_path=str(FIXTURES / "ecb_decision.html"),
    )
    return Normalizer().parse(doc)


def ecb_publication(**kw) -> Publication:
    fields = dict(
        central_bank="ecb",
        title="Monetary policy decisions",
        url=ECB_URL,
        source_id="ecb-govcdec",
        source_url="https://www.ecb.europa.eu/press/govcdec/html/feed.xml",
        id="pub-ecb-1",
    )
    fields.update(kw)
    return Publication(**fields)


def facts_by_subject(result) -> dict:
    by_subject: dict[str, list] = {}
    for fact in result.facts:
        by_subject.setdefault(fact.subject, []).append(fact)
    return by_subject


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------


def test_ecb_fixture_extracts_decision_date():
    extractor = EcbDecisionExtractor()
    result = extractor.extract(ecb_publication(), normalized_ecb_document())
    assert result.warnings == []
    date_facts = [f for f in result.facts if f.subject == SUBJECT_DECISION and f.predicate == "date"]
    assert len(date_facts) == 1
    fact = date_facts[0]
    assert fact.predicate == "date"
    assert fact.value.kind is ValueKind.DATE
    assert fact.value.value == "2026-07-23"
    assert fact.value.source_text == "23 July 2026"
    assert fact.effective_date.date().isoformat() == "2026-07-23"
    assert fact.source_location.section == 0


def test_ecb_fixture_extracts_key_rates():
    extractor = EcbDecisionExtractor()
    result = extractor.extract(ecb_publication(), normalized_ecb_document())
    expected = {
        SUBJECT_MAIN_REFINANCING: 2.00,
        SUBJECT_MARGINAL_LENDING: 2.25,
        SUBJECT_DEPOSIT_FACILITY: 1.75,
    }
    for subject, value in expected.items():
        value_facts = [f for f in result.facts if f.subject == subject and f.predicate == "value"]
        assert len(value_facts) == 1, subject
        fact = value_facts[0]
        assert fact.value.kind is ValueKind.PERCENTAGE
        assert fact.value.value == value
        # explicit effective date from the source is preserved
        assert fact.effective_date.date().isoformat() == "2026-08-02"
        assert fact.source_location.section == 2
        assert fact.source_text  # provenance preserved


def test_ecb_fixture_extracts_explicit_change_and_direction():
    extractor = EcbDecisionExtractor()
    result = extractor.extract(ecb_publication(), normalized_ecb_document())
    for subject in (SUBJECT_MAIN_REFINANCING, SUBJECT_MARGINAL_LENDING, SUBJECT_DEPOSIT_FACILITY):
        change_facts = [f for f in result.facts if f.subject == subject and f.predicate == "change"]
        assert len(change_facts) == 1, subject
        fact = change_facts[0]
        assert fact.value.kind is ValueKind.BASIS_POINTS
        assert fact.value.value == -25.0  # easing, sign preserved
        assert "25 basis points" in fact.value.source_text


def test_ecb_fixture_provenance_is_traceable():
    extractor = EcbDecisionExtractor()
    result = extractor.extract(ecb_publication(), normalized_ecb_document())
    # extractor version + extraction method on every fact
    for fact in result.facts:
        assert fact.extraction_version == EcbDecisionExtractor.extraction_version
        assert fact.extraction_method
        assert fact.source_location is not None
        assert fact.source_text
        assert fact.publication_id == "pub-ecb-1"
        assert fact.document_id
    # values never invented: date + 3 levels + 3 changes + 2 wording + 2
    # asset-purchase decisions (APP, PEPP) — nothing else
    assert len(result.facts) == 11


def test_hold_statement_produces_no_change_fact():
    """A decision that only states the level ("kept unchanged") emits a value
    fact but no delta — nothing is invented."""
    from argus.documents.base import DocumentSection, NormalizedDocument

    doc = NormalizedDocument(
        publication_id="pub-ecb-1",
        document_id="sha-hold",
        source_url=ECB_URL,
        local_path=None,
        document_kind="html",
        sections=[
            DocumentSection(order=0, heading="", level=0, text="9 July 2026"),
            DocumentSection(order=1, heading="Monetary policy decisions", level=1, text="The Governing Council kept the three key ECB interest rates unchanged."),
            DocumentSection(order=2, heading="Key ECB interest rates", level=2, text="The interest rate on the main refinancing operations and the interest rates on the marginal lending facility and the deposit facility remain at 2.00%, 2.25% and 1.75% respectively."),
        ],
    )
    result = EcbDecisionExtractor().extract(ecb_publication(), doc)
    assert result.warnings == []
    changes = [f for f in result.facts if f.predicate == "change"]
    assert changes == []
    values = [f for f in result.facts if f.predicate == "value"]
    assert len(values) == 3


# ---------------------------------------------------------------------------
# golden facts across all ECB decision fixtures
# ---------------------------------------------------------------------------

GOLDEN = {
    "ecb_decision.html": {
        "date": ("2026-07-23", 0),
        "levels": {SUBJECT_MAIN_REFINANCING: 2.0, SUBJECT_MARGINAL_LENDING: 2.25, SUBJECT_DEPOSIT_FACILITY: 1.75},
        "changes": {SUBJECT_MAIN_REFINANCING: -25.0, SUBJECT_MARGINAL_LENDING: -25.0, SUBJECT_DEPOSIT_FACILITY: -25.0},
        "effective": "2026-08-02",
        "warnings": [],
        "statements": [
            "The Governing Council today decided to lower the three key ECB interest rates by 25 basis points.",
            "Against this background, the Governing Council decided to lower the deposit facility rate to 1.75 per cent.",
        ],
        "guidance": [],  # "stands ready …" sits in the Monetary-policy-statement section → Phase 6 boundary
        "assets": [
            ("app:0", "Under the APP, the Governing Council intends to stop reinvesting the proceeds from maturing securities.", None),
            ("pepp:0", "The Governing Council intends to reinvest the principal payments from maturing securities purchased under the PEPP during the first half of 2027.", "semester:2027-H1"),
        ],
    },
    "ecb_decision_increase.html": {
        "date": ("2026-09-10", 0),
        "levels": {SUBJECT_DEPOSIT_FACILITY: 2.0, SUBJECT_MAIN_REFINANCING: 2.5, SUBJECT_MARGINAL_LENDING: 2.75},
        "changes": {SUBJECT_DEPOSIT_FACILITY: 50.0, SUBJECT_MAIN_REFINANCING: 50.0, SUBJECT_MARGINAL_LENDING: 50.0},
        "effective": "2026-10-01",
        "warnings": [],
        "statements": [
            "The Governing Council today decided to raise the three key ECB interest rates by 50 basis points.",
        ],
        # in the decision body here → extracted (contrast with ecb_decision.html)
        "guidance": [
            "Against this background, the Governing Council stands ready to adjust all of its instruments within its mandate to ensure that inflation returns to its 2% target in a timely manner.",
        ],
        "assets": [],
    },
    "ecb_decision_hold.html": {
        "date": ("2026-06-11", 0),
        "levels": {SUBJECT_MAIN_REFINANCING: 2.0, SUBJECT_MARGINAL_LENDING: 2.25, SUBJECT_DEPOSIT_FACILITY: 1.75},
        "changes": {},
        "effective": None,
        "warnings": [],
        "statements": [
            "The Governing Council today decided to keep the three key ECB interest rates unchanged.",
        ],
        "guidance": [],
        "assets": [],
    },
    "ecb_decision_per_instrument.html": {
        "date": ("2026-03-17", 0),
        "levels": {SUBJECT_DEPOSIT_FACILITY: 1.5, SUBJECT_MAIN_REFINANCING: 1.75, SUBJECT_MARGINAL_LENDING: 2.0},
        "changes": {SUBJECT_DEPOSIT_FACILITY: -25.0, SUBJECT_MARGINAL_LENDING: -25.0},
        "effective": "2026-03-25",
        "warnings": [],
        "statements": [
            "The Governing Council decided today to lower all three key ECB interest rates.",
        ],
        "guidance": [],
        "assets": [],
    },
    "ecb_decision_minimal.html": {
        "date": ("2026-05-14", 0),
        "levels": {SUBJECT_DEPOSIT_FACILITY: 1.5},
        "changes": {SUBJECT_DEPOSIT_FACILITY: -25.0},
        "effective": None,
        "warnings": ["no_rates_section"],
        "statements": [
            "The Governing Council decided today to lower the deposit facility rate by 25 basis points to 1.50 per cent.",
        ],
        "guidance": [],
        "assets": [],
    },
    "ecb_decision_full.html": {
        "date": ("2027-01-12", 0),
        "levels": {SUBJECT_MAIN_REFINANCING: 2.5, SUBJECT_MARGINAL_LENDING: 2.75, SUBJECT_DEPOSIT_FACILITY: 2.25},
        "changes": {SUBJECT_MAIN_REFINANCING: 50.0, SUBJECT_MARGINAL_LENDING: 50.0, SUBJECT_DEPOSIT_FACILITY: 50.0},
        "effective": "2027-01-13",
        "warnings": [],
        "statements": [
            "The Governing Council today decided to raise the three key ECB interest rates by 50 basis points.",
        ],
        "guidance": [
            "The Governing Council will keep the key ECB interest rates sufficiently restrictive for as long as necessary to return inflation to target.",
        ],
        # risk wording + press-conference note in the statement section are NOT extracted
        "assets": [
            ("app:0", "Under the APP, the Governing Council intends to stop reinvesting the proceeds from maturing securities from the beginning of February 2027.", None),
            ("pepp:0", "The Governing Council intends to reinvest the principal payments from maturing securities purchased under the PEPP during the first half of 2027.", "semester:2027-H1"),
            ("tltro:0", "The Governing Council decided to continue its targeted longer-term refinancing operations on unchanged terms, with banks free to repay early.", None),
        ],
    },
}

ALL_RATES = (SUBJECT_MAIN_REFINANCING, SUBJECT_MARGINAL_LENDING, SUBJECT_DEPOSIT_FACILITY)


def normalized_fixture(name: str) -> object:
    doc = Document(
        publication_id="pub-ecb-1",
        url=ECB_URL,
        kind="html",
        status=DocumentStatus.FETCHED,
        local_path=str(FIXTURES / name),
    )
    return Normalizer().parse(doc)


def extract_fixture(name: str):
    return EcbDecisionExtractor().extract(ecb_publication(), normalized_fixture(name))


def fact_by(result, subject: str, predicate: str):
    matches = [f for f in result.facts if f.subject == subject and f.predicate == predicate]
    assert len(matches) == 1, f"{subject}/{predicate}: {len(matches)} facts"
    return matches[0]


def test_golden_facts_across_all_fixtures():
    for name, expected in GOLDEN.items():
        result = extract_fixture(name)
        assert result.warnings == expected["warnings"], (name, result.warnings)

        iso, section = expected["date"]
        f = fact_by(result, SUBJECT_DECISION, "date")
        assert f.value.kind is ValueKind.DATE
        assert f.value.value == iso
        assert f.source_location.section == section

        for subject, value in expected["levels"].items():
            f = fact_by(result, subject, "value")
            assert f.value.kind is ValueKind.PERCENTAGE
            assert f.value.value == value, (name, subject)
            if expected["effective"]:
                assert f.effective_date.date().isoformat() == expected["effective"]
            else:
                assert f.effective_date is None
        for subject in ALL_RATES:
            if subject not in expected["levels"]:
                assert not [x for x in result.facts if x.subject == subject and x.predicate == "value"], name

        for subject, delta in expected["changes"].items():
            f = fact_by(result, subject, "change")
            assert f.value.kind is ValueKind.BASIS_POINTS
            assert f.value.value == delta, (name, subject)
        for subject in ALL_RATES:
            if subject not in expected["changes"]:
                assert not [x for x in result.facts if x.subject == subject and x.predicate == "change"], name

        # decision wording
        statements = [f for f in result.facts if f.subject == SUBJECT_DECISION and f.predicate == "statement"]
        assert sorted(f.value.value for f in statements) == sorted(expected["statements"]), name
        for f in statements:
            assert f.value.kind is ValueKind.TEXT
            assert f.value.source_text == f.value.value
            assert f.identity_qualifier

        # forward guidance
        guidance = [f for f in result.facts if f.subject == SUBJECT_POLICY_GUIDANCE]
        assert sorted(f.value.value for f in guidance) == sorted(expected["guidance"]), name
        for f in guidance:
            assert f.value.kind is ValueKind.TEXT

        # asset-purchase / balance-sheet decisions
        assets = [f for f in result.facts if f.subject == SUBJECT_ASSET_PURCHASE]
        expected_assets = [a[0] for a in expected["assets"]]
        assert sorted(f.identity_qualifier for f in assets) == sorted(expected_assets), name
        for (qualifier, sentence, period), f in zip(
            sorted(expected["assets"], key=lambda a: a[0]),
            sorted(assets, key=lambda a: a.identity_qualifier),
        ):
            assert f.value.value == sentence, name
            f_period = f.period.canonical() if f.period else None
            assert f_period == period, (name, qualifier, f_period, period)

        # never fabricated: no vote, no risk-assessment facts from decisions
        assert not [x for x in result.facts if x.subject == "vote"], name
        assert not [x for x in result.facts if x.subject == "risk_assessment"], name


def test_fixture_date_robustness_against_arbitrary_dates():
    """The hold fixture mentions a second date (12 June 2026, press conference)
    inside the rates section; the decision date must stay the leading date."""
    normalized = normalized_fixture("ecb_decision_hold.html")
    assert "12 June 2026" in normalized.sections[2].text
    f = fact_by(extract_fixture("ecb_decision_hold.html"), SUBJECT_DECISION, "date")
    assert f.value.value == "2026-06-11"
    assert f.source_location.section == 0


def test_fixture_minimal_does_not_invent_rates():
    result = extract_fixture("ecb_decision_minimal.html")
    subjects = {f.subject for f in result.facts}
    assert SUBJECT_MAIN_REFINANCING not in subjects
    assert SUBJECT_MARGINAL_LENDING not in subjects
    assert "no_rates_section" in result.warnings


def test_fixture_source_text_is_verbatim_in_section():
    for name in GOLDEN:
        document = normalized_fixture(name)
        result = extract_fixture(name)
        for fact in result.facts:
            section_text = document.sections[fact.source_location.section].text or ""
            assert fact.source_text in section_text, (name, fact.subject, fact.predicate)
            assert fact.value.source_text in section_text, (name, fact.subject, fact.predicate)
            assert fact.confidence is Confidence.HIGH


def test_fixture_extraction_is_deterministic(tmp_path):
    for name in GOLDEN:
        store = Store(tmp_path / f"{name}.db")
        store.upsert_publication(ecb_publication())
        document = normalized_fixture(name)
        store.upsert_normalized_document(document)
        result = extract_fixture(name)
        store.rebuild_facts_for_document(document.document_id, result)
        first = sorted((f.fact_id, f.subject, f.predicate, f.value.value) for f in store.get_facts(publication_id="pub-ecb-1"))
        store.rebuild_facts_for_document(document.document_id, result)
        second = sorted((f.fact_id, f.subject, f.predicate, f.value.value) for f in store.get_facts(publication_id="pub-ecb-1"))
        assert first == second, name
        assert len(first) == len(result.facts), name


def test_fixture_increase_order_reads_source_naming():
    """The increase fixture names the deposit facility first: item i must map to
    the i-th named instrument, not a canonical order."""
    result = extract_fixture("ecb_decision_increase.html")
    assert fact_by(result, SUBJECT_DEPOSIT_FACILITY, "value").value.value == 2.0
    assert fact_by(result, SUBJECT_MAIN_REFINANCING, "value").value.value == 2.5
    assert fact_by(result, SUBJECT_MARGINAL_LENDING, "value").value.value == 2.75


def test_guidance_in_statement_section_is_not_extracted():
    """The identical "stands ready …" sentence sits in the decision body of the
    increase fixture (extracted) but in the Monetary-policy-statement section of
    ecb_decision.html (NOT extracted — Phase 6 boundary)."""
    increased = extract_fixture("ecb_decision_increase.html")
    assert any(f.subject == SUBJECT_POLICY_GUIDANCE for f in increased.facts)
    baseline = extract_fixture("ecb_decision.html")
    assert not any(f.subject == SUBJECT_POLICY_GUIDANCE for f in baseline.facts)


def test_risk_assessment_is_not_extracted_from_decisions():
    """The full fixture carries explicit risk wording ("Risks to the economic
    outlook … tilted to the upside") inside its Monetary policy statement
    section. That belongs to Phase 6 and must never surface as a decision fact."""
    result = extract_fixture("ecb_decision_full.html")
    assert not any(f.subject == "risk_assessment" for f in result.facts)
    assert not any(f.subject == "inflation_risk" for f in result.facts)


def test_no_vote_fact_is_fabricated():
    """ECB Monetary Policy Decisions do not report individual votes. The
    extractor must never invent a vote fact, for any fixture."""
    for name in GOLDEN:
        result = extract_fixture(name)
        assert not any(f.subject == "vote" for f in result.facts), name


def test_no_invented_asset_purchase_or_guidance_facts():
    """Absence of a programme statement or guidance must not become an invented
    "no change" / "no action" fact."""
    result = extract_fixture("ecb_decision_minimal.html")
    assert not any(f.subject == SUBJECT_ASSET_PURCHASE for f in result.facts)
    assert not any(f.subject == SUBJECT_POLICY_GUIDANCE for f in result.facts)
    result = extract_fixture("ecb_decision_hold.html")
    assert not any(f.subject == SUBJECT_ASSET_PURCHASE for f in result.facts)


# ---------------------------------------------------------------------------
# vertical slice: extractor → ExtractionResult → Store
# ---------------------------------------------------------------------------


def _store_ecb(tmp_path) -> Store:
    store = Store(tmp_path / "argus.db")
    store.upsert_publication(ecb_publication())
    store.upsert_normalized_document(normalized_ecb_document())
    return store


def test_extract_decision_persists_facts(tmp_path):
    store = _store_ecb(tmp_path)
    results = extract_decision(store, ecb_publication())
    assert len(results) == 1
    persisted = store.get_facts(publication_id="pub-ecb-1")
    assert len(persisted) == 11
    by = {(f.subject, f.predicate) for f in persisted}
    assert (SUBJECT_DECISION, "date") in by
    assert (SUBJECT_DECISION, "statement") in by
    assert (SUBJECT_DEPOSIT_FACILITY, "value") in by
    assert (SUBJECT_DEPOSIT_FACILITY, "change") in by
    assert (SUBJECT_ASSET_PURCHASE, "decision") in by
    deposit = next(f for f in persisted if (f.subject, f.predicate) == (SUBJECT_DEPOSIT_FACILITY, "value"))
    assert deposit.value.value == 1.75
    assert deposit.central_bank == "ecb"  # filled from the publication
    assert deposit.effective_date.date().isoformat() == "2026-08-02"


def test_extract_decision_is_idempotent(tmp_path):
    store = _store_ecb(tmp_path)
    pub = ecb_publication()
    extract_decision(store, pub)
    extract_decision(store, pub)  # re-run: same deterministic fact_ids
    assert len(store.get_facts(publication_id="pub-ecb-1")) == 11


def test_value_correction_updates_in_place(tmp_path):
    store = _store_ecb(tmp_path)
    pub = ecb_publication()
    extract_decision(store, pub)
    # Simulate re-extraction with the real extractor on the same fixture — the
    # deterministic fact_id means the row is updated, never duplicated.
    deposit_before = next(
        f for f in store.get_facts(subject=SUBJECT_DEPOSIT_FACILITY, predicate="value")
    )
    extract_decision(store, pub)
    deposit_after = next(
        f for f in store.get_facts(subject=SUBJECT_DEPOSIT_FACILITY, predicate="value")
    )
    assert deposit_before.fact_id == deposit_after.fact_id


def test_extract_decision_skips_non_decision_publications(tmp_path):
    store = _store_ecb(tmp_path)
    pub = ecb_publication(publication_type="press_conference")  # stale cache disagrees
    results = extract_decision(store, pub)
    assert results == []
    assert store.get_facts(publication_id="pub-ecb-1") == []
    # authoritative classification record also gates extraction
    store.set_classification(
        "pub-ecb-1",
        central_bank="ecb",
        publication_type="minutes",
        confidence=Confidence.HIGH.value,
        method="title_pattern",
        evidence=[],
    )
    assert extract_decision(store, ecb_publication()) == []


def test_extract_decision_batch_runs_all_decisions(tmp_path):
    store = _store_ecb(tmp_path)
    results = extract_decision_batch(store)
    assert len(results) == 1
    assert len(store.get_facts(bank="ecb")) == 11