from argus.discovery import create
from conftest import make_client, make_source, response


def test_rss2_discovery(fixture_bytes):
    url = "https://x.test/feed.xml"
    source = make_source(id_="s", bank="boe", url=url)
    from conftest import FakeSession

    session = FakeSession({url: response(fixture_bytes("boe_news.xml"), url=url, content_type="application/xml")})
    pubs = create(source, make_client(session)).discover()
    assert len(pubs) == 3
    assert pubs[0].central_bank == "boe"
    assert pubs[0].source_id == "s"
    assert pubs[0].publication_date is not None
    assert "/monetary-policy-summary-and-minutes/2026/july-2026" in pubs[0].url
    assert pubs[0].title.startswith("Bank Rate maintained")


def test_rdf_rss1_discovery(fixture_bytes):
    url = "https://x.test/rdf.xml"
    source = make_source(id_="s", bank="boc", url=url)
    from conftest import FakeSession

    session = FakeSession({url: response(fixture_bytes("boc_press.xml"), url=url, content_type="application/rss+xml")})
    pubs = create(source, make_client(session)).discover()
    assert len(pubs) == 2
    assert all(p.publication_date is not None for p in pubs)
    assert "fad-press-release-2026-07-15" in pubs[0].url


def test_atom_discovery(fixture_bytes):
    url = "https://x.test/atom.xml"
    source = make_source(id_="s", bank="x", url=url)
    from conftest import FakeSession

    session = FakeSession({url: response(fixture_bytes("atom_feed.xml"), url=url, content_type="application/atom+xml")})
    pubs = create(source, make_client(session)).discover()
    assert len(pubs) == 2
    assert any("atom-decision" in p.url for p in pubs)


def test_enclosure_becomes_document_url(fixture_bytes):
    url = "https://x.test/snb.xml"
    source = make_source(id_="s", bank="snb", url=url)
    from conftest import FakeSession

    session = FakeSession({url: response(fixture_bytes("snb_mopo.xml"), url=url, content_type="application/xml")})
    pubs = create(source, make_client(session)).discover()
    assert pubs[0].document_urls
    assert pubs[0].document_urls[0].endswith("/source")


def test_rss_include_filter(fixture_bytes):
    url = "https://x.test/feed.xml"
    source = make_source(
        id_="s", bank="boe", url=url,
        include=(r"/monetary-policy-summary-and-minutes/",),
    )
    from conftest import FakeSession

    session = FakeSession({url: response(fixture_bytes("boe_news.xml"), url=url, content_type="application/xml")})
    pubs = create(source, make_client(session)).discover()
    assert len(pubs) == 1
    assert "monetary-policy-summary-and-minutes" in pubs[0].url


def test_lookback_window_filters_old_items(fixture_bytes):
    from datetime import timedelta
    from argus.normalize import now_utc

    url = "https://x.test/feed.xml"
    source = make_source(id_="s", bank="boe", url=url, lookback_window_days=30)
    from conftest import FakeSession

    session = FakeSession({url: response(fixture_bytes("boe_news.xml"), url=url, content_type="application/xml")})
    pubs = create(source, make_client(session), now=lambda: now_utc()).discover()
    assert len(pubs) == 2
    for p in pubs:
        assert p.publication_date is None or (now_utc() - p.publication_date).days <= 30