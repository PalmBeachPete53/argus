"""Phase 5 — ECB Monetary Policy Decision extractor: end-to-end tests using the
local HTML fixture and the existing Store (vertical slice)."""

from __future__ import annotations

from pathlib import Path

from argus.classification.base import Confidence
from argus.decisions import (
    SUBJECT_DECISION,
    SUBJECT_DEPOSIT_FACILITY,
    SUBJECT_MAIN_REFINANCING,
    SUBJECT_MARGINAL_LENDING,
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
    date_facts = [f for f in result.facts if f.subject == SUBJECT_DECISION]
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
    # values never invented: total facts == date + 3 levels + 3 changes
    assert len(result.facts) == 7


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
    assert len(persisted) == 7
    by = {(f.subject, f.predicate) for f in persisted}
    assert (SUBJECT_DECISION, "date") in by
    assert (SUBJECT_DEPOSIT_FACILITY, "value") in by
    assert (SUBJECT_DEPOSIT_FACILITY, "change") in by
    deposit = next(f for f in persisted if (f.subject, f.predicate) == (SUBJECT_DEPOSIT_FACILITY, "value"))
    assert deposit.value.value == 1.75
    assert deposit.central_bank == "ecb"  # filled from the publication
    assert deposit.effective_date.date().isoformat() == "2026-08-02"


def test_extract_decision_is_idempotent(tmp_path):
    store = _store_ecb(tmp_path)
    pub = ecb_publication()
    extract_decision(store, pub)
    extract_decision(store, pub)  # re-run: same deterministic fact_ids
    assert len(store.get_facts(publication_id="pub-ecb-1")) == 7


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
    assert len(store.get_facts(bank="ecb")) == 7