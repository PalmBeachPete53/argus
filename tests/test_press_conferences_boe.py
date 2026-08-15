"""Phase 4.x — BoE Press Conference extractor: contract, provenance, speaker
attribution, Q&A boundary, value gate, determinism, immutability, gating and
end-to-end integration tests.

The BoE MPR press conference transcript is a **turn-based dialog** drawn from
the official PDF: standalone capitalized name labels start each turn, the first
MPC-member turn before any journalist label is the collective **remarks**, and
every MPC-member turn after a journalist label is an individual **answer**
(attributed verbatim). Journalist questions are never mined. The transcript is a
pure Q&A document (opening remarks live in a separate PDF) that opens with the
tail of the Governor's closing remarks; wrapped PDF lines and page breaks fall
between and sometimes inside turns, and the walker accumulates a turn across
page sections before mining it. This suite runs against the local synthetic
fixture ``boe_press_conf.txt`` and inline synthetic documents.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from argus.classification.base import Confidence
from argus.classification.classifier import PublicationClassifier
from argus.documents import Normalizer
from argus.documents.base import DocumentSection, NormalizedDocument
from argus.facts import LocationKind, PeriodKind, ValueKind
from argus.models import Document, DocumentStatus, Publication
from argus.press_conferences import (
    SUBJECT_FINANCIAL_CONDITIONS,
    SUBJECT_GDP,
    SUBJECT_GROWTH_RISK,
    SUBJECT_INFLATION,
    SUBJECT_INFLATION_DRIVER,
    SUBJECT_INFLATION_RISK,
    SUBJECT_MONETARY_POLICY,
    SUBJECT_POLICY_GUIDANCE,
    SUBJECT_RISK,
    BoEPressConferenceExtractor,
    get_extractor,
)
from argus.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "documents"
BOE_PDF_URL = (
    "https://www.bankofengland.co.uk/-/media/boe/files/monetary-policy-report/"
    "2026/july/mpr-press-conference-transcript-july-2026.pdf"
)


def _publication(**kw) -> Publication:
    fields = dict(
        central_bank="boe",
        title="MPR Press Conference Transcript - June 2026",
        url=BOE_PDF_URL,
        source_id="boe-pressconf",
        source_url="https://www.bankofengland.co.uk/monetary-policy-report/2026/june-2026",
        id="pub-boe-pressconf",
    )
    fields.update(kw)
    return Publication(**fields)


def _synthetic(sections_text: list[tuple[str, str]], doc_id: str = "sha-synthetic") -> NormalizedDocument:
    sections = [
        DocumentSection(order=i, heading=heading, text=text)
        for i, (heading, text) in enumerate(sections_text)
    ]
    return NormalizedDocument(
        publication_id="pub-boe-pressconf",
        document_id=doc_id,
        source_url=BOE_PDF_URL,
        local_path=None,
        document_kind="pdf",
        sections=sections,
    )


def _synthetic_conference(turns: str, *, pages: bool = False) -> NormalizedDocument:
    """Build a BoE-style turn document.

    Without ``pages`` a single section reproduces a one-page transcript; with
    ``pages`` the input is split on blank lines into page sections so a turn
    spanning a page break exercises the pending-accumulation path.
    """
    if not pages:
        return _synthetic([("", turns)])
    parts = [p.strip() for p in turns.split("\n\n") if p.strip()]
    return _synthetic([(f"page {i + 1}", p) for i, p in enumerate(parts)])


def _load_fixture() -> NormalizedDocument:
    """Parse the synthetic BoE transcript fixture into page sections."""
    text = (FIXTURES / "boe_press_conf.txt").read_text()
    body = text.split("==== PAGE 1 ====", 1)[1]
    parts = re.split(r"==== PAGE \d+ ====\s*", body)
    parts = [p.strip() for p in parts if p.strip()]
    sections = [
        DocumentSection(order=i, heading=f"page {i + 1}", text=p)
        for i, p in enumerate(parts)
    ]
    return NormalizedDocument(
        publication_id="pub-boe-pressconf",
        document_id="sha-fixture",
        source_url=BOE_PDF_URL,
        local_path=None,
        document_kind="pdf",
        title="MPR Press Conference Transcript - June 2026",
        text="\n\n".join(parts),
        sections=sections,
        extraction_method="pdf_text",
    )


def _extract():
    return BoEPressConferenceExtractor().extract(_publication(), _load_fixture())


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
    # remarks — collective MPC communication (speaker always None)
    ("monetary_policy", "statement", "text",
     "We took a decision today to leave Bank Rate unchanged, and that is the relevant conclusion.", None),
    # Q&A answers — individual, verbatim name label
    ("gdp", "value", "percentage", 0.1, None),
    ("policy_guidance", "statement", "text",
     "And it's about those pieces of data and how they all interact with each other that will form the judgment on where rates need to go.", None),
    ("financial_conditions", "assessment", "text",
     "10-year gilt yields have risen by about 350 basis points.", None),
    ("inflation_risk", "assessment", "categorical", "upside", None),
    ("policy_guidance", "statement", "text",
     "There will be a decision in September, as there always is, and we will announce it.", None),
    ("inflation", "value", "percentage", 2.1, "year:2027"),
    # answer on a later page — proves the wrapped "Charter Act." fragment did
    # not break the turn boundary
    ("financial_conditions", "assessment", "text",
     "Financial conditions have tightened notably over the course of this year.", None),
}

SPEAKER_BY_QUALIFIER = {
    "remarks:0": None,
    "answer:2:0": "Andrew Bailey",
    "answer:2:1": "Clare Lombardelli",
    "answer:2:2": "Andrew Bailey",
    "answer:3:0": "Dave Ramsden",
    "answer:3:1": "Dave Ramsden",
    "answer:3:2": "Andrew Bailey",
    "answer:3:3": "Andrew Bailey",
    "answer:3:4": "Andrew Bailey",
}


# ---------------------------------------------------------------------------
# contract & dispatch
# ---------------------------------------------------------------------------


def test_extractor_identity_and_dispatch():
    assert BoEPressConferenceExtractor.bank == "boe"
    assert BoEPressConferenceExtractor.extraction_version
    assert get_extractor("boe").__class__.__name__ == "BoEPressConferenceExtractor"
    assert get_extractor("ecb").__class__.__name__ == "EcbPressConferenceExtractor"
    assert get_extractor("fed").__class__.__name__ == "FedPressConferenceExtractor"


def test_golden_facts():
    result = _extract()
    got = {_signature(f) for f in result.facts}
    assert got == GOLDEN, f"missing: {GOLDEN - got} | extra: {got - GOLDEN}"


def test_golden_fact_count_and_warnings():
    result = _extract()
    assert len(result.facts) == 8
    assert result.warnings == []


def test_contract_fields():
    result = _extract()
    doc = _load_fixture()
    section_count = len(doc.sections)
    assert result.facts
    for fact in result.facts:
        assert fact.publication_id == "pub-boe-pressconf"
        assert fact.document_id == doc.document_id
        assert fact.effective_date is None
        assert fact.source_location is not None
        assert fact.source_location.kind == LocationKind.SECTION
        assert 0 <= fact.source_location.section < section_count
        assert fact.source_text
        assert fact.extraction_method == "regex"
        assert fact.extraction_version
        assert fact.confidence is not None
        assert fact.speaker == SPEAKER_BY_QUALIFIER[fact.identity_qualifier]
        owning = doc.sections[fact.source_location.section].text or ""
        assert _norm(fact.source_text) in _norm(owning), (
            f"source_text not in owning section: {fact.source_text!r}"
        )


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def test_identity_qualifiers_follow_phase7_contract():
    result = _extract()
    for fact in result.facts:
        if fact.identity_qualifier.startswith("remarks:"):
            assert fact.speaker is None, "remarks are never attributed to an individual"
        else:
            assert fact.identity_qualifier.startswith("answer:"), fact.identity_qualifier
            turn, n = fact.identity_qualifier.replace("answer:", "").split(":")
            assert turn.isdigit() and n.isdigit()


def test_value_facts_carry_percentage():
    for fact in _extract().facts:
        if fact.predicate == "value":
            assert fact.value.kind == ValueKind.PERCENTAGE
            assert fact.confidence == Confidence.HIGH


def test_deterministic_repeated_extraction():
    r1 = _extract()
    r2 = _extract()
    assert [f.resolve_id() for f in r1.facts] == [f.resolve_id() for f in r2.facts]
    assert [f.to_dict() for f in r1.facts] == [f.to_dict() for f in r2.facts]


def test_source_immutability():
    pub = _publication()
    doc = _load_fixture()
    import copy

    sections_before = copy.deepcopy(doc.sections)
    BoEPressConferenceExtractor().extract(pub, doc)
    assert [s.heading for s in doc.sections] == [s.heading for s in sections_before]
    assert [s.text for s in doc.sections] == [s.text for s in sections_before]
    assert pub.id == "pub-boe-pressconf"


# ---------------------------------------------------------------------------
# speaker attribution — explicit, verbatim, never inferred
# ---------------------------------------------------------------------------


def test_boe_official_label_preserved_verbatim():
    # The Governor's opening-remarks tail is collective (speaker None); each
    # later MPC member's answer is attributed verbatim to their label.
    doc = _synthetic_conference(
        "Andrew Bailey\n"
        "So with that, Dave, Clare and I will be happy to take your questions. Thank you.\n"
        "Mehreen Khan\n"
        "A question for the Deputy Governor.\n"
        "Clare Lombardelli\n"
        "Inflation is expected to average 2.1 percent in 2027.\n"
    )
    result = BoEPressConferenceExtractor().extract(_publication(), doc)
    inflation = [f for f in result.facts if f.subject == SUBJECT_INFLATION]
    assert inflation and inflation[0].speaker == "Clare Lombardelli"
    assert inflation[0].identity_qualifier.startswith("answer:")


def test_journalist_label_never_attributed_and_question_never_mined():
    # The journalist's question carries a numeric claim; it is never mined and
    # never attributed. The official's answer is mined as it is.
    doc = _synthetic_conference(
        "Mehreen Khan\n"
        "Governor, is Bank Rate really expected to rise to 5.5 percent this year?\n"
        "Andrew Bailey\n"
        "We will decide meeting by meeting as the incoming data arrive.\n"
    )
    result = BoEPressConferenceExtractor().extract(_publication(), doc)
    assert not any(f.predicate == "value" for f in result.facts)  # question 5.5% never mined
    guidance = [f for f in result.facts if f.subject == SUBJECT_POLICY_GUIDANCE]
    assert len(guidance) == 1
    assert guidance[0].speaker == "Andrew Bailey"
    assert guidance[0].identity_qualifier.startswith("answer:")


def test_remarks_speaker_never_set():
    doc = _synthetic_conference(
        "Andrew Bailey\n"
        "Inflation has eased considerably over the past year.\n"
        "Mehreen Khan\n"
        "Any comment?\n"
        "Clare Lombardelli\n"
        "Unemployment is expected to average 4.5 percent in 2027.\n"
    )
    result = BoEPressConferenceExtractor().extract(_publication(), doc)
    remarks = [f for f in result.facts if f.identity_qualifier.startswith("remarks:")]
    answers = [f for f in result.facts if f.identity_qualifier.startswith("answer:")]
    assert remarks and all(f.speaker is None for f in remarks)
    assert answers and all(f.speaker == "Clare Lombardelli" for f in answers)


def test_wrapped_fragment_and_interjection_never_become_labels():
    # A single-word interjection ("Yeah.") and a wrapped PDF fragment
    # ("Charter Act.") must not create a spurious journalist boundary: the
    # answers they sit inside are still mined under the real speaker.
    doc = _synthetic_conference(
        "Andrew Bailey\n"
        "We took a decision today to leave Bank Rate unchanged.\n"
        "Tim Wallace\n"
        "On activity, was growth really about 0.1 percent last quarter?\n"
        "Clare Lombardelli\n"
        "Yeah.\n"
        "So it was about 0.1% growth in the last quarter.\n"
        "Andrew Bailey\n"
        "Our balance sheet is split into two parts by the 1844 Bank\n"
        "Charter Act.\n"
        "Financial conditions have tightened notably over the course of this year.\n"
    )
    result = BoEPressConferenceExtractor().extract(_publication(), doc)
    gdp = [f for f in result.facts if f.subject == SUBJECT_GDP]
    assert gdp and gdp[0].speaker == "Clare Lombardelli"
    fin = [f for f in result.facts if f.subject == SUBJECT_FINANCIAL_CONDITIONS]
    assert fin and fin[0].speaker == "Andrew Bailey"
    assert all(f.speaker != "Yeah" for f in result.facts)
    assert all(f.speaker != "Charter Act" for f in result.facts)


# ---------------------------------------------------------------------------
# Q&A boundary & turn numbering
# ---------------------------------------------------------------------------


def test_turn_counter_increments_at_each_journalist_label():
    doc = _synthetic_conference(
        "Andrew Bailey\n"
        "Inflation has eased considerably over the past year.\n"
        "Mehreen Khan\n"
        "Question one.\n"
        "Andrew Bailey\n"
        "Unemployment is expected to average 4.5 percent in 2027.\n"
        "Tim Wallace\n"
        "Question two.\n"
        "Andrew Bailey\n"
        "GDP is expected to grow by 1.5 percent in 2027.\n"
    )
    result = BoEPressConferenceExtractor().extract(_publication(), doc)
    quals = [f.identity_qualifier for f in result.facts]
    assert "answer:1:0" in quals
    assert "answer:2:0" in quals
    assert all(q == "remarks:0" or q.startswith("answer:") for q in quals)
    assert "answer:0:0" not in quals


def test_unprefixed_content_is_unattributed_and_never_mined():
    doc = _synthetic_conference("Inflation has eased considerably over the past year.\n")
    result = BoEPressConferenceExtractor().extract(_publication(), doc)
    assert result.facts == []
    assert "no_remarks" in result.warnings
    assert "no_qna" in result.warnings


def test_journalist_label_ends_remarks():
    doc = _synthetic_conference(
        "Andrew Bailey\n"
        "Inflation has eased considerably over the past year.\n"
        "Mehreen Khan\n"
        "A question for the Governor, please.\n"
        "Andrew Bailey\n"
        "The labour market remains solid.\n"
    )
    result = BoEPressConferenceExtractor().extract(_publication(), doc)
    assert any(q.startswith("remarks:") for q in [f.identity_qualifier for f in result.facts])
    assert any(q.startswith("answer:") for q in [f.identity_qualifier for f in result.facts])


def test_turn_spanning_page_break_is_accumulated_then_mined():
    # A turn that starts on page 1 and continues onto page 2 is mined once with
    # the speaker of its label.
    doc = _synthetic_conference(
        "Andrew Bailey\n"
        "We took a decision today to leave Bank Rate unchanged.\n"
        "Mehreen Khan\n"
        "Was growth really about 0.1 percent last quarter?\n"
        "Clare Lombardelli\n"
        "So it was about 0.1% growth in the last quarter.\n"
        "\n"
        "And I would add that this is below what we would expect supply to be.\n",
        pages=True,
    )
    result = BoEPressConferenceExtractor().extract(_publication(), doc)
    gdp = [f for f in result.facts if f.subject == SUBJECT_GDP]
    assert gdp and gdp[0].speaker == "Clare Lombardelli"
    assert gdp[0].source_location.section == 0
    # provenance holds across the accumulated pages
    joined = _norm(doc.sections[0].text + " " + doc.sections[1].text)
    assert _norm(gdp[0].source_text) in joined


# ---------------------------------------------------------------------------
# value gate & forecasts / periods
# ---------------------------------------------------------------------------


def test_value_gate_matches_approximation_qualifier():
    # Phase 4.x gate regression: "was about 0.1% growth" is an explicit value
    # claim (the approximation qualifier does not defeat the gate).
    doc = _synthetic_conference(
        "Andrew Bailey\n"
        "Inflation has eased considerably over the past year.\n"
        "Mehreen Khan\n"
        "Was growth really about 0.1 percent last quarter?\n"
        "Clare Lombardelli\n"
        "So it was about 0.1% growth in the last quarter.\n"
    )
    result = BoEPressConferenceExtractor().extract(_publication(), doc)
    gdp = [f for f in result.facts if f.subject == SUBJECT_GDP and f.predicate == "value"]
    assert gdp and gdp[0].value.value == 0.1
    assert gdp[0].value.source_text == "0.1%"


def test_forecast_without_period_is_ignored():
    doc = _synthetic_conference(
        "Andrew Bailey\n"
        "Unemployment is projected to average 4.5 percent.\n"
    )
    result = BoEPressConferenceExtractor().extract(_publication(), doc)
    assert not any(f.predicate == "value" for f in result.facts)


def test_no_value_without_explicit_claim_verb():
    doc = _synthetic_conference(
        "Andrew Bailey\n"
        "Inflation is 2.4 percent in 2027.\n"
    )
    result = BoEPressConferenceExtractor().extract(_publication(), doc)
    assert not any(f.subject == SUBJECT_INFLATION and f.predicate == "value" for f in result.facts)


def test_share_units_never_percentages():
    doc = _synthetic_conference(
        "Andrew Bailey\n"
        "The deficit is projected to be 3.0 percent of GDP in 2026.\n"
    )
    result = BoEPressConferenceExtractor().extract(_publication(), doc)
    assert not any(f.predicate == "value" for f in result.facts)


def test_percent_target_phrase_never_a_value():
    doc = _synthetic_conference(
        "Andrew Bailey\n"
        "Inflation is converging towards our 2 percent goal.\n"
    )
    result = BoEPressConferenceExtractor().extract(_publication(), doc)
    assert not any(f.subject == SUBJECT_INFLATION and f.predicate == "value" for f in result.facts)


def test_quarter_period_from_wording():
    doc = _synthetic_conference(
        "Andrew Bailey\n"
        "GDP is expected to grow by 0.8 percent in the first quarter of 2027.\n"
    )
    result = BoEPressConferenceExtractor().extract(_publication(), doc)
    gdp = [f for f in result.facts if f.subject == SUBJECT_GDP and f.predicate == "value"]
    assert gdp and gdp[0].period.kind == PeriodKind.QUARTER
    assert gdp[0].period.value == "2027-Q1"


def test_year_period_from_wording():
    doc = _synthetic_conference(
        "Andrew Bailey\n"
        "Inflation is expected to average 2.1 percent in 2027.\n"
    )
    result = BoEPressConferenceExtractor().extract(_publication(), doc)
    infl = [f for f in result.facts if f.subject == SUBJECT_INFLATION and f.predicate == "value"]
    assert infl and infl[0].period.kind == PeriodKind.YEAR
    assert infl[0].period.value == "2027"


# ---------------------------------------------------------------------------
# negative epistemic — never interpretation, never other phases' subjects
# ---------------------------------------------------------------------------


def test_no_downstream_semantics():
    for fact in _extract().facts:
        assert fact.subject not in ("hawkish", "dovish", "stance", "rate_expectation")
        assert "hawkish" not in (fact.source_text or "").lower()


def test_guidance_never_becomes_rate_path():
    doc = _synthetic_conference(
        "Andrew Bailey\n"
        "We will continue to assess the incoming data carefully, and we will take the incoming data into account.\n"
    )
    result = BoEPressConferenceExtractor().extract(_publication(), doc)
    guidance = [f for f in result.facts if f.subject == SUBJECT_POLICY_GUIDANCE]
    assert guidance and guidance[0].predicate == "statement"
    assert guidance[0].value.kind == ValueKind.TEXT
    assert not any(f.subject == "rate_expectation" for f in result.facts)


def test_no_rationale_subject():
    for fact in _extract().facts:
        assert fact.subject != "monetary_policy" or fact.predicate == "statement"


def test_quoted_third_party_never_attributed():
    # A researcher's view quoted by the Governor is not an MPC assertion and is
    # never mined.
    doc = _synthetic_conference(
        "Andrew Bailey\n"
        "One researcher told us that the natural rate may have risen, but we do not share that view.\n"
    )
    result = BoEPressConferenceExtractor().extract(_publication(), doc)
    assert result.facts == []
    assert "no_risk_assessment" in result.warnings


# ---------------------------------------------------------------------------
# provenance & dispatch boundaries
# ---------------------------------------------------------------------------


def test_provenance_roundtrip_via_store(tmp_path):
    store = Store(tmp_path / "boe_pressconf.db")
    pub = _publication()
    store.upsert_publication(pub)
    store.set_classification(
        pub.id,
        central_bank="boe",
        publication_type="press_conference",
        confidence=Confidence.HIGH.value,
        method="source_type_hint",
        evidence=["type_hint=press_conference"],
    )
    doc = _load_fixture()
    store.upsert_normalized_document(doc)
    from argus.press_conferences import extract_press_conference

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
    store = Store(tmp_path / "boe_pressconf_gate.db")
    pub = _publication()
    store.upsert_publication(pub)
    doc = _load_fixture()
    store.upsert_normalized_document(doc)
    from argus.press_conferences import extract_press_conference

    assert extract_press_conference(store, pub) == []
    assert store.get_facts(publication_id=pub.id) == []


def test_gating_refuses_non_press_conference_type(tmp_path):
    store = Store(tmp_path / "boe_pressconf_wrongtype.db")
    pub = _publication()
    store.upsert_publication(pub)
    store.set_classification(
        pub.id, central_bank="boe", publication_type="monetary_policy_decision",
        confidence=Confidence.HIGH.value, method="source_type_hint", evidence=[],
    )
    doc = _load_fixture()
    store.upsert_normalized_document(doc)
    from argus.press_conferences import extract_press_conference

    assert extract_press_conference(store, pub) == []
    assert store.get_facts(publication_id=pub.id) == []


def test_persistence_idempotent(tmp_path):
    store = Store(tmp_path / "boe_pressconf_idem.db")
    pub = _publication()
    store.upsert_publication(pub)
    store.set_classification(
        pub.id,
        central_bank="boe",
        publication_type="press_conference",
        confidence=Confidence.HIGH.value,
        method="source_type_hint",
        evidence=["type_hint=press_conference"],
    )
    doc = _load_fixture()
    store.upsert_normalized_document(doc)
    from argus.press_conferences import extract_press_conference

    extract_press_conference(store, pub)
    first = {f.resolve_id() for f in store.get_facts(publication_id=pub.id)}
    extract_press_conference(store, pub)
    second = {f.resolve_id() for f in store.get_facts(publication_id=pub.id)}
    assert first == second


def test_extraction_batch_picks_up_boe(tmp_path):
    store = Store(tmp_path / "boe_pressconf_batch.db")
    pub = _publication()
    store.upsert_publication(pub)
    store.set_classification(
        pub.id,
        central_bank="boe",
        publication_type="press_conference",
        confidence=Confidence.HIGH.value,
        method="source_type_hint",
        evidence=["type_hint=press_conference"],
    )
    doc = _load_fixture()
    store.upsert_normalized_document(doc)
    from argus.press_conferences import extract_press_conference_batch

    results = extract_press_conference_batch(store)
    assert any(r.publication_id == pub.id for r in results)


def test_missing_publication_metadata_allowed_for_direct_extract():
    doc = _load_fixture()
    pub = Publication(central_bank="boe", title="Transcript", url="u", source_id="s", source_url="su", id=None)
    result = BoEPressConferenceExtractor().extract(pub, doc)
    assert result.facts


# ---------------------------------------------------------------------------
# classification — BoE press conference sources (source-type-hint path)
# ---------------------------------------------------------------------------


def _classify(url: str = "", title: str = "", **extra):
    from argus.registry import SourceRegistry

    pub = Publication(
        central_bank="boe", title=title or url, url=url,
        source_id="boe_mpc_press_conference",
        source_url=url or "https://www.bankofengland.co.uk",
        extra=extra,
    )
    return PublicationClassifier(registry=SourceRegistry()).classify(pub)


def test_classification_press_conference_pdf_via_source_type():
    # The BoE press conference PDF is a declared-type source: classification
    # flows through the boe_mpc_press_conference source_type_hint, not a URL
    # pattern. The MPR issue page (the discovery parent) also carries the type.
    for url in (
        BOE_PDF_URL,
        "https://www.bankofengland.co.uk/-/media/boe/files/monetary-policy-report/2026/may/mpr-press-conference-transcript-may-2026.pdf",
    ):
        result = _classify(url=url)
        assert result.publication_type == "press_conference", url
        assert result.method == "source_type_hint"
        assert result.confidence == Confidence.HIGH


def test_classification_press_conference_issue_page():
    result = _classify(url="https://www.bankofengland.co.uk/monetary-policy-report/2026/june-2026")
    assert result.publication_type == "press_conference"
    assert result.method == "source_type_hint"


def test_classification_unrelated_boe_item_not_forced_press_conference():
    # An unrelated BoE news item discovered via the broad news source must not
    # be forced into a HIGH press-conference classification: the declared
    # press-conference type only applies to items found on the dedicated
    # boe_mpc_press_conference source.
    from argus.registry import SourceRegistry

    pub = Publication(
        central_bank="boe",
        title="Bank of England launches new website feature",
        url="https://www.bankofengland.co.uk/news/2026/july/boe-launches-new-website-feature",
        source_id="boe_news_rss",
        source_url="https://www.bankofengland.co.uk/rss/news",
    )
    result = PublicationClassifier(registry=SourceRegistry()).classify(pub)
    assert result.publication_type == "unknown"
    assert result.confidence == Confidence.LOW