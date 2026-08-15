from datetime import datetime, timezone
from pathlib import Path

from argus.discovery import create
from conftest import make_client, make_source, response


def _load(name):
    return Path(__file__).parent.joinpath("fixtures", name).read_text()


def test_html_listing_plus_pagination(fixture_bytes):
    url = "https://x.test/archive"
    page2 = "https://x.test/archive/page/2"
    source = make_source(
        id_="s", bank="x", kind="html", url=url,
        scope_prefixes=("https://x.test/monetary-policy/",),
        pagination_urls=(page2,),
    )
    from conftest import FakeSession

    routes = {
        url: response(fixture_bytes("html_listing.html"), url=url),
        page2: response(fixture_bytes("html_listing_page2.html"), url=page2),
    }
    pubs = create(source, make_client(FakeSession(routes))).discover()
    assert len(pubs) == 4
    assert "decision-2026-08-01" in pubs[0].url
    assert any("decision-2026-02-15" in p.url for p in pubs)


def test_html_filters_out_of_scope_and_contact():
    url = "https://x.test/archive"
    source = make_source(
        id_="s", bank="x", kind="html", url=url,
        scope_prefixes=("https://x.test/monetary-policy/",),
    )
    from conftest import FakeSession

    session = FakeSession({
        url: response(
            '<html><body><ul><li><a href="/monetary-policy/decision-a">A</a></li>'
            '<li><a href="/about/contact">Contact</a></li></ul></body></html>',
            url=url,
        ),
    })
    pubs = create(source, make_client(session)).discover()
    assert len(pubs) == 1
    assert "/monetary-policy/decision-a" in pubs[0].url


def test_html_scope_filter(fixture_bytes):
    url = "https://x.test/archive"
    source = make_source(
        id_="s", bank="x", kind="html", url=url,
        scope_prefixes=("https://x.test/monetary-policy/",),
    )
    from conftest import FakeSession

    routes = {
        url: response(_load("html_listing.html"), url=url),
    }
    pubs = create(source, make_client(FakeSession(routes))).discover()
    assert len(pubs) == 2
    assert all("/monetary-policy/" in p.url for p in pubs)


def test_html_calendar_captures_future_when_allowed(fixture_bytes):
    url = "https://x.test/calendar"
    source = make_source(
        id_="s", bank="x", kind="html", url=url,
        allow_future=True,
        title_from_url=True,
    )
    from conftest import FakeSession

    html = (
        '<html><body><ul>'
        '<li><span>December 15, 2026</span><a href="/monetary-policy/meeting-2026-q4">Q4 meeting</a></li>'
        '</ul></body></html>'
    )
    routes = {url: response(html, url=url)}
    now = datetime(2026, 8, 13, tzinfo=timezone.utc)
    pubs = create(source, make_client(FakeSession(routes)), now=lambda: now).discover()
    assert len(pubs) == 1
    assert pubs[0].publication_date.year == 2026
    assert pubs[0].publication_date.month == 12


def test_html_calendar_skips_future_when_not_allowed():
    url = "https://x.test/calendar"
    source = make_source(id_="s", bank="x", kind="html", url=url)
    from conftest import FakeSession

    html = (
        '<html><body><ul>'
        '<li><span>December 15, 2026</span><a href="/monetary-policy/meeting-2026-q4">Q4 meeting</a></li>'
        '</ul></body></html>'
    )
    routes = {url: response(html, url=url)}
    now = datetime(2026, 8, 13, tzinfo=timezone.utc)
    pubs = create(source, make_client(FakeSession(routes)), now=lambda: now).discover()
    assert pubs == []


def test_html_skips_document_links_by_default():
    url = "https://x.test/archive"
    source = make_source(
        id_="s", bank="x", kind="html", url=url,
        include=(r"press-conference-transcript",),
    )
    from conftest import FakeSession

    html = (
        '<html><body><ul>'
        '<li><a href="/media/transcripts/docs/press-conference-transcript-july-2026.pdf">Transcript (PDF)</a></li>'
        '</ul></body></html>'
    )
    pubs = create(source, make_client(FakeSession({url: response(html, url=url)}))).discover()
    assert pubs == []


def test_html_keep_documents_recovers_pdf_links():
    url = "https://x.test/archive"
    source = make_source(
        id_="s", bank="x", kind="html", url=url,
        include=(r"press-conference-transcript",),
        keep_documents=True,
        scope_prefixes=("https://x.test/media/",),
    )
    from conftest import FakeSession

    html = (
        '<html><body><ul>'
        '<li><a href="/media/transcripts/docs/press-conference-transcript-july-2026.pdf">Transcript (PDF)</a></li>'
        '<li><a href="/media/transcripts/docs/other-report-july-2026.pdf">Other report (PDF)</a></li>'
        '</ul></body></html>'
    )
    pubs = create(source, make_client(FakeSession({url: response(html, url=url)}))).discover()
    assert len(pubs) == 1
    assert pubs[0].url.endswith("/press-conference-transcript-july-2026.pdf")
    assert pubs[0].title == "Transcript (PDF)"