import os
from datetime import datetime, timezone

from argus.models import Document, DocumentStatus, Publication, PublicationStatus
from argus.store import ActiveCollectionError, Store


def make_pub(bank="fed", title="Statement", url=None, date=None, **kw):
    fields = dict(
        central_bank=bank,
        title=title,
        url=url or "https://x.test/pubs/statement",
        source_id="src",
        source_url="https://x.test/feed",
        publication_date=date,
    )
    fields.update(kw)
    return Publication(**fields)


def test_insert_and_reinsert_is_idempotent(tmp_path):
    store = Store(tmp_path / "s.db")
    pub = make_pub(date=datetime(2026, 7, 29, tzinfo=timezone.utc))
    first = store.upsert_publication(pub)
    second = store.upsert_publication(pub)
    assert first.id == second.id
    assert first.dedup_key == second.dedup_key
    assert first.first_seen_at == second.first_seen_at
    assert len(store.list_publications()) == 1


def test_same_document_via_different_sources_deduped(tmp_path):
    store = Store(tmp_path / "s.db")
    pub_a = make_pub(source_id="src_a", title="FOMC Statement")
    pub_b = make_pub(source_id="src_b", title="FOMC Statement")
    a = store.upsert_publication(pub_a)
    b = store.upsert_publication(pub_b)
    assert a.id == b.id
    assert store.list_publications().__len__() == 1


def test_dedup_url_variants(tmp_path):
    store = Store(tmp_path / "s.db")
    a = make_pub(url="https://x.test/p?b=2&a=1#frag")
    b = make_pub(url="https://x.test/p?a=1&b=2")
    assert store.upsert_publication(a).id == store.upsert_publication(b).id


def test_url_and_text_identity_merge(tmp_path):
    store = Store(tmp_path / "s.db")
    dated = datetime(2026, 7, 1, tzinfo=timezone.utc)
    by_url = make_pub(title="A title", url="https://x.test/p")
    by_text = make_pub(title="A title", url="", date=dated)
    first = store.upsert_publication(by_url)
    second = store.upsert_publication(by_text)
    assert first.id != second.id  # distinct identity forms
    assert len(store.list_publications()) == 2


def test_distinct_urls_are_distinct_publications(tmp_path):
    """The URL-dedup contract is documented and explicit: a publication is
    identified by its canonical URL, so two *different* URLs are two different
    publications even when they serve the same physical content. Semantic
    deduplication is deliberately out of scope; a source that knows two URLs are
    the same publication must supply `dedup_key`/`canonical_url` explicitly."""
    store = Store(tmp_path / "s.db")
    a = store.upsert_publication(make_pub(title="Statement", url="https://x.test/pubs/stmt"))
    b = store.upsert_publication(make_pub(title="Statement", url="https://x.test/files/stmt.pdf"))
    assert a.id != b.id
    assert len(store.list_publications()) == 2


def test_explicit_canonical_url_coalesces_distinct_sources(tmp_path):
    """A source that supplies an explicit `canonical_url`/`dedup_key` opts into
    URL-dedup across otherwise-different URLs."""
    store = Store(tmp_path / "s.db")
    a = make_pub(title="Same", url="https://x.test/a")
    b = make_pub(title="Same", url="https://x.test/b")
    b.dedup_key = store._dedup(a)
    store.upsert_publication(a)
    store.upsert_publication(b)
    assert len(store.list_publications()) == 1


def test_extra_and_document_urls_union(tmp_path):
    store = Store(tmp_path / "s.db")
    first = store.upsert_publication(
        make_pub(extra={"k": 1}, document_urls=("https://x.test/a.pdf",))
    )
    second = store.upsert_publication(
        make_pub(extra={"k2": 2}, document_urls=("https://x.test/a.pdf", "https://x.test/b.pdf"))
    )
    assert set(second.document_urls) == {"https://x.test/a.pdf", "https://x.test/b.pdf"}
    assert second.extra["k"] == 1 and second.extra["k2"] == 2


def test_fetched_status_retained_on_rediscovery(tmp_path):
    store = Store(tmp_path / "s.db")
    pub = store.upsert_publication(make_pub())
    store.set_publication_status(pub.id, PublicationStatus.FETCHED)
    re = store.upsert_publication(make_pub())
    assert re.status == PublicationStatus.FETCHED


def test_documents_crud(tmp_path):
    store = Store(tmp_path / "s.db")
    pub = store.upsert_publication(make_pub())
    doc = store.upsert_document(
        Document(
            publication_id=pub.id,
            url="https://x.test/p.pdf",
            kind="pdf",
            status=DocumentStatus.FETCHED,
            sha256="abc",
            size=10,
        )
    )
    assert doc.id is not None
    assert store.document_count(pub.id) == 1
    assert store.get_document(pub.id, "https://x.test/p.pdf").sha256 == "abc"
    store.upsert_document(
        Document(
            publication_id=pub.id,
            url="https://x.test/p.pdf",
            kind="pdf",
            status=DocumentStatus.FETCHED,
            sha256="def",
            size=11,
        )
    )
    assert store.get_document(pub.id, "https://x.test/p.pdf").sha256 == "def"
    assert store.document_count(pub.id) == 1


def test_list_filters(tmp_path):
    store = Store(tmp_path / "s.db")
    store.upsert_publication(make_pub(bank="fed", title="a"))
    store.upsert_publication(make_pub(bank="ecb", title="b"))
    assert len(store.list_publications(bank="fed")) == 1
    assert len(store.list_publications(bank="zzz")) == 0


def test_list_publications_date_bounds(tmp_path):
    store = Store(tmp_path / "s.db")
    store.upsert_publication(make_pub(title="jun", url="https://x.test/jun", date=datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)))
    store.upsert_publication(make_pub(title="jul_01", url="https://x.test/jul01", date=datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)))
    store.upsert_publication(make_pub(title="jul_31", url="https://x.test/jul31", date=datetime(2026, 7, 31, 23, 59, tzinfo=timezone.utc)))
    store.upsert_publication(make_pub(title="aug", url="https://x.test/aug", date=datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)))
    store.upsert_publication(make_pub(title="nodate", url="https://x.test/nodate", date=None))

    july = store.list_publications(
        date_start=datetime(2026, 7, 1, tzinfo=timezone.utc),
        date_end=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    assert sorted(p.title for p in july) == ["jul_01", "jul_31"]

    year = store.list_publications(
        date_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        date_end=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )
    assert sorted(p.title for p in year) == ["aug", "jul_01", "jul_31", "jun"]

    none_bounded = store.list_publications(
        date_start="2026-07-01T00:00:00+00:00",
        date_end="2026-08-01T00:00:00+00:00",
    )
    assert all("nodate" != p.title for p in none_bounded)


def test_date_bounds_exclude_undated(tmp_path):
    store = Store(tmp_path / "s.db")
    store.upsert_publication(make_pub(title="undated", date=None))
    store.upsert_publication(make_pub(title="dated", date=datetime(2026, 7, 10, tzinfo=timezone.utc)))
    found = store.list_publications(
        date_start=datetime(2026, 7, 1, tzinfo=timezone.utc),
        date_end=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    assert [p.title for p in found] == ["dated"]


def test_normalized_document_pages_round_trip(tmp_path):
    from argus.documents.base import DocumentPage, NormalizedDocument

    store = Store(tmp_path / "s.db")
    pub = store.upsert_publication(make_pub())
    doc = NormalizedDocument(
        publication_id=pub.id,
        document_id="doc-1",
        source_url="https://x.test/report.pdf",
        local_path="/raw/report.pdf",
        document_kind="pdf",
        mime_type="application/pdf",
        title="Report",
        text="page one\npage two",
        pages=[DocumentPage(number=1, text="page one"), DocumentPage(number=2, text="page two")],
        extraction_method="pdf_text",
    )
    store.upsert_normalized_document(doc)
    restored = store.get_normalized_document("doc-1")
    assert restored is not None
    assert [(p.number, p.text) for p in restored.pages] == [(1, "page one"), (2, "page two")]
    assert restored.publication_id == pub.id
    assert restored.extraction_method == "pdf_text"
    # idempotent re-save keeps a single row
    store.upsert_normalized_document(doc)
    assert store.get_normalized_document("doc-1") is not None


def test_collection_run_lifecycle(tmp_path):
    store = Store(tmp_path / "s.db")
    store.start_collection_run("c1", ["fed"], pid=42, publications_total=3)
    run = store.get_collection_run("c1")
    assert run["status"] == "running"
    assert run["pid"] == 42
    assert run["banks"] == ["fed"]
    assert run["publications_total"] == 3
    assert run["publications_completed"] == 0

    store.set_collection_progress("c1", completed=1, total=3)
    assert store.get_collection_run("c1")["publications_completed"] == 1

    store.finish_collection_run("c1", status="completed")
    run = store.get_collection_run("c1")
    assert run["status"] == "completed"
    assert run["finished_at"] is not None
    assert store.latest_collection_run()["run_id"] == "c1"


def test_start_collection_run_enforces_single_active(tmp_path):
    store = Store(tmp_path / "s.db")
    store.start_collection_run("c-a", ["fed"], pid=1)
    try:
        store.start_collection_run("c-b", ["ecb"], pid=2)
    except ActiveCollectionError as exc:
        assert "c-a" in str(exc)
    else:
        raise AssertionError("a second active collection campaign must be refused")
    assert store.latest_collection_run()["run_id"] == "c-a"
    assert store.get_collection_run("c-b") is None


def test_collection_run_releases_active_after_finish(tmp_path):
    store = Store(tmp_path / "s.db")
    store.start_collection_run("c-x", ["fed"], pid=1)
    store.finish_collection_run("c-x", status="cancelled")
    store.start_collection_run("c-y", ["ecb"], pid=2)  # no longer active
    assert store.latest_collection_run()["run_id"] == "c-y"


def test_start_collection_run_self_adoption_same_run_id(tmp_path):
    """A second claim of the *same* run_id is the detached subprocess adopting
    the launcher's pre-registered row (collection-run-begin), not a competing
    campaign: it updates the row in place, never raising ActiveCollectionError."""
    store = Store(tmp_path / "s.db")
    store.start_collection_run("c-begin", [], pid=4242, publications_total=0)
    begun = store.get_collection_run("c-begin")
    assert begun["status"] == "running"
    assert begun["pid"] == 4242
    assert begun["banks"] == []

    store.start_collection_run("c-begin", ["fed"], pid=os.getpid(), publications_total=3)
    adopted = store.get_collection_run("c-begin")
    assert adopted["status"] == "running"
    assert adopted["pid"] == os.getpid()
    assert adopted["banks"] == ["fed"]
    assert adopted["publications_total"] == 3
    assert store.latest_collection_run()["run_id"] == "c-begin"


def test_start_collection_run_ab_isolation(tmp_path):
    """A stale run A must never adopt run B's pre-registered row (nor a fresh
    begin claim run B while a *different* A is active)."""
    store = Store(tmp_path / "s.db")
    store.start_collection_run("A", ["fed"], pid=1)
    store.finish_collection_run("A", status="completed")

    store.start_collection_run("B", [], pid=4242, publications_total=0)  # begin B
    assert store.get_collection_run("B")["status"] == "running"

    # A's stale subprocess trying to re-claim run A while B is active: refused.
    try:
        store.start_collection_run("A", ["fed"], pid=2)
    except ActiveCollectionError as exc:
        assert "B" in str(exc)
    else:
        raise AssertionError("run A must not adopt while B holds the active slot")
    assert store.get_collection_run("A")["status"] == "completed"  # untouched


