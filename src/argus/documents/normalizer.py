from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from ..models import Document, DocumentStatus, Publication
from .base import METHOD_UNAVAILABLE, DocumentParser, NormalizedDocument
from .dates import extract_publication_date_from_metadata
from .registry import ParserRegistry

if TYPE_CHECKING:
    from ..store import Store


def document_id_of(document: Document) -> str:
    """Stable identity for a raw document (its SHA-256, computed on demand)."""
    if document.sha256:
        return document.sha256
    if document.local_path:
        try:
            return hashlib.sha256(Path(document.local_path).read_bytes()).hexdigest()
        except OSError:
            pass
    return hashlib.sha256(document.url.encode("utf-8")).hexdigest()


class Normalizer:
    """Phase 2A — turns raw ``Document``s into ``NormalizedDocument``s.

    Independent of collection: it only reads documents already stored on disk
    (``Document.local_path``) and never performs HTTP requests, so parsers and
    normalization can be re-run at any time without re-fetching.

    `Normalizer` is the sole owner of a document's stable identity: parsers
    return a `NormalizedDocument` with an empty ``document_id`` and `parse`
    rewrites it from `document_id_of()` (SHA-256 of the raw bytes). Code that
    uses parsers directly without `Normalizer.parse` therefore gets no identity
    claim, which is intended — only this class decides it.
    """

    def __init__(
        self,
        store: Store | None = None,
        parsers: list[DocumentParser] | None = None,
        raw_root: Path | str | None = None,
    ) -> None:
        self.store = store
        self.registry = ParserRegistry(parsers)
        self.raw_root = Path(raw_root) if raw_root else None

    def resolve_path(self, document: Document) -> str | None:
        if document.local_path is None:
            return None
        path = Path(document.local_path)
        if path.exists():
            return document.local_path
        if self.raw_root is not None and not path.is_absolute():
            candidate = self.raw_root / path
            if candidate.exists():
                return str(candidate)
        return document.local_path

    def parse(self, document: Document) -> NormalizedDocument:
        """Parse a raw document into a NormalizedDocument (no persistence)."""
        resolved = Document(
            publication_id=document.publication_id,
            url=document.url,
            kind=document.kind,
            status=document.status,
            local_path=self.resolve_path(document),
            sha256=document.sha256,
            content_type=document.content_type,
            size=document.size,
            retrieved_at=document.retrieved_at,
            retries=document.retries,
            error=document.error,
            id=document.id,
        )
        normalized = self.registry.parse(resolved)
        normalized.document_id = document_id_of(document)
        return normalized

    def normalize(
        self,
        document: Document,
        *,
        force: bool = False,
        persist: bool = True,
    ) -> NormalizedDocument | None:
        """Parse and (optionally) persist a single document."""
        doc_id = document_id_of(document)
        if (
            self.store is not None
            and not force
            and self.store.get_normalized_document(doc_id) is not None
        ):
            return None
        normalized = self.parse(document)
        if persist and self.store is not None:
            self.store.upsert_normalized_document(normalized)
            self._refine_publication_date(document, normalized)
        return normalized

    def _refine_publication_date(
        self,
        document: Document,
        normalized: NormalizedDocument,
    ) -> None:
        """Establish the publication's temporal identity from the document's
        authoritative metadata (JSON-LD / OpenGraph / ``<time>``) when the
        publication currently has no ``publication_date``.

        A crawl signal (fetch/discovery timestamp, sitemap ``lastmod``) is never
        used here — only structured, publisher-authored document metadata.
        """
        if self.store is None or not normalized.ok:
            return
        date, source = extract_publication_date_from_metadata(normalized.metadata)
        if date is None:
            return
        self.store.set_publication_date_if_missing(
            document.publication_id, date, source=source
        )

    def normalize_documents(
        self,
        documents,
        *,
        force: bool = False,
    ) -> list[NormalizedDocument]:
        results: list[NormalizedDocument] = []
        for document in documents:
            normalized = self.normalize(document, force=force)
            if normalized is not None:
                results.append(normalized)
        return results

    def normalize_publication(
        self,
        publication: Publication,
        *,
        force: bool = False,
    ) -> list[NormalizedDocument]:
        if self.store is None:
            return []
        documents = self.store.list_documents(publication.id or "")
        fetched = [d for d in documents if d.status == DocumentStatus.FETCHED]
        return self.normalize_documents(fetched, force=force)

    def normalize_all(
        self,
        *,
        banks: tuple[str, ...] | list[str] | None = None,
        ids: tuple[str, ...] | list[str] | None = None,
        force: bool = False,
    ) -> list[NormalizedDocument]:
        if self.store is None:
            return []
        publications = self.store.list_publications(bank=banks, ids=tuple(ids or ()))
        results: list[NormalizedDocument] = []
        for publication in publications:
            results.extend(self.normalize_publication(publication, force=force))
        return results

    def extraction_stats(self, documents: list[NormalizedDocument]) -> dict[str, int]:
        stats: dict[str, int] = {}
        for doc in documents:
            stats[doc.extraction_method] = stats.get(doc.extraction_method, 0) + 1
        return stats