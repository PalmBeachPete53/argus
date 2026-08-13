"""Deterministic classification tests — evidence tiers and store persistence."""

from __future__ import annotations

from argus.classification import (
    Confidence,
    METHOD_SOURCE_TYPE_HINT,
    METHOD_TITLE_PATTERN,
    METHOD_UNRESOLVED,
    METHOD_URL_PATTERN,
    PublicationClassifier,
)
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