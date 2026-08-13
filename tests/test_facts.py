"""Phase 4 — Fact model, identity, persistence and provenance tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from argus.classification.base import Confidence
from argus.facts import (
    ExtractionResult,
    Fact,
    FactLocation,
    FactPeriod,
    FactValue,
    LocationKind,
    METHOD_RULE,
    METHOD_TABLE,
    PeriodKind,
    ValueKind,
    basis_points,
    boolean_value,
    categorical,
    date_value,
    fact_id_of,
    null_value,
    number,
    percentage,
    quarter,
    text_value,
    year,
)
from argus.models import Document, DocumentStatus, Publication
from argus.store import Store


def make_publication(store: Store, bank="ecb") -> Publication:
    return store.upsert_publication(
        Publication(
            central_bank=bank,
            title="Monetary policy decisions",
            url=f"https://x.test/pubs/decisions-{bank}",
            source_id="src",
            source_url="https://x.test/feed",
            publication_date=datetime(2026, 8, 13, tzinfo=timezone.utc),
        )
    )


def rate_fact(pub, *, doc="doc-1", value=4.25, **kw) -> Fact:
    fields = dict(
        publication_id=pub.id if pub is not None else "pub-1",
        document_id=doc,
        subject="policy_rate",
        predicate="value",
        value=percentage(value, source_text=f"{value} percent"),
        extraction_method=METHOD_RULE,
        extraction_version="1.0.0",
        confidence=Confidence.HIGH,
    )
    fields.update(kw)
    return Fact(**fields)


def store_pub(tmp_path) -> Store:
    store = Store(tmp_path / "facts.db")
    return store


# ---------------------------------------------------------------------------
# model semantics
# ---------------------------------------------------------------------------


def test_value_kinds_are_machine_readable():
    assert percentage(4.25).value == 4.25
    assert percentage(4.25).kind is ValueKind.PERCENTAGE
    assert basis_points(25).value == 25
    assert basis_points(-25).value == -25
    assert number(1.4, unit="trn").value == 1.4
    assert number(1.4, unit="trn").unit == "trn"


def test_numeric_kinds_reject_non_numbers():
    with pytest.raises(TypeError):
        percentage("4.25")


def test_null_value():
    v = null_value(source_text="not disclosed")
    assert v.value is None
    assert v.kind is ValueKind.NULL


def test_boolean_and_date_values():
    assert boolean_value(True).value is True
    assert date_value("2026-08-14").value == "2026-08-14"


# ---------------------------------------------------------------------------
# 1. quantitative fact  2. percentage fact
# ---------------------------------------------------------------------------


def test_quantitative_percentage_fact():
    fact = rate_fact(None)
    assert fact.subject == "policy_rate"
    assert fact.predicate == "value"
    assert fact.value.kind is ValueKind.PERCENTAGE
    assert fact.value.value == 4.25
    assert fact.value.source_text == "4.25 percent"


# ---------------------------------------------------------------------------
# 3. basis-point change
# ---------------------------------------------------------------------------


def test_basis_point_change():
    fact = Fact(
        publication_id="p1",
        document_id="d1",
        subject="policy_rate",
        predicate="change",
        value=basis_points(25, source_text="raised by 25 basis points"),
        previous_value=percentage(4.00),
        change=basis_points(25),
        extraction_method=METHOD_RULE,
    )
    assert fact.change.value == 25
    assert fact.change.kind is ValueKind.BASIS_POINTS
    assert fact.previous_value.value == 4.00


# ---------------------------------------------------------------------------
# 4. projection with forecast period
# ---------------------------------------------------------------------------


def test_projection_fact_with_period():
    fact = Fact(
        publication_id="p1",
        document_id="d1",
        subject="inflation",
        predicate="projection",
        value=percentage(2.1, source_text="2.1 percent in 2027"),
        period=year("2027", label="in 2027"),
        extraction_method=METHOD_RULE,
    )
    assert fact.period.kind is PeriodKind.YEAR
    assert fact.period.value == "2027"
    assert fact.period.canonical() == "year:2027"


def test_period_canonical_forms_are_sortable():
    periods = [quarter("2027-Q4"), year("2028"), quarter("2027-Q1")]
    keys = [p.canonical() for p in sorted(periods, key=lambda p: p.canonical())]
    assert keys == ["quarter:2027-Q1", "quarter:2027-Q4", "year:2028"]


# ---------------------------------------------------------------------------
# 5. qualitative fact  6. text/source statement
# ---------------------------------------------------------------------------


def test_qualitative_categorical_fact():
    fact = Fact(
        publication_id="p1",
        document_id="d1",
        subject="inflation_risk",
        predicate="assessment",
        value=categorical("upside", source_text="risks to inflation are tilted to the upside"),
        extraction_method=METHOD_RULE,
    )
    assert fact.value.value == "upside"
    assert fact.value.source_text == "risks to inflation are tilted to the upside"


def test_text_statement_fact_preserves_wording():
    quote = "the policy rate may need to remain restrictive for longer"
    fact = Fact(
        publication_id="p1",
        document_id="d1",
        subject="forward_guidance",
        predicate="statement",
        value=text_value(quote),
        source_text=quote,
        extraction_method=METHOD_RULE,
    )
    assert fact.value.value == quote
    assert fact.source_text == quote


# ---------------------------------------------------------------------------
# 7. multiple temporal fields
# ---------------------------------------------------------------------------


def test_multiple_temporal_dimensions_coexist():
    fact = Fact(
        publication_id="p1",
        document_id="d1",
        subject="policy_rate",
        predicate="value",
        value=percentage(4.25),
        period=year("2028", label="forecast horizon 2028"),
        effective_date=datetime(2026, 8, 14, tzinfo=timezone.utc),
        extraction_method=METHOD_RULE,
    )
    assert fact.period is not None
    assert fact.effective_date.date().isoformat() == "2026-08-14"


# ---------------------------------------------------------------------------
# 8. provenance  9. source location
# ---------------------------------------------------------------------------


def test_provenance_identifies_document_publication():
    fact = Fact(
        publication_id="pub-1",
        document_id="sha256-abc",
        subject="policy_rate",
        predicate="value",
        value=percentage(4.25),
        extraction_method=METHOD_RULE,
    )
    assert fact.publication_id == "pub-1"
    assert fact.document_id == "sha256-abc"


def test_html_compatible_source_location():
    fact = Fact(
        publication_id="p1",
        document_id="d1",
        subject="policy_rate",
        predicate="value",
        value=percentage(4.25),
        source_location=FactLocation(LocationKind.SECTION, section=2),
        extraction_method=METHOD_RULE,
    )
    assert fact.source_location.kind is LocationKind.SECTION
    assert fact.source_location.section == 2


def test_pdf_compatible_source_location():
    fact = Fact(
        publication_id="p1",
        document_id="d1",
        subject="policy_rate",
        predicate="value",
        value=percentage(4.25),
        source_location=FactLocation(LocationKind.PAGE, page=7),
        extraction_method=METHOD_RULE,
    )
    assert fact.source_location.page == 7


def test_table_compatible_source_location():
    fact = Fact(
        publication_id="p1",
        document_id="d1",
        subject="gdp",
        predicate="projection",
        value=percentage(1.4),
        source_location=FactLocation(LocationKind.TABLE, table=0, row=3, column=2),
        extraction_method=METHOD_TABLE,
    )
    assert fact.source_location.table == 0
    assert fact.source_location.row == 3
    assert fact.source_location.column == 2


# ---------------------------------------------------------------------------
# 10. serialization / deserialization
# ---------------------------------------------------------------------------


def test_round_trip_dict():
    fact = Fact(
        publication_id="p1",
        document_id="d1",
        central_bank="ecb",
        subject="inflation",
        predicate="projection",
        value=percentage(2.1, source_text="2.1 percent"),
        previous_value=percentage(2.4),
        change=percentage(-0.3),
        period=year("2027", label="in 2027"),
        effective_date=datetime(2026, 8, 14, tzinfo=timezone.utc),
        source_location=FactLocation(LocationKind.TABLE, table=0, row=2),
        source_text="2.1 percent",
        extraction_method=METHOD_RULE,
        extraction_version="1.0",
        confidence=Confidence.HIGH,
        extracted_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
    )
    fact.resolve_id()
    restored = Fact.from_dict(fact.to_dict())
    assert restored == fact
    assert restored.value.source_text == "2.1 percent"
    assert restored.period.canonical() == "year:2027"


def test_factvalue_round_trip_range():
    v = FactValue(ValueKind.RANGE, min=1.0, max=2.5, unit="quarter")
    restored = FactValue.from_dict(v.to_dict())
    assert restored == v


# ---------------------------------------------------------------------------
# 11+12+13. identity, persistence, idempotence
# ---------------------------------------------------------------------------


def test_deterministic_identity():
    kw = dict(
        publication_id="pub-1",
        document_id="doc-1",
        subject="policy_rate",
        predicate="value",
    )
    a = rate_fact(None)
    b = rate_fact(None)
    assert a.compute_fact_id() == b.compute_fact_id()
    assert a.compute_fact_id() == fact_id_of(**kw)


def test_identity_excludes_value_but_includes_period():
    base = dict(publication_id="p", document_id="d", subject="policy_rate", predicate="value")
    assert fact_id_of(**base) == fact_id_of(**base, **{})
    assert fact_id_of(**base) == fact_id_of(**base)
    assert fact_id_of(**base, period=year("2027")) != fact_id_of(**base)
    assert fact_id_of(**base, qualifier="target_range") != fact_id_of(**base)


def test_persist_and_retrieve(tmp_path):
    store = store_pub(tmp_path)
    pub = make_publication(store)
    fact = rate_fact(pub)
    fact.extracted_at = datetime(2026, 8, 13, tzinfo=timezone.utc)
    store.save_fact(fact)
    stored = store.get_fact(fact.resolve_id())
    assert stored is not None
    assert stored.subject == "policy_rate"
    assert stored.predicate == "value"
    assert stored.value.value == 4.25
    assert stored.value.kind is ValueKind.PERCENTAGE
    assert stored.central_bank == "ecb"  # filled in from the publication
    assert stored.publication_id == pub.id
    assert stored.document_id == "doc-1"
    assert stored.extraction_version == "1.0.0"


def test_reinsert_is_idempotent(tmp_path):
    store = store_pub(tmp_path)
    pub = make_publication(store)
    store.save_fact(rate_fact(pub))
    store.save_fact(rate_fact(pub))
    assert len(store.get_facts(publication_id=pub.id)) == 1


def test_upsert_updates_value_in_place(tmp_path):
    store = store_pub(tmp_path)
    pub = make_publication(store)
    store.save_fact(rate_fact(pub, value=4.25))
    store.save_fact(rate_fact(pub, value=4.50))  # correction, same identity
    facts = store.get_facts(publication_id=pub.id)
    assert len(facts) == 1  # no duplicate
    assert facts[0].value.value == 4.50


# ---------------------------------------------------------------------------
# 14. retrieval
# ---------------------------------------------------------------------------


def test_retrieval_filters(tmp_path):
    store = store_pub(tmp_path)
    ecb = make_publication(store, bank="ecb")
    fed = make_publication(store, bank="fed")
    ecb_rate = rate_fact(ecb, doc="d-ecb")
    ecb_infl = Fact(
        publication_id=ecb.id,
        document_id="d-ecb",
        subject="inflation",
        predicate="projection",
        value=percentage(2.1),
        period=year("2027"),
        extraction_method=METHOD_RULE,
    )
    fed_rate = rate_fact(fed, doc="d-fed", value=4.00)
    for f in (ecb_rate, ecb_infl, fed_rate):
        store.save_fact(f)
    assert len(store.get_facts()) == 3
    assert len(store.get_facts(bank="ecb")) == 2
    assert [f.subject for f in store.get_facts(subject="policy_rate")] == ["policy_rate"] * 2
    assert len(store.get_facts(predicate="projection")) == 1
    assert len(store.get_facts(value_type="percentage")) == 3
    assert len(store.get_facts(document_id="d-fed")) == 1
    assert len(store.get_facts(publication_id=ecb.id, limit=1)) == 1


# ---------------------------------------------------------------------------
# 15. reprocessing / rebuild
# ---------------------------------------------------------------------------


def test_rebuild_facts_for_document(tmp_path):
    store = store_pub(tmp_path)
    pub = make_publication(store)
    store.save_fact(rate_fact(pub))
    stale = Fact(
        publication_id=pub.id,
        document_id="doc-1",
        subject="unemployment",
        predicate="projection",
        value=percentage(4.2),
        extraction_method=METHOD_RULE,
    )
    store.save_fact(stale)
    assert len(store.get_facts(document_id="doc-1")) == 2
    result = ExtractionResult(
        publication_id=pub.id,
        document_id="doc-1",
        facts=[rate_fact(pub, value=4.25)],
        warnings=["projection table ignored"],
    )
    count = store.rebuild_facts_for_document("doc-1", result)
    assert count == 1
    remaining = store.get_facts(document_id="doc-1")
    assert len(remaining) == 1
    assert remaining[0].subject == "policy_rate"


def test_delete_facts_for_publication(tmp_path):
    store = store_pub(tmp_path)
    pub = make_publication(store)
    store.save_fact(rate_fact(pub))
    assert store.delete_facts_for_publication(pub.id) == 1
    assert len(store.get_facts()) == 0


def test_save_facts_accepts_extraction_result(tmp_path):
    store = store_pub(tmp_path)
    pub = make_publication(store)
    result = ExtractionResult(publication_id=pub.id, document_id="doc-1", facts=[rate_fact(pub)])
    assert store.save_facts(result) == 1
    assert len(store.get_facts(document_id="doc-1")) == 1


# ---------------------------------------------------------------------------
# 16. missing optional fields
# ---------------------------------------------------------------------------


def test_fact_without_optional_fields(tmp_path):
    store = store_pub(tmp_path)
    pub = make_publication(store)
    fact = Fact(
        publication_id=pub.id,
        document_id="doc-1",
        subject="meeting",
        predicate="decision",
        extraction_method=METHOD_RULE,
    )
    store.save_fact(fact)
    stored = store.get_fact(fact.resolve_id())
    assert stored is not None
    assert stored.value is None
    assert stored.period is None
    assert stored.source_location is None
    assert stored.effective_date is None
    assert stored.previous_value is None
    assert stored.change is None


# ---------------------------------------------------------------------------
# ExtractionResult contract
# ---------------------------------------------------------------------------


def test_extraction_result_contract():
    pub_id, doc_id = "pub-1", "doc-1"
    result = ExtractionResult(
        publication_id=pub_id,
        document_id=doc_id,
        warnings=["table skipped"],
    )
    assert result.publication_id == pub_id
    assert result.document_id == doc_id
    result.add(Fact(publication_id=pub_id, document_id=doc_id, subject="s", predicate="p"))
    assert len(result.facts) == 1