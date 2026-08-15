"""SNB discussion-summary classification — ``minutes`` boundary tests.

The SNB has published "Summary of the monetary policy assessment discussion"
documents (HTML pages under ``/publications/communication/summaries/zus_…``)
since September 2025. They are minutes-like discussion summaries and must
classify as ``minutes`` — never as ``monetary_policy_decision``, whose broad
title rule ("monetary policy assessment") previously captured them.

The same title also covers the older RSS-discovered shape
(``pre_<date>_1`` press-release URLs) and the PDF asset URLs.
"""

from __future__ import annotations

from argus.classification import (
    Confidence,
    METHOD_SOURCE_TYPE_HINT,
    METHOD_TITLE_PATTERN,
    METHOD_URL_PATTERN,
    PublicationClassifier,
)
from argus.decisions import extract_decision
from argus.documents import Normalizer
from argus.minutes import extract_minutes
from argus.models import Document, DocumentStatus, Publication
from argus.registry import SourceRegistry
from argus.store import Store

MINUTES = "minutes"
DECISION = "monetary_policy_decision"

SUMMARIES = (
    # (label, url, title, source_id)
    (
        "new /summaries/ URL (June 2026)",
        "https://www.snb.ch/en/publications/communication/summaries/zus_20260716",
        "Monetary policy assessment of June 2026: Summary of discussion",
        "snb_mopo_rss",
    ),
    (
        "canonical /summaries/ URL (December 2025)",
        "https://www.snb.ch/en/publications/communication/summaries/zus_20260108",
        "Monetary policy assessment of December 2025: Summary of discussion",
        "snb_mopo_rss",
    ),
    (
        "old RSS press-release URL (September 2025)",
        "https://www.snb.ch/en/publications/communication/press-releases/2025/pre_20251023_1",
        "Monetary policy assessment of September 2025: Summary of discussion",
        "snb_mopo_rss",
    ),
    (
        "PDF asset URL (March 2026)",
        "https://www.snb.ch/public/asset/en/www-snb-ch/publications/communication/summaries/zus_20260416/publications0_en/zus_20260416.en.pdf",
        "Monetary policy assessment of March 2026: Summary of discussion",
        "",
    ),
    (
        "bare 'Summary of the monetary policy assessment discussion' title",
        "https://www.snb.ch/en/publications/communication/summaries/zus_20251023",
        "Summary of the monetary policy assessment discussion",
        "",
    ),
)

DECISIONS = (
    # (label, url, title, source_id)
    (
        "June 2026 decision",
        "https://www.snb.ch/en/publications/communication/press-releases-restricted/pre_20260618",
        "Monetary policy assessment of 18 June 2026",
        "snb_mopo_rss",
    ),
    (
        "June 2025 decision (pre_…_2 URL)",
        "https://www.snb.ch/en/publications/communication/press-releases-restricted/pre_20250619_2",
        "Monetary policy assessment of 19 June 2025",
        "snb_mopo_rss",
    ),
    (
        "reference PDF URL",
        "https://www.snb.ch/en/mmr/reference/pre_20260619/source",
        "Monetary policy assessment of 19 June 2026",
        "",
    ),
    (
        "decision without day in title (URL is authoritative)",
        "https://www.snb.ch/en/mmr/reference/pre_20260619/source",
        "Monetary policy assessment",
        "",
    ),
)

UNRELATED = (
    # (label, url, title, source_id) — must NOT classify as minutes
    (
        "speech",
        "https://www.snb.ch/en/the-snb/events/speeches/path-to-the-monetary-policy-decision",
        "The path to the monetary policy decision",
        "",
    ),
    (
        "press release",
        "https://www.snb.ch/en/publications/communication/press-releases-restricted/pre_20260812",
        "Swiss National Bank welcomes measures to strengthen 'too big to fail' regulations",
        "snb_pressrel_rss",
    ),
    (
        "quarterly bulletin",
        "https://www.snb.ch/en/publications/quarterly-bulletin/2026/quartbul_2026_2_komplett",
        "Quarterly Bulletin 2/2026",
        "snb_mopo_rss",
    ),
)


def _pub(title, url, source_id="", source_url="", **kw) -> Publication:
    return Publication(
        central_bank="snb",
        title=title,
        url=url,
        source_id=source_id,
        source_url=source_url,
        **kw,
    )


def _classify(pub, registry=None):
    return PublicationClassifier(registry=registry or SourceRegistry()).classify(pub)


# ---------------------------------------------------------------------------
# Discussion summary -> minutes
# ---------------------------------------------------------------------------


def test_discussion_summary_classifies_minutes():
    for label, url, title, source_id in SUMMARIES:
        result = _classify(_pub(title, url, source_id))
        assert result.publication_type == MINUTES, f"{label}: {result.publication_type}"
        assert result.publication_type != DECISION


def test_discussion_summary_never_classifies_as_decision():
    for label, url, title, source_id in SUMMARIES:
        result = _classify(_pub(title, url, source_id))
        assert DECISION not in result.evidence or result.publication_type == MINUTES, label
        assert result.publication_type == MINUTES, label


def test_discussion_summary_old_rss_url_wins_on_title_contradiction():
    # The Sept 2025 RSS entry uses a `pre_<date>_1` URL that the decision URL
    # rule matches; the explicit ": Summary of discussion" title is the
    # stronger, later signal and must override it.
    result = _classify(
        _pub(
            "Monetary policy assessment of September 2025: Summary of discussion",
            "https://www.snb.ch/en/publications/communication/press-releases/2025/pre_20251023_1",
            "snb_mopo_rss",
        )
    )
    assert result.publication_type == MINUTES
    assert result.method in (METHOD_TITLE_PATTERN, METHOD_URL_PATTERN)


def test_discussion_summary_new_url_uses_url_pattern():
    result = _classify(
        _pub(
            "Monetary policy assessment of June 2026: Summary of discussion",
            "https://www.snb.ch/en/publications/communication/summaries/zus_20260716",
            "snb_mopo_rss",
        )
    )
    assert result.publication_type == MINUTES
    assert result.method == METHOD_URL_PATTERN


def test_discussion_summary_classification_is_deterministic():
    pub = _pub(
        "Monetary policy assessment of June 2026: Summary of discussion",
        "https://www.snb.ch/en/publications/communication/summaries/zus_20260716",
        "snb_mopo_rss",
    )
    first = _classify(pub).to_dict()
    first.pop("classified_at", None)
    for _ in range(5):
        second = _classify(pub).to_dict()
        second.pop("classified_at", None)
        assert second == first


# ---------------------------------------------------------------------------
# Monetary policy decision boundary
# ---------------------------------------------------------------------------


def test_snb_decision_remains_decision():
    for label, url, title, source_id in DECISIONS:
        result = _classify(_pub(title, url, source_id))
        assert result.publication_type == DECISION, f"{label}: {result.publication_type}"
        assert result.publication_type != MINUTES


def test_snb_decision_url_pattern_still_authoritative():
    result = _classify(
        _pub(
            "Monetary policy assessment of 18 June 2026",
            "https://www.snb.ch/en/publications/communication/press-releases-restricted/pre_20260618",
            "snb_mopo_rss",
        )
    )
    assert result.publication_type == DECISION
    assert result.method == METHOD_URL_PATTERN
    assert result.confidence == Confidence.HIGH


# ---------------------------------------------------------------------------
# False-positive boundary — unrelated SNB publications stay unaffected
# ---------------------------------------------------------------------------


def test_unrelated_snb_publications_never_minutes():
    for label, url, title, source_id in UNRELATED:
        result = _classify(_pub(title, url, source_id))
        assert result.publication_type != MINUTES, f"{label}: {result.publication_type}"


def test_speech_with_policy_title_is_speech_not_minutes():
    result = _classify(
        _pub(
            "Economic outlook",
            "https://www.snb.ch/en/mmr/speeches/id/speech_2026_0312",
            "snb-speeches",
        )
    )
    assert result.publication_type == "speech"


def test_snb_decision_title_without_day_stays_decision_via_url():
    # The narrowed decision title rule requires a day ("of 18 June …"). A bare
    # "Monetary policy assessment" title still classifies as decision through
    # the `pre_<date>` URL rule.
    result = _classify(
        _pub(
            "Monetary policy assessment",
            "https://www.snb.ch/en/mmr/reference/pre_20260619/source",
            "",
        )
    )
    assert result.publication_type == DECISION
    assert result.method == METHOD_URL_PATTERN


# ---------------------------------------------------------------------------
# Source type hint precedence (snb_summaries adapter source)
# ---------------------------------------------------------------------------


def test_summaries_source_type_hint_classifies_minutes_high():
    registry = SourceRegistry()
    source = registry.source("snb_summaries")
    assert source is not None
    assert source.publication_types == (MINUTES,)
    result = _classify(
        _pub(
            "Summary",
            "https://www.snb.ch/en/publications/communication/summaries/zus_20260716",
            "snb_summaries",
        ),
        registry=registry,
    )
    assert result.publication_type == MINUTES
    assert result.method == METHOD_SOURCE_TYPE_HINT
    assert result.confidence == Confidence.HIGH


# ---------------------------------------------------------------------------
# Downstream extraction consequence
# ---------------------------------------------------------------------------


def _summary_store(tmp_path, *, publication_type=MINUTES) -> Store:
    store = Store(tmp_path / "argus.db")
    pub = store.upsert_publication(
        _pub(
            "Monetary policy assessment of March 2026: Summary of discussion",
            "https://www.snb.ch/en/publications/communication/summaries/zus_20260416",
            "snb_summaries",
        )
    )
    store.set_classification(
        pub.id,
        central_bank="snb",
        publication_type=publication_type,
        confidence=Confidence.HIGH.value,
        method=METHOD_SOURCE_TYPE_HINT,
        evidence=[],
    )
    doc = Document(
        publication_id=pub.id,
        url=pub.url,
        kind="html",
        status=DocumentStatus.FETCHED,
        local_path=str(
            __import__("pathlib").Path(__file__).parent / "fixtures" / "documents" / "snb_summary.html"
        ),
    )
    normalized = Normalizer().parse(doc)
    store.upsert_normalized_document(normalized)
    return store, store.get_publication(pub.id)


def test_minutes_classified_summary_is_never_mined_as_decision(tmp_path):
    # A `minutes` classification must stop the SNB Decision extractor from
    # mining the discussion summary (which would otherwise yield spurious
    # `policy_rate` / decision-date facts).
    store, pub = _summary_store(tmp_path)
    assert extract_decision(store, pub) == []
    assert store.get_facts(publication_id=pub.id) == []


def test_minutes_classified_summary_is_document_only(tmp_path):
    # The Minutes family has no SNB extractor: classification and extraction
    # availability are distinct — the summary is intentionally classified but
    # remains unextracted (document-only), never silently treated as a decision.
    from argus.minutes import get_extractor

    assert get_extractor("snb") is None
    store, pub = _summary_store(tmp_path)
    assert extract_minutes(store, pub) == []
    assert store.get_facts(publication_id=pub.id) == []


def test_decision_classified_summary_old_behaviour_would_mine_facts(tmp_path):
    # Regression guard: the *reason* for the fix — under the pre-fix
    # classification the SNB Decision extractor mined the summary into
    # spurious facts. Assert the decision path still has that capability on a
    # decision-classified summary so the boundary stays explicit.
    store, pub = _summary_store(tmp_path, publication_type=DECISION)
    results = extract_decision(store, pub)
    assert len(results) == 1
    facts = store.get_facts(publication_id=pub.id)
    assert any(f.subject == "policy_rate" for f in facts)


# ---------------------------------------------------------------------------
# Discovery — snb_summaries adapter source
# ---------------------------------------------------------------------------


def test_snb_summaries_source_declaration():
    from argus.adapters.snb import SNBAdapter

    adapter = SNBAdapter()
    source = next(s for s in adapter.sources if s.id == "snb_summaries")
    assert source.discovery.kind == "html"
    assert source.publication_types == (MINUTES,)
    assert "zus_\\d{8}" in source.discovery.include
    assert source.priority < 4  # never displaces the mopo RSS primary source


def test_snb_summaries_html_discovery(fixture_bytes):
    from argus.adapters.snb import SNBAdapter
    from argus.discovery import create
    from conftest import FakeSession, make_client, response

    adapter = SNBAdapter()
    source = next(s for s in adapter.sources if s.id == "snb_summaries")
    routes = {source.discovery.url: response(fixture_bytes("snb_summaries.html"), url=source.discovery.url)}
    publications = create(source, make_client(FakeSession(routes))).discover()
    assert len(publications) == 4
    for pub in publications:
        assert pub.central_bank == "snb"
        assert pub.source_id == source.id
        assert "zus_" in pub.url
        assert "minutes" in pub.extra["type_hint"]