from __future__ import annotations

import json
import os
import sqlite3
import time as time_mod
from datetime import datetime
from pathlib import Path

from .models import (
    CollectError,
    Document,
    DocumentStatus,
    Publication,
    PublicationStatus,
    Source,
)
from .normalize import compute_dedup_key, from_iso, iso, now_utc

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    central_bank TEXT NOT NULL,
    name TEXT,
    discovery_kind TEXT,
    discovery_url TEXT,
    priority INTEGER DEFAULT 100,
    enabled INTEGER DEFAULT 1,
    publication_types TEXT,
    fallback_for TEXT,
    last_success TEXT,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS publications (
    id TEXT PRIMARY KEY,
    dedup_key TEXT UNIQUE,
    central_bank TEXT NOT NULL,
    title TEXT,
    publication_date TEXT,
    meeting_date TEXT,
    url TEXT,
    canonical_url TEXT,
    source_id TEXT,
    source_url TEXT,
    publication_type TEXT,
    language TEXT,
    document_urls_json TEXT,
    extra_json TEXT,
    status TEXT,
    first_seen_at TEXT,
    last_seen_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_publications_bank_date
    ON publications(central_bank, publication_date);
CREATE INDEX IF NOT EXISTS idx_publications_status
    ON publications(status);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    publication_id TEXT NOT NULL,
    url TEXT NOT NULL,
    kind TEXT,
    status TEXT,
    local_path TEXT,
    sha256 TEXT,
    content_type TEXT,
    size INTEGER,
    retrieved_at TEXT,
    retries INTEGER DEFAULT 0,
    error TEXT,
    UNIQUE(publication_id, url)
);
CREATE INDEX IF NOT EXISTS idx_documents_publication
    ON documents(publication_id);
CREATE TABLE IF NOT EXISTS collect_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    bank_id TEXT,
    source_id TEXT,
    strategy TEXT,
    url TEXT,
    status_code INTEGER,
    error_type TEXT,
    message TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_errors_bank ON collect_errors(bank_id);
CREATE TABLE IF NOT EXISTS normalized_documents (
    document_id TEXT PRIMARY KEY,
    publication_id TEXT NOT NULL,
    source_url TEXT,
    local_path TEXT,
    document_kind TEXT,
    mime_type TEXT,
    title TEXT,
    text TEXT,
    extraction_method TEXT,
    extraction_warnings_json TEXT,
    metadata_json TEXT,
    pages_json TEXT,
    normalized_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_normdocs_publication
    ON normalized_documents(publication_id);
CREATE INDEX IF NOT EXISTS idx_normdocs_kind
    ON normalized_documents(document_kind);
CREATE TABLE IF NOT EXISTS document_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL,
    position INTEGER,
    heading TEXT,
    level INTEGER,
    text TEXT,
    page INTEGER,
    UNIQUE(document_id, position)
);
CREATE INDEX IF NOT EXISTS idx_sections_document
    ON document_sections(document_id);
CREATE TABLE IF NOT EXISTS document_tables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL,
    position INTEGER,
    name TEXT,
    headers_json TEXT,
    rows_json TEXT,
    page INTEGER,
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_tables_document
    ON document_tables(document_id);
CREATE TABLE IF NOT EXISTS classifications (
    -- Single source of truth for a publication's classification: type,
    -- confidence, method and evidence. `publications.publication_type` is a
    -- denormalized cache refreshed atomically by set_classification().
    publication_id TEXT PRIMARY KEY,
    central_bank TEXT,
    publication_type TEXT,
    confidence TEXT,
    method TEXT,
    evidence_json TEXT,
    classified_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_classifications_type
    ON classifications(publication_type);
CREATE TABLE IF NOT EXISTS facts (
    -- Phase 4 — structured, provenance-carrying assertions extracted from a
    -- normalized document. `fact_id` is a deterministic SHA-256 over stable
    -- semantic + provenance fields (see src/argus/facts/identity.py) so
    -- re-running an extractor updates the row instead of duplicating it.
    fact_id TEXT PRIMARY KEY,
    publication_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    central_bank TEXT,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value_type TEXT,
    value_json TEXT,
    previous_value_json TEXT,
    change_json TEXT,
    period_kind TEXT,
    period_value TEXT,
    period_label TEXT,
    effective_date TEXT,
    source_location_json TEXT,
    source_text TEXT,
    extraction_method TEXT,
    extraction_version TEXT,
    confidence TEXT,
    speaker TEXT,
    identity_qualifier TEXT,
    extracted_at TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_facts_publication
    ON facts(publication_id);
CREATE INDEX IF NOT EXISTS idx_facts_document
    ON facts(document_id);
CREATE INDEX IF NOT EXISTS idx_facts_subject
    ON facts(subject, predicate);
CREATE INDEX IF NOT EXISTS idx_facts_bank_subject
    ON facts(central_bank, subject);
"""


class Store:
    def __init__(self, path: str | os.PathLike) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        if str(self.path) != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        try:
            self._conn.execute("ALTER TABLE facts ADD COLUMN identity_qualifier TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            self._conn.execute("ALTER TABLE facts ADD COLUMN speaker TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            self._conn.execute("ALTER TABLE normalized_documents ADD COLUMN pages_json TEXT")
        except sqlite3.OperationalError:
            pass
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def upsert_source(self, source: Source) -> None:
        self._conn.execute(
            """
            INSERT INTO sources
                (id, central_bank, name, discovery_kind, discovery_url, priority,
                 enabled, publication_types, fallback_for)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                discovery_kind=excluded.discovery_kind,
                discovery_url=excluded.discovery_url,
                priority=excluded.priority,
                enabled=excluded.enabled,
                publication_types=excluded.publication_types,
                fallback_for=excluded.fallback_for
            """,
            (
                source.id,
                source.central_bank,
                source.name,
                source.discovery.kind,
                source.discovery.url,
                int(source.priority),
                int(source.enabled),
                json.dumps(list(source.publication_types)),
                json.dumps(list(source.fallback_for)),
            ),
        )
        self._conn.commit()

    def record_source_result(self, source_id: str, *, ok: bool, error: str | None = None) -> None:
        if ok:
            self._conn.execute(
                "UPDATE sources SET last_success=?, last_error=NULL WHERE id=?",
                (iso(now_utc()), source_id),
            )
        else:
            self._conn.execute(
                "UPDATE sources SET last_error=? WHERE id=?",
                (error, source_id),
            )
        self._conn.commit()

    def _dedup(self, pub: Publication) -> str:
        return compute_dedup_key(
            pub.central_bank,
            url=pub.url or None,
            title=pub.title,
            date=pub.publication_date,
        )

    def upsert_publication(self, pub: Publication) -> Publication:
        now = now_utc()
        dedup_key = pub.dedup_key or self._dedup(pub)
        pub_id = pub.id or dedup_key
        canonical = pub.canonical_url
        if canonical is None and pub.url:
            from .normalize import canonical_url as canon

            canonical = canon(pub.url)
        existing = self._select_publication(dedup_key)
        if existing is not None:
            doc_urls = list(existing.document_urls)
            for doc_url in pub.document_urls:
                if doc_url not in doc_urls:
                    doc_urls.append(doc_url)
            extra = dict(existing.extra)
            for key, value in pub.extra.items():
                extra.setdefault(key, value)
            changed = (
                existing.title != pub.title
                or existing.url != pub.url
                or existing.publication_date != pub.publication_date
                or existing.meeting_date != pub.meeting_date
            )
            status = existing.status
            if status in (PublicationStatus.FETCHED, PublicationStatus.PARTIAL) and changed:
                status = PublicationStatus.UPDATED
            updated_at = iso(now) if changed else iso(existing.updated_at)
            self._conn.execute(
                """
                UPDATE publications SET
                    title=?, publication_date=?, meeting_date=?, url=?, canonical_url=?,
                    source_id=?, source_url=?, publication_type=?, language=?,
                    document_urls_json=?, extra_json=?, status=?, last_seen_at=?, updated_at=?
                WHERE dedup_key=?
                """,
                (
                    pub.title,
                    iso(pub.publication_date),
                    iso(pub.meeting_date),
                    pub.url,
                    canonical,
                    pub.source_id,
                    pub.source_url,
                    pub.publication_type,
                    pub.language,
                    json.dumps(doc_urls),
                    json.dumps(extra),
                    status.value,
                    iso(now),
                    updated_at,
                    dedup_key,
                ),
            )
            self._conn.commit()
            return self._select_publication(dedup_key) or pub
        canonical = canonical or None
        self._conn.execute(
            """
            INSERT INTO publications
                (id, dedup_key, central_bank, title, publication_date, meeting_date,
                 url, canonical_url, source_id, source_url, publication_type, language,
                 document_urls_json, extra_json, status, first_seen_at, last_seen_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pub_id,
                dedup_key,
                pub.central_bank,
                pub.title,
                iso(pub.publication_date),
                iso(pub.meeting_date),
                pub.url,
                canonical,
                pub.source_id,
                pub.source_url,
                pub.publication_type,
                pub.language,
                json.dumps(list(pub.document_urls)),
                json.dumps(pub.extra),
                pub.status.value,
                iso(now),
                iso(now),
                iso(now),
            ),
        )
        self._conn.commit()
        return self._select_publication(dedup_key) or pub

    def _select_publication(self, dedup_key: str) -> Publication | None:
        row = self._conn.execute(
            "SELECT * FROM publications WHERE dedup_key = ?", (dedup_key,)
        ).fetchone()
        return self._pub_from_row(row) if row else None

    def get_publication(self, pub_id: str) -> Publication | None:
        row = self._conn.execute(
            "SELECT * FROM publications WHERE id = ?", (pub_id,)
        ).fetchone()
        return self._pub_from_row(row) if row else None

    def list_publications(
        self,
        *,
        bank: str | tuple[str, ...] | None = None,
        statuses: tuple = (),
        ids: tuple[str, ...] = (),
        date_start=None,
        date_end=None,
        limit: int | None = None,
    ) -> list[Publication]:
        query = "SELECT * FROM publications"
        clauses: list[str] = []
        params: list = []
        if bank is not None:
            if isinstance(bank, str):
                clauses.append("central_bank = ?")
                params.append(bank)
            else:
                bank = tuple(bank)
                if bank:
                    clauses.append(f"central_bank IN ({','.join('?' * len(bank))})")
                    params.extend(bank)
        if statuses:
            clauses.append(f"status IN ({','.join('?' * len(statuses))})")
            params.extend(s.value if hasattr(s, "value") else s for s in statuses)
        if ids:
            clauses.append(f"id IN ({','.join('?' * len(ids))})")
            params.extend(ids)
        if date_start is not None:
            clauses.append("publication_date IS NOT NULL AND publication_date >= ?")
            params.append(self._date_bound(date_start))
        if date_end is not None:
            clauses.append("publication_date IS NOT NULL AND publication_date < ?")
            params.append(self._date_bound(date_end))
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY publication_date DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [self._pub_from_row(r) for r in rows]

    @staticmethod
    def _date_bound(value) -> str:
        if isinstance(value, datetime):
            return iso(value)
        return iso(from_iso(str(value))) or str(value)

    @staticmethod
    def _pub_from_row(row: sqlite3.Row) -> Publication:
        return Publication(
            id=row["id"],
            dedup_key=row["dedup_key"],
            central_bank=row["central_bank"],
            title=row["title"],
            publication_date=from_iso(row["publication_date"]),
            meeting_date=from_iso(row["meeting_date"]),
            url=row["url"] or "",
            canonical_url=row["canonical_url"],
            source_id=row["source_id"],
            source_url=row["source_url"],
            publication_type=row["publication_type"],
            language=row["language"],
            document_urls=tuple(json.loads(row["document_urls_json"] or "[]")),
            extra=json.loads(row["extra_json"] or "{}"),
            status=PublicationStatus(row["status"]),
            first_seen_at=from_iso(row["first_seen_at"]),
            last_seen_at=from_iso(row["last_seen_at"]),
            updated_at=from_iso(row["updated_at"]),
        )

    def set_publication_status(self, pub_id: str, status: PublicationStatus) -> None:
        self._conn.execute(
            "UPDATE publications SET status=? WHERE id=?",
            (status.value, pub_id),
        )
        self._conn.commit()

    def document_count(self, publication_id: str, status: DocumentStatus | None = None) -> int:
        if status is None:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM documents WHERE publication_id=?", (publication_id,)
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM documents WHERE publication_id=? AND status=?",
                (publication_id, status.value),
            ).fetchone()
        return int(row["n"])

    def list_documents(self, publication_id: str) -> list[Document]:
        rows = self._conn.execute(
            "SELECT * FROM documents WHERE publication_id=? ORDER BY id", (publication_id,)
        ).fetchall()
        return [self._doc_from_row(r) for r in rows]

    def get_document(self, publication_id: str, url: str) -> Document | None:
        row = self._conn.execute(
            "SELECT * FROM documents WHERE publication_id=? AND url=?",
            (publication_id, url),
        ).fetchone()
        return self._doc_from_row(row) if row else None

    def upsert_document(self, doc: Document) -> Document:
        existing = self.get_document(doc.publication_id, doc.url)
        if existing is not None:
            self._conn.execute(
                """
                UPDATE documents SET kind=?, status=?, local_path=?, sha256=?,
                    content_type=?, size=?, retrieved_at=?, retries=?, error=?
                WHERE publication_id=? AND url=?
                """,
                (
                    doc.kind,
                    doc.status.value,
                    doc.local_path,
                    doc.sha256,
                    doc.content_type,
                    doc.size,
                    iso(doc.retrieved_at),
                    doc.retries,
                    doc.error,
                    doc.publication_id,
                    doc.url,
                ),
            )
            self._conn.commit()
            updated = self.get_document(doc.publication_id, doc.url)
            if updated is not None:
                return updated
        self._conn.execute(
            """
            INSERT INTO documents
                (publication_id, url, kind, status, local_path, sha256,
                 content_type, size, retrieved_at, retries, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc.publication_id,
                doc.url,
                doc.kind,
                doc.status.value,
                doc.local_path,
                doc.sha256,
                doc.content_type,
                doc.size,
                iso(doc.retrieved_at),
                doc.retries,
                doc.error,
            ),
        )
        self._conn.commit()
        inserted = self.get_document(doc.publication_id, doc.url)
        if inserted is not None:
            return inserted
        doc.id = self._conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        return doc

    @staticmethod
    def _doc_from_row(row: sqlite3.Row) -> Document:
        return Document(
            id=row["id"],
            publication_id=row["publication_id"],
            url=row["url"],
            kind=row["kind"],
            status=DocumentStatus(row["status"]),
            local_path=row["local_path"],
            sha256=row["sha256"],
            content_type=row["content_type"],
            size=row["size"],
            retrieved_at=from_iso(row["retrieved_at"]),
            retries=row["retries"],
            error=row["error"],
        )

    def log_error(self, error: CollectError) -> None:
        self._conn.execute(
            """
            INSERT INTO collect_errors
                (run_id, bank_id, source_id, strategy, url, status_code, error_type, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                error.run_id,
                error.bank_id,
                error.source_id,
                error.strategy,
                error.url,
                error.status_code,
                error.error_type,
                error.message,
                iso(error.timestamp),
            ),
        )
        self._conn.commit()

    def list_errors(self, *, run_id: str | None = None, bank_id: str | None = None) -> list[CollectError]:
        query = "SELECT * FROM collect_errors"
        clauses: list[str] = []
        params: list = []
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if bank_id:
            clauses.append("bank_id = ?")
            params.append(bank_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id"
        rows = self._conn.execute(query, params).fetchall()
        errors = []
        for row in rows:
            errors.append(
                CollectError(
                    bank_id=row["bank_id"],
                    source_id=row["source_id"],
                    strategy=row["strategy"],
                    url=row["url"],
                    error_type=row["error_type"],
                    message=row["message"],
                    status_code=row["status_code"],
                    run_id=row["run_id"],
                    timestamp=from_iso(row["created_at"]),
                )
            )
        return errors

    def run_stamp(self) -> str:
        return time_mod.strftime("%Y%m%dT%H%M%S") + f"-{os.getpid()}"

    # ------------------------------------------------------------------
    # Phase 2A — normalization persistence
    # ------------------------------------------------------------------

    def upsert_normalized_document(self, document) -> None:
        from .documents.base import DocumentSection, DocumentTable  # noqa: F401

        doc_id = document.document_id
        self._conn.execute(
            """
            INSERT INTO normalized_documents
                (document_id, publication_id, source_url, local_path, document_kind,
                 mime_type, title, text, extraction_method, extraction_warnings_json,
                 metadata_json, pages_json, normalized_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                publication_id=excluded.publication_id,
                source_url=excluded.source_url,
                local_path=excluded.local_path,
                document_kind=excluded.document_kind,
                mime_type=excluded.mime_type,
                title=excluded.title,
                text=excluded.text,
                extraction_method=excluded.extraction_method,
                extraction_warnings_json=excluded.extraction_warnings_json,
                metadata_json=excluded.metadata_json,
                pages_json=excluded.pages_json,
                normalized_at=excluded.normalized_at
            """,
            (
                doc_id,
                document.publication_id,
                document.source_url,
                document.local_path,
                document.document_kind,
                document.mime_type,
                document.title,
                document.text,
                document.extraction_method,
                json.dumps(document.extraction_warnings),
                json.dumps(document.metadata, ensure_ascii=False, default=str),
                json.dumps(
                    [{"number": p.number, "text": p.text} for p in document.pages],
                    ensure_ascii=False,
                ),
                iso(document.normalized_at or now_utc()),
            ),
        )
        self._conn.execute("DELETE FROM document_sections WHERE document_id=?", (doc_id,))
        self._conn.execute("DELETE FROM document_tables WHERE document_id=?", (doc_id,))
        for position, section in enumerate(document.sections):
            self._conn.execute(
                """
                INSERT INTO document_sections
                    (document_id, position, heading, level, text, page)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (doc_id, position, section.heading, section.level, section.text, section.page),
            )
        for position, table in enumerate(document.tables):
            self._conn.execute(
                """
                INSERT INTO document_tables
                    (document_id, position, name, headers_json, rows_json, page, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    doc_id,
                    position,
                    table.name,
                    json.dumps(table.headers, ensure_ascii=False, default=str),
                    json.dumps(table.rows, ensure_ascii=False, default=str),
                    table.page,
                    json.dumps(table.metadata, ensure_ascii=False, default=str),
                ),
            )
        self._conn.commit()

    def _normalized_from_row(self, row: sqlite3.Row):
        from .documents.base import (
            DocumentPage,
            DocumentSection,
            DocumentTable,
            NormalizedDocument,
        )

        doc_id = row["document_id"]
        pages = [
            DocumentPage(number=p["number"], text=p["text"])
            for p in json.loads(row["pages_json"] or "[]")
        ]
        sections = [
            DocumentSection(
                order=r["position"],
                level=r["level"],
                heading=r["heading"] or "",
                text=r["text"] or "",
                page=r["page"],
                id=r["id"],
            )
            for r in self._conn.execute(
                "SELECT * FROM document_sections WHERE document_id=? ORDER BY position", (doc_id,)
            )
        ]
        tables = []
        for r in self._conn.execute(
            "SELECT * FROM document_tables WHERE document_id=? ORDER BY position", (doc_id,)
        ):
            tables.append(
                DocumentTable(
                    order=r["position"],
                    name=r["name"] or "",
                    headers=json.loads(r["headers_json"] or "[]"),
                    rows=json.loads(r["rows_json"] or "[]"),
                    page=r["page"],
                    metadata=json.loads(r["metadata_json"] or "{}"),
                    id=r["id"],
                )
            )
        return NormalizedDocument(
            publication_id=row["publication_id"],
            document_id=doc_id,
            source_url=row["source_url"] or "",
            local_path=row["local_path"],
            document_kind=row["document_kind"] or "",
            mime_type=row["mime_type"],
            title=row["title"],
            text=row["text"] or "",
            sections=sections,
            tables=tables,
            pages=pages,
            metadata=json.loads(row["metadata_json"] or "{}"),
            extraction_method=row["extraction_method"] or "",
            extraction_warnings=json.loads(row["extraction_warnings_json"] or "[]"),
            normalized_at=from_iso(row["normalized_at"]),
        )

    def get_normalized_document(self, document_id: str):
        row = self._conn.execute(
            "SELECT * FROM normalized_documents WHERE document_id = ?", (document_id,)
        ).fetchone()
        return self._normalized_from_row(row) if row else None

    def normalized_documents_for_publication(self, publication_id: str):
        rows = self._conn.execute(
            "SELECT * FROM normalized_documents WHERE publication_id = ? ORDER BY document_id",
            (publication_id,),
        ).fetchall()
        return [self._normalized_from_row(r) for r in rows]

    def list_normalized_documents(
        self,
        *,
        publication_id: str | None = None,
        bank: str | tuple[str, ...] | None = None,
        kinds: tuple[str, ...] = (),
    ):
        query = (
            "SELECT n.* FROM normalized_documents n "
            "LEFT JOIN publications p ON p.id = n.publication_id"
        )
        clauses: list[str] = []
        params: list = []
        if publication_id is not None:
            clauses.append("n.publication_id = ?")
            params.append(publication_id)
        if bank is not None:
            banks = (bank,) if isinstance(bank, str) else tuple(bank)
            if banks:
                clauses.append(f"p.central_bank IN ({','.join('?' * len(banks))})")
                params.extend(banks)
        if kinds:
            clauses.append(f"n.document_kind IN ({','.join('?' * len(kinds))})")
            params.extend(kinds)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY n.publication_id"
        rows = self._conn.execute(query, params).fetchall()
        return [self._normalized_from_row(r) for r in rows]

    # ------------------------------------------------------------------
    # Phase 2B — classification persistence
    # ------------------------------------------------------------------

    def set_classification(
        self,
        publication_id: str,
        *,
        central_bank: str | None = None,
        publication_type: str,
        confidence: str,
        method: str,
        evidence: list[str],
        classified_at=None,
    ) -> None:
        """Persist a classification (upsert on ``publication_id``).

        The ``classifications`` table is the **single source of truth**.
        ``publications.publication_type`` is kept as a denormalized quick-filter
        cache and is updated in the same transaction, so both always agree.
        Read the authoritative record via ``get_classification``.
        """
        try:
            self._conn.execute(
                """
                INSERT INTO classifications
                    (publication_id, central_bank, publication_type, confidence, method,
                     evidence_json, classified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(publication_id) DO UPDATE SET
                    central_bank=excluded.central_bank,
                    publication_type=excluded.publication_type,
                    confidence=excluded.confidence,
                    method=excluded.method,
                    evidence_json=excluded.evidence_json,
                    classified_at=excluded.classified_at
                """,
                (
                    publication_id,
                    central_bank,
                    publication_type,
                    confidence,
                    method,
                    json.dumps(evidence),
                    iso(classified_at or now_utc()),
                ),
            )
            self._conn.execute(
                "UPDATE publications SET publication_type=? WHERE id=?",
                (publication_type, publication_id),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def get_classification(self, publication_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM classifications WHERE publication_id = ?", (publication_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            "publication_id": row["publication_id"],
            "central_bank": row["central_bank"],
            "publication_type": row["publication_type"],
            "confidence": row["confidence"],
            "method": row["method"],
            "evidence": json.loads(row["evidence_json"] or "[]"),
            "classified_at": row["classified_at"],
        }

    def list_classifications(
        self,
        *,
        bank: str | tuple[str, ...] | None = None,
        publication_type: str | None = None,
    ) -> list[dict]:
        query = "SELECT * FROM classifications"
        clauses: list[str] = []
        params: list = []
        if bank is not None:
            banks = (bank,) if isinstance(bank, str) else tuple(bank)
            if banks:
                clauses.append(f"central_bank IN ({','.join('?' * len(banks))})")
                params.extend(banks)
        if publication_type is not None:
            clauses.append("publication_type = ?")
            params.append(publication_type)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY publication_id"
        rows = self._conn.execute(query, params).fetchall()
        result: list[dict] = []
        for row in rows:
            result.append(
                {
                    "publication_id": row["publication_id"],
                    "central_bank": row["central_bank"],
                    "publication_type": row["publication_type"],
                    "confidence": row["confidence"],
                    "method": row["method"],
                    "evidence": json.loads(row["evidence_json"] or "[]"),
                    "classified_at": row["classified_at"],
                }
            )
        return result

    # ------------------------------------------------------------------
    # Phase 4 — facts persistence
    # ------------------------------------------------------------------

    def save_fact(self, fact) -> None:
        """Persist one fact, upserting by its deterministic ``fact_id``.

        Idempotent: re-saving the same fact (or a corrected version of it —
        the extracted value is not part of the identity) overwrites the row in
        place, preserving ``created_at``. ``central_bank`` is filled in from the
        publication when the fact does not carry it.
        """
        from .facts import Fact
        from .facts.base import FactPeriod

        if not isinstance(fact, Fact):
            raise TypeError(f"expected Fact, got {type(fact).__name__}")
        fact_id = fact.resolve_id()
        central_bank = fact.central_bank
        if not central_bank:
            pub = self.get_publication(fact.publication_id)
            central_bank = pub.central_bank if pub else None
        now = now_utc()
        now_iso = iso(now)
        existing = self._conn.execute(
            "SELECT created_at FROM facts WHERE fact_id = ?", (fact_id,)
        ).fetchone()
        created_at = existing["created_at"] if existing else now_iso
        self._conn.execute(
            """
            INSERT INTO facts
                (fact_id, publication_id, document_id, central_bank, subject, predicate,
                 value_type, value_json, previous_value_json, change_json,
                 period_kind, period_value, period_label, effective_date,
                 source_location_json, source_text, extraction_method,
                 extraction_version, confidence, speaker, identity_qualifier, extracted_at,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fact_id) DO UPDATE SET
                publication_id=excluded.publication_id,
                document_id=excluded.document_id,
                central_bank=excluded.central_bank,
                subject=excluded.subject,
                predicate=excluded.predicate,
                value_type=excluded.value_type,
                value_json=excluded.value_json,
                previous_value_json=excluded.previous_value_json,
                change_json=excluded.change_json,
                period_kind=excluded.period_kind,
                period_value=excluded.period_value,
                period_label=excluded.period_label,
                effective_date=excluded.effective_date,
                source_location_json=excluded.source_location_json,
                source_text=excluded.source_text,
                extraction_method=excluded.extraction_method,
                extraction_version=excluded.extraction_version,
                confidence=excluded.confidence,
                speaker=excluded.speaker,
                identity_qualifier=excluded.identity_qualifier,
                extracted_at=excluded.extracted_at,
                updated_at=excluded.updated_at
            """,
            (
                fact_id,
                fact.publication_id,
                fact.document_id,
                central_bank,
                fact.subject,
                fact.predicate,
                fact.value.kind.value if fact.value else None,
                json.dumps(fact.value.to_dict()) if fact.value else None,
                json.dumps(fact.previous_value.to_dict()) if fact.previous_value else None,
                json.dumps(fact.change.to_dict()) if fact.change else None,
                fact.period.kind.value if fact.period else None,
                fact.period.value if fact.period else None,
                fact.period.label if fact.period else None,
                iso(fact.effective_date),
                json.dumps(fact.source_location.to_dict()) if fact.source_location else None,
                fact.source_text,
                fact.extraction_method,
                fact.extraction_version,
                fact.confidence.value if fact.confidence else None,
                fact.speaker,
                fact.identity_qualifier,
                iso(fact.extracted_at),
                created_at,
                now_iso,
            ),
        )
        self._conn.commit()

    def save_facts(self, facts) -> int:
        """Persist a list of Facts (or an ``ExtractionResult``). Returns count."""
        if hasattr(facts, "facts"):
            facts = facts.facts
        count = 0
        for fact in facts:
            self.save_fact(fact)
            count += 1
        return count

    def get_fact(self, fact_id: str):
        row = self._conn.execute("SELECT * FROM facts WHERE fact_id = ?", (fact_id,)).fetchone()
        return self._fact_from_row(row) if row else None

    def get_facts(
        self,
        *,
        publication_id: str | None = None,
        document_id: str | None = None,
        bank: str | tuple[str, ...] | None = None,
        subject: str | None = None,
        predicate: str | None = None,
        value_type: str | None = None,
        limit: int | None = None,
    ) -> list:
        query = "SELECT * FROM facts"
        clauses: list[str] = []
        params: list = []
        if publication_id is not None:
            clauses.append("publication_id = ?")
            params.append(publication_id)
        if document_id is not None:
            clauses.append("document_id = ?")
            params.append(document_id)
        if bank is not None:
            banks = (bank,) if isinstance(bank, str) else tuple(bank)
            if banks:
                clauses.append(f"central_bank IN ({','.join('?' * len(banks))})")
                params.extend(banks)
        if subject is not None:
            clauses.append("subject = ?")
            params.append(subject)
        if predicate is not None:
            clauses.append("predicate = ?")
            params.append(predicate)
        if value_type is not None:
            clauses.append("value_type = ?")
            params.append(value_type)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY central_bank, subject, predicate, fact_id"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [self._fact_from_row(r) for r in rows]

    def delete_facts_for_document(self, document_id: str) -> int:
        """Delete all facts of a document (used when re-normalizing/re-extracting)."""
        cursor = self._conn.execute("DELETE FROM facts WHERE document_id = ?", (document_id,))
        self._conn.commit()
        return cursor.rowcount

    def delete_facts_for_publication(self, publication_id: str) -> int:
        cursor = self._conn.execute(
            "DELETE FROM facts WHERE publication_id = ?", (publication_id,)
        )
        self._conn.commit()
        return cursor.rowcount

    def rebuild_facts_for_document(self, document_id: str, facts) -> int:
        """Replace a document's facts with ``facts`` in one transaction.

        ``facts`` is a list of ``Fact`` or an ``ExtractionResult``. Facts already
        present that are not part of the new set are removed, so re-extraction
        never leaves stale rows behind.
        """
        from .facts import Fact
        from .facts.base import ExtractionResult

        if isinstance(facts, ExtractionResult):
            facts = facts.facts
        try:
            self._conn.execute("DELETE FROM facts WHERE document_id = ?", (document_id,))
            count = 0
            for fact in facts:
                if not isinstance(fact, Fact):
                    raise TypeError(f"expected Fact, got {type(fact).__name__}")
                fact_id = fact.resolve_id()
                central_bank = fact.central_bank
                if not central_bank:
                    pub = self.get_publication(fact.publication_id)
                    central_bank = pub.central_bank if pub else None
                now_iso = iso(now_utc())
                self._conn.execute(
                    """
                    INSERT INTO facts
                        (fact_id, publication_id, document_id, central_bank, subject, predicate,
                         value_type, value_json, previous_value_json, change_json,
                         period_kind, period_value, period_label, effective_date,
                         source_location_json, source_text, extraction_method,
                         extraction_version, confidence, speaker, identity_qualifier, extracted_at,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fact_id,
                        fact.publication_id,
                        fact.document_id,
                        central_bank,
                        fact.subject,
                        fact.predicate,
                        fact.value.kind.value if fact.value else None,
                        json.dumps(fact.value.to_dict()) if fact.value else None,
                        json.dumps(fact.previous_value.to_dict()) if fact.previous_value else None,
                        json.dumps(fact.change.to_dict()) if fact.change else None,
                        fact.period.kind.value if fact.period else None,
                        fact.period.value if fact.period else None,
                        fact.period.label if fact.period else None,
                        iso(fact.effective_date),
                        json.dumps(fact.source_location.to_dict()) if fact.source_location else None,
                        fact.source_text,
                        fact.extraction_method,
                        fact.extraction_version,
                        fact.confidence.value if fact.confidence else None,
                        fact.speaker,
                        fact.identity_qualifier,
                        iso(fact.extracted_at),
                        now_iso,
                        now_iso,
                    ),
                )
                count += 1
            self._conn.commit()
            return count
        except Exception:
            self._conn.rollback()
            raise

    @staticmethod
    def _fact_from_row(row: sqlite3.Row):
        from .facts.base import (
            Confidence,
            Fact,
            FactLocation,
            FactPeriod,
            FactValue,
        )

        return Fact(
            fact_id=row["fact_id"],
            publication_id=row["publication_id"],
            document_id=row["document_id"],
            central_bank=row["central_bank"],
            subject=row["subject"],
            predicate=row["predicate"],
            value=FactValue.from_dict(json.loads(row["value_json"])) if row["value_json"] else None,
            previous_value=(
                FactValue.from_dict(json.loads(row["previous_value_json"]))
                if row["previous_value_json"]
                else None
            ),
            change=FactValue.from_dict(json.loads(row["change_json"])) if row["change_json"] else None,
            period=(
                FactPeriod(kind=row["period_kind"], value=row["period_value"], label=row["period_label"])
                if row["period_kind"]
                else None
            ),
            effective_date=from_iso(row["effective_date"]),
            source_location=(
                FactLocation.from_dict(json.loads(row["source_location_json"]))
                if row["source_location_json"]
                else None
            ),
            source_text=row["source_text"],
            extraction_method=row["extraction_method"] or "",
            extraction_version=row["extraction_version"],
            confidence=Confidence(row["confidence"]) if row["confidence"] else None,
            speaker=row["speaker"],
            identity_qualifier=row["identity_qualifier"] or "",
            extracted_at=from_iso(row["extracted_at"]),
        )