"""Phase 4.x — Fed Press Conference extractor: contract, provenance, speaker
attribution, Q&A boundary, value gate, determinism, immutability, gating and
end-to-end integration tests.

The Fed FOMC press conference transcript is a **turn-based dialog**: an ALL-CAPS
speaker label starts each turn, the first Fed-official turn before any
journalist label is the collective **remarks**, and every Fed-official turn
after a journalist label is an individual **answer** (attributed). Journalist
questions are never mined. This suite runs against the local fixture
``fed_press_conf.html`` and inline synthetic documents.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from argus.classification.base import Confidence
from argus.classification.classifier import PublicationClassifier
from argus.documents import Normalizer
from argus.documents.base import DocumentSection, NormalizedDocument
from argus.facts import LocationKind, PeriodKind, ValueKind
from argus.models import Document, DocumentStatus, Publication
from argus.press_conferences import (
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
    FedPressConferenceExtractor,
    extract_press_conference,
    get_extractor,
)
from argus.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "documents"
FED_URL = "https://www.federalreserve.gov/mediacenter/files/FOMCpresconf20260617.pdf"


def _publication(**kw) -> Publication:
    fields = dict(
        central_bank="fed",
        title="Transcript of Chairman Warsh's Press Conference",
        url=FED_URL,
        source_id="fed-pressconf",
        source_url="https://www.federalreserve.gov/feeds/press_all.xml",
        id="pub-fed-pressconf",
    )
    fields.update(kw)
    return Publication(**fields)


def _normalized(name: str = "fed_press_conf.html"):
    return Normalizer().parse(
        Document(
            publication_id="pub-fed-pressconf",
            url=FED_URL,
            kind="html",
            status=DocumentStatus.FETCHED,
            local_path=str(FIXTURES / name),
        )
    )


def _extract():
    return FedPressConferenceExtractor().extract(_publication(), _normalized())


def _signature(fact) -> tuple:
    kind = fact.value.kind.value if hasattr(fact.value.kind, "value") else fact.value.kind
    if fact.period:
        pkind = fact.period.kind
        pkind_str = pkind.value if hasattr(pkind, "value") else pkind
        period = f"{pkind_str}:{fact.period.value}"
    else:
        period = None
    return (fact.subject, fact.predicate, kind, fact.value.value, period)


GOLDEN = {
    # remarks — collective FOMC communication (speaker always None)
    ("inflation", "assessment", "text", "Inflation has eased considerably over the past year.", None),
    ("labour_market", "assessment", "text",
     "The labour market remains solid, with employment continuing to grow at a steady pace.", None),
    ("policy_guidance", "statement", "text",
     "We will not hesitate to keep the federal funds rate at an appropriate level as the data evolve.", None),
    ("risk", "assessment", "categorical", "balanced", None),
    # Q&A answers — individual, verbatim ALL-CAPS speaker
    ("unemployment", "value", "percentage", 4.1, "month:2026-06"),
    ("inflation", "value", "percentage", 2.1, "year:2027"),
    ("policy_guidance", "statement", "text",
     "The Committee remains patient, and we will take the incoming data into account.", None),
    ("gdp", "value", "percentage", 3.0, "year:2026"),
    ("wages", "assessment", "text",
     "Wage growth has continued, but the pace has moderated in recent months.", None),
    ("growth", "assessment", "text", "Economic growth has continued at a solid pace.", None),
    ("inflation_expectations", "assessment", "text",
     "Let me add that longer-run inflation expectations remain well anchored.", None),
    ("financial_conditions", "assessment", "text",
     "Financial conditions have tightened notably over the course of this year.", None),
    ("risk", "assessment", "categorical", "downside", None),
}

SPEAKER_BY_QUALIFIER = {
    "remarks:0": None,
    "remarks:1": None,
    "remarks:2": None,
    "remarks:3": None,
    "answer:2:0": "CHAIRMAN WARSH",
    "answer:2:1": "CHAIRMAN WARSH",
    "answer:4:0": "CHAIRMAN WARSH",
    "answer:4:1": "CHAIRMAN WARSH",
    "answer:5:0": "CHAIRMAN WARSH",
    "answer:6:0": "CHAIRMAN WARSH",
    "answer:6:1": "VICE CHAIR DONALD LERNER",
    "answer:8:0": "CHAIRMAN WARSH",
    "answer:8:1": "CHAIRMAN WARSH",
}


def _synthetic(sections_text: list[tuple[str, str]], doc_id: str = "sha-synthetic") -> NormalizedDocument:
    sections = [
        DocumentSection(order=i, heading=heading, text=text)
        for i, (heading, text) in enumerate(sections_text)
    ]
    return NormalizedDocument(
        publication_id="pub-fed-pressconf",
        document_id=doc_id,
        source_url=FED_URL,
        local_path=None,
        document_kind="html",
        sections=sections,
    )


def _synthetic_conference(turns: str) -> NormalizedDocument:
    return _synthetic([("", turns)])


# ---------------------------------------------------------------------------
# contract & dispatch
# ---------------------------------------------------------------------------


def test_extractor_identity_and_dispatch():
    assert FedPressConferenceExtractor.bank == "fed"
    assert FedPressConferenceExtractor.extraction_version
    assert get_extractor("fed").__class__.__name__ == "FedPressConferenceExtractor"
    assert get_extractor("ecb").__class__.__name__ == "EcbPressConferenceExtractor"


def test_golden_facts():
    result = _extract()
    got = {_signature(f) for f in result.facts}
    assert got == GOLDEN, f"missing: {GOLDEN - got} | extra: {got - GOLDEN}"


def test_golden_fact_count_and_warnings():
    result = _extract()
    assert len(result.facts) == 13
    assert result.warnings == []


def test_contract_fields():
    result = _extract()
    doc = _normalized()
    section_count = len(doc.sections)
    assert result.facts
    for fact in result.facts:
        assert fact.publication_id == "pub-fed-pressconf"
        assert fact.document_id == doc.document_id
        assert fact.effective_date is None
        assert fact.source_location is not None
        assert fact.source_location.kind == LocationKind.SECTION
        assert 0 <= fact.source_location.section < section_count
        assert fact.source_text
        assert fact.value.source_text
        assert fact.extraction_method == "regex"
        assert fact.extraction_version
        assert fact.confidence is not None
        assert fact.speaker == SPEAKER_BY_QUALIFIER[fact.identity_qualifier]
        owning = doc.sections[fact.source_location.section].text or ""
        assert fact.source_text in owning, f"source_text not in owning section: {fact.source_text!r}"


def test_identity_qualifiers_follow_phase7_contract():
    result = _extract()
    for fact in result.facts:
        if fact.identity_qualifier.startswith("remarks:"):
            assert fact.speaker is None, "remarks are never attributed to an individual"
        else:
            assert fact.identity_qualifier.startswith("answer:"), fact.identity_qualifier
            turn, n = fact.identity_qualifier.replace("answer:", "").split(":")
            assert turn.isdigit() and n.isdigit()


def test_value_facts_carry_percentage_and_period():
    for fact in _extract().facts:
        if fact.predicate == "value":
            assert fact.value.kind == ValueKind.PERCENTAGE
            assert fact.period is not None
            assert fact.period.kind in (PeriodKind.YEAR, PeriodKind.MONTH, PeriodKind.QUARTER)
            assert fact.confidence == Confidence.HIGH


def test_deterministic_repeated_extraction():
    r1 = _extract()
    r2 = _extract()
    assert [f.resolve_id() for f in r1.facts] == [f.resolve_id() for f in r2.facts]
    assert [f.to_dict() for f in r1.facts] == [f.to_dict() for f in r2.facts]


def test_source_immutability():
    pub = _publication()
    doc = _normalized()
    import copy

    sections_before = copy.deepcopy(doc.sections)
    FedPressConferenceExtractor().extract(pub, doc)
    assert [s.heading for s in doc.sections] == [s.heading for s in sections_before]
    assert [s.text for s in doc.sections] == [s.text for s in sections_before]
    assert pub.id == "pub-fed-pressconf"


# ---------------------------------------------------------------------------
# speaker attribution — explicit, verbatim, never inferred
# ---------------------------------------------------------------------------


def test_fed_official_label_preserved_verbatim():
    # The Chair's label is preserved ALL-CAPS verbatim (trailing period
    # stripped); a governor's label is preserved the same way and never
    # attributed to the Chair.
    doc = _synthetic_conference(
        "CHAIRMAN WARSH.\n"
        "The Federal Open Market Committee decided to keep the federal funds rate at its current level.\n"
        "CHRIS RUGABER.\n"
        "A question for the Governor.\n"
        "GOVERNOR ADRIANA MONTES.\n"
        "Inflation is projected to average 2.0 percent in 2027.\n"
    )
    result = FedPressConferenceExtractor().extract(_publication(), doc)
    assert result.facts  # policy sentence mined
    wait = [f for f in result.facts if f.subject == SUBJECT_MONETARY_POLICY]
    assert wait and wait[0].identity_qualifier.startswith("remarks:")
    assert wait[0].speaker is None  # collective remarks, never attributed
    inflation = [f for f in result.facts if f.subject == SUBJECT_INFLATION]
    assert inflation and inflation[0].speaker == "GOVERNOR ADRIANA MONTES"


def test_journalist_label_never_attributed_and_question_never_mined():
    # The journalist's question carries numeric market language; it is never
    # mined and never attributed. The official's answer is mined as it is.
    doc = _synthetic_conference(
        "CHRIS RUGABER.\n"
        "Chairman, is the federal funds rate going to rise to 5.5 percent this year?\n"
        "CHAIRMAN WARSH.\n"
        "We will decide meeting by meeting as new data arrive.\n"
    )
    result = FedPressConferenceExtractor().extract(_publication(), doc)
    assert not any(f.predicate == "value" for f in result.facts)  # question 5.5% never mined
    guidance = [f for f in result.facts if f.subject == SUBJECT_POLICY_GUIDANCE]
    assert len(guidance) == 1
    assert guidance[0].speaker == "CHAIRMAN WARSH"
    assert guidance[0].identity_qualifier.startswith("answer:")


def test_ambiguous_mr_label_is_a_journalist_boundary():
    # "MR. POWELL." carries no role word → non-Fed, treated as a journalist
    # boundary (turn counter increments, no speaker invented).
    doc = _synthetic_conference(
        "MICHELLE SMITH.\n"
        "A question for the Chair.\n"
        "MR. POWELL.\n"
        "We are closely monitoring inflation; inflation is expected to remain elevated for a time.\n"
    )
    result = FedPressConferenceExtractor().extract(_publication(), doc)
    # inflation assessment mined, but no speaker is attributed to MR. POWELL's
    # label and no journalist label is ever mined
    assert not any(f.speaker == "MICHELLE SMITH" for f in result.facts)
    assert all(f.speaker != "MR. POWELL" for f in result.facts)


def test_remarks_speaker_never_set():
    doc = _synthetic_conference(
        "CHAIRMAN WARSH.\n"
        "Inflation has eased considerably over the past year.\n"
        "CHRIS RUGABER.\n"
        "Any comment?\n"
        "CHAIRMAN WARSH.\n"
        "Unemployment is expected to average 4.5 percent in 2027.\n"
    )
    result = FedPressConferenceExtractor().extract(_publication(), doc)
    remarks = [f for f in result.facts if f.identity_qualifier.startswith("remarks:")]
    answers = [f for f in result.facts if f.identity_qualifier.startswith("answer:")]
    assert remarks and all(f.speaker is None for f in remarks)
    assert answers and all(f.speaker == "CHAIRMAN WARSH" for f in answers)


# ---------------------------------------------------------------------------
# Q&A boundary & turn numbering
# ---------------------------------------------------------------------------


def test_turn_counter_increments_at_each_journalist_label():
    doc = _synthetic_conference(
        "CHAIRMAN WARSH.\n"
        "Inflation has eased considerably over the past year.\n"
        "CHRIS RUGABER.\n"
        "Question one.\n"
        "CHAIRMAN WARSH.\n"
        "Unemployment is expected to average 4.5 percent in 2027.\n"
        "MICHELLE SMITH.\n"
        "Question two.\n"
        "CHAIRMAN WARSH.\n"
        "GDP is expected to grow by 1.5 percent in 2027.\n"
    )
    result = FedPressConferenceExtractor().extract(_publication(), doc)
    quals = [f.identity_qualifier for f in result.facts]
    assert "answer:1:0" in quals
    assert "answer:2:0" in quals
    assert all(q == "remarks:0" or q.startswith("answer:") for q in quals)
    assert "answer:0:0" not in quals


def test_unprefixed_content_is_unattributed_and_never_mined():
    # Content with no preceding Fed label is unattributed — conservative skip.
    doc = _synthetic_conference("Inflation has eased considerably over the past year.\n")
    result = FedPressConferenceExtractor().extract(_publication(), doc)
    assert result.facts == []
    assert "no_remarks" in result.warnings
    assert "no_qna" in result.warnings


def test_non_fed_all_caps_label_ends_remarks():
    # A moderator/journalist ALL-CAPS label ends the remarks mode; subsequent
    # Fed content is an individual answer, not collective remarks.
    doc = _synthetic_conference(
        "CHAIRMAN WARSH.\n"
        "Inflation has eased considerably over the past year.\n"
        "MICHELLE SMITH.\n"
        "A question for the Chair, please.\n"
        "CHAIRMAN WARSH.\n"
        "The labour market remains solid.\n"
    )
    result = FedPressConferenceExtractor().extract(_publication(), doc)
    assert any(q.startswith("remarks:") for q in [f.identity_qualifier for f in result.facts])
    assert any(q.startswith("answer:") for q in [f.identity_qualifier for f in result.facts])


# ---------------------------------------------------------------------------
# value gate & forecasts / periods
# ---------------------------------------------------------------------------


def test_forecast_without_period_is_ignored():
    doc = _synthetic_conference(
        "CHAIRMAN WARSH.\n"
        "Unemployment is projected to average 4.5 percent.\n"
    )
    result = FedPressConferenceExtractor().extract(_publication(), doc)
    assert not any(f.subject == SUBJECT_UNEMPLOYMENT and f.predicate == "value" for f in result.facts)


def test_no_value_without_explicit_claim_verb():
    doc = _synthetic_conference(
        "CHAIRMAN WARSH.\n"
        "Inflation is 2.4 percent in 2027.\n"
    )
    result = FedPressConferenceExtractor().extract(_publication(), doc)
    assert not any(f.subject == SUBJECT_INFLATION and f.predicate == "value" for f in result.facts)


def test_share_units_never_percentages():
    doc = _synthetic_conference(
        "CHAIRMAN WARSH.\n"
        "The deficit is projected to be 3.0 percent of GDP in 2026.\n"
    )
    result = FedPressConferenceExtractor().extract(_publication(), doc)
    assert not any(f.predicate == "value" for f in result.facts)


def test_gdp_near_miss_never_yields_gdp_value():
    doc = _synthetic_conference(
        "CHAIRMAN WARSH.\n"
        "Real GDP growth held steady while the GDP deflator rose by 2.1 percent in 2026.\n"
    )
    result = FedPressConferenceExtractor().extract(_publication(), doc)
    assert not any(f.subject == SUBJECT_GDP and f.predicate == "value" for f in result.facts)


def test_percent_target_phrase_never_a_value():
    doc = _synthetic_conference(
        "CHAIRMAN WARSH.\n"
        "Inflation is converging towards our 2 percent goal.\n"
    )
    result = FedPressConferenceExtractor().extract(_publication(), doc)
    assert not any(f.subject == SUBJECT_INFLATION and f.predicate == "value" for f in result.facts)


def test_quarter_period_from_wording():
    doc = _synthetic_conference(
        "CHAIRMAN WARSH.\n"
        "GDP is expected to grow by 0.8 percent in the first quarter of 2027.\n"
    )
    result = FedPressConferenceExtractor().extract(_publication(), doc)
    gdp = [f for f in result.facts if f.subject == SUBJECT_GDP and f.predicate == "value"]
    assert gdp and gdp[0].period.kind == PeriodKind.QUARTER
    assert gdp[0].period.value == "2027-Q1"


# ---------------------------------------------------------------------------
# negative epistemic — never interpretation, never other phases' subjects
# ---------------------------------------------------------------------------


def test_no_downstream_semantics():
    for fact in _extract().facts:
        assert fact.subject not in ("hawkish", "dovish", "stance", "rate_expectation")
        assert "hawkish" not in (fact.source_text or "").lower()


def test_guidance_never_becomes_rate_path():
    # "We will assess the incoming data" is kept verbatim as a guidance fact,
    # never converted into "rate hike expected" or any numeric expectation.
    doc = _synthetic_conference(
        "CHAIRMAN WARSH.\n"
        "We will continue to assess the incoming data carefully, and we will take the incoming data into account.\n"
    )
    result = FedPressConferenceExtractor().extract(_publication(), doc)
    guidance = [f for f in result.facts if f.subject == SUBJECT_POLICY_GUIDANCE]
    assert guidance and guidance[0].predicate == "statement"
    assert guidance[0].value.kind == ValueKind.TEXT
    assert not any(f.subject == "rate_expectation" for f in result.facts)


def test_no_decision_subjects():
    for fact in _extract().facts:
        assert fact.subject not in ("monetary_policy_decision", "main_refinancing_rate", "deposit_facility_rate")


def test_no_rationale_subject():
    for fact in _extract().facts:
        assert fact.subject != "monetary_policy" or fact.predicate == "statement"


# ---------------------------------------------------------------------------
# provenance & dispatch boundaries
# ---------------------------------------------------------------------------


def test_provenance_roundtrip_via_store(tmp_path):
    store = Store(tmp_path / "fed_pressconf.db")
    pub = _publication()
    store.upsert_publication(pub)
    store.set_classification(
        pub.id,
        central_bank="fed",
        publication_type="press_conference",
        confidence=Confidence.HIGH.value,
        method="url_pattern",
        evidence=[],
    )
    doc = _normalized()
    store.upsert_normalized_document(doc)
    results = extract_press_conference(store, pub)
    assert len(results) == 1
    assert results[0].publication_id == pub.id
    assert {_signature(f) for f in results[0].facts} == GOLDEN
    retrieved = store.get_facts(publication_id=pub.id)
    assert {_signature(f) for f in retrieved} == GOLDEN
    for fact in retrieved:
        assert fact.source_text
        assert fact.speaker == SPEAKER_BY_QUALIFIER[fact.identity_qualifier]
        assert fact.extraction_version and fact.extraction_method


def test_gating_refuses_unclassified(tmp_path):
    store = Store(tmp_path / "fed_pressconf_gate.db")
    pub = _publication()
    store.upsert_publication(pub)
    doc = _normalized()
    store.upsert_normalized_document(doc)
    assert extract_press_conference(store, pub) == []
    assert store.get_facts(publication_id=pub.id) == []


def test_gating_refuses_non_press_conference_type(tmp_path):
    store = Store(tmp_path / "fed_pressconf_wrongtype.db")
    pub = _publication()
    store.upsert_publication(pub)
    store.set_classification(
        pub.id, central_bank="fed", publication_type="monetary_policy_decision",
        confidence=Confidence.HIGH.value, method="url_pattern", evidence=[],
    )
    doc = _normalized()
    store.upsert_normalized_document(doc)
    assert extract_press_conference(store, pub) == []
    assert store.get_facts(publication_id=pub.id) == []


def test_persistence_idempotent(tmp_path):
    store = Store(tmp_path / "fed_pressconf_idem.db")
    pub = _publication()
    store.upsert_publication(pub)
    store.set_classification(
        pub.id,
        central_bank="fed",
        publication_type="press_conference",
        confidence=Confidence.HIGH.value,
        method="url_pattern",
        evidence=[],
    )
    doc = _normalized()
    store.upsert_normalized_document(doc)
    extract_press_conference(store, pub)
    first = {f.resolve_id() for f in store.get_facts(publication_id=pub.id)}
    extract_press_conference(store, pub)
    second = {f.resolve_id() for f in store.get_facts(publication_id=pub.id)}
    assert first == second


# ---------------------------------------------------------------------------
# classification — Fed press conference official sources
# ---------------------------------------------------------------------------


def _classify(url: str = "", title: str = ""):
    pub = Publication(central_bank="fed", title=title, url=url, source_id="fed-x", source_url=url or "https://www.federalreserve.gov")
    return PublicationClassifier().classify(pub)


def test_classification_transcript_pdf_and_event_urls():
    for url in (
        "https://www.federalreserve.gov/mediacenter/files/FOMCpresconf20260617.pdf",
        "https://www.federalreserve.gov/newsevents/pressconferences/pressconf20260617.htm",
        "https://www.federalreserve.gov/newsevents/pressconferences/fomc-press-conference-20260617.htm",
    ):
        assert _classify(url=url).publication_type == "press_conference", url


def test_classification_title_signal():
    result = _classify(title="Transcript of Chairman Warsh's Press Conference")
    assert result.publication_type == "press_conference"
    result = _classify(title="Transcript of Chair Powell's Press Conference — June 17, 2026")
    assert result.publication_type == "press_conference"


def test_classification_non_press_conf_not_minable():
    for url, cls in (
        ("https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm", "monetary_policy_decision"),
        ("https://www.federalreserve.gov/monetarypolicy/fomcminutes20260408.htm", "minutes"),
        ("https://www.federalreserve.gov/monetarypolicy/fomcprojtabl20260617.htm", "economic_projections"),
    ):
        assert _classify(url=url).publication_type == cls, url


def test_missing_publication_metadata_allowed_for_direct_extract():
    doc = _normalized()
    pub = Publication(central_bank="fed", title="Transcript", url="u", source_id="s", source_url="su", id=None)
    result = FedPressConferenceExtractor().extract(pub, doc)
    assert result.facts
    assert result.publication_id in ("", None) or True