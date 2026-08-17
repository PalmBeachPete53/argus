from datetime import datetime, timezone
from pathlib import Path

import pytest

from argus.errors import InvalidDocumentContent, TransportError
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


# ---------------------------------------------------------------------------
# Content validation (🟠 audit finding #5) and atomic writes (#6)
# ---------------------------------------------------------------------------

def _fetch_one(tmp_path, session, target_url=None):
    """Fetch a single-url publication (the session's first route by default)."""
    store = make_store(tmp_path)
    target = target_url or next(iter(session.routes))
    the_pub = pub()
    the_pub.url = target
    the_pub.document_urls = (target,)
    persisted = store.upsert_publication(the_pub)
    fetcher = Fetcher(make_client(session), store, tmp_path / "raw")
    result = fetcher.fetch(persisted)
    return store, persisted, fetcher, result


def test_empty_200_body_is_rejected(tmp_path):
    store, persisted, fetcher, result = _fetch_one(
        tmp_path,
        FakeSession({"https://x.test/p.pdf": response(b"", url="https://x.test/p.pdf", content_type="application/pdf")}),
    )
    assert result.ok is False
    doc = store.get_document(persisted.id, "https://x.test/p.pdf")
    assert doc.status == DocumentStatus.FAILED
    assert "InvalidDocumentContent" in doc.error
    assert store.get_publication(persisted.id).status == PublicationStatus.FAILED


def test_html_challenge_page_is_rejected(tmp_path):
    challenge = (
        "<html><head><title>Just a moment...</title></head><body>"
        "Checking your browser before accessing. Please enable JavaScript and "
        "cookies to continue.</body></html>"
    )
    store, persisted, fetcher, result = _fetch_one(
        tmp_path,
        FakeSession({"https://x.test/p.pdf": response(challenge, url="https://x.test/p.pdf", content_type="text/html")}),
    )
    assert result.ok is False
    doc = store.get_document(persisted.id, "https://x.test/p.pdf")
    assert doc.status == DocumentStatus.FAILED
    assert "challenge" in doc.error
    assert store.get_publication(persisted.id).status == PublicationStatus.FAILED


def test_html_for_declared_pdf_content_type_rejected(tmp_path):
    """A `.pdf` URL answered with HTML — even when the server *declares*
    application/pdf — is rejected: the URL asked for a binary document and the
    bytes are HTML."""
    store, persisted, fetcher, result = _fetch_one(
        tmp_path,
        FakeSession({"https://x.test/p.pdf": response("<html><body>error</body></html>", url="https://x.test/p.pdf", content_type="application/pdf")}),
    )
    assert result.ok is False
    doc = store.get_document(persisted.id, "https://x.test/p.pdf")
    assert doc.status == DocumentStatus.FAILED
    assert "HTML page returned for a binary document URL" in doc.error
    assert store.get_publication(persisted.id).status == PublicationStatus.FAILED


def test_octet_stream_with_valid_bytes_accepted(tmp_path):
    """A server using an imprecise MIME (application/octet-stream) for a real
    PDF is accepted — content validation never rejects on MIME alone."""
    store, persisted, fetcher, result = _fetch_one(
        tmp_path,
        FakeSession({"https://x.test/p.pdf": response(b"%PDF-1.4 real", url="https://x.test/p.pdf", content_type="application/octet-stream")}),
    )
    assert result.ok is True
    doc = store.get_document(persisted.id, "https://x.test/p.pdf")
    assert doc.status == DocumentStatus.FETCHED
    assert store.get_publication(persisted.id).status == PublicationStatus.FETCHED


def test_pdf_url_with_html_mime_generic_page_rejected(tmp_path):
    """The exact problematic case: `https://.../statement.pdf` answered with
    `Content-Type: text/html` and a generic HTML body. Previously the MIME
    classified the response as `html` and (without challenge markers) it could
    be persisted; now the URL's document extension is authoritative and the
    response is rejected."""
    store, persisted, fetcher, result = _fetch_one(
        tmp_path,
        FakeSession({"https://x.test/statement.pdf": response("<html><body>This is an error page, not a document.</body></html>", url="https://x.test/statement.pdf", content_type="text/html")}),
    )
    assert result.ok is False
    doc = store.get_document(persisted.id, "https://x.test/statement.pdf")
    assert doc.status == DocumentStatus.FAILED
    assert "HTML page returned for a binary document URL" in doc.error
    assert store.get_publication(persisted.id).status == PublicationStatus.FAILED
    assert doc.local_path is None


def test_extensionless_url_with_pdf_mime_accepted(tmp_path):
    """A document without a file extension, served with a valid PDF MIME and
    genuine PDF bytes, is accepted and classified as a PDF (MIME classifies
    extensionless URLs)."""
    store, persisted, fetcher, result = _fetch_one(
        tmp_path,
        FakeSession({"https://x.test/download": response(b"%PDF-1.4 real", url="https://x.test/download", content_type="application/pdf")}),
    )
    assert result.ok is True
    doc = store.get_document(persisted.id, "https://x.test/download")
    assert doc.status == DocumentStatus.FETCHED
    assert doc.kind == "pdf"
    assert store.get_publication(persisted.id).status == PublicationStatus.FETCHED


def test_pdf_url_with_html_mime_real_pdf_stored_as_pdf(tmp_path):
    """A `.pdf` URL answered with genuine PDF bytes even under a sloppy
    `text/html` Content-Type is accepted and stored as a PDF — the URL is
    authoritative for classification, never re-classified as HTML."""
    store, persisted, fetcher, result = _fetch_one(
        tmp_path,
        FakeSession({"https://x.test/p.pdf": response(b"%PDF-1.4 real", url="https://x.test/p.pdf", content_type="text/html")}),
    )
    assert result.ok is True
    doc = store.get_document(persisted.id, "https://x.test/p.pdf")
    assert doc.status == DocumentStatus.FETCHED
    assert doc.kind == "pdf"
    assert store.get_publication(persisted.id).status == PublicationStatus.FETCHED


@pytest.mark.parametrize("ext", [".doc", ".docx", ".xls", ".xlsx", ".zip", ".csv"])
def test_binary_document_url_with_html_body_rejected(tmp_path, ext):
    """A URL that clearly asks for a binary document (doc/docx/xls/xlsx/zip/csv)
    answered with HTML — generic or challenge — is rejected even when the server
    declares `text/html` (the old behaviour could have stored it as HTML)."""
    store, persisted, fetcher, result = _fetch_one(
        tmp_path,
        FakeSession({f"https://x.test/file{ext}": response("<html><body>not a document</body></html>", url=f"https://x.test/file{ext}", content_type="text/html")}),
    )
    assert result.ok is False
    doc = store.get_document(persisted.id, f"https://x.test/file{ext}")
    assert doc.status == DocumentStatus.FAILED
    assert "InvalidDocumentContent" in doc.error
    assert store.get_publication(persisted.id).status == PublicationStatus.FAILED
    assert doc.local_path is None


def test_valid_html_landing_page_accepted(tmp_path):
    store, persisted, fetcher, result = _fetch_one(
        tmp_path,
        FakeSession({"https://x.test/p.htm": response("<html><body>Statement</body></html>", url="https://x.test/p.htm", content_type="text/html")}),
    )
    assert result.ok is True
    assert store.get_document(persisted.id, "https://x.test/p.htm").status == DocumentStatus.FETCHED


def test_rejected_content_follows_error_path_partial(tmp_path):
    """A publication with one good document and one rejected document becomes
    PARTIAL — the rejected one is FAILED, never FETCHED, never a raw file."""
    store = make_store(tmp_path)
    the_pub = pub()
    the_pub.document_urls = ("https://x.test/ok.pdf", "https://x.test/bad.pdf")
    persisted = store.upsert_publication(the_pub)
    session = FakeSession({
        "https://x.test/ok.pdf": response(b"%PDF-1.4 good", url="https://x.test/ok.pdf", content_type="application/pdf"),
        "https://x.test/bad.pdf": response(b"", url="https://x.test/bad.pdf", content_type="application/pdf"),
    })
    fetcher = Fetcher(make_client(session), store, tmp_path / "raw")
    result = fetcher.fetch(persisted)
    assert result.ok is False
    ok = store.get_document(persisted.id, "https://x.test/ok.pdf")
    bad = store.get_document(persisted.id, "https://x.test/bad.pdf")
    assert ok.status == DocumentStatus.FETCHED
    assert bad.status == DocumentStatus.FAILED
    assert store.get_publication(persisted.id).status == PublicationStatus.PARTIAL
    # no file was ever written for the rejected body
    assert bad.local_path is None


def test_atomic_write_leaves_no_temp_and_full_final_file(tmp_path):
    """After a successful fetch the final file is complete and no temporary
    `.tmp` file remains — the visible document only ever exists in full."""
    store = make_store(tmp_path)
    persisted = store.upsert_publication(pub())
    session = FakeSession(routes())
    fetcher = Fetcher(make_client(session), store, tmp_path / "raw")
    result = fetcher.fetch(persisted)
    for d in result.documents:
        if d.status == DocumentStatus.FETCHED:
            assert Path(d.local_path).read_bytes() == session.routes[d.url].content
            assert not list(Path(d.local_path).parent.glob(".*.tmp"))


def test_atomic_write_failure_cleans_temp_and_no_document_row(tmp_path):
    """If the write fails the exception propagates (the fetch does not pretend
    success), nothing is persisted for the target, and no temp file remains —
    the store invariant is preserved on failure."""
    store = make_store(tmp_path)
    persisted = store.upsert_publication(pub())
    original_write_raw = Fetcher._write_raw

    def _exploding_write_raw(self, publication, kind, digest, ext, body):
        raise OSError("disk full")

    Fetcher._write_raw = _exploding_write_raw
    try:
        session = FakeSession(routes())
        fetcher = Fetcher(make_client(session), store, tmp_path / "raw")
        with pytest.raises(OSError):
            fetcher.fetch(persisted)
    finally:
        Fetcher._write_raw = original_write_raw
    # nothing persisted for the failed target, no temp files anywhere
    assert store.document_count(persisted.id) == 0
    assert not list((tmp_path / "raw").rglob("*.tmp"))


# ---------------------------------------------------------------------------
# `retries` convention (🔴 audit finding #2): total-attempt counter
# ---------------------------------------------------------------------------

def test_retries_counts_total_attempts_including_first_success(tmp_path):
    """`retries` is a *total attempt* counter: a document fetched successfully
    on the first attempt records retries == 1 (the initial attempt counts, not
    only the retries after it). This is the documented convention the future
    parallel Collection must preserve."""
    store = make_store(tmp_path)
    persisted = store.upsert_publication(pub())
    session = FakeSession(routes())
    fetcher = Fetcher(make_client(session), store, tmp_path / "raw")
    result = fetcher.fetch(persisted)
    for d in result.documents:
        assert d.retries == 1, f"{d.url}: expected a single first attempt, got retries={d.retries}"


def test_retries_increments_on_each_failed_attempt(tmp_path):
    """A FAILED document increments `retries` on every actual re-fetch, so the
    counter reflects how many times the URL was really attempted."""
    store = make_store(tmp_path)
    persisted = store.upsert_publication(pub())
    pdf = "https://x.test/files/report.pdf"
    session = FakeSession({
        "https://x.test/monetarypolicy/statement.htm": response(page_html(), url="https://x.test/monetarypolicy/statement.htm"),
        pdf: TransportError(pdf, "boom"),
        "https://x.test/files/data.xlsx": response(b"PK", url="https://x.test/files/data.xlsx", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    })
    fetcher = Fetcher(make_client(session), store, tmp_path / "raw")

    fetcher.fetch(persisted)
    assert store.get_document(persisted.id, pdf).retries == 1
    fetcher.fetch(persisted)
    assert store.get_document(persisted.id, pdf).retries == 2
    fetcher.fetch(persisted)
    assert store.get_document(persisted.id, pdf).retries == 3


def test_retries_cap_stops_reattempting_failed_document(tmp_path):
    """Once `retries` reaches `max_retries`, the Fetcher returns the existing
    FAILED document without any further network call — a permanently failing
    document is not hammered forever."""
    from argus.http import HttpConfig

    store = make_store(tmp_path)
    the_pub = pub()
    the_pub.document_urls = ("https://x.test/files/report.pdf",)
    persisted = store.upsert_publication(the_pub)
    pdf = "https://x.test/files/report.pdf"
    session = FakeSession({pdf: TransportError(pdf, "boom")})
    # Single-attempt HTTP client (max_retries=0) so the *Fetcher*'s own cap is
    # what limits the number of network calls, not the HTTP retry layer.
    client = make_client(session)
    client.config = HttpConfig(respect_robots=False, min_interval=0.0, max_retries=0, jitter=0.0)
    fetcher = Fetcher(client, store, tmp_path / "raw", max_retries=3)

    for _ in range(4):  # one Fetcher attempt per pass; the 4th must not hit the network
        fetcher.fetch(persisted)
    calls_for_pdf = sum(1 for c in session.calls if c == pdf)
    assert calls_for_pdf == 3  # exactly 3 attempts, then the cap kicks in
    assert store.get_document(persisted.id, pdf).retries == 3


def test_retries_increments_when_repair_succeeds(tmp_path):
    """A document that fails then succeeds records the cumulative attempt count
    (failures + the successful re-fetch)."""
    store = make_store(tmp_path)
    persisted = store.upsert_publication(pub())
    pdf = "https://x.test/files/report.pdf"
    session = FakeSession({
        "https://x.test/monetarypolicy/statement.htm": response(page_html(), url="https://x.test/monetarypolicy/statement.htm"),
        pdf: TransportError(pdf, "boom"),
        "https://x.test/files/data.xlsx": response(b"PK", url="https://x.test/files/data.xlsx", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    })
    fetcher = Fetcher(make_client(session), store, tmp_path / "raw")
    fetcher.fetch(persisted)
    assert store.get_document(persisted.id, pdf).status == DocumentStatus.FAILED

    session.routes[pdf] = response(b"%PDF-1.4 healed", url=pdf, content_type="application/pdf")
    fetcher.fetch(persisted)
    doc = store.get_document(persisted.id, pdf)
    assert doc.status == DocumentStatus.FETCHED
    assert doc.retries == 2  # 1 failed attempt + 1 successful re-fetch