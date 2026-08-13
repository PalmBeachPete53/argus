"""Deterministic classification tests — evidence tiers and store persistence."""

from __future__ import annotations

from argus.classification import (
    Confidence,
    METHOD_CONTENT_HEURISTIC,
    METHOD_DOCUMENT_METADATA,
    METHOD_SOURCE_TYPE_HINT,
    METHOD_TITLE_PATTERN,
    METHOD_UNRESOLVED,
    METHOD_URL_PATTERN,
    PublicationClassifier,
)
from argus.documents import NormalizedDocument
from argus.models import Publication


def publication(**kw) -> Publication:
    fields = dict(
        central_bank="ecb",
        title="Monetary policy decisions",
        url="https://www.ecb.europa.eu/press/pr/date/2026/html/ecb.mp260703.en.html",
        source_id="ecb_press_rss",
        source_url="https://www.ecb.europa.eu/rss/press.html",
    )
    fields.update(kw)
    return Publication(**fields)


def classify(pub, **kw):
    return PublicationClassifier().classify(pub, **kw)


def evidence_prefix(evidence, prefix: str) -> bool:
    return any(item.startswith(prefix) for item in evidence)


def normalized(**kw) -> NormalizedDocument:
    fields = dict(
        publication_id="pub-1",
        document_id="doc-1",
        source_url="",
        local_path=None,
        document_kind="html",
    )
    fields.update(kw)
    return NormalizedDocument(**fields)


# ---------------------------------------------------------------------------
# Tier 1 — source type hint
# ---------------------------------------------------------------------------


def test_single_source_type_hint_high_confidence():
    result = classify(publication(central_bank="norges", extra={"type_hint": ["monetary_policy_decision"]}))
    assert result.publication_type == "monetary_policy_decision"
    assert result.confidence == Confidence.HIGH
    assert result.method == METHOD_SOURCE_TYPE_HINT
    assert "type_hint=monetary_policy_decision" in result.evidence


def test_type_hint_distinct_from_publication_type_field():
    # The model must keep the *hint set* separate from the *classified type*.
    pub = publication(extra={"type_hint": ["monetary_policy_decision", "press_release"]})
    assert classify(pub).publication_type == "monetary_policy_decision"
    assert pub.extra["type_hint"] == ["monetary_policy_decision", "press_release"]
    assert pub.publication_type is None


# ---------------------------------------------------------------------------
# Tiers 2/3 — url / title, with the source-set used for confidence
# ---------------------------------------------------------------------------


def test_url_pattern_with_in_source_hint_is_high():
    result = classify(
        publication(
            central_bank="fed",
            title="Federal Reserve issues FOMC statement",
            url="https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm",
            extra={"type_hint": ["monetary_policy_decision", "monetary_policy_statement"]},
        )
    )
    assert result.publication_type == "monetary_policy_decision"
    assert result.confidence == Confidence.HIGH
    assert result.method == METHOD_URL_PATTERN
    assert evidence_prefix(result.evidence, "url_pattern=monetary_policy_decision")


def test_title_pattern_medium_when_not_declared():
    result = classify(
        publication(
            central_bank="boe",
            title="Minutes of the Monetary Policy Committee meeting, June 2026",
            url="https://www.bankofengland.co.uk/news/2026/06/monetary-policy",
            source_id="boe_press_rss",
        )
    )
    assert result.publication_type == "minutes"
    assert result.confidence == Confidence.MEDIUM
    assert result.method == METHOD_TITLE_PATTERN


def test_ecb_meeting_account_from_url():
    result = classify(
        publication(
            title="Account of the monetary policy meeting",
            url="https://www.ecb.europa.eu/press/accounts/2026/html/ecb.acc260715.en.html",
        )
    )
    assert result.publication_type == "meeting_account"
    assert result.method == METHOD_URL_PATTERN
    assert result.confidence == Confidence.MEDIUM
    assert evidence_prefix(result.evidence, "url_pattern=meeting_account")


def test_snb_bank_rule_from_url():
    result = classify(
        publication(
            central_bank="snb",
            title="Monetary policy assessment of 18 June 2026",
            url="https://www.snb.ch/en/mmr/reference/pre_20260618/source",
        )
    )
    assert result.publication_type == "monetary_policy_decision"
    assert result.method == METHOD_URL_PATTERN
    assert result.confidence == Confidence.MEDIUM


def test_title_pattern_kept_policy_rate_norges():
    result = classify(
        publication(
            central_bank="norges",
            title="Policy rate kept unchanged at 4.25 percent",
            url="https://www.norges-bank.no/en/news-updates/2026/03/policy-rate/",
            source_id="norges_press_releases_rss",
        )
    )
    assert result.publication_type == "monetary_policy_decision"
    assert result.method == METHOD_TITLE_PATTERN


def test_title_contradiction_skips_url_tier():
    # The URL slug is generic enough to match the statement rule, but the
    # (stronger) title is explicit about minutes.
    result = classify(
        publication(
            central_bank="fed",
            title="Minutes of the Federal Open Market Committee, April 2026",
            url="https://www.federalreserve.gov/monetarypolicy/fomcminutes20260408.htm",
        )
    )
    assert result.publication_type == "minutes"
    assert result.method in (METHOD_TITLE_PATTERN, METHOD_URL_PATTERN)


# ---------------------------------------------------------------------------
# Tier 4/5 — document metadata & content heuristic
# ---------------------------------------------------------------------------


def test_document_metadata_title_used_when_publication_title_empty():
    # The downloaded document's own title is a separate signal from the feed
    # title and is only consulted once url/title tiers produced nothing.
    doc = normalized(title="Account of the monetary policy meeting")
    result = classify(
        publication(
            title="index.en",
            url="https://www.ecb.europa.eu/press/miscellaneous/2026/index.en.html",
        ),
        normalized=doc,
    )
    assert result.publication_type == "meeting_account"
    assert result.method == METHOD_DOCUMENT_METADATA
    assert result.confidence == Confidence.MEDIUM
    assert evidence_prefix(result.evidence, "document_metadata=meeting_account")


def test_content_heuristic_low_confidence():
    doc = normalized(text="The governing council decided to keep the policy stance unchanged.")
    result = classify(
        publication(
            central_bank="ecb",
            title="ECB Economic Bulletin, Issue 3/2026",
            url="https://www.ecb.europa.eu/pub/economic-bulletin/html/eb202603.en.html",
        ),
        normalized=doc,
    )
    assert result.publication_type == "monetary_policy_decision"
    assert result.method == METHOD_CONTENT_HEURISTIC
    assert result.confidence == Confidence.LOW
    assert evidence_prefix(result.evidence, "content_heuristic=monetary_policy_decision")


def test_content_window_is_configurable():
    # The distinguishing passage sits beyond the default window: with the
    # default content_window the content tier sees nothing, but a larger,
    # explicitly configured window lets the content heuristic resolve it.
    filler = "forecast tables and charts\n" * 1500  # ~48k chars of non-signals
    pub_kwargs = dict(
        central_bank="fed",
        title="FOMC dots and summary",
        url="https://www.federalreserve.gov/aboutthefed/2026/dots-notes.htm",
        extra={},
    )
    doc = normalized(text=filler + "The Committee decided to raise the target range.")

    default = PublicationClassifier().classify(publication(**pub_kwargs), normalized=doc)
    assert default.publication_type == "unknown"

    wide = PublicationClassifier(content_window=60_000).classify(publication(**pub_kwargs), normalized=doc)
    assert wide.publication_type == "monetary_policy_decision"
    assert wide.method == METHOD_CONTENT_HEURISTIC
    assert wide.confidence == Confidence.LOW


def test_invalid_content_configuration_rejected():
    import pytest

    with pytest.raises(ValueError):
        PublicationClassifier(content_window=0)
    with pytest.raises(ValueError):
        PublicationClassifier(content_scope="last_n_chars")


def test_metadata_contradiction_beats_url_tier():
    # An explicit document title is a stronger, more specific signal than the
    # shared URL slug: the url tier is skipped and the metadata tier decides.
    doc = normalized(title="Minutes of the Federal Open Market Committee")
    result = classify(
        publication(
            central_bank="fed",
            title="index.en",
            url="https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm",
        ),
        normalized=doc,
    )
    assert result.publication_type == "minutes"
    assert result.method == METHOD_DOCUMENT_METADATA


# ---------------------------------------------------------------------------
# Fallbacks & vocabulary guarantees
# ---------------------------------------------------------------------------


def test_unresolvable_publication_is_unknown():
    result = classify(
        publication(
            central_bank="ecb",
            title="The new banknote series",
            url="https://www.ecb.europa.eu/press/other/2026/html/index.en.html",
            extra={},
        )
    )
    assert result.publication_type == "unknown"
    assert result.confidence == Confidence.LOW
    assert result.method == METHOD_UNRESOLVED
    assert result.evidence  # never silently empty


def test_type_hint_and_source_id_recorded_in_evidence():
    result = classify(publication(extra={"type_hint": ["minutes"]}))
    assert "source_id=ecb_press_rss" in result.evidence
    assert "type_hint=minutes" in result.evidence


def test_classified_type_always_in_vocabulary():
    candidates = [
        publication(),
        publication(central_bank="fed", url="https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm"),
        publication(central_bank="boe", title="Monetary Policy Report – May 2026"),
        publication(central_bank="xbank", title="something entirely unrelated"),
    ]
    from argus.classification import PUBLICATION_TYPES

    for candidate in candidates:
        result = classify(candidate)
        assert result.publication_type in PUBLICATION_TYPES, result.publication_type


def test_broad_feed_declares_no_type():
    # Regression: broad "press releases" feeds must NOT declare a single type,
    # otherwise every unrelated item (speeches, surveys, explainers) is forced
    # into a HIGH-confidence decision. Only type-specific sources declare types.
    from argus.classification import canonical_types
    from argus.registry import SourceRegistry

    registry = SourceRegistry()

    def types_of(source_id):
        source = registry.source(source_id)
        return tuple(canonical_types(source.publication_types))

    assert types_of("boc_press_releases_rss") == ()
    assert types_of("norges_press_releases_rss") == ()
    assert types_of("riksbank_press_releases_rss") == ()
    assert types_of("rba_media_releases_rss") == ()
    assert types_of("boe_news_rss") == ()
    assert types_of("boj_whatsnew_rss") == ()

    # Type-specific sources still declare their type.
    assert types_of("boc_fad_archive") == ("monetary_policy_decision",)
    assert types_of("norges_mpr_rss") == ("monetary_policy_report",)
    assert types_of("fed_monetary_press_rss") == ("monetary_policy_decision", "monetary_policy_statement")


def test_unrelated_item_from_broad_feed_is_not_forced_decision():
    from argus.registry import SourceRegistry

    classifier = PublicationClassifier(registry=SourceRegistry())
    result = classifier.classify(
        publication(
            central_bank="boc",
            title="What is a central bank?",
            url="https://www.bankofcanada.ca/2026/07/what-is-a-central-bank/",
            source_id="boc_press_releases_rss",
            extra={},
        )
    )
    assert result.publication_type == "unknown"
    assert result.confidence == Confidence.LOW


def test_stale_stored_hint_does_not_override_live_declaration():
    # A publication stored with a stale decision hint from an old (corrected)
    # adapter must not be forced into a HIGH decision when the live source now
    # declares no types.
    from argus.registry import SourceRegistry

    classifier = PublicationClassifier(registry=SourceRegistry())
    result = classifier.classify(
        publication(
            central_bank="norges",
            title="Norges Bank launches new website feature",
            url="https://www.norges-bank.no/en/news-updates/2026/07/new-feature/",
            source_id="norges_press_releases_rss",
            extra={"type_hint": ["monetary_policy_decision"]},  # stale
        )
    )
    assert result.publication_type == "unknown"
    assert result.method == METHOD_UNRESOLVED


def test_classification_is_repeatable_and_deterministic():
    pub = publication(central_bank="boe", title="Minutes of the Monetary Policy Committee meeting, July 2026")
    first = classify(pub)
    second = classify(pub)
    assert first.publication_type == second.publication_type
    assert first.method == second.method
    assert first.evidence == second.evidence


# ---------------------------------------------------------------------------
# Batched + persistence against the store
# ---------------------------------------------------------------------------


def test_classify_many_and_persist(tmp_path):
    from conftest import make_store

    store = make_store(tmp_path)
    classifier = PublicationClassifier(store=store)

    stored = store.upsert_publication(publication())
    assert stored.id is not None

    results = classifier.classify_many([stored])
    assert len(results) == 1
    record = store.get_classification(stored.id)
    assert record is not None
    assert record["publication_type"] == "monetary_policy_decision"
    assert record["confidence"] == "medium"
    assert record["method"] == METHOD_TITLE_PATTERN


def test_classifications_is_single_source_of_truth(tmp_path):
    # `classifications` carries the authoritative type + method + evidence;
    # `publications.publication_type` is only a denormalized cache written in
    # the same transaction, so both always agree by construction.
    from conftest import make_store

    store = make_store(tmp_path)
    stored = store.upsert_publication(publication())
    store.set_classification(
        stored.id,
        central_bank="ecb",
        publication_type="monetary_policy_decision",
        confidence="high",
        method="source_type_hint",
        evidence=["type_hint=monetary_policy_decision"],
    )
    record = store.get_classification(stored.id)
    cached = store.get_publication(stored.id)
    assert record is not None
    assert record["publication_type"] == cached.publication_type == "monetary_policy_decision"
    # The cache never carries the reasoning — only the classification table does.
    assert "evidence" in record and record["confidence"] == "high"
    assert record["method"] == "source_type_hint"


def test_classify_does_not_persist_without_store(tmp_path):
    from conftest import make_store

    store = make_store(tmp_path)
    pub = publication(extra={"type_hint": ["monetary_policy_decision"]})
    stored = store.upsert_publication(pub)
    PublicationClassifier().classify(stored)  # no store attached
    assert store.get_classification(stored.id) is None


def test_classify_all_scoped_by_bank(tmp_path):
    from conftest import make_store

    store = make_store(tmp_path)
    store.upsert_publication(
        publication(central_bank="fed", url="https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm")
    )
    store.upsert_publication(publication(central_bank="ecb"))
    classifier = PublicationClassifier(store=store)
    ecb_results = classifier.classify_all(banks=("ecb",))
    assert {r.central_bank for r in ecb_results} == {"ecb"}
    assert len(store.list_classifications()) == 1