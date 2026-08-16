"""Search Discovery fallback (SearXNG) — SearchProvider, SearchDiscovery,
fallback semantics, provenance, deduplication and the discovery/fetch boundary.

All tests are offline: the SearXNG HTTP layer is driven with ``FakeSession``
and the collector's fallback with a stub provider. No network is touched.
"""

from __future__ import annotations

import pytest

from conftest import FakeSession, make_client, make_store, response

from argus.collector import CentralBankCollector
from argus.discovery.search import SearchDiscovery, _matches_domain
from argus.errors import HttpError, TransportError
from argus.models import CentralBank, Publication
from argus.registry import SourceRegistry
from argus.search import SearchProvider, SearchResult, SearxngSearchProvider
from argus.adapters.base import BankAdapter, rss_source

SEARXNG_BASE = "https://searxng.test"
SEARXNG_URL = "https://searxng.test/search?q=site%3Arba.gov.au+%22Monetary+Policy+Decision%22&format=json"


def searxng_json(*items: dict) -> str:
    return '{"query": "x", "results": [' + ",".join(__import__("json").dumps(i) for i in items) + "]}"


# ---------------------------------------------------------------------------
# SearxngSearchProvider — parsing, errors, configuration
# ---------------------------------------------------------------------------


def test_searxng_parse_results():
    session = FakeSession({
        SEARXNG_URL: response(
            searxng_json(
                {
                    "url": "https://www.rba.gov.au/monetary-policy/int-rate-decisions/2026/mr-26-07.html",
                    "title": "Monetary Policy Decision",
                    "content": "The Board decided to lower the cash rate target.",
                    "publishedDate": "2026-08-04T12:30:00+10:00",
                    "engine": "google",
                },
                {"url": "https://www.rba.gov.au/media-releases/2026/mr-26-19.html", "title": "Monetary Policy Decision"},
                {"title": "no url here"},
            ),
            url=SEARXNG_URL,
            content_type="application/json",
        ),
    })
    provider = SearxngSearchProvider(SEARXNG_BASE, client=make_client(session))
    results = provider.search('site:rba.gov.au "Monetary Policy Decision"')
    assert len(results) == 2
    assert results[0].rank == 1
    assert results[0].url.startswith("https://www.rba.gov.au/")
    assert results[0].title == "Monetary Policy Decision"
    assert results[0].snippet.startswith("The Board decided")
    assert results[0].published_date is not None
    assert results[0].engine == "google"
    assert results[1].rank == 2


def test_searxng_query_url_includes_engines_and_language():
    provider = SearxngSearchProvider(
        SEARXNG_BASE, client=make_client(FakeSession({})), engines=("google", "bing"), language="en"
    )
    url = provider._search_url('site:rba.gov.au "Decision"', ("startpage",))
    assert url.startswith("https://searxng.test/search?")
    assert "format=json" in url
    assert "q=site%3Arba.gov.au+%22Decision%22" in url
    assert "engines=startpage" in url


def test_searxng_http_error_raises():
    session = FakeSession({SEARXNG_URL: response("denied", status=500, url=SEARXNG_URL)})
    provider = SearxngSearchProvider(SEARXNG_BASE, client=make_client(session))
    with pytest.raises(HttpError):
        provider.search('site:rba.gov.au "Monetary Policy Decision"')


def test_searxng_empty_results():
    session = FakeSession({SEARXNG_URL: response(searxng_json(), url=SEARXNG_URL, content_type="application/json")})
    provider = SearxngSearchProvider(SEARXNG_BASE, client=make_client(session))
    assert provider.search('site:rba.gov.au "Monetary Policy Decision"') == []


def test_searxng_malformed_response():
    url = "https://searxng.test/search?q=x&format=json"
    session = FakeSession({url: response("<html>oops</html>", url=url, content_type="text/html")})
    provider = SearxngSearchProvider(SEARXNG_BASE, client=make_client(session))
    with pytest.raises(HttpError):
        provider.search("x")


def test_searxng_requires_base_url():
    with pytest.raises(ValueError):
        SearxngSearchProvider("")


# ---------------------------------------------------------------------------
# _matches_domain / SearchDiscovery
# ---------------------------------------------------------------------------


def test_matches_domain():
    assert _matches_domain("https://www.rba.gov.au/x", "rba.gov.au")
    assert _matches_domain("https://rba.gov.au/x", "rba.gov.au")
    assert _matches_domain("https://www.rbnz.govt.nz/x", "rbnz.govt.nz")
    assert not _matches_domain("https://example.com/x", "rba.gov.au")
    assert not _matches_domain("https://rba.gov.au.evil.com/x", "rba.gov.au")


class _StubProvider(SearchProvider):
    name = "stub"

    def __init__(self, results: list[SearchResult], *, query: str | None = None):
        self.results = results
        self.seen: list[str] = []
        self.query = query

    def search(self, query: str, *, engines: tuple[str, ...] = ()) -> list[SearchResult]:
        self.seen.append(query)
        return self.results


def _search_source(bank="fb", domain="fake.test", query='site:fake.test "Decision"'):
    return rss_source(
        "native",
        bank,
        "native feed",
        f"https://{domain}/feed.xml",
        search_query=query,
        search_domain=domain,
    )


def test_search_discovery_transforms_and_filters_domain():
    source = _search_source()
    provider = _StubProvider([
        SearchResult(url="https://www.fake.test/decisions/mr-26-07.html", title="Monetary Policy Decision", rank=1),
        SearchResult(url="https://example.com/decisions/x.html", title="Monetary Policy Decision", rank=2),
    ])
    pubs = SearchDiscovery(source, provider).discover()
    assert len(pubs) == 1  # example.com filtered out by search_domain
    pub = pubs[0]
    assert pub.url == "https://www.fake.test/decisions/mr-26-07.html"
    assert pub.central_bank == "fb"
    assert pub.source_id == "native"
    assert provider.seen == ['site:fake.test "Decision"']


def test_search_discovery_provenance():
    source = _search_source()
    provider = _StubProvider([SearchResult(url="https://www.fake.test/a", title="A", rank=3)])
    pub = SearchDiscovery(source, provider).discover()[0]
    assert pub.extra["discovery_method"] == "search"
    assert pub.extra["search_provider"] == "stub"
    assert pub.extra["search_query"] == 'site:fake.test "Decision"'
    assert pub.extra["search_rank"] == 3
    assert pub.extra["search_result_url"] == "https://www.fake.test/a"


def test_search_discovery_no_query_returns_empty():
    source = rss_source("native", "fb", "feed", "https://fake.test/feed.xml")
    provider = _StubProvider([SearchResult(url="https://www.fake.test/a", title="A")])
    assert SearchDiscovery(source, provider).discover() == []
    assert provider.seen == []


# ---------------------------------------------------------------------------
# RBA / RBNZ search fallback configuration
# ---------------------------------------------------------------------------


def test_rba_source_has_search_fallback_config():
    source = SourceRegistry().source("rba_media_releases_rss")
    assert source is not None
    assert source.discovery.search_query == 'site:rba.gov.au "Monetary Policy Decision"'
    assert source.discovery.search_domain == "rba.gov.au"


def test_rbnz_source_has_search_fallback_config():
    source = SourceRegistry().source("rbnz_ocr_decisions")
    assert source is not None
    assert source.discovery.search_query == 'site:rbnz.govt.nz "Monetary Policy Statement"'
    assert source.discovery.search_domain == "rbnz.govt.nz"


def test_ecb_source_has_no_search_fallback():
    source = SourceRegistry().source("ecb_press_rss")
    assert source is not None
    assert source.discovery.search_query is None


# ---------------------------------------------------------------------------
# Collector fallback semantics
# ---------------------------------------------------------------------------


class _FakeBankAdapter(BankAdapter):
    def _build(self):
        bank = CentralBank("fb", "Fake Bank", "XXX", "fake.test")
        sources = [
            rss_source(
                "native",
                "fb",
                "native feed",
                "https://fake.test/feed.xml",
                search_query='site:fake.test "Decision"',
                search_domain="fake.test",
            )
        ]
        return bank, sources


def _collector(tmp_path, session, provider):
    store = make_store(tmp_path)
    registry = SourceRegistry([_FakeBankAdapter()])
    return store, CentralBankCollector(
        store=store, registry=registry, client=make_client(session), search_provider=provider
    )


def test_fallback_on_native_error(tmp_path):
    # native feed raises a transport error (e.g. HTTP 403) → search fallback
    session = FakeSession({"https://fake.test/feed.xml": TransportError("https://fake.test/feed.xml", "403")})
    provider = _StubProvider([SearchResult(url="https://www.fake.test/decisions/mr-26-07.html", title="Decision")])
    store, collector = _collector(tmp_path, session, provider)
    pubs = collector.discover_all()
    assert len(pubs) == 1
    assert pubs[0].url == "https://www.fake.test/decisions/mr-26-07.html"
    assert pubs[0].extra["discovery_method"] == "search"
    # native error is still logged for observability
    errors = store.list_errors()
    assert any(e.source_id == "native" for e in errors)
    assert provider.seen == ['site:fake.test "Decision"']


def test_no_fallback_when_native_succeeds(tmp_path):
    feed = b"<rss><channel><item><title>Decision</title><link>https://www.fake.test/decisions/a.html</link></item></channel></rss>"
    session = FakeSession({
        "https://fake.test/feed.xml": response(feed, url="https://fake.test/feed.xml", content_type="application/xml"),
    })
    provider = _StubProvider([SearchResult(url="https://www.fake.test/b", title="B")])
    store, collector = _collector(tmp_path, session, provider)
    pubs = collector.discover_all()
    assert len(pubs) == 1
    assert pubs[0].url == "https://www.fake.test/decisions/a.html"
    assert "discovery_method" not in pubs[0].extra
    assert provider.seen == []  # search never invoked


def test_no_fallback_without_provider(tmp_path):
    session = FakeSession({"https://fake.test/feed.xml": TransportError("https://fake.test/feed.xml", "403")})
    store, collector = _collector(tmp_path, session, None)
    pubs = collector.discover_all()
    assert pubs == []
    errors = store.list_errors()
    assert any(e.source_id == "native" for e in errors)


def test_empty_native_is_valid_unless_opted_in(tmp_path):
    # native feed parses to zero items; without search_fallback_on_empty the
    # empty state is respected and search is NOT invoked.
    empty = b"<rss><channel></channel></rss>"
    session = FakeSession({
        "https://fake.test/feed.xml": response(empty, url="https://fake.test/feed.xml", content_type="application/xml"),
    })
    provider = _StubProvider([SearchResult(url="https://www.fake.test/a", title="A")])
    store, collector = _collector(tmp_path, session, provider)
    assert collector.discover_all() == []
    assert provider.seen == []


def test_deduplication_native_and_search_same_publication(tmp_path):
    """A publication reached both natively and via search is one object."""
    url = "https://www.fake.test/decisions/mr-26-07.html"
    feed = (
        b"<rss><channel><item><title>Decision</title>"
        + b"<link>" + url.encode() + b"</link></item></channel></rss>"
    )
    session = FakeSession({
        "https://fake.test/feed.xml": response(feed, url="https://fake.test/feed.xml", content_type="application/xml"),
    })
    provider = _StubProvider([SearchResult(url=url, title="Decision")])
    store, collector = _collector(tmp_path, session, provider)

    # native run only
    collector.discover_all()
    assert len(store.list_publications()) == 1

    # a source without search config would not fall back, but this source has
    # search_query; simulate a native failure on the second pass → search returns
    # the same URL → upsert keeps a single publication.
    session.routes["https://fake.test/feed.xml"] = TransportError("https://fake.test/feed.xml", "403")
    collector.discover_all()
    pubs = store.list_publications()
    assert len(pubs) == 1
    assert pubs[0].url == url


# ---------------------------------------------------------------------------
# Discovery / Fetch separation
# ---------------------------------------------------------------------------


def test_search_discovery_never_returns_content(tmp_path):
    """SearchDiscovery yields candidate URLs only; document content comes from
    the Fetcher hitting that URL (never from the provider)."""
    url = "https://www.fake.test/decisions/mr-26-07.html"
    source = _search_source()
    provider = _StubProvider([SearchResult(url=url, title="Decision")])
    pubs = SearchDiscovery(source, provider).discover()
    assert len(pubs) == 1
    # the candidate carries no document content
    assert pubs[0].document_urls == ()
    assert not any("content" in k for k in pubs[0].extra)

    # the Fetcher receives the URL and fetches from it (fake transport)
    body = b"<html><body><h1>Decision</h1></body></html>"
    session = FakeSession({url: response(body, url=url, content_type="text/html")})
    from argus.fetcher import Fetcher

    pub = Publication(
        central_bank="fb", title="Decision", url=url, source_id="native",
        source_url="https://fake.test/feed.xml", id="pub-1", extra=pubs[0].extra,
    )
    store = make_store(tmp_path)
    store.upsert_publication(pub)
    fetch = Fetcher(make_client(session), store, tmp_path / "raw").fetch(pub)
    assert fetch.ok
    assert any(d.url == url for d in fetch.documents)


# ---------------------------------------------------------------------------
# CLI environment configuration
# ---------------------------------------------------------------------------


def test_search_provider_from_env(monkeypatch):
    from argus.cli import _search_provider_from_env

    monkeypatch.delenv("SEARCH_PROVIDER", raising=False)
    assert _search_provider_from_env() is None

    monkeypatch.setenv("SEARCH_PROVIDER", "searxng")
    monkeypatch.setenv("SEARXNG_BASE_URL", "https://searxng.test")
    monkeypatch.setenv("SEARXNG_ENGINES", "google, startpage")
    monkeypatch.setenv("SEARXNG_LANGUAGE", "en")
    provider = _search_provider_from_env()
    assert isinstance(provider, SearxngSearchProvider)
    assert provider.base_url == "https://searxng.test/"
    assert provider.engines == ("google", "startpage")
    assert provider.language == "en"


def test_search_provider_from_env_missing_base_url(monkeypatch):
    from argus.cli import _search_provider_from_env

    monkeypatch.setenv("SEARCH_PROVIDER", "searxng")
    monkeypatch.delenv("SEARXNG_BASE_URL", raising=False)
    assert _search_provider_from_env() is None
