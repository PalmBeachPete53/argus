from datetime import datetime, timezone

from argus.adapters.base import BankAdapter, rss_source
from argus.collector import CentralBankCollector
from argus.errors import TransportError
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


def test_discover_all_applies_date_window_before_persisting(tmp_path, fixture_bytes):
    """The Core applies the publication-date window itself (start-inclusive,
    end-exclusive): out-of-window publications never enter the store."""
    from conftest import make_store, make_client

    session = FakeSession({FED_URL: response(fixture_bytes(FED_FIXTURE), url=FED_URL, content_type="application/xml")})
    collector, store = build_collector(tmp_path, session)
    window = collector.discover_all(
        date_start=datetime(2026, 7, 1, tzinfo=timezone.utc),
        date_end=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    assert len(window) == 1  # fixture holds 2026-06-17 and 2026-07-29
    assert window[0].publication_date.date() == datetime(2026, 7, 29).date()
    assert [p.id for p in store.list_publications()] == [window[0].id]
    assert store.count_publications() == 1

    # without bounds the behaviour is unchanged: every discovery is persisted
    clean_session = FakeSession({FED_URL: response(fixture_bytes(FED_FIXTURE), url=FED_URL, content_type="application/xml")})
    store2 = make_store(tmp_path / "nowindow")
    collector2 = CentralBankCollector(
        store=store2,
        client=make_client(clean_session),
        raw_root=tmp_path / "nowindow" / "raw",
    )
    pub2 = collector2.discover_all()
    assert len(pub2) == 2
    assert store2.count_publications() == 2


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


def test_fetch_all_skips_unchanged_fetched_publications(tmp_path, fixture_bytes):
    page1 = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm"
    page2 = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260617a.htm"
    session = FakeSession({
        FED_URL: response(fixture_bytes(FED_FIXTURE), url=FED_URL, content_type="application/xml"),
        page1: response("<html><body><h1>Statement</h1></body></html>", url=page1),
        page2: response("<html><body><h1>Statement 2</h1></body></html>", url=page2),
    })
    collector, store = build_collector(tmp_path, session)
    collector.discover_all()
    collector.fetch_all()
    assert len(store.list_publications(statuses=(PublicationStatus.FETCHED,))) == 2
    calls_before = len(session.calls)
    results = collector.fetch_all()
    assert len(results) == 0
    assert len(session.calls) == calls_before


def test_updated_publication_is_refetched_and_returned_to_fetched(tmp_path, fixture_bytes):
    page1 = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm"
    page2 = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260617a.htm"
    report_pdf = "https://www.federalreserve.gov/files/report.pdf"
    session = FakeSession({
        FED_URL: response(fixture_bytes(FED_FIXTURE), url=FED_URL, content_type="application/xml"),
        page1: response("<html><body><h1>Statement</h1></body></html>", url=page1),
        page2: response("<html><body><h1>Statement 2</h1></body></html>", url=page2),
        report_pdf: response(b"%PDF-1.4 fake", url=report_pdf, content_type="application/pdf"),
    })
    collector, store = build_collector(tmp_path, session)
    collector.discover_all()
    collector.fetch_all()
    assert len(store.list_publications(statuses=(PublicationStatus.FETCHED,))) == 2

    # A rediscovery with changed metadata turns a FETCHED publication into UPDATED.
    changed = store.upsert_publication(Publication(
        central_bank="fed",
        title="Federal Reserve issues FOMC statement (revised)",
        url=page1,
        source_id="fed_rss",
        source_url=FED_URL,
        publication_date=datetime(2026, 7, 29, tzinfo=timezone.utc),
        document_urls=(page1, report_pdf),
    ))
    assert changed.status == PublicationStatus.UPDATED

    calls_before = len(session.calls)
    results = collector.fetch_all()
    assert len(results) == 1
    assert results[0].publication_id == changed.id
    after = store.get_publication(changed.id)
    assert after.status == PublicationStatus.FETCHED
    assert len(session.calls) > calls_before  # the new linked document was fetched

    docs = store.list_documents(changed.id)
    assert {d.url for d in docs} == {page1, report_pdf}
    assert store.document_count(changed.id) == 2  # no duplicate for the unchanged page

    # idempotence: a second pass does nothing and creates nothing.
    calls_before2 = len(session.calls)
    results2 = collector.fetch_all()
    assert len(results2) == 0
    assert len(session.calls) == calls_before2
    assert store.document_count(changed.id) == 2
    assert len(store.list_publications()) == 2


def test_run_single_run_id_shared_by_discover_and_fetch(tmp_path, fixture_bytes):
    from argus.discovery import STRATEGIES
    from argus.discovery.base import DiscoveryStrategy
    from argus.errors import DiscoveryError
    from argus.models import DiscoverySpec
    from argus.registry import SourceRegistry

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

    page1 = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm"
    original_explode = STRATEGIES.get("explode")
    STRATEGIES["explode"] = ExplodingDiscovery
    try:
        session = FakeSession({
            FED_URL: response(fixture_bytes(FED_FIXTURE), url=FED_URL, content_type="application/xml"),
            page1: TransportError(page1, "connection reset"),
        })
        registry = SourceRegistry(adapters=[FakeFedAdapter(), ExplodingAdapter()])
        store = make_store(tmp_path)
        collector = CentralBankCollector(
            store=store,
            registry=registry,
            client=make_client(session),
            raw_root=tmp_path / "raw",
        )
        result = collector.run()
        assert result.run_id
        assert len(result.errors) >= 1
        stored = store.list_errors()
        assert stored
        assert all(e.run_id == result.run_id for e in stored)
        assert all(e.run_id == result.run_id for e in result.errors)
    finally:
        if original_explode is None:
            STRATEGIES.pop("explode", None)
        else:
            STRATEGIES["explode"] = original_explode


def test_direct_calls_respect_explicit_run_id(tmp_path, fixture_bytes):
    from argus.discovery import STRATEGIES
    from argus.discovery.base import DiscoveryStrategy
    from argus.errors import DiscoveryError
    from argus.models import DiscoverySpec
    from argus.registry import SourceRegistry

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

    original_explode = STRATEGIES.get("explode")
    STRATEGIES["explode"] = ExplodingDiscovery
    try:
        session = FakeSession({FED_URL: response(fixture_bytes(FED_FIXTURE), url=FED_URL, content_type="application/xml")})
        registry = SourceRegistry(adapters=[FakeFedAdapter(), ExplodingAdapter()])
        store = make_store(tmp_path)
        collector = CentralBankCollector(
            store=store,
            registry=registry,
            client=make_client(session),
            raw_root=tmp_path / "raw",
        )
        collector.discover_all(run_id="discover-1")
        errors = store.list_errors()
        assert errors
        assert all(e.run_id == "discover-1" for e in errors)
    finally:
        if original_explode is None:
            STRATEGIES.pop("explode", None)
        else:
            STRATEGIES["explode"] = original_explode