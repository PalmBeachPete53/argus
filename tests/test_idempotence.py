from argus.models import PublicationStatus
from argus.discovery import create
from argus.fetcher import Fetcher


def test_repeated_runs_do_not_duplicate(tmp_path):
    from conftest import FakeSession, make_client, make_source, response

    url = "https://x.test/feed.xml"
    body = (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>'
        "<item><title>MPS</title><link>https://x.test/pubs/mps-1</link>"
        "<pubDate>Wed, 29 Jul 2026 14:00:00 -0400</pubDate></item>"
        "</channel></rss>"
    )
    from argus.store import Store

    store = Store(tmp_path / "s.db")
    client = make_client(FakeSession({url: response(body, url=url, content_type="text/xml")}))
    source = make_source(id_="s", bank="bank", url=url)

    def run():
        pubs = create(source, client).discover()
        return [store.upsert_publication(p) for p in pubs]

    first = run()
    second = run()
    third = run()
    assert len(store.list_publications()) == 1
    assert first[0].id == second[0].id == third[0].id
    assert second[0].first_seen_at == first[0].first_seen_at


def test_discovery_to_fetch_full_cycle_is_idempotent(tmp_path):
    from conftest import FakeSession, make_client, make_source, response

    feed_url = "https://x.test/feed.xml"
    page_url = "https://x.test/pubs/mps-1"
    feed_body = (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>'
        f"<item><title>MPS</title><link>{page_url}</link>"
        "<pubDate>Wed, 29 Jul 2026 14:00:00 -0400</pubDate></item>"
        "</channel></rss>"
    )
    session = FakeSession({
        feed_url: response(feed_body, url=feed_url, content_type="text/xml"),
        page_url: response("<html><body>Body</body></html>", url=page_url),
    })
    from argus.store import Store

    store = Store(tmp_path / "s.db")
    client = make_client(session)
    source = make_source(id_="s", bank="bank", url=feed_url)
    fetcher = Fetcher(client, store, tmp_path / "raw")

    publications = create(source, client).discover()
    persisted = store.upsert_publication(publications[0])
    r1 = fetcher.fetch(persisted)
    docs1 = list(store.list_documents(r1.publication_id))
    count1 = len(session.calls)
    r2 = fetcher.fetch(persisted)
    assert store.document_count(r2.publication_id) == len(docs1)
    assert len(session.calls) == count1
    assert store.list_publications().__len__() == 1
    assert store.get_publication(r2.publication_id).status == PublicationStatus.FETCHED