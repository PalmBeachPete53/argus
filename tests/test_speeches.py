"""Phase 4.7 — ECB Speech extractor: end-to-end tests using the local HTML
fixtures and the existing Store (vertical slice).

Covers: classification gating (``speech``), conservative section routing
(known economic section → mined in full, known non-economic heading →
IGNORED, unknown heading → strictly mined — explicit assertions only, never
``UNKNOWN ≠ ECONOMIC`` automatic facts), content-first sentence classification
(guidance > policy > risk > financial > inflation > labour > growth), explicit
value claims only (percentage + explicit reference period; forecast without a
period is ignored; share units are never percentages), categorical risk
orientations (upside / downside / balanced) only when explicit, explicit
speaker attribution (body ``Speaker:`` line > metadata author, never inferred,
quoted authors never attributed to the speaker), provenance (verbatim
source_text, source_location, extraction version/method, ``speech:`` identity
qualifiers), within-run deduplication, deterministic extraction, idempotent and
empty-result persistence, and Phase 4.1/6/7/8/9/10 coexistence.
"""

from __future__ import annotations

from pathlib import Path

from argus.classification.base import Confidence
from argus.decisions import extract_decision
from argus.documents import Normalizer
from argus.documents.base import DocumentSection, NormalizedDocument
from argus.facts import ExtractionResult, LocationKind, ValueKind
from argus.minutes import extract_minutes
from argus.models import Document, DocumentStatus, Publication
from argus.press_conferences import extract_press_conference
from argus.projections import extract_projections
from argus.reports import extract_report
from argus.speeches import (
    SPEECH_PUBLICATION_TYPES,
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
    EcbSpeechExtractor,
    SpeechExtractor,
    extract_speech,
    extract_speech_batch,
)
from argus.speeches.ecb import CAT_IGNORE, CAT_UNKNOWN, _section_category
from argus.statements import extract_statement
from argus.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "documents"
ECB_URL = "https://www.ecb.europa.eu/press/key/html/ecb.sp260312.en.html"


def speeches_publication(**kw) -> Publication:
    fields = dict(
        central_bank="ecb",
        title="Monetary policy in a changing economy",
        url=ECB_URL,
        source_id="ecb-speech",
        source_url="https://www.ecb.europa.eu/press/key/html/index.en.html",
        id="pub-ecb-speech",
    )
    fields.update(kw)
    return Publication(**fields)


def normalized_fixture(name: str) -> object:
    doc = Document(
        publication_id="pub-ecb-speech",
        url=ECB_URL,
        kind="html",
        status=DocumentStatus.FETCHED,
        local_path=str(FIXTURES / name),
    )
    return Normalizer().parse(doc)


def extract_fixture(name: str):
    return EcbSpeechExtractor().extract(speeches_publication(), normalized_fixture(name))


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
        publication_id="pub-ecb-speech",
        document_id="sha-sections",
        source_url=ECB_URL,
        local_path=None,
        document_kind="html",
        sections=sections,
    )


def _section(heading: str, text: str) -> DocumentSection:
    return DocumentSection(order=0, heading=heading, level=2, text=text)


def _numeric_values(result, subject: str) -> dict:
    return {
        (period_of(f), f.value.value)
        for f in facts_by(result, subject, "value")
        if f.value.value is not None
    }


# ---------------------------------------------------------------------------
# golden facts across all ECB speech fixtures
# ---------------------------------------------------------------------------

GOLDEN = {
    "ecb_speech.html": {
        "warnings": [],
        "count": 16,
        "speaker": "Christine Lagarde",
        "triples": [
            (SUBJECT_GROWTH, "assessment", None),
            (SUBJECT_GDP, "value", "year:2026"),
            (SUBJECT_GDP, "value", "year:2027"),
            (SUBJECT_INFLATION, "value", "year:2025"),
            (SUBJECT_INFLATION, "value", "year:2026"),
            (SUBJECT_INFLATION, "value", "year:2027"),
            (SUBJECT_CORE_INFLATION, "value", "month:2026-06"),
            (SUBJECT_INFLATION_EXPECTATIONS, "assessment", None),
            (SUBJECT_UNEMPLOYMENT, "value", "year:2026"),
            (SUBJECT_WAGES, "value", "year:2026"),
            (SUBJECT_FINANCIAL_CONDITIONS, "assessment", None),
            (SUBJECT_FINANCIAL_CONDITIONS, "value", "month:2026-06"),
            (SUBJECT_MONETARY_POLICY, "statement", None),
            (SUBJECT_POLICY_GUIDANCE, "statement", None),
            (SUBJECT_GROWTH_RISK, "assessment", None),
            (SUBJECT_INFLATION_RISK, "assessment", None),
        ],
        "values": {
            (SUBJECT_GDP, "year:2026"): 1.2,
            (SUBJECT_GDP, "year:2027"): 1.4,
            (SUBJECT_INFLATION, "year:2025"): 2.4,
            (SUBJECT_INFLATION, "year:2026"): 2.0,
            (SUBJECT_INFLATION, "year:2027"): 1.9,
            (SUBJECT_CORE_INFLATION, "month:2026-06"): 2.3,
            (SUBJECT_UNEMPLOYMENT, "year:2026"): 6.2,
            (SUBJECT_WAGES, "year:2026"): 3.8,
            (SUBJECT_FINANCIAL_CONDITIONS, "month:2026-06"): 4.2,
        },
        "risk_orientations": {
            (SUBJECT_GROWTH_RISK, None): "balanced",
            (SUBJECT_INFLATION_RISK, None): "upside",
        },
    },
    "ecb_speech_unknown.html": {
        "warnings": ["no_forward_guidance", "quoted_content_skipped"],
        "count": 3,
        "speaker": "Christine Lagarde",
        "triples": [
            (SUBJECT_INFLATION, "value", "year:2026"),
            (SUBJECT_GROWTH_RISK, "assessment", None),
            (SUBJECT_GDP, "value", "year:2026"),
        ],
        "values": {
            (SUBJECT_INFLATION, "year:2026"): 2.4,
            (SUBJECT_GDP, "year:2026"): 1.2,
        },
        "risk_orientations": {
            (SUBJECT_GROWTH_RISK, None): "downside",
        },
    },
    "ecb_speech_personal.html": {
        "warnings": ["no_risk_assessment", "no_forward_guidance"],
        "count": 0,
        "speaker": None,
        "triples": [],
        "values": {},
        "risk_orientations": {},
    },
    "ecb_speech_minimal.html": {
        "warnings": ["no_risk_assessment", "no_forward_guidance"],
        "count": 1,
        "speaker": None,
        "triples": [(SUBJECT_INFLATION, "value", "year:2026")],
        "values": {(SUBJECT_INFLATION, "year:2026"): 2.0},
        "risk_orientations": {},
    },
    "ecb_speech_adversarial.html": {
        "warnings": ["quoted_content_skipped"],
        "count": 8,
        "speaker": "Christine Lagarde",
        "triples": [
            (SUBJECT_GDP, "value", None),
            (SUBJECT_GROWTH, "assessment", None),
            (SUBJECT_GROWTH, "assessment", None),
            (SUBJECT_INFLATION, "value", "year:2027"),
            (SUBJECT_WAGES, "assessment", None),
            (SUBJECT_FINANCIAL_CONDITIONS, "value", None),
            (SUBJECT_POLICY_GUIDANCE, "statement", None),
            (SUBJECT_RISK, "assessment", None),
        ],
        "values": {
            (SUBJECT_GDP, None): 2.4,
            (SUBJECT_INFLATION, "year:2027"): 2.1,
            (SUBJECT_FINANCIAL_CONDITIONS, None): 3.0,
        },
        "risk_orientations": {
            (SUBJECT_RISK, None): "downside",
        },
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
        for fact in result.facts:
            assert fact.speaker == expected["speaker"], (name, fact.subject)


# ---------------------------------------------------------------------------
# conservative section routing — Phase 4.7 (UNKNOWN sections are strictly mined)
# ---------------------------------------------------------------------------


def test_known_economic_sections_are_mined():
    result = extract_fixture("ecb_speech.html")
    assert len(result.facts) == 16


def test_non_economic_sections_are_ignored():
    result = extract_fixture("ecb_speech.html")
    assert not any("Thank you for your attention" in (f.source_text or "") for f in result.facts)
    result = extract_fixture("ecb_speech_unknown.html")
    assert not any("Question:" in (f.source_text or "") for f in result.facts)
    result = extract_fixture("ecb_speech_personal.html")
    assert result.facts == []
    assert not any("Christine Lagarde was born in Paris" in (f.source_text or "") for f in result.facts)


def test_unknown_sections_are_strictly_mined():
    # unknown heading ≠ automatic facts: only explicit assertions pass — an
    # explicit value claim and a categorical risk orientation are kept, a bare
    # qualitative assessment is not
    sections = [
        _section("Some future section", "Inflation is expected to remain elevated."),
        _section("Some future section", "Inflation is projected to average 2.4% in 2026."),
        _section("Some future section", "Risks to growth were tilted to the downside."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert len(result.facts) == 2
    assert {f.subject for f in result.facts} == {SUBJECT_INFLATION, SUBJECT_GROWTH_RISK}


def test_unknown_section_keeps_only_explicit_risk_orientation():
    sections = [
        _section("Some future section", "Risks to growth remained elevated."),
        _section("Some future section", "Risks to growth were tilted to the downside."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert len(result.facts) == 1
    assert result.facts[0].value.value == "downside"


def test_analytical_boxes_are_never_mined():
    sections = [
        _section("Box 1 — The global economy", "Inflation is projected to average 2.4% in 2026."),
        _section("Box 2 — Fiscal developments", "Inflation is projected to average 2.0% in 2027."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert result.facts == []


def test_heading_less_section_is_ignored():
    sections = [
        DocumentSection(order=0, heading="", level=0, text="Inflation is projected to average 2.4% in 2026."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert result.facts == []


def test_known_non_economic_headings_are_ignored():
    # every controlled non-economic heading is a known IGNORE section: 0 facts
    # even under economic-looking content
    for heading in (
        "Speech", "Speech by", "Remarks", "Address", "Keynote speech", "Keynote address",
        "About the speaker", "Speaker biography", "Biography",
        "Acknowledgements", "Thanks", "Thank you",
        "Closing remarks", "Concluding remarks", "Closing",
        "Questions and answers", "Q&A", "Questions",
        "References", "Bibliography", "Notes", "Annex", "Appendix",
        "Legal notice", "Disclaimer", "Copyright", "Imprint", "Glossary",
    ):
        result = EcbSpeechExtractor().extract(
            speeches_publication(),
            _doc_with_sections([_section(heading, "Inflation is projected to average 2.4% in 2026.")]),
        )
        assert result.facts == [], heading


def test_near_miss_headings_are_strictly_mined_never_ignored():
    # a near-miss heading ("Risk management", "Financial institutions",
    # "Economic history") is UNKNOWN, not IGNORE: explicit assertions still
    # pass (precision is kept by strictness, not by heading coincidence)
    for heading in ("Risk management", "Financial institutions", "Economic history"):
        result = EcbSpeechExtractor().extract(
            speeches_publication(),
            _doc_with_sections(
                [_section(heading, "Inflation is projected to average 2.4% in 2026. "
                                   "Risks to growth were tilted to the downside.")]
            ),
        )
        assert {f.subject for f in result.facts} == {SUBJECT_INFLATION, SUBJECT_GROWTH_RISK}, heading


def test_heading_routing_is_exact_identity_not_substring():
    mined = EcbSpeechExtractor().extract(
        speeches_publication(),
        _doc_with_sections([_section("Economic outlook", "The economy is expected to expand at a moderate pace.")]),
    )
    assert len(mined.facts) == 1
    assert mined.facts[0].subject == SUBJECT_GROWTH
    # the near-miss heading falls to UNKNOWN: the same bare qualitative
    # sentence is no longer an automatic fact
    strict = EcbSpeechExtractor().extract(
        speeches_publication(),
        _doc_with_sections([_section("Economic outlook for 2026", "The economy is expected to expand at a moderate pace.")]),
    )
    assert strict.facts == []


def test_heading_normalization_controls_case_numbering_punctuation_and_the():
    for heading in (
        "ECONOMIC OUTLOOK",
        "2 Economic outlook",
        "Economic outlook:",
        "The economic outlook",
    ):
        result = EcbSpeechExtractor().extract(
            speeches_publication(),
            _doc_with_sections([_section(heading, "The economy is expected to expand at a moderate pace.")]),
        )
        assert result.facts, heading
        assert result.facts[0].subject == SUBJECT_GROWTH, heading


def test_ignore_identity_is_exact_never_substring():
    assert _section_category("About the speaker") == CAT_IGNORE
    assert _section_category("Speaker biography") == CAT_IGNORE
    assert _section_category("Closing remarks") == CAT_IGNORE
    assert _section_category("Questions and answers") == CAT_IGNORE
    assert _section_category("Q&A") == CAT_IGNORE
    # near-misses are never those headings: they fall to the normal exact rules
    assert _section_category("About the speaker's journey") == CAT_UNKNOWN
    assert _section_category("Closing ceremony") == CAT_UNKNOWN
    assert _section_category("Q") == CAT_UNKNOWN


# ---------------------------------------------------------------------------
# speaker attribution — explicit only
# ---------------------------------------------------------------------------


def test_body_speaker_line_wins_over_metadata_author():
    result = extract_fixture("ecb_speech.html")  # meta author "Isabel Schnabel", body "Speaker: Christine Lagarde"
    assert len(result.facts) == 16
    assert all(f.speaker == "Christine Lagarde" for f in result.facts)


def test_metadata_author_is_the_fallback():
    result = extract_fixture("ecb_speech_unknown.html")  # no body line, meta author "Christine Lagarde"
    assert result.facts
    assert all(f.speaker == "Christine Lagarde" for f in result.facts)


def test_speaker_is_never_inferred():
    sections = [
        _section("Economic outlook", "Inflation is projected to average 2.0% in 2026."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert len(result.facts) == 1
    assert result.facts[0].speaker is None
    # a name mentioned in the prose is never read as the speaker
    sections = [
        _section("Economic outlook", "Christine Lagarde said inflation will rise."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert result.facts
    assert all(f.speaker is None for f in result.facts)


def test_speaker_label_is_preserved_verbatim():
    sections = [
        _section("Economic outlook", "Speaker: Christine Lagarde, President of the ECB\n\nInflation is projected to average 2.0% in 2026."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert len(result.facts) == 1
    assert result.facts[0].speaker == "Christine Lagarde, President of the ECB"


def test_personal_biography_history_ceremonial_content_yields_no_facts():
    result = extract_fixture("ecb_speech_personal.html")
    assert result.facts == []
    assert result.warnings == ["no_risk_assessment", "no_forward_guidance"]


def test_quoted_author_content_is_skipped():
    result = extract_fixture("ecb_speech_unknown.html")
    assert "quoted_content_skipped" in result.warnings
    assert not any("Keynes" in (f.source_text or "") for f in result.facts)


def test_quoted_other_is_never_attributed_to_the_speaker():
    sections = [
        _section("Economic outlook", "As Isabel Schnabel noted, inflation will rise."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert result.facts == []
    assert "quoted_content_skipped" in result.warnings


def test_self_quotations_are_not_skipped():
    # the speaker quoting their own past words is never a quotation of another
    sections = [
        _section("Economic outlook", "As I said a year ago, inflation will rise."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert len(result.facts) == 1
    assert "quoted_content_skipped" not in result.warnings


def test_self_quotation_by_name_is_not_skipped():
    sections = [
        _section("Economic outlook", "As Christine Lagarde has said, inflation will rise."),
        _section("Economic outlook", "Speaker: Christine Lagarde"),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert len(result.facts) == 1
    assert result.facts[0].speaker == "Christine Lagarde"
    assert "quoted_content_skipped" not in result.warnings


# ---------------------------------------------------------------------------
# content-first classification precedence
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
    ]
    for sentence, subject in cases:
        result = EcbSpeechExtractor().extract(
            speeches_publication(), _doc_with_sections([_section("Overview", sentence)])
        )
        assert {f.subject for f in result.facts} == {subject}, sentence


def test_precedence_guidance_over_policy_risk_is_deterministic():
    sentence = (
        "The Council stood ready to adjust its instruments within its mandate, "
        "and downside risks to growth remained."
    )
    doc = _doc_with_sections([_section("Risk assessment", sentence)])
    first = EcbSpeechExtractor().extract(speeches_publication(), doc)
    second = EcbSpeechExtractor().extract(speeches_publication(), doc)
    assert [f.subject for f in first.facts] == [SUBJECT_POLICY_GUIDANCE]
    assert [f.subject for f in second.facts] == [SUBJECT_POLICY_GUIDANCE]


def test_policy_stance_requires_a_policy_term():
    sections = [
        _section("Economic activity", "The stance of the economy remained fragile."),
        _section("Economic activity", "The monetary policy stance remained appropriately calibrated."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    subjects = [f.subject for f in result.facts]
    assert SUBJECT_MONETARY_POLICY in subjects
    assert SUBJECT_GROWTH in subjects  # the "economy" sentence is growth, not policy


def test_risk_outranks_inflation():
    sections = [
        _section("Inflation", "Risks to inflation were tilted to the upside."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert len(result.facts) == 1
    assert result.facts[0].subject == SUBJECT_INFLATION_RISK


def test_financial_outranks_inflation():
    sections = [
        _section("Inflation", "Financial conditions are projected to ease gradually."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert len(result.facts) == 1
    assert result.facts[0].subject == SUBJECT_FINANCIAL_CONDITIONS
    assert result.facts[0].predicate == "assessment"


def test_no_fact_from_neutral_sentences():
    sections = [
        _section("Economic activity", "The Governing Council will assess the outlook at its next meeting."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert result.facts == []


# ---------------------------------------------------------------------------
# inflation / growth / labour market subject resolution
# ---------------------------------------------------------------------------


def test_inflation_subjects_are_distinct():
    sections = [
        _section("Inflation", "Inflation is projected to average 2.0% in 2026."),
        _section("Inflation", "Core inflation is projected to average 2.3% in June 2026."),
        _section("Inflation", "Inflation expectations remained well anchored."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert {f.subject for f in result.facts} == {
        SUBJECT_INFLATION, SUBJECT_CORE_INFLATION, SUBJECT_INFLATION_EXPECTATIONS,
    }
    assert facts_by(result, SUBJECT_CORE_INFLATION, "value")[0].period.canonical() == "month:2026-06"


def test_plain_growth_sentence_is_qualitative():
    sections = [
        _section("Economic outlook", "Economic activity continued to expand at a moderate pace."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert len(result.facts) == 1
    assert result.facts[0].subject == SUBJECT_GROWTH
    assert result.facts[0].predicate == "assessment"
    assert result.facts[0].value.kind is ValueKind.TEXT


def test_quantitative_growth_is_gdp_value():
    sections = [
        _section("Economic outlook", "Real GDP is projected to grow by 1.2% in 2026."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert len(result.facts) == 1
    assert result.facts[0].subject == SUBJECT_GDP
    assert result.facts[0].predicate == "value"
    assert result.facts[0].value.value == 1.2


def test_gdp_near_misses_never_anchor_growth():
    # "GDP deflator", "GDP per capita" and "per capita GDP" are distinct
    # measures and must never anchor (or emit) a GDP value fact (same guard as
    # Phase 4.6 reports).
    sections = [
        _section("Economic outlook", "The GDP deflator rose by 2.1% in 2026."),
        _section("Economic outlook", "GDP per capita increased by 1.1% in 2026."),
        _section("Economic outlook", "Per capita GDP increased by 1.1% in 2026."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert result.facts == []


def test_gdp_near_miss_never_leaks_into_a_growth_value():
    # a deflator / per-capita mention inside an otherwise-growth sentence must
    # not leak into a GDP value fact (precision first).
    sections = [
        _section("Economic outlook", "Real GDP growth held steady while the GDP deflator rose by 2.1%."),
        _section("Economic outlook", "Growth remained solid and GDP per capita rose by 1.1%."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert result.facts == []


def test_gdp_value_facts_still_mined_when_not_a_near_miss():
    sections = [
        _section("Economic outlook", "GDP growth is projected to reach 1.4% in 2027."),
        _section("Economic outlook", "GDP increased by 0.4% in the first quarter of 2026."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    values = facts_by(result, SUBJECT_GDP, "value")
    assert {(f.value.value, period_of(f)) for f in values} == {(1.4, "year:2027"), (0.4, "quarter:2026-Q1")}


def test_labour_market_subjects():
    sections = [
        _section("Labour market", "The unemployment rate is projected to average 6.2% in 2026."),
        _section("Labour market", "Wage growth is projected to average 3.8% in 2026."),
        _section("Labour market", "The labour market remained resilient."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert {f.subject for f in result.facts} == {
        SUBJECT_UNEMPLOYMENT, SUBJECT_WAGES, SUBJECT_LABOUR_MARKET,
    }


# ---------------------------------------------------------------------------
# value gate: explicit value claims with explicit units and periods
# ---------------------------------------------------------------------------


def test_value_requires_an_explicit_value_claim_verb():
    sections = [
        _section("Inflation", "Inflation stood at 2.4% in 2025."),
        _section("Inflation", "Inflation is 2.4% in 2025."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    value_facts = facts_by(result, SUBJECT_INFLATION, "value")
    assert len(value_facts) == 1  # only "stood at" is a value claim
    assert value_facts[0].value.value == 2.4


def test_periods_come_from_the_sentence_wording():
    sections = [
        _section("Inflation", "Inflation is projected to average 2.4% in 2025."),
        _section("Inflation", "Inflation is projected to average 2.4% in June 2025."),
        _section("Inflation", "Inflation is projected to average 2.4% in the first quarter of 2025."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    periods = {period_of(f) for f in facts_by(result, SUBJECT_INFLATION, "value")}
    assert periods == {"year:2025", "month:2025-06", "quarter:2025-Q1"}


def test_forecast_without_explicit_period_is_ignored():
    sections = [
        _section("Inflation", "Inflation is projected to average 2.4%."),
        _section("Inflation", "Inflation averaged 2.4% in 2025."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    value_facts = facts_by(result, SUBJECT_INFLATION, "value")
    assert len(value_facts) == 1  # the period-less forecast is under-determined and ignored
    assert value_facts[0].period.canonical() == "year:2025"


def test_share_units_are_never_percentages():
    sections = [
        _section("Inflation", "Inflation is projected to average 3.0% of GDP in 2026."),
        _section("Inflation", "Core inflation is projected to average 2.0% of total in 2027."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert not any(f.value.kind is ValueKind.PERCENTAGE for f in result.facts)


def test_basis_points_are_not_extracted_as_percentages():
    sections = [
        _section("Financial stability", "Market rates rose by 25 basis points."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert not any(f.value.kind in (ValueKind.PERCENTAGE, ValueKind.BASIS_POINTS) for f in result.facts)


def test_value_facts_carry_percentage_kind_and_verbatim_token():
    result = extract_fixture("ecb_speech.html")
    gdp = next(f for f in facts_by(result, SUBJECT_GDP, "value") if period_of(f) == "year:2026")
    assert gdp.value.kind is ValueKind.PERCENTAGE
    assert gdp.value.value == 1.2
    assert gdp.value.source_text == "1.2%"
    assert gdp.period.label == "in 2026"


# ---------------------------------------------------------------------------
# risk facts
# ---------------------------------------------------------------------------


def test_risk_orientation_is_categorical_high_confidence():
    result = extract_fixture("ecb_speech.html")
    growth_risk = facts_by(result, SUBJECT_GROWTH_RISK, "assessment")[0]
    assert growth_risk.value.kind is ValueKind.CATEGORICAL
    assert growth_risk.value.value == "balanced"
    assert growth_risk.confidence is Confidence.HIGH


def test_risk_target_read_from_wording():
    sections = [
        _section("Risks", "Risks to inflation were tilted to the upside."),
        _section("Risks", "Risks to the growth outlook were tilted to the downside."),
        _section("Risks", "Risks were broadly balanced."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert {f.subject for f in result.facts} == {SUBJECT_INFLATION_RISK, SUBJECT_GROWTH_RISK, SUBJECT_RISK}
    orientations = {f.subject: f.value.value for f in result.facts}
    assert orientations == {
        SUBJECT_INFLATION_RISK: "upside",
        SUBJECT_GROWTH_RISK: "downside",
        SUBJECT_RISK: "balanced",
    }


def test_risk_without_orientation_is_verbatim_in_known_sections():
    sections = [
        _section("Risks", "Risks to growth remained elevated."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert len(result.facts) == 1
    assert result.facts[0].subject == SUBJECT_GROWTH_RISK
    assert result.facts[0].value.kind is ValueKind.TEXT
    assert result.facts[0].confidence is Confidence.MEDIUM


# ---------------------------------------------------------------------------
# within-run deduplication
# ---------------------------------------------------------------------------


def test_identical_assertion_across_sections_is_emitted_once():
    sections = [
        _section("Economic outlook", "Inflation is projected to average 2.0% in 2026."),
        _section("Inflation", "Inflation is projected to average 2.0% in 2026."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert len(result.facts) == 1


def test_distinct_assertions_with_same_value_are_kept():
    sections = [
        _section("Inflation", "Inflation is projected to average 2.0% in 2026."),
        _section("Inflation", "Inflation expectations are projected to average 2.0% in 2026."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    subjects = {f.subject for f in result.facts}
    assert SUBJECT_INFLATION in subjects and SUBJECT_INFLATION_EXPECTATIONS in subjects
    assert len(result.facts) == 2


def test_risk_sentences_with_same_orientation_but_different_text_both_kept():
    sections = [
        _section("Risks", "Risks to the growth outlook were tilted to the downside."),
        _section("Risks", "The risks to growth were assessed as being on the downside."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert len(facts_by(result, SUBJECT_GROWTH_RISK, "assessment")) == 2


# ---------------------------------------------------------------------------
# provenance + no interpretation
# ---------------------------------------------------------------------------


def test_provenance_is_traceable():
    for name in GOLDEN:
        document = normalized_fixture(name)
        result = extract_fixture(name)
        for fact in result.facts:
            assert fact.extraction_version == EcbSpeechExtractor.extraction_version
            assert fact.publication_id == "pub-ecb-speech"
            assert fact.document_id
            assert fact.effective_date is None
            assert fact.identity_qualifier.startswith("speech:")
            assert fact.source_location.kind is LocationKind.SECTION
            assert fact.source_text in document.sections[fact.source_location.section].text


def test_speaker_is_preserved_in_provenance():
    result = extract_fixture("ecb_speech.html")
    assert all(f.speaker == "Christine Lagarde" for f in result.facts)
    assert result.facts[0].extraction_method == "regex"


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
    result = extract_fixture("ecb_speech.html")
    policy = facts_by(result, SUBJECT_MONETARY_POLICY, "statement")
    assert len(policy) == 1
    assert policy[0].value.kind is ValueKind.TEXT
    assert policy[0].value.value == "The Governing Council decided to keep its key interest rates unchanged."
    assert policy[0].predicate != "decision"
    assert policy[0].value.value != "unchanged"  # never turned into a categorical outcome


def test_identity_qualifiers_are_ordinal_and_deterministic():
    # ordinals are per (subject, predicate, period): same-period qualitative
    # assessments increment, values in different periods start afresh
    sections = [
        _section("Economic outlook", "The economy is expected to expand at a moderate pace."),
        _section("Economic outlook", "The economy is expected to expand at a solid pace."),
    ]
    doc_result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert [f.identity_qualifier for f in doc_result.facts] == ["speech:growth:0", "speech:growth:1"]
    result = extract_fixture("ecb_speech.html")
    growth = facts_by(result, SUBJECT_GROWTH, "assessment")
    assert [f.identity_qualifier for f in growth] == ["speech:growth:0"]
    gdp = facts_by(result, SUBJECT_GDP, "value")
    assert [f.identity_qualifier for f in gdp] == ["speech:gdp:0", "speech:gdp:0"]
    assert all(f.identity_qualifier.startswith("speech:") for f in result.facts)


# ---------------------------------------------------------------------------
# warnings + empty documents
# ---------------------------------------------------------------------------


def test_empty_document_warns_no_sections():
    doc = NormalizedDocument(
        publication_id="pub-ecb-speech",
        document_id="sha-empty",
        source_url=ECB_URL,
        local_path=None,
        document_kind="html",
        sections=[],
        tables=[],
    )
    result = EcbSpeechExtractor().extract(speeches_publication(), doc)
    assert result.warnings == ["no_sections"]
    assert result.facts == []


def test_unknown_fixture_warns_guidance_and_quoted():
    result = extract_fixture("ecb_speech_unknown.html")
    assert result.warnings == ["no_forward_guidance", "quoted_content_skipped"]


def test_personal_fixture_warns_missing_risk_and_guidance():
    result = extract_fixture("ecb_speech_personal.html")
    assert result.warnings == ["no_risk_assessment", "no_forward_guidance"]


def test_speech_publication_types_are_recognized():
    assert SPEECH_PUBLICATION_TYPES == ("speech",)


# ---------------------------------------------------------------------------
# determinism + idempotent persistence (vertical slice)
# ---------------------------------------------------------------------------


def _store_speech(tmp_path, name: str = "ecb_speech.html") -> Store:
    store = Store(tmp_path / f"{name}.db")
    store.upsert_publication(speeches_publication())
    store.upsert_normalized_document(normalized_fixture(name))
    return store


def classify_speech(store: Store, *, publication_type: str = "speech") -> None:
    store.set_classification(
        "pub-ecb-speech",
        central_bank="ecb",
        publication_type=publication_type,
        confidence=Confidence.HIGH.value,
        method="url_pattern",
        evidence=[],
    )


class _ZeroFactSpeechesExtractor(SpeechExtractor):
    """Stub speech extractor that yields no facts — used to simulate a
    re-extraction of an already-persisted document that now produces nothing."""

    bank = "ecb"
    extraction_version = "test-zero"

    def extract(self, publication, document):
        return ExtractionResult(
            publication_id=publication.id or publication.dedup_key or "",
            document_id=document.document_id,
        )


def test_extract_speech_persists_facts(tmp_path):
    store = _store_speech(tmp_path)
    classify_speech(store)
    results = extract_speech(store, speeches_publication())
    assert len(results) == 1
    persisted = store.get_facts(publication_id="pub-ecb-speech")
    assert len(persisted) == 16
    assert all(f.central_bank == "ecb" for f in persisted)
    assert all(f.extraction_version == EcbSpeechExtractor.extraction_version for f in persisted)
    assert all(f.speaker == "Christine Lagarde" for f in persisted)
    gdp = next(f for f in persisted if f.subject == SUBJECT_GDP and period_of(f) == "year:2026")
    assert gdp.value.kind.value == "percentage"
    assert gdp.value.value == 1.2
    assert gdp.speaker == "Christine Lagarde"


def test_extract_speech_is_idempotent(tmp_path):
    store = _store_speech(tmp_path)
    classify_speech(store)
    pub = speeches_publication()
    extract_speech(store, pub)
    first = sorted((f.fact_id, f.subject, f.predicate, period_of(f), f.value.value) for f in store.get_facts(publication_id="pub-ecb-speech"))
    extract_speech(store, pub)  # re-run: same deterministic fact_ids
    second = sorted((f.fact_id, f.subject, f.predicate, period_of(f), f.value.value) for f in store.get_facts(publication_id="pub-ecb-speech"))
    assert first == second
    assert len(second) == 16


def test_extraction_is_deterministic(tmp_path):
    for name in GOLDEN:
        store = _store_speech(tmp_path, name)
        result = extract_fixture(name)
        store.rebuild_facts_for_document(result.document_id, result)
        first = sorted((f.fact_id, f.subject, f.predicate, period_of(f), f.value.value) for f in store.get_facts(publication_id="pub-ecb-speech"))
        store.rebuild_facts_for_document(result.document_id, result)
        second = sorted((f.fact_id, f.subject, f.predicate, period_of(f), f.value.value) for f in store.get_facts(publication_id="pub-ecb-speech"))
        assert first == second, name
        assert len(first) == len(result.facts), name
        ids = [f.fact_id for f in result.facts]
        assert len(ids) == len(set(ids)), name  # no fact_id collisions


# ---------------------------------------------------------------------------
# classification gating (single source of truth = classifications table)
# ---------------------------------------------------------------------------


def test_gating_speech_classification_allows_extraction(tmp_path):
    store = _store_speech(tmp_path)
    classify_speech(store, publication_type="speech")
    results = extract_speech(store, speeches_publication())
    assert len(results) == 1
    assert len(store.get_facts(publication_id="pub-ecb-speech")) == 16


def test_gating_other_classification_refuses_extraction(tmp_path):
    store = _store_speech(tmp_path)
    classify_speech(store, publication_type="press_conference")
    assert extract_speech(store, speeches_publication()) == []
    assert store.get_facts(publication_id="pub-ecb-speech") == []


def test_gating_absent_classification_refuses_extraction(tmp_path):
    store = _store_speech(tmp_path)
    assert extract_speech(store, speeches_publication()) == []
    assert store.get_facts(publication_id="pub-ecb-speech") == []


def test_gating_publication_type_cache_alone_never_authorizes(tmp_path):
    store = _store_speech(tmp_path)
    pub = speeches_publication(publication_type="speech")
    # the denormalized cache says speech, but there is no authoritative
    # classification record -> extraction must be refused
    assert extract_speech(store, pub) == []
    assert store.get_facts(publication_id="pub-ecb-speech") == []


def test_gating_batch_respects_classification(tmp_path):
    store = _store_speech(tmp_path)
    assert extract_speech_batch(store) == []  # unclassified -> nothing extracted
    assert store.get_facts(publication_id="pub-ecb-speech") == []
    classify_speech(store)
    results = extract_speech_batch(store)
    assert len(results) == 1
    assert len(store.get_facts(bank="ecb")) == 16


def test_gating_never_persists_facts_when_not_authorized(tmp_path):
    store = _store_speech(tmp_path)
    classify_speech(store, publication_type="economic_projections")
    assert extract_speech(store, speeches_publication()) == []
    assert extract_speech_batch(store) == []
    assert store.get_facts(publication_id="pub-ecb-speech") == []


def test_gating_refusal_never_deletes_existing_facts(tmp_path):
    """A classification that refuses extraction must NOT delete facts that an
    earlier authorized extraction persisted (X-1)."""
    store = _store_speech(tmp_path)
    classify_speech(store)
    assert len(extract_speech(store, speeches_publication())) == 1
    assert len(store.get_facts(publication_id="pub-ecb-speech")) == 16
    classify_speech(store, publication_type="press_conference")
    assert extract_speech(store, speeches_publication()) == []
    assert len(store.get_facts(publication_id="pub-ecb-speech")) == 16


# ---------------------------------------------------------------------------
# empty-result persistence: the current extraction result is the source of truth
# ---------------------------------------------------------------------------


def test_empty_result_persistence_clears_stale_facts(tmp_path):
    store = _store_speech(tmp_path)
    classify_speech(store)
    pub = speeches_publication()
    extract_speech(store, pub)
    assert len(store.get_facts(publication_id="pub-ecb-speech")) == 16
    results = extract_speech(store, pub, extractor=_ZeroFactSpeechesExtractor())
    assert len(results) == 1
    assert results[0].facts == []
    assert store.get_facts(publication_id="pub-ecb-speech") == []


def test_empty_result_persistence_preserves_other_documents(tmp_path):
    store = _store_speech(tmp_path)
    classify_speech(store)
    pub = speeches_publication()
    extract_speech(store, pub)
    extract_speech(store, pub, document=normalized_fixture("ecb_speech_minimal.html"))
    assert len(store.get_facts(publication_id="pub-ecb-speech")) == 17
    # zero-out only the nominal document; the other document's facts must stay
    extract_speech(
        store, pub, document=normalized_fixture("ecb_speech.html"),
        extractor=_ZeroFactSpeechesExtractor(),
    )
    persisted = store.get_facts(publication_id="pub-ecb-speech")
    assert len(persisted) == 1
    assert persisted[0].document_id == normalized_fixture("ecb_speech_minimal.html").document_id
    assert persisted[0].subject == SUBJECT_INFLATION


def test_empty_result_persistence_is_idempotent(tmp_path):
    store = _store_speech(tmp_path)
    classify_speech(store)
    pub = speeches_publication()
    extract_speech(store, pub)
    assert len(store.get_facts(publication_id="pub-ecb-speech")) == 16
    zero = _ZeroFactSpeechesExtractor()
    extract_speech(store, pub, extractor=zero)
    extract_speech(store, pub, extractor=zero)
    assert store.get_facts(publication_id="pub-ecb-speech") == []


# ---------------------------------------------------------------------------
# Phase 4.1 / 6 / 7 / 8 / 9 / 10 coexistence
# ---------------------------------------------------------------------------


def test_other_extractors_do_not_overlap_with_speeches(tmp_path):
    """A speech publication never feeds the decision, statement, press
    conference, minutes, projections or report extractors (gating on
    classification), and Phase 4.7 never emits Phase 4.1/6/7/8/9/10 fact
    subjects."""
    store = _store_speech(tmp_path)
    pub = speeches_publication()
    classify_speech(store)
    # store-level helpers are gated on classification
    assert extract_decision(store, pub) == []
    assert extract_statement(store, pub) == []
    assert extract_press_conference(store, pub) == []
    assert extract_minutes(store, pub) == []
    assert extract_projections(store, pub) == []
    assert extract_report(store, pub) == []
    # Phase 4.7 extraction produces its own facts only
    extract_speech(store, pub)
    persisted = store.get_facts(publication_id="pub-ecb-speech")
    phase_subjects = {
        "monetary_policy_decision", "main_refinancing_rate", "marginal_lending_rate",
        "deposit_facility_rate", "asset_purchase", "vote",
    }
    assert not phase_subjects & {f.subject for f in persisted}
    assert all(f.predicate in ("assessment", "statement", "value") for f in persisted)
    assert all(f.extraction_version == EcbSpeechExtractor.extraction_version for f in persisted)
    assert all(f.identity_qualifier.startswith("speech:") for f in persisted)


def test_speeches_extractor_refuses_other_publication_types(tmp_path):
    store = _store_speech(tmp_path)
    classify_speech(store, publication_type="meeting_account")
    assert extract_speech(store, speeches_publication()) == []
    assert store.get_facts(publication_id="pub-ecb-speech") == []


# ---------------------------------------------------------------------------
# Phase 4.7 hardening — precision over recall: economic vocabulary alone is
# never a fact; an explicit assertion is required for qualitative facts, and
# generic content anchors were replaced or removed.
# ---------------------------------------------------------------------------


def test_hardening_qualitative_gate_rejects_platitudes_and_topic_mentions():
    for sentence in (
        "The economy is important for our society.",
        "The economy remains a priority.",
        "The economy matters.",
        "Investment is essential for Europe's future.",
        "Investment remains a key priority.",
        "Consumption is central to our society.",
        "Inflation is important.",
        "Inflation expectations are important.",
        "There are risks.",
        "Credit is important.",
        "We will continue our work.",
    ):
        result = EcbSpeechExtractor().extract(
            speeches_publication(),
            _doc_with_sections([_section("Economic outlook", sentence)]),
        )
        assert result.facts == [], sentence


def test_hardening_generic_anchor_removal():
    for sentence in (
        "The recovery of trust is important.",
        "Recession is important.",
        "The expansion of the euro area is important.",
        "The production of goods increased.",
        "Demand is a challenge for policy.",
    ):
        result = EcbSpeechExtractor().extract(
            speeches_publication(),
            _doc_with_sections([_section("Economic outlook", sentence)]),
        )
        assert result.facts == [], sentence


def test_hardening_preserved_assertions_still_extract():
    for sentence in (
        "Economic activity strengthened.",
        "Output increased.",
        "GDP growth accelerated.",
        "Domestic demand weakened.",
        "Aggregate demand declined.",
        "Total demand fell.",
        "Consumption increased.",
        "Investment declined.",
    ):
        result = EcbSpeechExtractor().extract(
            speeches_publication(),
            _doc_with_sections([_section("Economic outlook", sentence)]),
        )
        assert [f.subject for f in result.facts] == [SUBJECT_GROWTH], sentence
        assert result.facts[0].predicate == "assessment", sentence


def test_hardening_guidance_and_policy_are_not_gated():
    result = EcbSpeechExtractor().extract(
        speeches_publication(),
        _doc_with_sections(
            [
                _section("Monetary policy", "Future policy decisions will depend on incoming data."),
                _section("Monetary policy", "Monetary policy remains restrictive."),
            ]
        ),
    )
    assert [f.subject for f in result.facts] == [SUBJECT_POLICY_GUIDANCE, SUBJECT_MONETARY_POLICY]


def test_hardening_credit_requires_a_contextual_marker():
    bare = EcbSpeechExtractor().extract(
        speeches_publication(),
        _doc_with_sections([_section("Financial stability", "Credit is important.")]),
    )
    assert bare.facts == []
    contextual = EcbSpeechExtractor().extract(
        speeches_publication(),
        _doc_with_sections([_section("Financial stability", "Credit growth increased by 3 percent.")]),
    )
    assert len(contextual.facts) == 1
    assert contextual.facts[0].subject == SUBJECT_FINANCIAL_CONDITIONS
    assert contextual.facts[0].predicate == "value"
    assert contextual.facts[0].value.value == 3.0


def test_hardening_known_and_unknown_sections_are_both_precise():
    known = EcbSpeechExtractor().extract(
        speeches_publication(),
        _doc_with_sections([_section("Economic outlook", "Investment is essential.")]),
    )
    assert known.facts == []
    known_assertion = EcbSpeechExtractor().extract(
        speeches_publication(),
        _doc_with_sections([_section("Economic outlook", "Investment declined.")]),
    )
    assert len(known_assertion.facts) == 1
    strict = EcbSpeechExtractor().extract(
        speeches_publication(),
        _doc_with_sections([_section("Some future section", "Investment declined.")]),
    )
    assert strict.facts == []


def test_hardening_no_period_contamination_across_sentences():
    sections = [
        _section("Inflation", "Inflation is 2.5 percent. This year has been challenging."),
    ]
    result = EcbSpeechExtractor().extract(speeches_publication(), _doc_with_sections(sections))
    assert result.facts == []


# ---------------------------------------------------------------------------
# Phase 4.7 assertion-signal hardening (correctif) — an economic anchor plus a
# generic assertion verb is never sufficient: the predicate must actually
# describe the economic subject. Rhetorical / institutional / personal
# constructions → 0 qualitative Facts. State/property assertions stay extracted.
# ---------------------------------------------------------------------------

def test_hardening2_anchor_plus_generic_verb_is_not_a_fact():
    for sentence in (
        "Our understanding of the economy improved.",
        "The economy has improved our understanding of the issue.",
        "The economy continues to be an important part of our mandate.",
        "Inflation remains an important challenge.",
        "Inflation remains a key challenge for monetary policy.",
        "Investment remains at the heart of our strategy.",
        "Credit continues to matter for households.",
        "Production remains an important objective.",
        "Demand continues to be a key priority.",
        "The recovery remains central to our political discussion.",
        "The economy is important for our society.",
        "We recovered from the disruption.",
    ):
        result = EcbSpeechExtractor().extract(
            speeches_publication(),
            _doc_with_sections([_section("Economic outlook", sentence)]),
        )
        assert result.facts == [], sentence


def test_hardening2_remain_and_continue_state_vs_rhetoric():
    for sentence in (
        "Inflation remains important.",
        "Investment remains a priority.",
        "Credit remains important.",
        "The economy remains central to our mandate.",
        "Economic activity continues to be important.",
    ):
        result = EcbSpeechExtractor().extract(
            speeches_publication(),
            _doc_with_sections([_section("Economic outlook", sentence)]),
        )
        assert result.facts == [], sentence
    expected_subject = {
        "Inflation remains elevated.": SUBJECT_INFLATION,
        "Investment remains weak.": SUBJECT_GROWTH,
        "Credit conditions remain tight.": SUBJECT_FINANCIAL_CONDITIONS,
        "Economic activity remains subdued.": SUBJECT_GROWTH,
        "Economic activity continues to weaken.": SUBJECT_GROWTH,
        "Growth remains robust.": SUBJECT_GROWTH,
        "Inflation expectations remain anchored.": SUBJECT_INFLATION_EXPECTATIONS,
    }
    for sentence, subject in expected_subject.items():
        result = EcbSpeechExtractor().extract(
            speeches_publication(),
            _doc_with_sections([_section("Economic outlook", sentence)]),
        )
        assert [f.subject for f in result.facts] == [subject], sentence
        assert result.facts[0].predicate == "assessment", sentence


def test_hardening2_improve_recover_expand_require_the_economic_subject():
    for sentence in (
        "Our understanding of the economy improved.",
        "The economy has improved our understanding of the issue.",
        "We recovered our previous position.",
        "We expanded our mandate.",
        "We recovered from the disruption.",
    ):
        result = EcbSpeechExtractor().extract(
            speeches_publication(),
            _doc_with_sections([_section("Economic outlook", sentence)]),
        )
        assert result.facts == [], sentence
    for sentence in (
        "Economic activity improved.",
        "The economy recovered.",
        "Output expanded.",
        "The economy has improved.",
    ):
        result = EcbSpeechExtractor().extract(
            speeches_publication(),
            _doc_with_sections([_section("Economic outlook", sentence)]),
        )
        assert [f.subject for f in result.facts] == [SUBJECT_GROWTH], sentence
        assert result.facts[0].predicate == "assessment", sentence


def test_hardening2_ease_tighten_loosen_institutional_ambiguity():
    for sentence in (
        "We need to ease the burden.",
        "We tightened our procedures.",
        "We need to loosen restrictions.",
    ):
        result = EcbSpeechExtractor().extract(
            speeches_publication(),
            _doc_with_sections([_section("Economic outlook", sentence)]),
        )
        assert result.facts == [], sentence
    for sentence in (
        "Financial conditions eased.",
        "Financial conditions tightened.",
        "Credit conditions tightened.",
        "Lending standards loosened.",
        "Lending standards tightened.",
    ):
        result = EcbSpeechExtractor().extract(
            speeches_publication(),
            _doc_with_sections([_section("Financial stability", sentence)]),
        )
        assert [f.subject for f in result.facts] == [SUBJECT_FINANCIAL_CONDITIONS], sentence
        assert result.facts[0].predicate == "assessment", sentence


def test_hardening2_narrow_widen_economic_vs_institutional():
    for sentence in (
        "We widened our mandate.",
        "We narrowed the scope of the discussion.",
        "We widened the scope of the programme.",
    ):
        result = EcbSpeechExtractor().extract(
            speeches_publication(),
            _doc_with_sections([_section("Economic outlook", sentence)]),
        )
        assert result.facts == [], sentence
    for sentence in (
        "The output gap narrowed.",
        "The output gap widened.",
        "Credit spreads widened.",
        "Credit spreads narrowed.",
    ):
        result = EcbSpeechExtractor().extract(
            speeches_publication(),
            _doc_with_sections([_section("Economic outlook", sentence)]),
        )
        assert result.facts, sentence
        assert result.facts[0].predicate == "assessment", sentence


def test_hardening2_gain_lose_are_not_independent_triggers():
    for sentence in (
        "The argument gained support.",
        "The proposal lost momentum.",
    ):
        result = EcbSpeechExtractor().extract(
            speeches_publication(),
            _doc_with_sections([_section("Economic outlook", sentence)]),
        )
        assert result.facts == [], sentence
    for sentence in (
        "Employment gained momentum.",
        "The economy lost momentum.",
    ):
        result = EcbSpeechExtractor().extract(
            speeches_publication(),
            _doc_with_sections([_section("Economic outlook", sentence)]),
        )
        assert len(result.facts) == 1, sentence
        assert result.facts[0].predicate == "assessment", sentence


def test_hardening2_pick_up_requires_economic_activity():
    for sentence in (
        "The discussion picked up momentum.",
        "The initiative picked up support.",
    ):
        result = EcbSpeechExtractor().extract(
            speeches_publication(),
            _doc_with_sections([_section("Economic outlook", sentence)]),
        )
        assert result.facts == [], sentence
    for sentence in (
        "Economic activity picked up.",
        "Growth picked up.",
        "Domestic demand picked up.",
    ):
        result = EcbSpeechExtractor().extract(
            speeches_publication(),
            _doc_with_sections([_section("Economic outlook", sentence)]),
        )
        assert [f.subject for f in result.facts] == [SUBJECT_GROWTH], sentence
        assert result.facts[0].predicate == "assessment", sentence


def test_hardening2_forecast_must_target_the_economic_subject():
    for sentence in (
        "The policy is expected to improve communication.",
        "The institution is expected to expand its role.",
        "We are expected to strengthen our outreach.",
    ):
        result = EcbSpeechExtractor().extract(
            speeches_publication(),
            _doc_with_sections([_section("Economic outlook", sentence)]),
        )
        assert result.facts == [], sentence
    for sentence in (
        "Inflation is expected to decline.",
        "Growth is projected to remain weak.",
        "Inflation is expected to remain elevated.",
    ):
        result = EcbSpeechExtractor().extract(
            speeches_publication(),
            _doc_with_sections([_section("Economic outlook", sentence)]),
        )
        assert len(result.facts) == 1, sentence
        assert result.facts[0].predicate == "assessment", sentence


def test_hardening2_numeric_facts_are_not_gated():
    result = EcbSpeechExtractor().extract(
        speeches_publication(),
        _doc_with_sections(
            [
                _section("Inflation", "Inflation increased to 2.1 percent in 2027."),
                _section("Economic outlook", "GDP growth is expected to be 2.4 percent in 2027."),
            ]
        ),
    )
    assert [f.subject for f in result.facts] == [SUBJECT_INFLATION, SUBJECT_GDP]
    assert facts_by(result, SUBJECT_INFLATION, "value")[0].value.value == 2.1
    assert facts_by(result, SUBJECT_GDP, "value")[0].value.value == 2.4


def test_hardening2_unknown_sections_keep_no_qualitative_facts():
    for sentence in (
        "Inflation remains elevated.",
        "Economic activity continues to weaken.",
        "Growth picked up.",
    ):
        result = EcbSpeechExtractor().extract(
            speeches_publication(),
            _doc_with_sections([_section("Some future section", sentence)]),
        )
        assert result.facts == [], sentence


def test_hardening2_quoted_narrative_stays_ignored():
    result = EcbSpeechExtractor().extract(
        speeches_publication(),
        _doc_with_sections(
            [(_section("Economic outlook", "As John Maynard Keynes once said, inflation will rise."))]
        ),
    )
    assert result.facts == []
    assert "quoted_content_skipped" in result.warnings


def test_hardening2_risk_policy_guidance_not_gated():
    result = EcbSpeechExtractor().extract(
        speeches_publication(),
        _doc_with_sections(
            [
                _section("Risk assessment", "Upside risks to inflation remained."),
                _section("Monetary policy", "Monetary policy remains restrictive."),
                _section("Monetary policy", "Future policy decisions will depend on incoming data."),
            ]
        ),
    )
    subjects = [f.subject for f in result.facts]
    assert SUBJECT_INFLATION_RISK in subjects
    assert SUBJECT_MONETARY_POLICY in subjects
    assert SUBJECT_POLICY_GUIDANCE in subjects


def test_hardening2_provenance_is_preserved_on_new_facts():
    result = EcbSpeechExtractor().extract(
        speeches_publication(),
        _doc_with_sections([_section("Economic outlook", "Economic activity remains subdued.")]),
    )
    assert len(result.facts) == 1
    fact = result.facts[0]
    assert fact.source_text == "Economic activity remains subdued."
    assert fact.source_location.section == 0
    assert fact.extraction_version == "11.0.0"
    assert fact.speaker is None
    assert fact.identity_qualifier == "speech:growth:0"
    assert fact.confidence is Confidence.MEDIUM
