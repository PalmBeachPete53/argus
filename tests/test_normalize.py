from datetime import datetime, timezone

from argus.normalize import (
    absolutize,
    canonical_url,
    compute_dedup_key,
    normalize_title,
    parse_datetime,
    slugify,
    title_from_url,
)


def test_canonical_url_removes_tracking_and_fragment():
    assert (
        canonical_url("https://www.Example.com/path/?utm_source=x&b=2&a=1#frag")
        == "https://www.example.com/path?a=1&b=2"
    )


def test_canonical_url_default_ports_removed():
    assert canonical_url("https://example.com:443/x/") == "https://example.com/x"


def test_canonical_url_homepage_kept():
    assert canonical_url("https://example.com") == "https://example.com/"


def test_dedup_key_url_and_string_forms_differ():
    url_key = compute_dedup_key("bank", url="https://example.com/a")
    text_key = compute_dedup_key("bank", title="A Title", date=None)
    assert url_key != text_key


def test_dedup_key_deterministic():
    a = compute_dedup_key("fed", url="https://example.com/x?a=1&b=2")
    b = compute_dedup_key("fed", url="https://example.com/x?b=2&a=1")
    assert a == b


def test_dedup_key_case_insensitive_titles():
    date = datetime(2026, 7, 1, tzinfo=timezone.utc)
    a = compute_dedup_key("fed", title="Statement on Monetary Policy", date=date)
    b = compute_dedup_key("fed", title="statement on monetary  POLICY", date=date)
    assert a == b


def test_absolutize():
    assert absolutize("https://x.test/a/", "b/c.html") == "https://x.test/a/b/c.html"
    assert absolutize("https://x.test/a/", "https://y.test/b") == "https://y.test/b"


def test_parse_datetime_variants():
    assert parse_datetime("Wed, 29 Jul 2026 14:00:00 -0400") is not None
    assert parse_datetime("2026-07-29") == datetime(2026, 7, 29, tzinfo=timezone.utc)
    assert parse_datetime("2026-07-29T12:00:00+00:00") == datetime(
        2026, 7, 29, 12, 0, tzinfo=timezone.utc
    )
    assert parse_datetime("July 29, 2026") == datetime(2026, 7, 29, tzinfo=timezone.utc)
    assert parse_datetime("not a date") is None


def test_normalize_title_and_slugify():
    assert normalize_title("  Statement   on\nMonetary Policy ") == "statement on monetary policy"
    assert slugify("Statement on Monetary Policy! (Aug)") == "statement-on-monetary-policy-aug"


def test_title_from_url():
    assert title_from_url("https://x.test/press/pr/date/2026/html/ecb.mp260703.en.html") == "ecb.mp260703.en"
    assert title_from_url("https://x.test/") == "Untitled"