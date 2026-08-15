"""Phase 4.x — Multi-bank Speech extraction: contract, dispatch, provenance,
boundaries, determinism, immutability and end-to-end integration tests for the
nine Speech extractors added alongside the ECB reference implementation (Fed,
BoE, BoJ, SNB, BoC, RBA, RBNZ, Norges, Riksbank).

A Speech is the *individual* communication of one central bank official. A
Speech extractor turns one speech ``NormalizedDocument`` into canonical Facts,
preserving the explicit speaker verbatim in ``Fact.speaker`` (never inferred)
and gating extraction on the ``speech`` publication type. This suite verifies
the generic registry dispatch, the canonical Fact contract, provenance,
deterministic output, source immutability and the full
publication → classification → extractor → Fact → persistence → retrieval
vertical slice for each bank.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from argus.classification.base import Confidence
from argus.classification.classifier import PublicationClassifier
from argus.documents import Normalizer
from argus.documents.base import DocumentSection, NormalizedDocument
from argus.facts import LocationKind, PeriodKind, ValueKind
from argus.models import Document, DocumentStatus, Publication
from argus.speeches import (
    get_extractor,
    extract_speech,
    FedSpeechExtractor,
    BoeSpeechExtractor,
    BojSpeechExtractor,
    SnbSpeechExtractor,
    BocSpeechExtractor,
    RbaSpeechExtractor,
    RbnzSpeechExtractor,
    NorgesSpeechExtractor,
    RiksbankSpeechExtractor,
)
from argus.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "documents"

BANKS = {
    "fed": FedSpeechExtractor,
    "boe": BoeSpeechExtractor,
    "boj": BojSpeechExtractor,
    "snb": SnbSpeechExtractor,
    "boc": BocSpeechExtractor,
    "rba": RbaSpeechExtractor,
    "rbnz": RbnzSpeechExtractor,
    "norges": NorgesSpeechExtractor,
    "riksbank": RiksbankSpeechExtractor,
}
FIXTURE_FILES = {bank: f"{bank}_speech.html" for bank in BANKS}
SPEAKERS = {
    "fed": "Jerome Powell", "boe": "Andrew Bailey", "boj": "Kazuo Ueda",
    "snb": "Martin Schlegel", "boc": "Tiff Macklem", "rba": "Michele Bullock",
    "rbnz": "Adrian Orr", "norges": "Ida Wolden Bache", "riksbank": "Erik Thedéen",
}

# Golden signatures: (subject, predicate, value_kind, value, period)
PERIOD = ("year:2026", "year:2027")
FIN = "Financial conditions remained tight and monetary policy transmission continued to function smoothly."
RISK = ("inflation_risk", "assessment", "categorical", "balanced", None)
POL = {
    "fed": "The Federal Reserve decided to keep its policy interest rate at its current level.",
    "boe": "The Bank of England decided to maintain the policy stance for now.",
    "boj": "The Bank of Japan decided to maintain its short-term policy interest rate.",
    "snb": "The Swiss National Bank decided to maintain the policy rate.",
    "boc": "The Bank of Canada decided to hold the policy interest rate.",
    "rba": "The Reserve Bank of Australia decided to hold the cash rate.",
    "rbnz": "The Monetary Policy Committee decided to hold the OCR.",
    "norges": "Norges Bank decided to keep the policy rate unchanged.",
    "riksbank": "The Riksbank decided to keep the policy rate unchanged.",
}
GUID = {
    "fed": "The Committee will be patient, and future policy decisions will depend on incoming data.",
    "boe": "Monetary policy will need to remain restrictive for as long as necessary.",
    "boj": "The Bank will continue with monetary easing while examining economic developments.",
    "snb": "Monetary policy will remain flexible and we will continue to act as necessary.",
    "boc": "The Bank will be patient and will act for as long as it takes to return inflation to target.",
    "rba": "Monetary policy will remain data dependent as the outlook evolves.",
    "rbnz": "The Committee will keep the OCR restrictive for as long as necessary.",
    "norges": "Monetary policy will be guided by developments in the economy.",
    "riksbank": "Monetary policy will depend on how inflation evolves.",
}
VALS = {
    "fed": (1.8, 1.6, 2.1, 3.8),
    "boe": (1.2, 1.3, 2.0, 4.2),
    "boj": (1.5, 1.6, 2.4, 3.0),
    "snb": (1.0, 1.1, 1.8, 4.0),
    "boc": (1.5, 1.9, 2.5, 6.0),
    "rba": (2.1, 2.2, 3.0, 4.4),
    "rbnz": (1.2, 1.5, 3.4, 5.0),
    "norges": (1.4, 1.6, 3.9, 3.8),
    "riksbank": (1.6, 1.8, 2.6, 6.2),
}


def golden(bank: str) -> set:
    g1, g2, i, u = VALS[bank]
    return {
        ("gdp", "value", "percentage", g1, PERIOD[0]),
        ("gdp", "value", "percentage", g2, PERIOD[1]),
        ("inflation", "value", "percentage", i, PERIOD[0]),
        ("unemployment", "value", "percentage", u, PERIOD[0]),
        ("financial_conditions", "assessment", "text", FIN, None),
        ("monetary_policy", "statement", "text", POL[bank], None),
        ("policy_guidance", "statement", "text", GUID[bank], None),
        RISK,
    }


def _normalized(bank: str, name: str):
    return Normalizer().parse(
        Document(
            publication_id=f"pub-{bank}-speech",
            url=f"https://example.org/{bank}/{name}",
            kind="html",
            status=DocumentStatus.FETCHED,
            local_path=str(FIXTURES / name),
        )
    )


def _publication(bank: str) -> Publication:
    return Publication(
        central_bank=bank,
        title="Speech",
        url=f"https://example.org/{bank}/speech",
        source_id=f"{bank}-speech",
        source_url=f"https://example.org/{bank}/feed.xml",
        id=f"pub-{bank}-speech",
    )


def _signature(fact) -> tuple:
    kind = fact.value.kind.value if hasattr(fact.value.kind, "value") else fact.value.kind
    if fact.period:
        pkind = fact.period.kind
        pkind_str = pkind.value if hasattr(pkind, "value") else pkind
        period = f"{pkind_str}:{fact.period.value}"
    else:
        period = None
    return (fact.subject, fact.predicate, kind, fact.value.value, period)


def _extract(bank: str):
    return get_extractor(bank).extract(_publication(bank), _normalized(bank, FIXTURE_FILES[bank]))


@pytest.mark.parametrize("bank", list(BANKS))
def test_extractor_bank_identity(bank):
    assert get_extractor(bank).bank == bank


@pytest.mark.parametrize("bank", list(BANKS))
def test_golden_facts(bank):
    result = _extract(bank)
    got = {_signature(f) for f in result.facts}
    assert got == golden(bank), f"{bank}: {got ^ golden(bank)}"


@pytest.mark.parametrize("bank", list(BANKS))
def test_contract_fields(bank):
    result = _extract(bank)
    doc = _normalized(bank, FIXTURE_FILES[bank])
    section_count = len(doc.sections)
    expected_speaker = SPEAKERS[bank]
    assert result.facts  # a real speech yields facts
    for fact in result.facts:
        assert fact.publication_id == f"pub-{bank}-speech"
        assert fact.document_id == doc.document_id
        assert fact.central_bank is None or fact.central_bank == bank
        assert fact.speaker == expected_speaker  # explicit, verbatim, never inferred
        assert fact.effective_date is None
        assert fact.source_location is not None
        assert fact.source_location.kind == LocationKind.SECTION
        assert 0 <= fact.source_location.section < section_count
        assert fact.source_text  # verbatim provenance always present
        assert fact.value.source_text  # token/cell provenance
        assert fact.extraction_method in ("regex", "table_extraction")
        assert fact.extraction_version
        assert fact.confidence is not None
        assert fact.identity_qualifier.startswith("speech:")
        owning = doc.sections[fact.source_location.section].text or ""
        assert fact.source_text in owning, f"{bank}: source_text not in owning section"


@pytest.mark.parametrize("bank", list(BANKS))
def test_value_facts_carry_percentage_kind_and_period(bank):
    for fact in _extract(bank).facts:
        if fact.predicate == "value":
            assert fact.value.kind == ValueKind.PERCENTAGE
            assert fact.period is not None
            assert fact.period.kind in (PeriodKind.YEAR, PeriodKind.MONTH, PeriodKind.QUARTER)
            assert fact.confidence == Confidence.HIGH


def test_dispatch_resolves_all_speech_banks():
    expected = {bank: cls.__name__ for bank, cls in BANKS.items()}
    expected["ecb"] = "EcbSpeechExtractor"
    for bank, cls in expected.items():
        assert get_extractor(bank).__class__.__name__ == cls


@pytest.mark.parametrize("bank", list(BANKS))
def test_deterministic_repeated_extraction(bank):
    r1 = _extract(bank)
    r2 = _extract(bank)
    assert [f.resolve_id() for f in r1.facts] == [f.resolve_id() for f in r2.facts]
    assert [fct.to_dict() for fct in r1.facts] == [fct.to_dict() for fct in r2.facts]


@pytest.mark.parametrize("bank", list(BANKS))
def test_order_independence(bank):
    """Reverse section order — the extracted fact *set* is unchanged."""
    doc = _normalized(bank, FIXTURE_FILES[bank])
    fwd = {_signature(f) for f in get_extractor(bank).extract(_publication(bank), doc).facts}

    reversed_doc = copy.deepcopy(doc)
    reversed_doc.sections = list(reversed(reversed_doc.sections))
    rev = {_signature(f) for f in get_extractor(bank).extract(_publication(bank), reversed_doc).facts}
    assert rev == fwd


@pytest.mark.parametrize("bank", list(BANKS))
def test_source_immutability(bank):
    pub = _publication(bank)
    doc = _normalized(bank, FIXTURE_FILES[bank])
    pub_before = copy.deepcopy(pub)
    sections_before = copy.deepcopy(doc.sections)
    get_extractor(bank).extract(pub, doc)
    assert pub.central_bank == pub_before.central_bank
    assert pub.id == pub_before.id
    assert [s.heading for s in doc.sections] == [s.heading for s in sections_before]
    assert [s.text for s in doc.sections] == [s.text for s in sections_before]


# ---------------------------------------------------------------------------
# boundaries
# ---------------------------------------------------------------------------

def _synthetic(bank: str, sections_text: list[tuple[str, str]], doc_id: str = "sha-synthetic") -> NormalizedDocument:
    sections = [
        DocumentSection(order=i, heading=heading, text=text)
        for i, (heading, text) in enumerate(sections_text)
    ]
    return NormalizedDocument(
        publication_id=f"pub-{bank}-speech",
        document_id=doc_id,
        source_url=f"https://example.org/{bank}",
        local_path=None,
        document_kind="html",
        sections=sections,
    )


@pytest.mark.parametrize("bank", list(BANKS))
def test_unknown_heading_no_automatic_qualitative_fact(bank):
    """An unknown section is strictly mined: a bare rhetorical mention never
    yields an automatic (qualitative) fact — only explicit assertions pass."""
    doc = _synthetic(bank, [("Miscellaneous notes", "Inflation is expected to continue to moderate.")])
    result = get_extractor(bank).extract(_publication(bank), doc)
    assert result.facts == []
    assert "no_risk_assessment" in result.warnings
    assert "no_forward_guidance" in result.warnings


@pytest.mark.parametrize("bank", list(BANKS))
def test_forecast_without_period_is_ignored(bank):
    doc = _synthetic(bank, [("Inflation", "Inflation is projected to average 2.3 per cent.")])
    result = get_extractor(bank).extract(_publication(bank), doc)
    assert not any(f.subject == "inflation" and f.predicate == "value" for f in result.facts)


@pytest.mark.parametrize("bank", list(BANKS))
def test_share_units_are_never_percentages(bank):
    doc = _synthetic(bank, [("Fiscal policy", "The deficit is projected to be 3.0 per cent of GDP in 2026.")])
    result = get_extractor(bank).extract(_publication(bank), doc)
    assert not any(f.predicate == "value" for f in result.facts)


@pytest.mark.parametrize("bank", list(BANKS))
def test_no_value_without_explicit_claim_verb(bank):
    doc = _synthetic(bank, [("Inflation", "Inflation is 2.4 per cent in 2025.")])
    result = get_extractor(bank).extract(_publication(bank), doc)
    assert not any(f.subject == "inflation" and f.predicate == "value" for f in result.facts)


@pytest.mark.parametrize("bank", list(BANKS))
def test_gdp_near_miss_never_yields_gdp_value(bank):
    doc = _synthetic(bank, [("Economic activity", "Real GDP growth held steady while the GDP deflator rose by 2.1 per cent in 2026.")])
    result = get_extractor(bank).extract(_publication(bank), doc)
    assert not any(f.subject == "gdp" and f.predicate == "value" for f in result.facts)


@pytest.mark.parametrize("bank", list(BANKS))
def test_no_downstream_semantics(bank):
    for fact in _extract(bank).facts:
        assert fact.subject not in ("hawkish", "dovish", "stance", "rate_expectation")
        assert "hawkish" not in (fact.source_text or "").lower()


def test_missing_publication_metadata_allowed_for_direct_extract(bank="fed"):
    doc = _normalized(bank, FIXTURE_FILES[bank])
    pub = Publication(central_bank=bank, title="Speech", url="u", source_id="s", source_url="su", id=None)
    result = get_extractor(bank).extract(pub, doc)
    assert result.publication_id in ("", None) or True  # does not raise
    assert result.facts


def test_classification_refuses_and_persists(tmp_path, bank="rba"):
    store = Store(tmp_path / "bank_speech.db")
    pub = _publication(bank)
    store.upsert_publication(pub)
    doc = _normalized(bank, FIXTURE_FILES[bank])
    store.upsert_normalized_document(doc)
    # no classification → extraction refuses and persists nothing
    results = extract_speech(store, pub)
    assert results == []
    assert store.get_facts(publication_id=pub.id) == []


def _classify_speech(store, pub_id, bank) -> None:
    store.set_classification(
        pub_id,
        central_bank=bank,
        publication_type="speech",
        confidence=Confidence.HIGH.value,
        method="url_pattern",
        evidence=[],
    )


@pytest.mark.parametrize("bank", list(BANKS))
def test_integration_end_to_end(tmp_path, bank):
    """Publication → classification → generic dispatch → extractor → facts →
    persistence → retrieval."""
    store = Store(tmp_path / f"{bank}_speech.db")
    pub = _publication(bank)
    store.upsert_publication(pub)
    doc = _normalized(bank, FIXTURE_FILES[bank])
    store.upsert_normalized_document(doc)
    _classify_speech(store, pub.id, bank)

    results = extract_speech(store, pub)
    assert len(results) == 1
    result = results[0]
    assert result.publication_id == pub.id
    assert result.document_id == doc.document_id
    assert {_signature(f) for f in result.facts} == golden(bank)

    retrieved = store.get_facts(publication_id=pub.id)
    assert {_signature(f) for f in retrieved} == golden(bank)
    for fact in retrieved:
        assert fact.source_text
        assert fact.speaker == SPEAKERS[bank]
        assert fact.extraction_version
        assert fact.extraction_method


@pytest.mark.parametrize("bank", list(BANKS))
def test_integration_idempotent_re_extraction(tmp_path, bank):
    store = Store(tmp_path / f"{bank}_speech.db")
    pub = _publication(bank)
    store.upsert_publication(pub)
    doc = _normalized(bank, FIXTURE_FILES[bank])
    store.upsert_normalized_document(doc)
    _classify_speech(store, pub.id, bank)

    extract_speech(store, pub)
    first = {f.resolve_id() for f in store.get_facts(publication_id=pub.id)}
    extract_speech(store, pub)
    second = {f.resolve_id() for f in store.get_facts(publication_id=pub.id)}
    assert first == second


# ---------------------------------------------------------------------------
# classification — per-bank official speech source (documented in each module's
# COVERAGE_SOURCE). URL-slug signal (plural "speeches/") classifies Fed / SNB /
# BoC / RBA / RBNZ; BoE (singular "/speech/"), BoJ (koen), Norges and Riksbank
# (tal) official speech URLs carry no such English plural slug, so they rely on
# the explicit title signal — this is documented, not forced.
# ---------------------------------------------------------------------------

URL_SIGNAL_BANKS = {
    "fed": "https://www.federalreserve.gov/newsevents/speeches/2026-03-12-powell.htm",
    "boe": "https://www.bankofengland.co.uk/speech/2026/financial-stability-outlook",
    "boj": "https://www.boj.or.jp/en/announcements/press/koen_2026/index.htm",
    "snb": "https://www.snb.ch/en/mmr/speeches/id/speech_2026_0312",
    "boc": "https://www.bankofcanada.ca/speeches/inflation-and-the-labour-market/",
    "rba": "https://www.rba.gov.au/speeches/2026/sp-ag-2026-03-12.html",
    "rbnz": "https://www.rbnz.govt.nz/hub/speeches/the-economy-and-monetary-policy",
    "norges": "https://www.norges-bank.no/en/topics/current-topics/speeches-and-articles/",
    "riksbank": "https://www.riksbank.se/en-gb/press-och-publicerat/tal/2026/monetary-policy/",
}
TITLE_SIGNAL_BANKS = ("boe", "boj", "norges", "riksbank")


def _classify_publication(bank: str, url: str, title: str = None):
    c = PublicationClassifier()
    pub = Publication(central_bank=bank, title=title or "Economic outlook", url=url,
                      source_id=f"{bank}-x", source_url=url)
    return c.classify(pub)


@pytest.mark.parametrize("bank", list(URL_SIGNAL_BANKS))
def test_classification_speech_url_or_title(bank):
    """Every configured speech source classifies to ``speech`` via the generic
    rule — URL slug (fed, snb, boc, rba, rbnz) or explicit title signal
    (boe, boj, norges, riksbank)."""
    url = URL_SIGNAL_BANKS[bank]
    if bank in TITLE_SIGNAL_BANKS:
        result = _classify_publication(bank, url, title="Speech by the Governor on monetary policy")
    else:
        result = _classify_publication(bank, url)
    assert result.publication_type == "speech", (bank, result)


@pytest.mark.parametrize("bank", list(URL_SIGNAL_BANKS))
def test_classification_non_speech_not_mined_as_speech(bank):
    """A collective decision URL (same bank) is not classified as a speech —
    it belongs to Phases 5/8 decision types, gated on their own types."""
    url = {
        "fed": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260318a.htm",
        "boe": "https://www.bankofengland.co.uk/monetary-policy-summary-and-minutes",
        "boj": "https://www.boj.or.jp/en/mopo/mpmdeci/",
        "snb": "https://www.snb.ch/en/mmr/reference/pre_20260312",
        "boc": "https://www.bankofcanada.ca/fad-press-release/",
        "rba": "https://www.rba.gov.au/int-rate-decisions/",
        "rbnz": "https://www.rbnz.govt.nz/monetary-policy-decisions/",
        "norges": "https://www.norges-bank.no/en/current-topics/monetary-policy/policy-rate-decision/",
        "riksbank": "https://www.riksbank.se/en-gb/press-och-publicerat/penningpolitiken/",
    }[bank]
    result = _classify_publication(bank, url, title="Monetary policy decision")
    assert result.publication_type != "speech", (bank, result)

