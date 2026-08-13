from datetime import datetime, timezone

import pytest

from argus.errors import TransportError
from argus.fetcher import Fetcher
from argus.models import DocumentStatus, Publication, PublicationStatus
from conftest import FakeResponse, FakeSession, make_client, make_store, response


def pub() -> Publication:
    return Publication(
        central_bank="fed",
        title="FOMC Statement",
        url="https://x.test/monetarypolicy/statement.htm",
        source_id="src",
        source_url="https://x.test/feed",
        publication_date=datetime(2026, 7, 29, tzinfo=timezone.utc),
    )


def page_html() -> str:
    return (
        '<html><body><h1>Statement</h1>'
        '<a href="/files/report.pdf">Full report (PDF)</a>'
        '<a href="/files/data.xlsx">Data (XLSX)</a>'
        '<a href="/about/contact">Contact</a>'
        '</body></html>'
    )


def routes():
    page = "https://x.test/monetarypolicy/statement.htm"
    pdf = "https://x.test/files/report.pdf"
    xlsx = "https://x.test/files/data.xlsx"
    return {
        page: response(page_html(), url=page),
        pdf: response(b"%PDF-1.4 fake", url=pdf, content_type="application/pdf"),
        xlsx: response(b"PKfake", url=xlsx, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    }


def test_fetch_page_and_linked_documents(tmp_path):
    store = make_store(tmp_path)
    persisted = store.upsert_publication(pub())
    client = make_client(FakeSession(routes()))
    fetcher = Fetcher(client, store, tmp_path / "raw")
    result = fetcher.fetch(persisted)
    assert result.ok
    assert len(result.documents) == 3
    assert all(d.status == DocumentStatus.FETCHED for d in result.documents)
    kinds = {d.kind for d in result.documents}
    assert "pdf" in kinds and "xlsx" in kinds and "html" in kinds
    assert all(d.local_path and d.sha256 for d in result.documents)
    for d in result.documents:
        assert (tmp_path / "raw" / d.local_path.split("raw/", 1)[1]).exists()
    updated = store.get_publication(persisted.id)
    assert updated.status == PublicationStatus.FETCHED


def test_fetch_idempotent_no_extra_network(tmp_path):
    store = make_store(tmp_path)
    persisted = store.upsert_publication(pub())
    session = FakeSession(routes())
    client = make_client(session)
    fetcher = Fetcher(client, store, tmp_path / "raw")
    first = fetcher.fetch(persisted)
    call_count = len(session.calls)
    fetcher.fetch(persisted)
    assert len(session.calls) == call_count
    assert store.document_count(persisted.id) == len(first.documents)


def test_fetch_partial_on_failed_document(tmp_path):
    store = make_store(tmp_path)
    persisted = store.upsert_publication(pub())
    page = "https://x.test/monetarypolicy/statement.htm"
    pdf = "https://x.test/files/report.pdf"
    xlsx = "https://x.test/files/data.xlsx"
    session = FakeSession({
        page: response(page_html(), url=page),
        pdf: TransportError(pdf, "connection refused"),
        xlsx: response(b"PK", url=xlsx, content_type="application/octet-stream"),
    })
    fetcher = Fetcher(make_client(session), store, tmp_path / "raw")
    result = fetcher.fetch(persisted)
    assert result.ok is False
    failed = [d for d in result.documents if d.status == DocumentStatus.FAILED]
    assert len(failed) == 1
    assert failed[0].url == pdf
    assert store.get_publication(persisted.id).status == PublicationStatus.PARTIAL


def test_fetch_force_refreshes_content(tmp_path):
    store = make_store(tmp_path)
    persisted = store.upsert_publication(pub())
    session = FakeSession(routes())
    fetcher = Fetcher(make_client(session), store, tmp_path / "raw")
    fetcher.fetch(persisted)
    old_sha = store.get_document(persisted.id, "https://x.test/files/report.pdf").sha256
    session.routes["https://x.test/files/report.pdf"] = response(
        b"%PDF-1.4 changed", url="https://x.test/files/report.pdf", content_type="application/pdf"
    )
    fetcher.fetch(persisted, force=True)
    new_sha = store.get_document(persisted.id, "https://x.test/files/report.pdf").sha256
    assert new_sha != old_sha


def test_fetch_without_target_leaves_discovered(tmp_path):
    store = make_store(tmp_path)
    no_url = pub()
    no_url.url = ""
    persisted = store.upsert_publication(no_url)
    fetcher = Fetcher(make_client(FakeSession({})), store, tmp_path / "raw")
    result = fetcher.fetch(persisted)
    assert result.ok is False
    assert store.get_publication(persisted.id).status == PublicationStatus.DISCOVERED


def test_fetcher_records_provenance_path(tmp_path):
    store = make_store(tmp_path)
    persisted = store.upsert_publication(pub())
    session = FakeSession(routes())
    fetcher = Fetcher(make_client(session), store, tmp_path / "raw")
    result = fetcher.fetch(persisted)
    html_doc = next(d for d in result.documents if d.kind == "html")
    assert html_doc.local_path.startswith(f"{tmp_path}/raw/fed/2026/07/")
    assert (tmp_path / "raw" / "fed" / "2026" / "07").is_dir()