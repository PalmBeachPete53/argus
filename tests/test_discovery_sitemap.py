from argus.discovery import create
from conftest import make_client, make_source, response


def test_sitemap_index_walk(fixture_bytes):
    index_url = "https://x.test/sitemap.xml"
    routes = {
        index_url: response(fixture_bytes("sitemap_index.xml"), url=index_url, content_type="application/xml"),
        "https://x.test/sitemap-a.xml": response(fixture_bytes("sitemap_a.xml"), url="https://x.test/sitemap-a.xml", content_type="application/xml"),
        "https://x.test/sitemap-b.xml": response(fixture_bytes("sitemap_b.xml"), url="https://x.test/sitemap-b.xml", content_type="application/xml"),
    }
    source = make_source(id_="s", bank="x", kind="sitemap", url=index_url)
    from conftest import FakeSession

    pubs = create(source, make_client(FakeSession(routes))).discover()
    assert len(pubs) == 5
    urls = [p.url for p in pubs]
    assert "https://x.test/monetary-policy/decision-2026-07-01" in urls
    assert "https://x.test/monetary-policy/minutes-2026-07-01" in urls


def test_sitemap_include_filter(fixture_bytes):
    url = "https://x.test/sitemap-a.xml"
    source = make_source(
        id_="s", bank="x", kind="sitemap", url=url,
        include=(r"decision-",),
    )
    from conftest import FakeSession

    session = FakeSession({url: response(fixture_bytes("sitemap_a.xml"), url=url, content_type="application/xml")})
    pubs = create(source, make_client(session)).discover()
    assert len(pubs) == 2
    assert all("decision-" in p.url for p in pubs)


def test_sitemap_derive_title_and_date(fixture_bytes):
    url = "https://x.test/sitemap-a.xml"
    source = make_source(id_="s", bank="x", kind="sitemap", url=url)
    from conftest import FakeSession

    session = FakeSession({url: response(fixture_bytes("sitemap_a.xml"), url=url, content_type="application/xml")})
    pubs = create(source, make_client(session)).discover()
    dated = [p for p in pubs if p.publication_date is not None]
    assert dated
    assert all(p.title for p in pubs)