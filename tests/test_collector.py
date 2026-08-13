from argus.adapters.base import BankAdapter, rss_source
from argus.collector import CentralBankCollector
from argus.models import CentralBank, Publication, PublicationStatus
from conftest import BANK, FakeSession, make_client, make_store, response

FED_FIXTURE = "fed_press_monetary.xml"
FED_URL = "https://www.federalreserve.gov/feeds/press_monetary.xml"


class FakeFedAdapter(BankAdapter):
    def _build(self):
        bank = CentralBank("fed", "Federal Reserve", "USD", "federalreserve.gov")
        sources = [rss_source("fed_rss", "fed", "Fed RSS", FED_URL, priority=1)]
        return bank, sources


def build_collector(tmp_path, session, registry=None):
    store = make_store(tmp_path)
    collector = CentralBankCollector(
        store=store,
        registry=registry,
        client=make_client(session),
        raw_root=tmp_path / "raw",
    )
    return collector, store


def test_discover_all_persists_publications(tmp_path, fixture_bytes):
    session = FakeSession({FED_URL: response(fixture_bytes(FED_FIXTURE), url=FED_URL, content_type="application/xml")})
    collector, store = build_collector(tmp_path, session)
    publications = collector.discover_all()
    assert len(publications) == 2
    assert all(p.id and p.dedup_key for p in publications)
    assert all(p.central_bank == "fed" for p in publications)
    assert len(store.list_publications()) == 2


def test_run_twice_is_idempotent(tmp_path, fixture_bytes):
    from argus.registry import SourceRegistry

    session = FakeSession({FED_URL: response(fixture_bytes(FED_FIXTURE), url=FED_URL, content_type="application/xml")})
    collector, store = build_collector(tmp_path, session, registry=SourceRegistry(adapters=[FakeFedAdapter()]))
    result1 = collector.run()
    assert len(result1.publications) == 2
    assert len(result1.errors) == 0
    result2 = collector.run()
    assert len(result2.publications) == 2
    assert len(store.list_publications()) == 2


def test_error_of_one_bank_does_not_block_others(tmp_path, fixture_bytes):
    from argus.discovery.base import DiscoveryStrategy
    from argus.errors import DiscoveryError
    from argus.models import DiscoverySpec

    class ExplodingDiscovery(DiscoveryStrategy):
        kind = "explode"

        def discover(self):
            raise DiscoveryError(self.source.id, self.kind, self.spec.url, "boom")

    class ExplodingAdapter(BankAdapter):
        def _build(self):
            bank = CentralBank("bad", "Bad Bank", "XXX", "bad.example")
            source = rss_source("bad_rss", "bad", "Bad RSS", "https://bad.example/feed.xml", priority=1)
            from argus.models import Source as SourceModel

            exploded = SourceModel(
                id=source.id,
                central_bank=source.central_bank,
                name=source.name,
                discovery=DiscoverySpec(kind="explode", url=source.discovery.url),
                priority=source.priority,
            )
            return bank, [exploded]

    from argus.discovery import STRATEGIES

    original_explode = STRATEGIES.get("explode")
    STRATEGIES["explode"] = ExplodingDiscovery
    try:
        session = FakeSession({FED_URL: response(fixture_bytes(FED_FIXTURE), url=FED_URL, content_type="application/xml")})
        from argus.registry import SourceRegistry

        registry = SourceRegistry(adapters=[FakeFedAdapter(), ExplodingAdapter()])
        store = make_store(tmp_path)
        collector = CentralBankCollector(
            store=store,
            registry=registry,
            client=make_client(session),
            raw_root=tmp_path / "raw",
        )
        publications = collector.discover_all()
        fed_pubs = [p for p in publications if p.central_bank == "fed"]
        assert len(fed_pubs) == 2
        errors = store.list_errors()
        assert len(errors) >= 1
        bad = [e for e in errors if e.source_id == "bad_rss"]
        assert bad and bad[0].strategy == "explode"
        assert bad[0].url == "https://bad.example/feed.xml"
    finally:
        if original_explode is None:
            STRATEGIES.pop("explode", None)
        else:
            STRATEGIES["explode"] = original_explode


def test_fetch_all_only_fetches_discovered(tmp_path, fixture_bytes):
    page_url = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm"
    page2 = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260617a.htm"
    session = FakeSession({
        FED_URL: response(fixture_bytes(FED_FIXTURE), url=FED_URL, content_type="application/xml"),
        page_url: response("<html><body><h1>Statement</h1></body></html>", url=page_url),
        page2: response("<html><body><h1>Statement 2</h1></body></html>", url=page2),
    })
    collector, store = build_collector(tmp_path, session)
    collector.discover_all()
    results = collector.fetch_all()
    assert len(results) == 2
    fetched = store.list_publications(statuses=(PublicationStatus.FETCHED,))
    assert len(fetched) == 2
    assert all(d.local_path for d in collector.store.list_documents(fetched[0].id))