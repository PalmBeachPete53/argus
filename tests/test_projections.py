"""Phase 9 — ECB Economic Projections extractor: end-to-end tests using the
local HTML fixtures and the existing Store (vertical slice).

Covers: classification gating (``economic_projections``), table-driven
extraction (row × column × year × value × unit integrity), variable identity
(``inflation`` / ``core_inflation`` / ``gdp``), periods from table headers,
units preserved explicitly (percentage vs percentage points), revisions only
when explicitly stated (never ``current − previous``), the value gate (a bare
cell without variable+year+unit identity is never a Fact,
``UNKNOWN ≠ PROJECTION``), provenance (table/row/column + verbatim rows),
``speaker`` never set, deterministic extraction, idempotent and empty-result
persistence, and Phase 5/6/7/8 coexistence.
"""

from __future__ import annotations

from pathlib import Path

from argus.classification.base import Confidence
from argus.decisions import extract_decision
from argus.documents import Normalizer
from argus.documents.base import DocumentTable, NormalizedDocument
from argus.facts import ExtractionResult, LocationKind, ValueKind
from argus.minutes import extract_minutes
from argus.models import Document, DocumentStatus, Publication
from argus.press_conferences import extract_press_conference
from argus.projections import (
    PROJECTIONS_PUBLICATION_TYPES,
    SUBJECT_CORE_INFLATION,
    SUBJECT_GDP,
    SUBJECT_INFLATION,
    EcbProjectionsExtractor,
    ProjectionsExtractor,
    extract_projections,
    extract_projections_batch,
)
from argus.statements import extract_statement
from argus.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "documents"
ECB_URL = "https://www.ecb.europa.eu/press/projections/html/ecb.projections202603_ecbstaff~ebe291cd3d.en.html"


def projections_publication(**kw) -> Publication:
    fields = dict(
        central_bank="ecb",
        title="ECB staff macroeconomic projections for the euro area, March 2026",
        url=ECB_URL,
        source_id="ecb-projections",
        source_url="https://www.ecb.europa.eu/press/projections/html/index.en.html",
        id="pub-ecb-projections",
    )
    fields.update(kw)
    return Publication(**fields)


def normalized_fixture(name: str) -> object:
    doc = Document(
        publication_id="pub-ecb-projections",
        url=ECB_URL,
        kind="html",
        status=DocumentStatus.FETCHED,
        local_path=str(FIXTURES / name),
    )
    return Normalizer().parse(doc)


def extract_fixture(name: str):
    return EcbProjectionsExtractor().extract(projections_publication(), normalized_fixture(name))


def facts_by(result, subject: str, predicate: str):
    return [f for f in result.facts if f.subject == subject and f.predicate == predicate]


def period_of(fact) -> str | None:
    if fact.period is None:
        return None
    kind = fact.period.kind
    kind_str = kind.value if hasattr(kind, "value") else kind  # persisted rows keep a plain string
    return f"{kind_str}:{fact.period.value}"


def _doc_with_tables(tables: list[DocumentTable]) -> NormalizedDocument:
    return NormalizedDocument(
        publication_id="pub-ecb-projections",
        document_id="sha-tables",
        source_url=ECB_URL,
        local_path=None,
        document_kind="html",
        tables=tables,
    )


# ---------------------------------------------------------------------------
# golden facts across all ECB projections fixtures
# ---------------------------------------------------------------------------

GOLDEN = {
    "ecb_projections.html": {
        "warnings": [],
        "count": 12,
        "primary": "projections:current",
        "qualifiers": {"projections:current"},
        "projections": {
            SUBJECT_INFLATION: {"year:2025": 2.4, "year:2026": 2.0, "year:2027": 1.9, "year:2028": 2.1},
            SUBJECT_CORE_INFLATION: {"year:2025": 2.6, "year:2026": 2.3, "year:2027": 2.1, "year:2028": 2.0},
            SUBJECT_GDP: {"year:2025": 0.9, "year:2026": 1.2, "year:2027": 1.4, "year:2028": 1.6},
        },
        "revisions": {},
    },
    "ecb_projections_revisions.html": {
        "warnings": [],
        "count": 27,
        "primary": "projections:2026-03",
        "qualifiers": {
            "projections:2026-03",
            "projections:2025-12",
            "projections:revision_vs:2025-12",
        },
        "projections": {
            SUBJECT_INFLATION: {"year:2026": 2.0, "year:2027": 1.9, "year:2028": 2.1},
            SUBJECT_CORE_INFLATION: {"year:2026": 2.3, "year:2027": 2.1, "year:2028": 2.0},
            SUBJECT_GDP: {"year:2026": 1.2, "year:2027": 1.4, "year:2028": 1.6},
        },
        "revisions": {
            SUBJECT_INFLATION: {"year:2026": 0.1, "year:2027": -0.2, "year:2028": 0.0},
            SUBJECT_CORE_INFLATION: {"year:2026": 0.0, "year:2027": -0.1, "year:2028": 0.1},
            SUBJECT_GDP: {"year:2026": 0.3, "year:2027": 0.1, "year:2028": 0.0},
        },
    },
    "ecb_projections_ambiguous.html": {
        "warnings": ["no_projection_table"],
        "count": 0,
        "primary": "projections:current",
        "qualifiers": set(),
        "projections": {},
        "revisions": {},
    },
    "ecb_projections_minimal.html": {
        "warnings": [],
        "count": 2,
        "primary": "projections:current",
        "qualifiers": {"projections:current"},
        "projections": {SUBJECT_INFLATION: {"year:2026": 2.0, "year:2027": 1.9}},
        "revisions": {},
    },
}


def test_golden_facts_across_all_fixtures():
    for name, expected in GOLDEN.items():
        result = extract_fixture(name)
        assert result.warnings == expected["warnings"], (name, result.warnings)
        assert len(result.facts) == expected["count"], name
        assert {f.identity_qualifier for f in result.facts} == expected["qualifiers"], (name, result.facts)

        for subject, predicate, expected_map in (
            (subj, "projection", expected["projections"][subj])
            for subj in expected["projections"]
        ):
            primary = [f for f in facts_by(result, subject, predicate) if f.identity_qualifier == expected["primary"]]
            got = {(period_of(f), f.value.value) for f in primary}
            assert got == set(expected_map.items()), (name, subject, got)

        for subject in expected["revisions"]:
            got = {(period_of(f), f.value.value) for f in facts_by(result, subject, "revision")}
            assert got == set(expected["revisions"][subject].items()), (name, subject, got)
            assert all(f.value.unit == "pp" for f in facts_by(result, subject, "revision")), name


# ---------------------------------------------------------------------------
# table structure: row × column × year × value × unit integrity
# ---------------------------------------------------------------------------


def test_subject_mapping_is_distinct():
    result = extract_fixture("ecb_projections.html")
    assert {f.subject for f in result.facts} == {SUBJECT_INFLATION, SUBJECT_CORE_INFLATION, SUBJECT_GDP}
    assert all(f.predicate == "projection" for f in result.facts)


def test_periods_come_from_table_headers():
    result = extract_fixture("ecb_projections.html")
    inflation = facts_by(result, SUBJECT_INFLATION, "projection")
    assert {period_of(f) for f in inflation} == {"year:2025", "year:2026", "year:2027", "year:2028"}
    hdr_2027 = next(f for f in inflation if period_of(f) == "year:2027")
    assert hdr_2027.period.label == "2027"


def test_value_kind_is_percentage_for_projections():
    result = extract_fixture("ecb_projections.html")
    for fact in result.facts:
        assert fact.value.kind is ValueKind.PERCENTAGE
        assert fact.value.value == float(fact.value.source_text)


def test_source_location_pins_table_row_column():
    result = extract_fixture("ecb_projections.html")
    hicp_2027 = next(
        f for f in result.facts if f.subject == SUBJECT_INFLATION and period_of(f) == "year:2027"
    )
    assert hicp_2027.source_location.kind is LocationKind.TABLE
    assert hicp_2027.source_location.table == 0
    assert hicp_2027.source_location.row == 0  # first data row = HICP
    assert hicp_2027.source_location.column == 3  # 2027 is the 3rd value column (after Variable)
    core_2028 = next(
        f for f in result.facts if f.subject == SUBJECT_CORE_INFLATION and period_of(f) == "year:2028"
    )
    assert (core_2028.source_location.row, core_2028.source_location.column) == (1, 4)


def test_unrecognized_variables_are_ignored():
    table = DocumentTable(
        order=0,
        name="Real GDP, trade and labour market projections",
        headers=["Variable", "2026", "2027", "2028"],
        rows=[
            ["Real GDP", "1.2", "1.4", "1.6"],
            ["Private consumption", "1.0", "1.1", "1.2"],
            ["Unemployment rate", "6.3", "6.1", "5.9"],
            ["Employment", "0.8", "0.9", "0.9"],
        ],
    )
    result = EcbProjectionsExtractor().extract(projections_publication(), _doc_with_tables([table]))
    assert {f.subject for f in result.facts} == {SUBJECT_GDP}
    assert len(result.facts) == 3
    assert not any("consumption" in f.subject for f in result.facts)


def test_technical_assumptions_table_is_never_mined():
    table = DocumentTable(
        order=0,
        name="Technical assumptions",
        headers=["Variable", "2026", "2027", "2028"],
        rows=[
            ["Oil price (USD/barrel)", "60", "62", "63"],
            ["USD/EUR exchange rate", "1.08", "1.08", "1.08"],
            ["Three-month EURIBOR (percentage per annum)", "2.1", "2.0", "1.9"],
        ],
    )
    result = EcbProjectionsExtractor().extract(projections_publication(), _doc_with_tables([table]))
    assert result.facts == []
    assert result.warnings == ["no_projection_table"]


def test_scenario_columns_without_years_are_not_projection_columns():
    table = DocumentTable(
        order=0,
        name="Growth and inflation projections under alternative scenarios",
        headers=["Variable", "Baseline", "Adverse scenario", "Severe scenario"],
        rows=[
            ["HICP", "2.0", "2.2", "2.4"],
            ["Real GDP", "1.2", "0.8", "0.4"],
        ],
    )
    result = EcbProjectionsExtractor().extract(projections_publication(), _doc_with_tables([table]))
    assert result.facts == []


def test_value_gate_bare_cell_without_identity_is_never_a_fact():
    # year headers but no variable label -> no identity -> no facts
    unlabelled = DocumentTable(
        order=0, name="Assumed paths", headers=["2026", "2027", "2028"],
        rows=[["", "2.0", "1.9"], ["", "1.2", "1.4"]],
    )
    # variable + year, but empty/placeholder cells only -> no facts
    empty_cells = DocumentTable(
        order=1, name="Partial projections", headers=["Variable", "2026", "2027"],
        rows=[["HICP", "", "-"], ["Real GDP", "–", "…"]],
    )
    result = EcbProjectionsExtractor().extract(
        projections_publication(), _doc_with_tables([unlabelled, empty_cells])
    )
    assert result.facts == []
    assert result.warnings == ["no_projection_table"]


def test_footnote_markers_are_stripped_not_invented():
    table = DocumentTable(
        order=0,
        name="Price and cost developments",
        headers=["Variable", "2026", "2027"],
        rows=[
            ["HICP", "2.0 1)", "1.9*"],
            ["Real GDP", "1.2", "1.4"],
        ],
    )
    result = EcbProjectionsExtractor().extract(projections_publication(), _doc_with_tables([table]))
    hicp_2026 = next(f for f in facts_by(result, SUBJECT_INFLATION, "projection") if period_of(f) == "year:2026")
    assert hicp_2026.value.value == 2.0
    assert hicp_2026.value.source_text == "2.0 1)"  # verbatim cell preserved
    hicp_2027 = next(f for f in facts_by(result, SUBJECT_INFLATION, "projection") if period_of(f) == "year:2027")
    assert hicp_2027.value.value == 1.9
    assert hicp_2027.value.source_text == "1.9*"


# ---------------------------------------------------------------------------
# units preserved explicitly
# ---------------------------------------------------------------------------


def test_units_are_preserved_explicitly():
    result = extract_fixture("ecb_projections_revisions.html")
    # projections: annual percentage changes -> percentage kind (2.1 stays 2.1%, never a plain number)
    assert all(f.value.kind is ValueKind.PERCENTAGE for f in result.facts if f.predicate == "projection")
    # revisions: percentage points -> number with unit "pp" (never basis points, never converted)
    for f in result.facts:
        if f.predicate == "revision":
            assert f.value.kind is ValueKind.NUMBER
            assert f.value.unit == "pp"
    revision = next(
        f for f in result.facts
        if f.predicate == "revision" and f.subject == SUBJECT_INFLATION and period_of(f) == "year:2027"
    )
    assert revision.value.value == -0.2  # in percentage points, never -20 basis points
    assert revision.value.unit == "pp"


# ---------------------------------------------------------------------------
# revisions: only when explicitly stated, never computed
# ---------------------------------------------------------------------------


def test_revisions_only_from_explicit_column_block():
    result = extract_fixture("ecb_projections_revisions.html")
    assert len([f for f in result.facts if f.predicate == "revision"]) == 9
    assert all(f.identity_qualifier == "projections:revision_vs:2025-12" for f in result.facts if f.predicate == "revision")


def test_revision_is_never_computed():
    result = extract_fixture("ecb_projections_revisions.html")
    # explicit revision for HICP 2027 is -0.2 pp; current 2027 = 1.9, previous 2027 = 2.0,
    # so the computed delta would be -0.1 — the stored value must be the EXPLICIT -0.2.
    revision = next(
        f for f in result.facts
        if f.predicate == "revision" and f.subject == SUBJECT_INFLATION and period_of(f) == "year:2027"
    )
    assert revision.value.value == -0.2
    # no fact carries a derived previous_value / change / computed delta
    assert all(f.previous_value is None for f in result.facts)
    assert all(f.change is None for f in result.facts)
    assert all(f.predicate in ("projection", "revision") for f in result.facts)


def test_current_and_previous_projections_are_distinguished_by_qualifier():
    result = extract_fixture("ecb_projections_revisions.html")
    by = {f.identity_qualifier: f for f in result.facts}
    current = [f for f in result.facts if f.identity_qualifier == "projections:2026-03"]
    previous = [f for f in result.facts if f.identity_qualifier == "projections:2025-12"]
    assert len(current) == 9
    assert len(previous) == 9
    # the two projection sets are kept separate, never merged or differenced
    cur_2027 = {f.subject: f.value.value for f in current if period_of(f) == "year:2027"}
    prev_2027 = {f.subject: f.value.value for f in previous if period_of(f) == "year:2027"}
    assert cur_2027 == {SUBJECT_INFLATION: 1.9, SUBJECT_CORE_INFLATION: 2.1, SUBJECT_GDP: 1.4}
    assert prev_2027 == {SUBJECT_INFLATION: 2.0, SUBJECT_CORE_INFLATION: 2.2, SUBJECT_GDP: 1.3}


def test_main_fixture_has_no_revisions_invented():
    result = extract_fixture("ecb_projections.html")
    assert all(f.predicate == "projection" for f in result.facts)
    assert all(f.identity_qualifier == "projections:current" for f in result.facts)


# ---------------------------------------------------------------------------
# provenance + no interpretation
# ---------------------------------------------------------------------------


def test_provenance_is_traceable():
    for name in GOLDEN:
        document = normalized_fixture(name)
        result = extract_fixture(name)
        for fact in result.facts:
            assert fact.extraction_version == EcbProjectionsExtractor.extraction_version
            assert fact.extraction_method == "table_extraction"
            assert fact.source_location is not None
            assert fact.source_location.kind is LocationKind.TABLE
            loc = fact.source_location
            table = document.tables[loc.table]
            assert fact.source_text == " | ".join(str(cell or "") for cell in table.rows[loc.row])
            assert fact.source_text in table.render()
            assert fact.value.source_text in fact.source_text
            assert fact.publication_id == "pub-ecb-projections"
            assert fact.document_id
            assert fact.effective_date is None
            assert fact.confidence is Confidence.HIGH
            assert fact.speaker is None


def test_speaker_never_invented():
    for name in GOLDEN:
        result = extract_fixture(name)
        assert all(f.speaker is None for f in result.facts), name


def test_no_hawkish_dovish_or_forex_interpretation():
    for name in GOLDEN:
        result = extract_fixture(name)
        for fact in result.facts:
            raw = str(fact.value.value or "").lower()
            assert "hawkish" not in raw
            assert "dovish" not in raw
            assert "bullish" not in raw and "bearish" not in raw
            assert "forex" not in raw and "eur/usd" not in raw
            assert fact.predicate not in ("sentiment", "market_reaction", "assessment", "statement")


def test_no_decision_statement_or_rationale_facts():
    result = extract_fixture("ecb_projections.html")
    phase_subjects = {
        "monetary_policy_decision", "main_refinancing_rate", "marginal_lending_rate",
        "deposit_facility_rate", "asset_purchase", "vote", "monetary_policy",
        "policy_guidance", "growth", "labour_market", "unemployment", "wages",
        "financial_conditions", "risk", "inflation_risk", "growth_risk",
    }
    assert not phase_subjects & {f.subject for f in result.facts}
    assert not any(f.predicate in ("date", "rationale", "change", "decision") for f in result.facts)


def test_projection_prose_is_never_mined():
    # the extractor is table-driven: numeric prose in sections yields nothing
    result = extract_fixture("ecb_projections.html")
    assert not any("projected" in (f.source_text or "") for f in result.facts)
    # the "2% target" wording is never a value
    assert not any(f.value.value == 2.0 and f.subject == SUBJECT_INFLATION and period_of(f) is None for f in result.facts)


# ---------------------------------------------------------------------------
# warnings + empty documents
# ---------------------------------------------------------------------------


def test_empty_document_warns_no_tables():
    doc = NormalizedDocument(
        publication_id="pub-ecb-projections",
        document_id="sha-empty",
        source_url=ECB_URL,
        local_path=None,
        document_kind="html",
        sections=[],
        tables=[],
    )
    result = EcbProjectionsExtractor().extract(projections_publication(), doc)
    assert result.warnings == ["no_tables"]
    assert result.facts == []


def test_ambiguous_fixture_warns_no_projection_table():
    result = extract_fixture("ecb_projections_ambiguous.html")
    assert result.warnings == ["no_projection_table"]
    assert result.facts == []


def test_non_projection_sections_are_ignored():
    # methodology / assumptions / disclaimer / legal-notice content yields nothing
    result = extract_fixture("ecb_projections_minimal.html")
    assert len(result.facts) == 2
    assert all(f.subject == SUBJECT_INFLATION for f in result.facts)


# ---------------------------------------------------------------------------
# determinism + idempotent persistence (vertical slice)
# ---------------------------------------------------------------------------


def _store_projections(tmp_path, name: str = "ecb_projections.html") -> Store:
    store = Store(tmp_path / f"{name}.db")
    store.upsert_publication(projections_publication())
    store.upsert_normalized_document(normalized_fixture(name))
    return store


def classify_projections(store: Store, *, publication_type: str = "economic_projections") -> None:
    store.set_classification(
        "pub-ecb-projections",
        central_bank="ecb",
        publication_type=publication_type,
        confidence=Confidence.HIGH.value,
        method="url_pattern",
        evidence=[],
    )


class _ZeroFactProjectionsExtractor(ProjectionsExtractor):
    """Stub projections extractor that yields no facts — used to simulate a
    re-extraction of an already-persisted document that now produces nothing."""

    bank = "ecb"
    extraction_version = "test-zero"

    def extract(self, publication, document):
        return ExtractionResult(
            publication_id=publication.id or publication.dedup_key or "",
            document_id=document.document_id,
        )


def test_extract_projections_persists_facts(tmp_path):
    store = _store_projections(tmp_path)
    classify_projections(store)
    results = extract_projections(store, projections_publication())
    assert len(results) == 1
    persisted = store.get_facts(publication_id="pub-ecb-projections")
    assert len(persisted) == 12
    assert {(f.subject, f.predicate) for f in persisted} == {
        (SUBJECT_INFLATION, "projection"),
        (SUBJECT_CORE_INFLATION, "projection"),
        (SUBJECT_GDP, "projection"),
    }
    inflation = next(f for f in persisted if f.subject == SUBJECT_INFLATION and period_of(f) == "year:2027")
    assert inflation.value.value == 1.9
    assert inflation.central_bank == "ecb"  # filled from the publication
    assert inflation.value.kind.value == "percentage"


def test_persistence_roundtrips_qualifier_and_unit(tmp_path):
    store = _store_projections(tmp_path, "ecb_projections_revisions.html")
    classify_projections(store)
    extract_projections(store, projections_publication())
    persisted = store.get_facts(publication_id="pub-ecb-projections")
    qualifiers = {f.identity_qualifier for f in persisted}
    assert "projections:2026-03" in qualifiers
    assert "projections:revision_vs:2025-12" in qualifiers
    revision = next(f for f in persisted if f.predicate == "revision")
    assert revision.value.unit == "pp"
    assert all(f.speaker is None for f in persisted)


def test_extract_projections_is_idempotent(tmp_path):
    store = _store_projections(tmp_path)
    classify_projections(store)
    pub = projections_publication()
    extract_projections(store, pub)
    first = sorted((f.fact_id, f.subject, f.predicate, period_of(f), f.value.value) for f in store.get_facts(publication_id="pub-ecb-projections"))
    extract_projections(store, pub)  # re-run: same deterministic fact_ids
    second = sorted((f.fact_id, f.subject, f.predicate, period_of(f), f.value.value) for f in store.get_facts(publication_id="pub-ecb-projections"))
    assert first == second
    assert len(second) == 12


def test_extraction_is_deterministic(tmp_path):
    for name in GOLDEN:
        store = _store_projections(tmp_path, name)
        result = extract_fixture(name)
        store.rebuild_facts_for_document(result.document_id, result)
        first = sorted((f.fact_id, f.subject, f.predicate, period_of(f), f.value.value) for f in store.get_facts(publication_id="pub-ecb-projections"))
        store.rebuild_facts_for_document(result.document_id, result)
        second = sorted((f.fact_id, f.subject, f.predicate, period_of(f), f.value.value) for f in store.get_facts(publication_id="pub-ecb-projections"))
        assert first == second, name
        assert len(first) == len(result.facts), name
        ids = [f.fact_id for f in result.facts]
        assert len(ids) == len(set(ids)), name  # no fact_id collisions


# ---------------------------------------------------------------------------
# classification gating (single source of truth = classifications table)
# ---------------------------------------------------------------------------


def test_gating_economic_projections_classification_allows_extraction(tmp_path):
    store = _store_projections(tmp_path)
    classify_projections(store, publication_type="economic_projections")
    results = extract_projections(store, projections_publication())
    assert len(results) == 1
    assert len(store.get_facts(publication_id="pub-ecb-projections")) == 12


def test_gating_other_classification_refuses_extraction(tmp_path):
    store = _store_projections(tmp_path)
    classify_projections(store, publication_type="press_conference")
    assert extract_projections(store, projections_publication()) == []
    assert store.get_facts(publication_id="pub-ecb-projections") == []


def test_gating_absent_classification_refuses_extraction(tmp_path):
    store = _store_projections(tmp_path)
    assert extract_projections(store, projections_publication()) == []
    assert store.get_facts(publication_id="pub-ecb-projections") == []


def test_gating_publication_type_cache_alone_never_authorizes(tmp_path):
    store = _store_projections(tmp_path)
    pub = projections_publication(publication_type="economic_projections")
    # the denormalized cache says economic_projections, but there is no
    # authoritative classification record -> extraction must be refused
    assert extract_projections(store, pub) == []
    assert store.get_facts(publication_id="pub-ecb-projections") == []


def test_gating_batch_respects_classification(tmp_path):
    store = _store_projections(tmp_path)
    assert extract_projections_batch(store) == []  # unclassified -> nothing extracted
    assert store.get_facts(publication_id="pub-ecb-projections") == []
    classify_projections(store)
    results = extract_projections_batch(store)
    assert len(results) == 1
    assert len(store.get_facts(bank="ecb")) == 12


def test_gating_never_persists_facts_when_not_authorized(tmp_path):
    store = _store_projections(tmp_path)
    classify_projections(store, publication_type="monetary_policy_report")
    assert extract_projections(store, projections_publication()) == []
    assert extract_projections_batch(store) == []
    assert store.get_facts(publication_id="pub-ecb-projections") == []


def test_projections_publication_types_are_recognized():
    assert PROJECTIONS_PUBLICATION_TYPES == ("economic_projections",)


# ---------------------------------------------------------------------------
# empty-result persistence: the current extraction result is the source of truth
# ---------------------------------------------------------------------------


def test_empty_result_persistence_clears_stale_facts(tmp_path):
    store = _store_projections(tmp_path)
    classify_projections(store)
    pub = projections_publication()
    extract_projections(store, pub)
    assert len(store.get_facts(publication_id="pub-ecb-projections")) == 12
    results = extract_projections(store, pub, extractor=_ZeroFactProjectionsExtractor())
    assert len(results) == 1
    assert results[0].facts == []
    assert store.get_facts(publication_id="pub-ecb-projections") == []


def test_empty_result_persistence_preserves_other_documents(tmp_path):
    store = _store_projections(tmp_path)
    classify_projections(store)
    pub = projections_publication()
    extract_projections(store, pub)
    extract_projections(store, pub, document=normalized_fixture("ecb_projections_minimal.html"))
    assert len(store.get_facts(publication_id="pub-ecb-projections")) == 14
    # zero-out only the nominal document; the other document's facts must stay
    extract_projections(
        store, pub, document=normalized_fixture("ecb_projections.html"),
        extractor=_ZeroFactProjectionsExtractor(),
    )
    persisted = store.get_facts(publication_id="pub-ecb-projections")
    assert len(persisted) == 2
    assert all(f.subject == SUBJECT_INFLATION for f in persisted)
    assert persisted[0].document_id == normalized_fixture("ecb_projections_minimal.html").document_id


def test_empty_result_persistence_is_idempotent(tmp_path):
    store = _store_projections(tmp_path)
    classify_projections(store)
    pub = projections_publication()
    extract_projections(store, pub)
    assert len(store.get_facts(publication_id="pub-ecb-projections")) == 12
    zero = _ZeroFactProjectionsExtractor()
    extract_projections(store, pub, extractor=zero)
    extract_projections(store, pub, extractor=zero)
    assert store.get_facts(publication_id="pub-ecb-projections") == []


# ---------------------------------------------------------------------------
# Phase 5 / 6 / 7 / 8 coexistence
# ---------------------------------------------------------------------------


def test_other_extractors_do_not_overlap_with_projections(tmp_path):
    """An economic projections publication never feeds the decision, statement,
    press conference or minutes extractors (gating on classification), and
    Phase 9 never emits Phase 5/6/7/8 fact subjects."""
    store = _store_projections(tmp_path)
    pub = projections_publication()
    classify_projections(store)
    # store-level helpers are gated on classification
    assert extract_decision(store, pub) == []
    assert extract_statement(store, pub) == []
    assert extract_press_conference(store, pub) == []
    assert extract_minutes(store, pub) == []
    # Phase 9 extraction produces its own facts only
    extract_projections(store, pub)
    persisted = store.get_facts(publication_id="pub-ecb-projections")
    phase_subjects = {
        "monetary_policy_decision", "main_refinancing_rate", "marginal_lending_rate",
        "deposit_facility_rate", "asset_purchase", "vote", "monetary_policy",
        "policy_guidance", "growth", "labour_market", "unemployment", "wages",
        "financial_conditions", "risk", "inflation_risk", "growth_risk",
    }
    assert not phase_subjects & {f.subject for f in persisted}
    assert all(f.predicate in ("projection", "revision") for f in persisted)
    assert all(f.extraction_version == EcbProjectionsExtractor.extraction_version for f in persisted)
    assert all(f.identity_qualifier.startswith("projections:") for f in persisted)


def test_projections_extractor_refuses_other_publication_types(tmp_path):
    store = _store_projections(tmp_path)
    classify_projections(store, publication_type="meeting_account")
    assert extract_projections(store, projections_publication()) == []
    assert store.get_facts(publication_id="pub-ecb-projections") == []