"""Phase 4.3 — ECB Press Conference extractor: end-to-end tests using the local
HTML fixtures and the existing Store (vertical slice).

Covers: remarks vs Q&A provenance, speaker attribution (never invented),
journalist questions never mined, categories A–G, quantitative values with
periods, no invented values, ambiguous wording never interpreted, determinism,
idempotence, empty-result persistence, strict classification gating and the
Phase 4.1 / Phase 4.2 boundaries.
"""

from __future__ import annotations

from pathlib import Path

from argus.classification.base import Confidence
from argus.decisions import extract_decision
from argus.documents import Normalizer
from argus.documents.base import DocumentSection, NormalizedDocument
from argus.facts import ExtractionResult, LocationKind, ValueKind
from argus.models import Document, DocumentStatus, Publication
from argus.press_conferences import (
    PRESS_CONFERENCE_PUBLICATION_TYPE,
    SUBJECT_CORE_INFLATION,
    SUBJECT_FINANCIAL_CONDITIONS,
    SUBJECT_GDP,
    SUBJECT_GROWTH,
    SUBJECT_GROWTH_RISK,
    SUBJECT_INFLATION,
    SUBJECT_INFLATION_DRIVER,
    SUBJECT_INFLATION_EXPECTATIONS,
    SUBJECT_INFLATION_RISK,
    SUBJECT_LABOUR_MARKET,
    SUBJECT_MONETARY_POLICY,
    SUBJECT_POLICY_GUIDANCE,
    SUBJECT_RISK,
    SUBJECT_UNEMPLOYMENT,
    SUBJECT_WAGES,
    EcbPressConferenceExtractor,
    PressConferenceExtractor,
    extract_press_conference,
    extract_press_conference_batch,
)
from argus.press_conferences.ecb import _mode_from_text
from argus.statements import extract_statement
from argus.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "documents"
ECB_URL = "https://www.ecb.europa.eu/press/press_conf/2026/html/ecb.mp260723.en.html"


def press_conference_publication(**kw) -> Publication:
    fields = dict(
        central_bank="ecb",
        title="Press conference",
        url=ECB_URL,
        source_id="ecb-pressconf",
        source_url="https://www.ecb.europa.eu/press/press_conf/html/feed.xml",
        id="pub-ecb-pressconf",
    )
    fields.update(kw)
    return Publication(**fields)


def normalized_fixture(name: str) -> object:
    doc = Document(
        publication_id="pub-ecb-pressconf",
        url=ECB_URL,
        kind="html",
        status=DocumentStatus.FETCHED,
        local_path=str(FIXTURES / name),
    )
    return Normalizer().parse(doc)


def extract_fixture(name: str):
    return EcbPressConferenceExtractor().extract(press_conference_publication(), normalized_fixture(name))


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
# golden facts across all ECB press conference fixtures
# ---------------------------------------------------------------------------

GOLDEN = {
    "ecb_press_conf.html": {
        "warnings": [],
        "count": 13,
        "subjects": {
            SUBJECT_GROWTH: {"texts": ["The euro area economy is growing at a moderate pace."]},
            SUBJECT_INFLATION: {"values": {2.2: "year:2027"}},
            SUBJECT_CORE_INFLATION: {"texts": ["Core inflation remains elevated but is expected to decline gradually."]},
            SUBJECT_INFLATION_EXPECTATIONS: {"texts": ["Inflation expectations remain well anchored."]},
            SUBJECT_INFLATION_DRIVER: {"texts": ["Inflation is currently being driven mainly by energy prices."]},
            SUBJECT_UNEMPLOYMENT: {"values": {6.4: "month:2026-06"}},
            SUBJECT_GDP: {"values": {1.6: "year:2028"}},
            SUBJECT_RISK: {"texts": ["balanced"]},
            SUBJECT_GROWTH_RISK: {"texts": ["balanced"]},
            SUBJECT_MONETARY_POLICY: {
                "texts": [
                    "The Governing Council is determined to ensure that inflation returns to the 2% target.",
                    "We do not pre-commit to a particular rate path.",
                ]
            },
            SUBJECT_POLICY_GUIDANCE: {
                "texts": [
                    "The Governing Council stands ready to adjust all of its instruments within its mandate.",
                    "We will decide meeting by meeting on the basis of the incoming data.",
                ]
            },
        },
    },
    "ecb_press_conf_qna.html": {
        "warnings": ["no_risk_assessment"],
        "count": 5,
        "subjects": {
            SUBJECT_GROWTH: {"texts": ["The euro area economy continues to grow at a modest pace."]},
            SUBJECT_INFLATION: {"values": {2.1: "year:2027"}},
            SUBJECT_WAGES: {"values": {3.0: "year:2027"}},
            SUBJECT_FINANCIAL_CONDITIONS: {
                "texts": ["Financing conditions remain tight, and monetary policy transmission is functioning smoothly."]
            },
            SUBJECT_POLICY_GUIDANCE: {"texts": ["Future policy decisions will depend on the incoming data."]},
        },
    },
    "ecb_press_conf_non_econ.html": {
        "warnings": ["non_economic_question_skipped"],
        "count": 4,
        "subjects": {
            SUBJECT_INFLATION_RISK: {
                "texts": ["Inflation remains elevated, but uncertainty surrounding the outlook remains high."]
            },
            SUBJECT_POLICY_GUIDANCE: {
                "texts": [
                    "We will assess the incoming data.",
                    "The Governing Council will be guided by the incoming data.",
                ]
            },
            SUBJECT_INFLATION: {"texts": ["Inflation is converging towards 2%."]},
        },
    },
    "ecb_press_conf_multi_speaker.html": {
        "warnings": ["no_risk_assessment"],
        "count": 4,
        "subjects": {
            SUBJECT_POLICY_GUIDANCE: {
                "texts": ["The Governing Council stands ready to adjust all of its instruments within its mandate."]
            },
            SUBJECT_LABOUR_MARKET: {"texts": ["The labour market remains resilient."]},
            SUBJECT_GDP: {"values": {1.4: "year:2027"}},
            SUBJECT_UNEMPLOYMENT: {"texts": ["Unemployment is expected to decline gradually."]},
        },
    },
    "ecb_press_conf_minimal.html": {
        "warnings": ["no_qna", "no_risk_assessment", "no_forward_guidance"],
        "count": 1,
        "subjects": {
            SUBJECT_INFLATION: {"values": {2.2: "year:2027"}},
        },
    },
    "ecb_press_conf_values.html": {
        "warnings": ["no_risk_assessment", "no_forward_guidance"],
        "count": 7,
        "subjects": {
            SUBJECT_INFLATION: {
                "values": {2.4: "month:2026-06", 2.2: "year:2027"},
                "texts": ["Inflation remains close to the 2% target."],
            },
            SUBJECT_UNEMPLOYMENT: {"values": {6.3: None}},
            SUBJECT_WAGES: {"values": {3.0: "year:2027"}},
            SUBJECT_GDP: {"values": {1.4: "year:2027", 1.6: "year:2028"}},
        },
    },
    "ecb_press_conf_ambiguous.html": {
        "warnings": [],
        "count": 3,
        "subjects": {
            SUBJECT_POLICY_GUIDANCE: {
                "texts": [
                    "The Governing Council will not pre-commit to a specific rate path.",
                    "We will decide on the basis of the incoming data.",
                ]
            },
            SUBJECT_RISK: {"texts": ["Uncertainty surrounding the outlook remains high."]},
        },
    },
}


def test_golden_facts_across_all_fixtures():
    for name, expected in GOLDEN.items():
        result = extract_fixture(name)
        assert result.warnings == expected["warnings"], (name, result.warnings)
        assert len(result.facts) == expected["count"], name
        present = {f.subject for f in result.facts}
        assert present == set(expected["subjects"]), (name, present)

        for subject, spec in expected["subjects"].items():
            facts = [f for f in result.facts if f.subject == subject]
            value_facts = [f for f in facts if f.predicate == "value"]
            other_facts = [f for f in facts if f.predicate != "value"]
            if "values" in spec:
                got = {(f.value.value, period_of(f)) for f in value_facts}
                assert got == set(spec["values"].items()), (name, subject, got)
            else:
                assert value_facts == [], (name, subject)
            if "texts" in spec:
                assert sorted(f.value.value for f in other_facts) == sorted(spec["texts"]), (name, subject)
            else:
                assert other_facts == [], (name, subject)


def test_all_categories_across_fixtures():
    seen: set[str] = set()
    for name in GOLDEN:
        for fact in extract_fixture(name).facts:
            seen.add(fact.subject)
    for subject in (
        SUBJECT_INFLATION,
        SUBJECT_CORE_INFLATION,
        SUBJECT_INFLATION_EXPECTATIONS,
        SUBJECT_INFLATION_DRIVER,
        SUBJECT_GROWTH,
        SUBJECT_GDP,
        SUBJECT_LABOUR_MARKET,
        SUBJECT_UNEMPLOYMENT,
        SUBJECT_WAGES,
        SUBJECT_MONETARY_POLICY,
        SUBJECT_RISK,
        SUBJECT_INFLATION_RISK,
        SUBJECT_GROWTH_RISK,
        SUBJECT_FINANCIAL_CONDITIONS,
        SUBJECT_POLICY_GUIDANCE,
    ):
        assert subject in seen, subject


# ---------------------------------------------------------------------------
# remarks vs Q&A provenance + speaker attribution
# ---------------------------------------------------------------------------


def test_remarks_vs_answer_provenance():
    result = extract_fixture("ecb_press_conf.html")
    for fact in result.facts:
        assert fact.identity_qualifier.startswith("remarks:") or fact.identity_qualifier.startswith("answer:"), fact.identity_qualifier
    remarks = [f for f in result.facts if f.identity_qualifier.startswith("remarks:")]
    answers = [f for f in result.facts if f.identity_qualifier.startswith("answer:")]
    assert len(remarks) == 9
    assert len(answers) == 4
    # remarks are the collective statement — never attributed to an individual
    for fact in remarks:
        assert fact.speaker is None
    for fact in answers:
        assert fact.identity_qualifier.startswith("answer:1:") or fact.identity_qualifier.startswith("answer:2:")


def test_speaker_attribution_exact():
    result = extract_fixture("ecb_press_conf.html")
    lagarde = [f for f in result.facts if f.speaker == "President Christine Lagarde"]
    guindos = [f for f in result.facts if f.speaker == "Vice-President Luis de Guindos"]
    assert len(lagarde) == 2
    assert len(guindos) == 2
    assert {f.value.value for f in lagarde} == {
        "We will decide meeting by meeting on the basis of the incoming data.",
        "We do not pre-commit to a particular rate path.",
    }
    assert {f.value.value for f in guindos} == {
        1.6,
        "balanced",
    }
    assert {f.period.label for f in guindos if isinstance(f.value.value, float)} == {"in 2028"}
    # the speaker is preserved verbatim, never invented
    assert all(f.speaker == "President Christine Lagarde" for f in lagarde)


def test_unlabelled_answer_never_invents_speaker():
    result = extract_fixture("ecb_press_conf_qna.html")
    unlabelled = [f for f in result.facts if f.identity_qualifier.startswith("answer:") and f.speaker is None]
    assert len(unlabelled) == 3  # inflation, financial conditions, guidance answers
    assert not any(f.speaker == "Answer" for f in result.facts)
    assert not any((f.speaker or "").startswith("Answer") for f in result.facts)


def test_multi_speaker_attribution():
    result = extract_fixture("ecb_press_conf_multi_speaker.html")
    by_speaker: dict[str, list] = {}
    for fact in result.facts:
        by_speaker.setdefault(fact.speaker, []).append(fact)
    assert len(by_speaker["President Christine Lagarde"]) == 2  # labour market + unemployment answers
    assert len(by_speaker["Vice-President Luis de Guindos"]) == 1  # gdp answer
    # the journalist's questions are never mined and never attributed
    assert not any("Question" in (f.speaker or "") for f in result.facts)


def test_journalist_question_never_mined():
    result = extract_fixture("ecb_press_conf.html")
    for sentence in (
        "Question: Will the Governing Council raise interest rates further this year?",
        "Question: What are your expectations for growth?",
    ):
        assert not any(f.source_text == sentence for f in result.facts)
    # a market-fact sentence inside a question is never attributed to the bank
    result2 = extract_fixture("ecb_press_conf_ambiguous.html")
    assert not any("rate hike" in f.source_text.lower() for f in result2.facts)
    assert not any("investors" in f.source_text.lower() for f in result2.facts)
    assert not any("1.50" in f.source_text for f in result2.facts)  # percentage in a question


# ---------------------------------------------------------------------------
# categories + quantitative values
# ---------------------------------------------------------------------------


def test_quantitative_values_carry_periods():
    result = extract_fixture("ecb_press_conf_values.html")
    inflation_jun = next(f for f in result.facts if f.subject == SUBJECT_INFLATION and period_of(f) == "month:2026-06")
    assert inflation_jun.value.kind is ValueKind.PERCENTAGE
    assert inflation_jun.value.value == 2.4
    assert inflation_jun.value.source_text == "2.4%"
    assert inflation_jun.confidence is Confidence.HIGH
    inflation_2027 = next(f for f in result.facts if f.subject == SUBJECT_INFLATION and period_of(f) == "year:2027")
    assert inflation_2027.period.label == "in 2027"
    unemployment = next(f for f in result.facts if f.subject == SUBJECT_UNEMPLOYMENT)
    assert unemployment.value.value == 6.3
    assert period_of(unemployment) is None  # bare percentage keeps no period


def test_target_phrasing_never_becomes_a_value():
    result = extract_fixture("ecb_press_conf_values.html")
    close = next(f for f in result.facts if f.subject == SUBJECT_INFLATION and f.predicate == "assessment")
    assert close.value.value == "Inflation remains close to the 2% target."
    assert not any(f.predicate == "value" and f.value.value == 2.0 for f in result.facts)
    result2 = extract_fixture("ecb_press_conf_non_econ.html")
    assert not any(f.predicate == "value" for f in result2.facts)  # "converging towards 2%" is not a value


def test_ambiguous_wording_never_becomes_interpretation():
    result = extract_fixture("ecb_press_conf_ambiguous.html")
    assert not any("vigilant" in f.source_text for f in result.facts)  # "we remain vigilant"
    assert not any("market expectations" in f.source_text for f in result.facts)
    assert not any("no such decision" in f.source_text for f in result.facts)
    assert not any("hike" in f.value.value.lower() for f in result.facts)
    guidance = [f for f in result.facts if f.subject == SUBJECT_POLICY_GUIDANCE]
    assert any(f.value.value == "We will decide on the basis of the incoming data." for f in guidance)
    # "uncertainty remains high" is verbatim risk text — never a downside orientation
    risk_texts = [f for f in result.facts if f.subject == SUBJECT_RISK and f.predicate == "assessment"]
    assert any(f.value.value == "Uncertainty surrounding the outlook remains high." for f in risk_texts)
    assert all(f.value.kind is ValueKind.TEXT for f in risk_texts)


def test_non_economic_question_turn_is_skipped():
    result = extract_fixture("ecb_press_conf_non_econ.html")
    assert "non_economic_question_skipped" in result.warnings
    # the answer to the non-economic question contains "growth" only incidentally
    assert not any("sustainable growth" in f.source_text for f in result.facts)
    assert not any("private matter" in f.source_text for f in result.facts)
    # the economic turns are still mined
    assert any(f.value.value == "We will assess the incoming data." for f in result.facts)
    assert any(f.value.value == "The Governing Council will be guided by the incoming data." for f in result.facts)


# ---------------------------------------------------------------------------
# question-filter hardening: generic personal tokens must not suppress answers
# ---------------------------------------------------------------------------


def _qna_doc(question: str, answer: str, *, document_id: str = "sha-filter") -> NormalizedDocument:
    return _one_section_doc(
        "Questions and answers",
        f"Question: {question}\nAnswer: {answer}",
        document_id=document_id,
    )


def test_personal_life_question_skips_answer():
    doc = _qna_doc(
        "Could you tell us about your personal life?",
        "Inflation is expected to average 2.0% in 2027.",
    )
    result = EcbPressConferenceExtractor().extract(press_conference_publication(), doc)
    assert "non_economic_question_skipped" in result.warnings
    assert result.facts == []
    assert not any("2.0%" in f.source_text for f in result.facts)


def test_economic_question_containing_personal_is_extracted():
    doc = _qna_doc(
        "What is your personal assessment of the inflation outlook?",
        "Inflation is expected to average 2.1% in 2027.",
    )
    result = EcbPressConferenceExtractor().extract(press_conference_publication(), doc)
    assert "non_economic_question_skipped" not in result.warnings
    assert len(result.facts) == 1
    fact = result.facts[0]
    assert fact.subject == SUBJECT_INFLATION
    assert fact.predicate == "value"
    assert fact.value.value == 2.1
    assert fact.value.source_text == "2.1%"
    assert period_of(fact) == "year:2027"
    assert fact.identity_qualifier == "answer:1:0"
    assert fact.source_text == "Inflation is expected to average 2.1% in 2027."
    assert fact.source_location.section == 0
    assert fact.speaker is None


def test_economic_question_containing_personally_is_extracted():
    doc = _qna_doc(
        "Do you personally expect inflation to return to target?",
        "Inflation is expected to average 2.0% in 2027.",
    )
    result = EcbPressConferenceExtractor().extract(press_conference_publication(), doc)
    assert "non_economic_question_skipped" not in result.warnings
    assert len(result.facts) == 1
    fact = result.facts[0]
    assert fact.subject == SUBJECT_INFLATION
    assert fact.value.value == 2.0
    assert fact.source_text == "Inflation is expected to average 2.0% in 2027."


def test_family_life_question_skips_answer():
    doc = _qna_doc(
        "How do you balance your family life with the presidency?",
        "The euro area economy is growing at a moderate pace.",
    )
    result = EcbPressConferenceExtractor().extract(press_conference_publication(), doc)
    assert "non_economic_question_skipped" in result.warnings
    assert result.facts == []
    assert not any("moderate pace" in f.source_text for f in result.facts)


def test_ambiguous_personal_wording_in_economic_question_is_not_filtered():
    doc = _qna_doc(
        "From your personal vantage point, will the Council cut rates this year?",
        "Future policy decisions will depend on the incoming data.",
    )
    result = EcbPressConferenceExtractor().extract(press_conference_publication(), doc)
    assert "non_economic_question_skipped" not in result.warnings
    assert len(result.facts) == 1
    fact = result.facts[0]
    assert fact.subject == SUBJECT_POLICY_GUIDANCE
    assert fact.value.value == "Future policy decisions will depend on the incoming data."
    assert fact.identity_qualifier == "answer:1:0"
    # the journalist's question is never mined
    assert fact.source_text != "From your personal vantage point, will the Council cut rates this year?"


def test_retirement_and_children_only_trigger_with_possessive_marker():
    # "retirement" or "children" used economically must NOT trigger the skip
    doc = _qna_doc(
        "Will the retirement of older workers hold back growth?",
        "The economy is expected to grow by 1.4% in 2027.",
    )
    result = EcbPressConferenceExtractor().extract(press_conference_publication(), doc)
    assert "non_economic_question_skipped" not in result.warnings
    assert len(result.facts) == 1
    assert result.facts[0].subject == SUBJECT_GDP
    assert result.facts[0].value.value == 1.4
    # "your retirement" / "your children" remain personal triggers
    for question in (
        "When do you plan to retire, and what will your retirement look like?",
        "How are your children coping with the presidency?",
    ):
        doc2 = _qna_doc(
            question,
            "The economy is expected to grow by 1.4% in 2027.",
            document_id=f"sha-{question[:12]}",
        )
        result2 = EcbPressConferenceExtractor().extract(press_conference_publication(), doc2)
        assert "non_economic_question_skipped" in result2.warnings, question
        assert result2.facts == [], question


def test_risk_orientations_are_categorical_only_when_explicit():
    result = extract_fixture("ecb_press_conf.html")
    balanced = fact_by(result, SUBJECT_RISK, "assessment")
    assert balanced.value.kind is ValueKind.CATEGORICAL
    assert balanced.value.value == "balanced"
    assert balanced.confidence is Confidence.HIGH
    assert fact_by(result, SUBJECT_GROWTH_RISK, "assessment").value.value == "balanced"


# ---------------------------------------------------------------------------
# provenance + no interpretation
# ---------------------------------------------------------------------------


def test_provenance_is_traceable():
    for name in GOLDEN:
        document = normalized_fixture(name)
        result = extract_fixture(name)
        for fact in result.facts:
            assert fact.extraction_version == EcbPressConferenceExtractor.extraction_version
            assert fact.extraction_method
            assert fact.source_location is not None
            assert fact.source_location.kind is LocationKind.SECTION
            assert fact.source_text
            assert fact.publication_id == "pub-ecb-pressconf"
            assert fact.document_id
            assert fact.effective_date is None
            section_text = document.sections[fact.source_location.section].text or ""
            assert fact.source_text in section_text, (name, fact.subject, fact.predicate)
            assert fact.value.source_text in section_text, (name, fact.subject, fact.predicate)


def test_no_hawkish_dovish_or_forex_interpretation():
    for name in GOLDEN:
        result = extract_fixture(name)
        for fact in result.facts:
            raw = str(fact.value.value or "").lower()
            assert "hawkish" not in raw
            assert "dovish" not in raw
            assert "bullish" not in raw and "bearish" not in raw
            assert "forex" not in raw and "eur/usd" not in raw
            assert "stance" not in fact.predicate
            assert fact.predicate not in ("sentiment", "market_reaction")


def test_no_decision_or_rationale_facts_from_press_conference():
    result = extract_fixture("ecb_press_conf.html")
    phase5_subjects = {
        "monetary_policy_decision", "main_refinancing_rate", "marginal_lending_rate",
        "deposit_facility_rate", "asset_purchase", "vote",
    }
    assert not phase5_subjects & {f.subject for f in result.facts}
    assert not any(f.predicate == "rationale" for f in result.facts)  # Phase 4.2 rationale stays in the statement
    assert not any(f.predicate == "change" for f in result.facts)
    assert not any(f.predicate == "date" for f in result.facts)


def test_empty_document_warns_no_sections():
    doc = NormalizedDocument(
        publication_id="pub-ecb-pressconf",
        document_id="sha-empty-pc",
        source_url=ECB_URL,
        local_path=None,
        document_kind="html",
        sections=[],
    )
    result = EcbPressConferenceExtractor().extract(press_conference_publication(), doc)
    assert result.warnings == ["no_sections"]
    assert result.facts == []


def test_qna_only_document_warns_no_remarks():
    doc = NormalizedDocument(
        publication_id="pub-ecb-pressconf",
        document_id="sha-qna-only",
        source_url=ECB_URL,
        local_path=None,
        document_kind="html",
        sections=[
            DocumentSection(
                order=0,
                heading="Questions and answers",
                text="Question: How is inflation evolving?\nAnswer: Inflation is expected to average 2.0% in 2027.",
            )
        ],
    )
    result = EcbPressConferenceExtractor().extract(press_conference_publication(), doc)
    assert result.warnings == ["no_remarks", "no_risk_assessment", "no_forward_guidance"]
    assert len(result.facts) == 1
    assert result.facts[0].subject == SUBJECT_INFLATION
    assert result.facts[0].value.value == 2.0


# ---------------------------------------------------------------------------
# section routing hardening: UNKNOWN ≠ REMARKS
# ---------------------------------------------------------------------------


def _one_section_doc(heading: str, text: str, *, document_id: str = "sha-route") -> NormalizedDocument:
    return NormalizedDocument(
        publication_id="pub-ecb-pressconf",
        document_id=document_id,
        source_url=ECB_URL,
        local_path=None,
        document_kind="html",
        sections=[DocumentSection(order=0, heading=heading, text=text)],
    )


def test_known_remarks_heading_is_remarks():
    doc = _one_section_doc("Introductory statement", "Inflation is projected to average 2.2% in 2027.")
    result = EcbPressConferenceExtractor().extract(press_conference_publication(), doc)
    assert result.warnings == ["no_qna", "no_risk_assessment", "no_forward_guidance"]
    assert len(result.facts) == 1
    fact = result.facts[0]
    assert fact.subject == SUBJECT_INFLATION
    assert fact.identity_qualifier.startswith("remarks:")
    assert fact.speaker is None  # remarks are collective, never attributed


def test_known_remarks_heading_variants_are_kept():
    for heading in ("Introductory statement", "Opening statement", "Introductory remarks", "Opening remarks"):
        doc = _one_section_doc(heading, "Inflation is projected to average 2.2% in 2027.", document_id=f"sha-{heading}")
        result = EcbPressConferenceExtractor().extract(press_conference_publication(), doc)
        assert len(result.facts) == 1, heading
        assert result.facts[0].identity_qualifier.startswith("remarks:"), heading


def test_known_qna_heading_is_qna():
    doc = _one_section_doc(
        "Questions and answers",
        "Question: How is inflation?\nAnswer: Inflation is expected to average 2.0% in 2027.",
    )
    result = EcbPressConferenceExtractor().extract(press_conference_publication(), doc)
    assert result.warnings == ["no_remarks", "no_risk_assessment", "no_forward_guidance"]
    assert len(result.facts) == 1
    fact = result.facts[0]
    assert fact.subject == SUBJECT_INFLATION
    assert fact.identity_qualifier.startswith("answer:")
    assert fact.value.value == 2.0
    assert fact.source_text == "Inflation is expected to average 2.0% in 2027."


def test_known_qna_heading_variants_are_kept():
    for heading in ("Questions and answers", "Questions", "Q&A", "Answers"):
        doc = _one_section_doc(
            heading,
            "Question: How is inflation?\nAnswer: Inflation is expected to average 2.0% in 2027.",
            document_id=f"sha-{heading}",
        )
        result = EcbPressConferenceExtractor().extract(press_conference_publication(), doc)
        assert len(result.facts) == 1, heading
        assert result.facts[0].identity_qualifier.startswith("answer:"), heading


def test_unknown_heading_with_qna_markers_is_qna():
    doc = _one_section_doc(
        "Additional Information",
        "Question: What is the outlook for activity?\nAnswer: GDP is projected to grow by 1.4% in 2027.",
    )
    result = EcbPressConferenceExtractor().extract(press_conference_publication(), doc)
    assert len(result.facts) == 1
    fact = result.facts[0]
    assert fact.subject == SUBJECT_GDP
    assert fact.predicate == "value"
    assert fact.value.value == 1.4
    assert fact.identity_qualifier.startswith("answer:")
    assert fact.source_location.section == 0
    assert fact.speaker is None  # unlabelled answer: never invented


def test_unknown_heading_without_signal_is_ignored():
    doc = _one_section_doc("Additional Information", "The euro area economy is growing at a moderate pace.")
    result = EcbPressConferenceExtractor().extract(press_conference_publication(), doc)
    assert result.facts == []
    assert "no_remarks" in result.warnings
    assert "no_qna" in result.warnings


def test_unknown_heading_with_economic_content_is_ignored():
    doc = _one_section_doc(
        "Appendix",
        "Inflation is projected to average 2.2% in 2027. The unemployment rate stood at 6.3%. Risks are broadly balanced.",
    )
    result = EcbPressConferenceExtractor().extract(press_conference_publication(), doc)
    assert result.facts == []


def test_unknown_heading_economic_phrases_never_extracted():
    # the audit's flagship case: an unknown section whose sentences WOULD match
    # the economic patterns must still produce 0 facts — UNKNOWN ≠ REMARKS
    doc = _one_section_doc("Additional Information", "Inflation is expected to remain elevated.")
    result = EcbPressConferenceExtractor().extract(press_conference_publication(), doc)
    assert result.facts == []
    assert not any(f.subject == SUBJECT_INFLATION for f in result.facts)
    assert "no_remarks" in result.warnings


def test_closing_remarks_heading_is_not_remarks():
    doc = _one_section_doc("Closing Remarks", "Inflation is expected to remain elevated.")
    result = EcbPressConferenceExtractor().extract(press_conference_publication(), doc)
    assert result.facts == []
    assert "no_remarks" in result.warnings


def test_risk_near_misses_require_risk_context():
    # X-2: "risky", "risk-free", "riskiness" carry the prefix "risk" but are
    # never risk anchors — precision first, near-misses yield nothing.
    doc = _one_section_doc(
        "Introductory statement",
        "The current approach is risky. The alternative is risk-free. There is riskiness in the plan.",
    )
    result = EcbPressConferenceExtractor().extract(press_conference_publication(), doc)
    assert result.facts == []


def test_heading_routing_is_exact_identity_not_substring():
    # X-3: a marker inside a near-miss heading is not enough. "Introductory
    # note" is not an introductory statement; "Questions and answers on
    # monetary policy" is not the Q&A heading.
    doc = _one_section_doc("Introductory note", "Inflation is projected to average 2.2% in 2027.")
    result = EcbPressConferenceExtractor().extract(press_conference_publication(), doc)
    assert result.facts == []
    assert "no_remarks" in result.warnings

    doc = _one_section_doc(
        "Questions and answers on monetary policy",
        "Inflation is projected to average 2.2% in 2027.",
    )
    result = EcbPressConferenceExtractor().extract(press_conference_publication(), doc)
    assert result.facts == []
    assert "no_remarks" in result.warnings
    assert "no_qna" in result.warnings


def test_question_word_without_colon_is_never_a_qna_marker():
    # X-3: a natural sentence beginning with "Question marks …" is not a Q&A
    # marker — only the labelled "Question:" / "Answer:" lines are.
    assert _mode_from_text("Question marks remain over the outlook for growth.") == "ignore"
    assert _mode_from_text("Question: What is the outlook?\nAnswer: Inflation is expected to average 2.0% in 2027.") == "qna"
    assert _mode_from_text("Question marks remain over the outlook.\nAnswer: Inflation is expected to average 2.0% in 2027.") == "qna"


def test_heading_normalization_controls_case_numbering_punctuation_and_the():
    for heading in ("1. Introductory statement", "Introductory Statement.", "The Introductory Statement"):
        doc = _one_section_doc(heading, "Inflation is projected to average 2.2% in 2027.", document_id=f"sha-{heading}")
        result = EcbPressConferenceExtractor().extract(press_conference_publication(), doc)
        assert len(result.facts) == 1, heading
        assert result.facts[0].identity_qualifier.startswith("remarks:"), heading
    for heading in ("Questions and Answers", "The Q&A", "1. Answers"):
        doc = _one_section_doc(
            heading,
            "Question: What is the outlook?\nAnswer: Inflation is projected to average 2.2% in 2027.",
            document_id=f"sha-{heading}",
        )
        result = EcbPressConferenceExtractor().extract(press_conference_publication(), doc)
        assert len(result.facts) == 1, heading
        assert result.facts[0].identity_qualifier.startswith("answer:"), heading


# ---------------------------------------------------------------------------
# determinism + idempotent persistence (vertical slice)
# ---------------------------------------------------------------------------


def _store_press_conf(tmp_path, name: str = "ecb_press_conf.html") -> Store:
    store = Store(tmp_path / f"{name}.db")
    store.upsert_publication(press_conference_publication())
    store.upsert_normalized_document(normalized_fixture(name))
    return store


def classify_press_conference(store: Store, *, publication_type: str = PRESS_CONFERENCE_PUBLICATION_TYPE) -> None:
    store.set_classification(
        "pub-ecb-pressconf",
        central_bank="ecb",
        publication_type=publication_type,
        confidence=Confidence.HIGH.value,
        method="url_pattern",
        evidence=[],
    )


class _ZeroFactPressConferenceExtractor(PressConferenceExtractor):
    """Stub press conference extractor that yields no facts — used to simulate a
    re-extraction of an already-persisted document that now produces nothing."""

    bank = "ecb"
    extraction_version = "test-zero"

    def extract(self, publication, document):
        return ExtractionResult(
            publication_id=publication.id or publication.dedup_key or "",
            document_id=document.document_id,
        )


def test_extract_press_conference_persists_facts(tmp_path):
    store = _store_press_conf(tmp_path)
    classify_press_conference(store)
    results = extract_press_conference(store, press_conference_publication())
    assert len(results) == 1
    persisted = store.get_facts(publication_id="pub-ecb-pressconf")
    assert len(persisted) == 13
    by = {(f.subject, f.predicate) for f in persisted}
    assert (SUBJECT_INFLATION, "value") in by
    assert (SUBJECT_POLICY_GUIDANCE, "statement") in by
    assert (SUBJECT_RISK, "assessment") in by
    inflation = next(f for f in persisted if (f.subject, f.predicate) == (SUBJECT_INFLATION, "value") and period_of(f) == "year:2027")
    assert inflation.value.value == 2.2
    assert inflation.central_bank == "ecb"  # filled from the publication
    assert inflation.period.label == "in 2027"


def test_speaker_is_persisted_roundtrip(tmp_path):
    store = _store_press_conf(tmp_path, "ecb_press_conf.html")
    classify_press_conference(store)
    extract_press_conference(store, press_conference_publication())
    persisted = store.get_facts(publication_id="pub-ecb-pressconf")
    lagarde = [f for f in persisted if f.speaker == "President Christine Lagarde"]
    guindos = [f for f in persisted if f.speaker == "Vice-President Luis de Guindos"]
    assert len(lagarde) == 2
    assert len(guindos) == 2
    # legacy facts without a speaker (Phase 4.1/6 style) still load as None
    assert all(f.speaker is None for f in persisted if f.identity_qualifier.startswith("remarks:"))


def test_extract_press_conference_is_idempotent(tmp_path):
    store = _store_press_conf(tmp_path)
    classify_press_conference(store)
    pub = press_conference_publication()
    extract_press_conference(store, pub)
    first = sorted((f.fact_id, f.subject, f.predicate, f.value.value) for f in store.get_facts(publication_id="pub-ecb-pressconf"))
    extract_press_conference(store, pub)  # re-run: same deterministic fact_ids
    second = sorted((f.fact_id, f.subject, f.predicate, f.value.value) for f in store.get_facts(publication_id="pub-ecb-pressconf"))
    assert first == second
    assert len(second) == 13


def test_extraction_is_deterministic(tmp_path):
    for name in GOLDEN:
        store = _store_press_conf(tmp_path, name)
        result = extract_fixture(name)
        store.rebuild_facts_for_document(result.document_id, result)
        first = sorted((f.fact_id, f.subject, f.predicate, f.value.value) for f in store.get_facts(publication_id="pub-ecb-pressconf"))
        store.rebuild_facts_for_document(result.document_id, result)
        second = sorted((f.fact_id, f.subject, f.predicate, f.value.value) for f in store.get_facts(publication_id="pub-ecb-pressconf"))
        assert first == second, name
        assert len(first) == len(result.facts), name
        ids = [f.fact_id for f in result.facts]
        assert len(ids) == len(set(ids)), name  # no fact_id collisions


# ---------------------------------------------------------------------------
# classification gating (single source of truth = classifications table)
# ---------------------------------------------------------------------------


def test_gating_press_conference_classification_allows_extraction(tmp_path):
    store = _store_press_conf(tmp_path)
    classify_press_conference(store)
    results = extract_press_conference(store, press_conference_publication())
    assert len(results) == 1
    assert len(store.get_facts(publication_id="pub-ecb-pressconf")) == 13


def test_gating_other_classification_refuses_extraction(tmp_path):
    store = _store_press_conf(tmp_path)
    classify_press_conference(store, publication_type="minutes")
    assert extract_press_conference(store, press_conference_publication()) == []
    assert store.get_facts(publication_id="pub-ecb-pressconf") == []


def test_gating_absent_classification_refuses_extraction(tmp_path):
    store = _store_press_conf(tmp_path)
    assert extract_press_conference(store, press_conference_publication()) == []
    assert store.get_facts(publication_id="pub-ecb-pressconf") == []


def test_gating_publication_type_cache_alone_never_authorizes(tmp_path):
    store = _store_press_conf(tmp_path)
    pub = press_conference_publication(publication_type="press_conference")
    # the denormalized cache says press_conference, but there is no authoritative
    # classification record → extraction must be refused
    assert extract_press_conference(store, pub) == []
    assert store.get_facts(publication_id="pub-ecb-pressconf") == []


def test_gating_batch_respects_classification(tmp_path):
    store = _store_press_conf(tmp_path)
    assert extract_press_conference_batch(store) == []  # unclassified → nothing extracted
    assert store.get_facts(publication_id="pub-ecb-pressconf") == []
    classify_press_conference(store)
    results = extract_press_conference_batch(store)
    assert len(results) == 1
    assert len(store.get_facts(bank="ecb")) == 13


def test_gating_never_persists_facts_when_not_authorized(tmp_path):
    store = _store_press_conf(tmp_path)
    classify_press_conference(store, publication_type="monetary_policy_report")
    assert extract_press_conference(store, press_conference_publication()) == []
    assert extract_press_conference_batch(store) == []
    assert store.get_facts(publication_id="pub-ecb-pressconf") == []


def test_gating_refusal_never_deletes_existing_facts(tmp_path):
    """A classification that refuses extraction must NOT delete facts that an
    earlier authorized extraction persisted (X-1)."""
    store = _store_press_conf(tmp_path)
    classify_press_conference(store)
    assert len(extract_press_conference(store, press_conference_publication())) == 1
    assert len(store.get_facts(publication_id="pub-ecb-pressconf")) == 13
    classify_press_conference(store, publication_type="minutes")
    assert extract_press_conference(store, press_conference_publication()) == []
    assert len(store.get_facts(publication_id="pub-ecb-pressconf")) == 13


# ---------------------------------------------------------------------------
# empty-result persistence: the current extraction result is the source of truth
# ---------------------------------------------------------------------------


def test_empty_result_persistence_clears_stale_facts(tmp_path):
    store = _store_press_conf(tmp_path)
    classify_press_conference(store)
    pub = press_conference_publication()
    extract_press_conference(store, pub)
    assert len(store.get_facts(publication_id="pub-ecb-pressconf")) == 13
    results = extract_press_conference(store, pub, extractor=_ZeroFactPressConferenceExtractor())
    assert len(results) == 1
    assert results[0].facts == []
    assert store.get_facts(publication_id="pub-ecb-pressconf") == []


def test_empty_result_persistence_preserves_other_documents(tmp_path):
    store = _store_press_conf(tmp_path)
    classify_press_conference(store)
    pub = press_conference_publication()
    extract_press_conference(store, pub)
    extract_press_conference(store, pub, document=normalized_fixture("ecb_press_conf_minimal.html"))
    assert len(store.get_facts(publication_id="pub-ecb-pressconf")) == 14
    # zero-out only the nominal document; the other document's facts must stay
    extract_press_conference(store, pub, document=normalized_fixture("ecb_press_conf.html"), extractor=_ZeroFactPressConferenceExtractor())
    persisted = store.get_facts(publication_id="pub-ecb-pressconf")
    assert len(persisted) == 1
    assert persisted[0].subject == SUBJECT_INFLATION
    assert persisted[0].document_id == normalized_fixture("ecb_press_conf_minimal.html").document_id


def test_empty_result_persistence_is_idempotent(tmp_path):
    store = _store_press_conf(tmp_path)
    classify_press_conference(store)
    pub = press_conference_publication()
    extract_press_conference(store, pub)
    assert len(store.get_facts(publication_id="pub-ecb-pressconf")) == 13
    zero = _ZeroFactPressConferenceExtractor()
    extract_press_conference(store, pub, extractor=zero)
    extract_press_conference(store, pub, extractor=zero)
    assert store.get_facts(publication_id="pub-ecb-pressconf") == []


# ---------------------------------------------------------------------------
# Phase 4.1 / Phase 4.2 coexistence
# ---------------------------------------------------------------------------


def test_phase5_and_phase6_do_not_overlap_with_press_conf(tmp_path):
    """A press conference publication never feeds the decision or statement
    extractors (gating on classification), and Phase 4.3 never emits Phase 4.1/6
    fact subjects."""
    store = _store_press_conf(tmp_path)
    pub = press_conference_publication()
    store.set_classification(
        "pub-ecb-pressconf",
        central_bank="ecb",
        publication_type="press_conference",
        confidence=Confidence.HIGH.value,
        method="url_pattern",
        evidence=[],
    )
    # store-level helpers are gated on classification
    assert extract_decision(store, pub) == []
    assert extract_statement(store, pub) == []
    # Phase 4.3 extraction produces its own facts only
    extract_press_conference(store, pub)
    persisted = store.get_facts(publication_id="pub-ecb-pressconf")
    phase5_subjects = {
        "monetary_policy_decision", "main_refinancing_rate", "marginal_lending_rate",
        "deposit_facility_rate", "asset_purchase", "vote",
    }
    assert not phase5_subjects & {f.subject for f in persisted}
    assert not any(f.predicate == "rationale" for f in persisted)  # Phase 4.2 rationale is not a Phase 4.3 category
    assert not any(f.predicate == "change" for f in persisted)
    assert all(f.extraction_version == EcbPressConferenceExtractor.extraction_version for f in persisted)
