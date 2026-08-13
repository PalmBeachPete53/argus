import pytest

from argus.adapters import ALL_ADAPTERS
from argus.adapters.boc import BoCAdapter
from argus.adapters.boe import BoEAdapter
from argus.adapters.boj import BoJAdapter
from argus.adapters.ecb import ECBAdapter
from argus.adapters.fed import FedAdapter
from argus.adapters.norges import NorgesBankAdapter
from argus.adapters.rba import RBAAdapter
from argus.adapters.rbnz import RBNZAdapter
from argus.adapters.riksbank import RiksbankAdapter
from argus.adapters.snb import SNBAdapter
from argus.discovery import create
from conftest import FakeSession, make_client, response

PRIMARY_FIXTURES = {
    "fed": ("fed_press_monetary.xml", "monetary20260729a"),
    "ecb": ("ecb_press.xml", "mp260703"),
    "boe": ("boe_news.xml", "july-2026"),
    "boj": ("boj_whatsnew.xml", "statement_20260731"),
    "snb": ("snb_mopo.xml", "pre_20260619"),
    "boc": ("boc_press.xml", "fad-press-release"),
    "rba": ("rba_media.xml", "mr-26-19"),
    "norges": ("norges_press.xml", "august-2026"),
    "riksbank": ("riksbank_press.xml", "june-2026"),
}

ADAPTER_CLASSES = {
    "fed": FedAdapter,
    "ecb": ECBAdapter,
    "boe": BoEAdapter,
    "boj": BoJAdapter,
    "snb": SNBAdapter,
    "boc": BoCAdapter,
    "rba": RBAAdapter,
    "norges": NorgesBankAdapter,
    "riksbank": RiksbankAdapter,
}


def _primary_source(adapter):
    return sorted(adapter.sources, key=lambda s: s.priority)[0]


@pytest.mark.parametrize("bank_id", list(PRIMARY_FIXTURES))
def test_adapter_primary_source_discovery(bank_id, fixture_bytes):
    adapter = ADAPTER_CLASSES[bank_id]()
    source = _primary_source(adapter)
    assert source.discovery.kind == "rss"
    fixture_name, fragment = PRIMARY_FIXTURES[bank_id]
    session = FakeSession({
        source.discovery.url: response(fixture_bytes(fixture_name), url=source.discovery.url, content_type="application/xml"),
    })
    publications = create(source, make_client(session)).discover()
    assert len(publications) >= 2
    assert any(fragment in p.url for p in publications)
    for pub in publications:
        assert pub.central_bank == bank_id
        assert pub.source_id == source.id
        assert pub.source_url == source.discovery.url
        assert pub.title
        assert pub.publication_date is not None


def test_rbnz_html_primary_discovery(fixture_bytes):
    adapter = RBNZAdapter()
    source = _primary_source(adapter)
    assert source.discovery.kind == "html"
    session = FakeSession({
        source.discovery.url: response(fixture_bytes("rbnz_decisions.html"), url=source.discovery.url),
    })
    publications = create(source, make_client(session)).discover()
    assert len(publications) == 2
    assert publications[0].central_bank == "rbnz"
    assert "monetary-policy-statement-july-2026" in publications[0].url
    assert publications[0].publication_date is not None


def test_snb_enclosure_recorded(fixture_bytes):
    adapter = SNBAdapter()
    source = _primary_source(adapter)
    session = FakeSession({
        source.discovery.url: response(fixture_bytes("snb_mopo.xml"), url=source.discovery.url, content_type="application/xml"),
    })
    publications = create(source, make_client(session)).discover()
    assert publications[0].document_urls
    assert publications[0].document_urls[0].endswith("/source")


def test_fed_calendar_fallback(fixture_bytes):
    adapter = FedAdapter()
    source = adapter.source("fed_fomc_calendar") if hasattr(adapter, "source") else next(
        s for s in adapter.sources if s.id == "fed_fomc_calendar"
    )
    session = FakeSession({
        source.discovery.url: response(fixture_bytes("fed_calendar.html"), url=source.discovery.url),
    })
    publications = create(source, make_client(session)).discover()
    assert len(publications) == 6
    for pub in publications:
        assert pub.central_bank == "fed"
        assert "/monetarypolicy/" in pub.url or "/newsevents/" in pub.url
    dated = [p for p in publications if p.publication_date is not None]
    assert dated


def test_boj_html_archive_fallback(fixture_bytes):
    adapter = BoJAdapter()
    source = next(s for s in adapter.sources if s.id == "boj_mopo_archive")
    routes = {
        source.discovery.url: response(fixture_bytes("boj_archive.html"), url=source.discovery.url),
    }
    for page in source.discovery.pagination_urls:
        routes[page] = response(fixture_bytes("boj_archive.html"), url=page)
    publications = create(source, make_client(FakeSession(routes))).discover()
    urls = [p.url for p in publications]
    assert len(urls) == 4
    assert len(set(urls)) == 4
    assert all("/mopo/" in p.url for p in publications)
    assert all("mpmsche_minu" not in p.url for p in publications)


def test_ecb_sitemap_fallback(fixture_bytes):
    adapter = ECBAdapter()
    source = next(s for s in adapter.sources if s.id == "ecb_sitemap_monetary")
    session = FakeSession({
        source.discovery.url: response(fixture_bytes("ecb_sitemap.xml"), url=source.discovery.url, content_type="application/xml"),
    })
    publications = create(source, make_client(session)).discover()
    assert len(publications) == 3
    assert all("/press/" in p.url for p in publications)
    assert all("careers" not in p.url for p in publications)


def test_all_adapters_are_exported():
    assert len(ALL_ADAPTERS) == 10
    assert {a.bank.id for a in ALL_ADAPTERS} == {
        "fed", "ecb", "boe", "boj", "snb", "boc", "rba", "rbnz", "norges", "riksbank",
    }