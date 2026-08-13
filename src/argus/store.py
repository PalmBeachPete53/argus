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