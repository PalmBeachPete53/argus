from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from bs4 import BeautifulSoup

from .errors import InvalidDocumentContent
from .models import Document, DocumentStatus, FetchResult, Publication, PublicationStatus
from .normalize import absolutize, is_same_host, now_utc, slugify

_EXT_TO_KIND = {
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "docx",
    ".xls": "xls",
    ".xlsx": "xlsx",
    ".csv": "csv",
    ".txt": "txt",
    ".xml": "xml",
    ".html": "html",
    ".htm": "html",
    ".zip": "zip",
}

_MIME_TO_KIND_EXT = {
    "application/pdf": ("pdf", ".pdf"),
    "text/html": ("html", ".html"),
    "application/xhtml+xml": ("html", ".html"),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ("docx", ".docx"),
    "application/msword": ("doc", ".doc"),
    "application/vnd.ms-excel": ("xls", ".xls"),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ("xlsx", ".xlsx"),
    "text/csv": ("csv", ".csv"),
    "application/xml": ("xml", ".xml"),
    "text/xml": ("xml", ".xml"),
    "application/zip": ("zip", ".zip"),
    "text/plain": ("txt", ".txt"),
}

_KIND_EXT = {kind: ext for kind, ext in _MIME_TO_KIND_EXT.values()}

_LINKED_DOC_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".zip")

DOCUMENT_ACCEPT = (
    "text/html, application/xhtml+xml, application/pdf, application/xml, "
    "text/xml, text/csv, text/plain, application/zip"
)


def _kind_from_url(url: str) -> str:
    path = (url.split("?", 1)[0]).split("#", 1)[0].lower()
    for ext, kind in _EXT_TO_KIND.items():
        if path.endswith(ext):
            return kind
    return "html"


def _kind_and_ext(url: str, content_type: str | None) -> tuple[str, str]:
    if content_type:
        media = content_type.split(";", 1)[0].strip().lower()
        if media in _MIME_TO_KIND_EXT:
            return _MIME_TO_KIND_EXT[media]
    kind = _kind_from_url(url)
    return kind, _KIND_EXT.get(kind, ".bin")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# Conservative HTML challenge / bot-page markers (lowercased). A document
# target that comes back as one of these pages is never a real document — it is
# the site's bot protection, not the requested resource. The list is deliberately
# short and generic so it works across central banks (Cloudflare, Akamai, etc.)
# without flagging legitimately plain pages.
_CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "verify you are human",
    "cf-chl",
    "challenge-platform",
    "captcha",
    "attention required",
    "access denied",
    "enable javascript and cookies",
    "ddos protection",
)

# Media types that promise a *binary document* (PDF, OOXML, legacy Office):
# receiving HTML bytes under one of these is a manifest contradiction — either
# a bot page, a server error page, or a misconfigured endpoint.
_BINARY_DOCUMENT_MEDIA = (
    "application/pdf",
    "application/zip",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)


def _validate_content(url: str, kind: str, content_type: str | None, body: bytes) -> None:
    """Minimal (conservative) content sanity check before a document is stored.

    A response passing this check is *not* guaranteed to be a valid document;
    the check only rejects the clear-cut failures: an empty body, a bot/challenge
    page served in place of a document, and a declared binary document whose
    bytes are actually HTML. It never rejects a valid response just because the
    server used an imprecise MIME. Raises :class:`InvalidDocumentContent`.
    """
    if not body:
        raise InvalidDocumentContent(url, kind, "empty body (HTTP 200 with no content)")
    media = (content_type or "").split(";", 1)[0].strip().lower()
    head = body[:8192].lower()
    is_html_bytes = head.lstrip().startswith(b"<")
    if is_html_bytes and any(marker in head.decode("utf-8", errors="replace") for marker in _CHALLENGE_MARKERS):
        raise InvalidDocumentContent(url, kind, "HTML challenge / bot page returned instead of the document")
    if kind != "html" and is_html_bytes and media in _BINARY_DOCUMENT_MEDIA:
        # The server promised a binary document but delivered HTML — a server
        # error page, a bot wall, or a misconfigured Content-Type. Rejecting
        # keeps such a response from being stored as a "fetched" document.
        raise InvalidDocumentContent(url, kind, f"HTML body received with declared binary media type {media!r}")


class Fetcher:
    def __init__(
        self,
        client,
        store,
        raw_root: Path,
        *,
        page_doc_extraction: bool = True,
        max_page_documents: int = 12,
        max_retries: int = 3,
    ) -> None:
        self.client = client
        self.store = store
        self.raw_root = Path(raw_root)
        self.page_doc_extraction = page_doc_extraction
        self.max_page_documents = max_page_documents
        self.max_retries = max_retries

    def fetch(self, publication: Publication, *, force: bool = False) -> FetchResult:
        if publication.id is None:
            persisted = self.store.upsert_publication(publication)
            publication = persisted
        targets = list(publication.document_urls) or ([publication.url] if publication.url else [])
        if not targets:
            return FetchResult(publication_id=publication.id or "", documents=[], ok=False, error="no fetch target")

        fetched: list[Document] = []
        extracted_urls: set[str] = set(targets)
        for url in targets:
            result = self._fetch_target(publication, url, force=force)
            if result is None:
                continue
            fetched.append(result)
            if self.page_doc_extraction and result.status == DocumentStatus.FETCHED and result.kind == "html":
                linked = self._extract_linked(publication, result, force=force, already=extracted_urls)
                fetched.extend(linked)
        for document in fetched:
            self.store.upsert_document(document)
        status = self._compute_status(publication.id or "", fetched)
        if status is not None:
            self.store.set_publication_status(publication.id, status)
        failed = [d.url for d in fetched if d.status == DocumentStatus.FAILED]
        ok = bool(fetched) and len(failed) == 0
        return FetchResult(
            publication_id=publication.id or "",
            documents=fetched,
            ok=ok,
            failed_urls=failed,
        )

    def _fetch_target(self, publication: Publication, url: str, *, force: bool) -> Document | None:
        existing = self.store.get_document(publication.id or "", url)
        if existing is not None and existing.status == DocumentStatus.FETCHED and not force:
            return existing
        if (
            existing is not None
            and existing.status == DocumentStatus.FAILED
            and existing.retries >= self.max_retries
            and not force
        ):
            return existing
        try:
            response = self.client.get(url, headers={"Accept": DOCUMENT_ACCEPT})
        except Exception as exc:
            retries = (existing.retries if existing else 0) + 1
            return Document(
                publication_id=publication.id or "",
                url=url,
                kind=existing.kind if existing else "html",
                status=DocumentStatus.FAILED,
                retries=retries,
                error=f"{exc.__class__.__name__}: {exc}",
            )
        content_type = response.content_type or ""
        kind, ext = _kind_and_ext(url, content_type)
        body = response.content
        digest = sha256_bytes(body)
        if kind == "html":
            ext = ".html"
        # A body that fails the minimal content sanity check follows the normal
        # error path (FAILED document → PARTIAL/FAILED publication) and is never
        # stored as FETCHED — even on HTTP 200.
        try:
            _validate_content(url, kind, content_type, body)
            local_path = self._write_raw(publication, kind, digest, ext, body)
        except Exception as exc:
            if isinstance(exc, InvalidDocumentContent):
                retries = (existing.retries if existing else 0) + 1
                return Document(
                    publication_id=publication.id or "",
                    url=url,
                    kind=kind,
                    status=DocumentStatus.FAILED,
                    retries=retries,
                    error=f"{exc.__class__.__name__}: {exc}",
                )
            raise
        return Document(
            publication_id=publication.id or "",
            url=url,
            kind=kind,
            status=DocumentStatus.FETCHED,
            local_path=str(local_path),
            sha256=digest,
            content_type=response.content_type,
            size=len(body),
            retrieved_at=now_utc(),
            retries=(existing.retries if existing else 0) + 1,
        )

    def _extract_linked(
        self,
        publication: Publication,
        source_doc: Document,
        *,
        force: bool,
        already: set[str],
    ) -> list[Document]:
        if source_doc.local_path is None:
            return []
        try:
            soup = BeautifulSoup(Path(source_doc.local_path).read_text(encoding="utf-8", errors="replace"), "html.parser")
        except OSError:
            return []
        discovered: list[Document] = []
        for anchor in soup.find_all("a"):
            href = anchor.get("href")
            if not href or not isinstance(href, str):
                continue
            url = absolutize(source_doc.url, href)
            lowered = url.lower()
            if not lowered.startswith(("http://", "https://")):
                continue
            if not lowered.endswith(_LINKED_DOC_EXTENSIONS):
                continue
            if url in already:
                continue
            if not is_same_host(url, source_doc.url):
                continue
            already.add(url)
            discovered.append(url)
            if len(discovered) >= self.max_page_documents:
                break
        documents: list[Document] = []
        for url in discovered:
            doc = self._fetch_target(publication, url, force=force)
            if doc is not None:
                documents.append(doc)
        return documents

    def _write_raw(self, publication: Publication, kind: str, digest: str, ext: str, body: bytes) -> str:
        """Persist a downloaded body to the raw tree, atomically.

        The body is written to a hidden temporary file in the *same* directory
        (so the final ``os.replace`` stays on one filesystem), flushed with
        ``fsync``, then atomically renamed into place. The visible final file can
        therefore only ever exist in its complete form — a crash during the
        download/write leaves at most a stray ``.tmp`` file (never referenced by
        any ``documents`` row), never a truncated real document. The store
        invariant holds: a ``documents`` row's ``local_path`` always points at a
        fully written file.
        """
        bank_dir = self.raw_root / publication.central_bank
        if publication.publication_date is not None:
            folder = publication.publication_date.strftime("%Y/%m")
        else:
            folder = "undated"
        target = bank_dir / folder
        target.mkdir(parents=True, exist_ok=True)
        short = digest[:8]
        base = slugify(publication.title, max_len=60)
        filename = f"{base}-{short}{ext}"
        path = target / filename

        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{filename}.", suffix=".tmp", dir=str(target)
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return str(path)

    def _compute_status(self, publication_id: str, fetched: list[Document]) -> PublicationStatus | None:
        stored = self.store.list_documents(publication_id)
        by_url: dict[str, Document] = {}
        for doc in stored:
            by_url[doc.url] = doc
        for doc in fetched:
            by_url[doc.url] = doc
        if not by_url:
            return PublicationStatus.DISCOVERED
        completed = [d for d in by_url.values() if d.status == DocumentStatus.FETCHED]
        failed = [d for d in by_url.values() if d.status == DocumentStatus.FAILED]
        if completed and failed:
            return PublicationStatus.PARTIAL
        if completed:
            return PublicationStatus.FETCHED
        if failed:
            return PublicationStatus.FAILED
        return PublicationStatus.DISCOVERED