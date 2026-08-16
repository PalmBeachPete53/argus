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
CREATE TABLE IF NOT EXISTS discovery_runs (
    -- One row per discovery campaign (launched from the CLI or the desktop
    -- GUI). The report layer for a discovery run: status, timing and scope.
    -- Discovery itself stays untouched in the collector/strategies — this only
    -- records the lifecycle of a campaign so the GUI can observe it.
    run_id TEXT PRIMARY KEY,
    started_at TEXT,
    finished_at TEXT,
    status TEXT,
    error TEXT,
    candidates INTEGER DEFAULT 0,
    banks_json TEXT,
    pid INTEGER,
    date_start TEXT,
    date_end TEXT,
    sources_total INTEGER DEFAULT 0,
    sources_completed INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_discovery_runs_started
    ON discovery_runs(started_at);
CREATE TABLE IF NOT EXISTS discovery_candidates (
    -- The result snapshot of a discovery campaign: one row per candidate the
    -- Core's discovery produced for that run, with the provenance the GUI
    -- needs (method Native/Search, new/known as of this run). The `publications`
    -- table remains the single source of truth for downstream work.
    run_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    publication_id TEXT,
    central_bank TEXT,
    bank_name TEXT,
    title TEXT,
    url TEXT,
    source_id TEXT,
    method TEXT,
    is_new INTEGER,
    discovered_at TEXT,
    publication_date TEXT,
    PRIMARY KEY (run_id, position)
);
CREATE INDEX IF NOT EXISTS idx_discovery_candidates_run
    ON discovery_candidates(run_id);
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
CREATE TABLE IF NOT EXISTS fact_changes (
    -- Phase 5 — analytic relations between two existing Facts over time
    -- (previous → current). `change_id` is a deterministic SHA-256 over the
    -- two source fact ids + the change kind, so re-running the analysis
    -- updates the row instead of duplicating it, and a change can never be
    -- "invented": both sides keep their fact/document/publication identity.
    change_id TEXT PRIMARY KEY,
    previous_fact_id TEXT NOT NULL,
    current_fact_id TEXT NOT NULL,
    change_type TEXT NOT NULL,
    central_bank TEXT,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value_kind TEXT,
    previous_value_json TEXT,
    current_value_json TEXT,
    delta_json TEXT,
    identity_qualifier TEXT,
    previous_period_kind TEXT,
    previous_period_value TEXT,
    previous_period_label TEXT,
    current_period_kind TEXT,
    current_period_value TEXT,
    current_period_label TEXT,
    previous_publication_id TEXT NOT NULL,
    current_publication_id TEXT NOT NULL,
    previous_document_id TEXT NOT NULL,
    current_document_id TEXT NOT NULL,
    previous_effective_date TEXT,
    current_effective_date TEXT,
    previous_source_text TEXT,
    current_source_text TEXT,
    analysis_version TEXT,
    analyzed_at TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_fact_changes_previous
    ON fact_changes(previous_fact_id);
CREATE INDEX IF NOT EXISTS idx_fact_changes_current
    ON fact_changes(current_fact_id);
CREATE INDEX IF NOT EXISTS idx_fact_changes_bank_subject
    ON fact_changes(central_bank, subject);
CREATE INDEX IF NOT EXISTS idx_fact_changes_publications
    ON fact_changes(previous_publication_id, current_publication_id);

CREATE TABLE IF NOT EXISTS policy_reactions (
    -- Phase 6 — empirical, INFERRED temporal relationships between two
    -- observed FactChanges: an earlier change and a later change. Not causal,
    -- not a reaction function (legacy table name "policy_reactions").
    -- `reaction_id` is a deterministic SHA-256 over the relationship
    -- (central_bank + condition_change_id + policy_change_id), so re-running
    -- the analysis updates the row instead of duplicating it. `inferred` is a
    -- constant 1 (never a Fact, never causal). Both sides keep their change /
    -- fact / publication / document provenance.
    reaction_id TEXT PRIMARY KEY,
    central_bank TEXT,
    inferred INTEGER NOT NULL DEFAULT 1,
    condition_change_id TEXT NOT NULL,
    condition_subject TEXT NOT NULL,
    condition_predicate TEXT NOT NULL,
    condition_value_kind TEXT,
    condition_previous_value_json TEXT,
    condition_current_value_json TEXT,
    condition_period_kind TEXT,
    condition_period_value TEXT,
    condition_period_label TEXT,
    condition_publication_id TEXT NOT NULL,
    condition_document_id TEXT NOT NULL,
    condition_effective_date TEXT,
    condition_source_text TEXT,
    condition_observed_at TEXT,
    policy_change_id TEXT NOT NULL,
    policy_subject TEXT NOT NULL,
    policy_predicate TEXT NOT NULL,
    policy_value_kind TEXT,
    policy_previous_value_json TEXT,
    policy_current_value_json TEXT,
    policy_period_kind TEXT,
    policy_period_value TEXT,
    policy_period_label TEXT,
    policy_publication_id TEXT NOT NULL,
    policy_document_id TEXT NOT NULL,
    policy_effective_date TEXT,
    policy_source_text TEXT,
    policy_observed_at TEXT,
    lag_days INTEGER,
    max_lag_days INTEGER,
    formulation TEXT,
    analysis_version TEXT,
    analyzed_at TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_policy_reactions_condition
    ON policy_reactions(condition_change_id);
CREATE INDEX IF NOT EXISTS idx_policy_reactions_policy
    ON policy_reactions(policy_change_id);
CREATE INDEX IF NOT EXISTS idx_policy_reactions_bank
    ON policy_reactions(central_bank);
CREATE INDEX IF NOT EXISTS idx_policy_reactions_publications
    ON policy_reactions(condition_publication_id, policy_publication_id);

CREATE TABLE IF NOT EXISTS monetary_policy_states (
    -- Phase 7 — derived, dated monetary policy state observations. Each row
    -- is ONE policy dimension of ONE central bank established by ONE
    -- FactChange: the current side of the change is the newest known value of
    -- the dimension, known at `observed_at` (meeting_date else
    -- publication_date of the current-side publication). `state_id` is a
    -- deterministic SHA-256 over (central_bank, source_change_id), so
    -- re-running the analysis updates the row instead of duplicating it.
    -- `synthesized` is a constant 1 (never a Fact, never inferred, never a
    -- stance/forecast/trading signal). Provenance is denormalized from the
    -- current side up to its change / fact / publication / document.
    state_id TEXT PRIMARY KEY,
    central_bank TEXT,
    synthesized INTEGER NOT NULL DEFAULT 1,
    source_change_id TEXT NOT NULL,
    dimension_key TEXT NOT NULL,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value_kind TEXT,
    qualifier TEXT,
    period_kind TEXT,
    period_value TEXT,
    period_label TEXT,
    publication_type TEXT NOT NULL,
    value_json TEXT,
    previous_value_json TEXT,
    observed_at TEXT,
    publication_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    effective_date TEXT,
    source_text TEXT,
    analysis_version TEXT,
    analyzed_at TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_policy_states_bank
    ON monetary_policy_states(central_bank);
CREATE INDEX IF NOT EXISTS idx_policy_states_change
    ON monetary_policy_states(source_change_id);
CREATE INDEX IF NOT EXISTS idx_policy_states_dimension
    ON monetary_policy_states(dimension_key);
CREATE INDEX IF NOT EXISTS idx_policy_states_observed_at
    ON monetary_policy_states(observed_at);
CREATE INDEX IF NOT EXISTS idx_policy_states_publication
    ON monetary_policy_states(publication_id);

CREATE TABLE IF NOT EXISTS forex_fundamentals (
    -- Phase 8 — derived, dated forex fundamentals. Each row is ONE fundamental
    -- dimension of ONE economy (currency) established by ONE source
    -- observation: a MonetaryPolicyState (Phase 7, source_kind
    -- 'monetary_state') or a Fact (Phase 4, source_kind 'fact'). `currency` is
    -- the ISO code of the economy (CentralBank.currency, canonical). The
    -- dimension is the currency-independent lineage `lineage_key` (subject,
    -- predicate, value_kind, canonical period, qualifier, publication_type);
    -- `dimension_key` is that lineage scoped to the currency. `fundamental_id`
    -- is a deterministic SHA-256 over (currency, source_kind, source_id), so
    -- re-running the analysis updates the row instead of duplicating it.
    -- `synthesized` is a constant 1 (never a Fact, never inferred, never a
    -- stance/forecast/fair value/trading signal). Provenance is denormalized
    -- from the source up to its publication / document.
    fundamental_id TEXT PRIMARY KEY,
    currency TEXT,
    synthesized INTEGER NOT NULL DEFAULT 1,
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    central_bank TEXT,
    dimension_key TEXT NOT NULL,
    lineage_key TEXT NOT NULL,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value_kind TEXT,
    qualifier TEXT,
    period_kind TEXT,
    period_value TEXT,
    period_label TEXT,
    publication_type TEXT NOT NULL,
    value_json TEXT,
    observed_at TEXT,
    publication_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    effective_date TEXT,
    source_text TEXT,
    analysis_version TEXT,
    analyzed_at TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_forex_fundamentals_currency
    ON forex_fundamentals(currency);
CREATE INDEX IF NOT EXISTS idx_forex_fundamentals_source
    ON forex_fundamentals(source_kind, source_id);
CREATE INDEX IF NOT EXISTS idx_forex_fundamentals_lineage
    ON forex_fundamentals(lineage_key);
CREATE INDEX IF NOT EXISTS idx_forex_fundamentals_observed_at
    ON forex_fundamentals(observed_at);
CREATE INDEX IF NOT EXISTS idx_forex_fundamentals_publication
    ON forex_fundamentals(publication_id);

CREATE TABLE IF NOT EXISTS forex_differentials (
    -- Phase 8 — derived, dated forex differentials. Each row is ONE
    -- arithmetic comparison of two fundamentals of two different economies on
    -- an explicitly declared shared dimension (`dimension_key` = the
    -- currency-independent lineage). The pair is ordered (base_currency /
    -- quote_currency) and the convention is never silently inverted. The quote
    -- observation is the latest of the lineage with observed_at <=
    -- base_observed_at (no look-ahead). `differential_id` is a deterministic
    -- SHA-256 over (base_currency, quote_currency, subject, predicate,
    -- base_source_id, quote_source_id). `synthesized` is a constant 1. Both
    -- sides carry full denormalized provenance; `value` is the arithmetic
    -- difference base_value - quote_value in the same unit/kind.
    differential_id TEXT PRIMARY KEY,
    base_currency TEXT,
    quote_currency TEXT,
    synthesized INTEGER NOT NULL DEFAULT 1,
    dimension_key TEXT NOT NULL,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value_kind TEXT,
    qualifier TEXT,
    period_kind TEXT,
    period_value TEXT,
    period_label TEXT,
    publication_type TEXT NOT NULL,
    base_fundamental_id TEXT NOT NULL,
    base_source_kind TEXT NOT NULL,
    base_source_id TEXT NOT NULL,
    base_central_bank TEXT,
    base_value_json TEXT,
    base_observed_at TEXT,
    base_publication_id TEXT,
    base_document_id TEXT,
    base_effective_date TEXT,
    base_source_text TEXT,
    quote_fundamental_id TEXT NOT NULL,
    quote_source_kind TEXT NOT NULL,
    quote_source_id TEXT NOT NULL,
    quote_central_bank TEXT,
    quote_value_json TEXT,
    quote_observed_at TEXT,
    quote_publication_id TEXT,
    quote_document_id TEXT,
    quote_effective_date TEXT,
    quote_source_text TEXT,
    value_json TEXT,
    formulation TEXT,
    analysis_version TEXT,
    analyzed_at TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_forex_differentials_base
    ON forex_differentials(base_currency);
CREATE INDEX IF NOT EXISTS idx_forex_differentials_quote
    ON forex_differentials(quote_currency);
CREATE INDEX IF NOT EXISTS idx_forex_differentials_lineage
    ON forex_differentials(dimension_key);
CREATE INDEX IF NOT EXISTS idx_forex_differentials_observed_at
    ON forex_differentials(base_observed_at);
CREATE INDEX IF NOT EXISTS idx_forex_differentials_publication
    ON forex_differentials(base_publication_id, quote_publication_id);
"""


class ActiveDiscoveryError(Exception):
    """A discovery campaign is already running/paused; only one may exist.

    Raised by ``start_discovery_run`` so the invariant "0 or 1 active campaign"
    is guaranteed by the Store itself, never by the GUI or the caller.
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
        # Concurrent claimers of the single active discovery campaign (or any
        # other writer) block briefly instead of failing immediately on the
        # write lock — the campaign-launch guard in `start_discovery_run`
        # serializes on this.
        self._conn.execute("PRAGMA busy_timeout=5000")
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
        try:
            self._conn.execute("ALTER TABLE discovery_runs ADD COLUMN pid INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            self._conn.execute("ALTER TABLE discovery_runs ADD COLUMN date_start TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            self._conn.execute("ALTER TABLE discovery_runs ADD COLUMN date_end TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            self._conn.execute(
                "ALTER TABLE discovery_runs ADD COLUMN sources_total INTEGER DEFAULT 0"
            )
        except sqlite3.OperationalError:
            pass
        try:
            self._conn.execute(
                "ALTER TABLE discovery_runs ADD COLUMN sources_completed INTEGER DEFAULT 0"
            )
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
            # A discovery that carries no date (e.g. a sitemap entry, whose
            # ``lastmod`` is a crawl signal, not a publication date) must never
            # blank an already-known temporal identity.
            publication_date = pub.publication_date if pub.publication_date is not None else existing.publication_date
            meeting_date = pub.meeting_date if pub.meeting_date is not None else existing.meeting_date
            changed = (
                existing.title != pub.title
                or existing.url != pub.url
                or (pub.publication_date is not None and existing.publication_date != pub.publication_date)
                or (pub.meeting_date is not None and existing.meeting_date != pub.meeting_date)
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
                    iso(publication_date),
                    iso(meeting_date),
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

    def set_publication_date_if_missing(
        self,
        publication_id: str,
        date: datetime,
        *,
        source: str | None = None,
    ) -> bool:
        """Refine a publication's temporal identity from an authoritative date
        (e.g. structured document metadata) when it has none yet.

        Only ever *adds* a date that is missing — it never overwrites an
        existing ``publication_date`` (a crawl signal must never clobber it).
        ``source`` is recorded in ``extra.publication_date_source`` for
        provenance. Returns ``True`` when the date was set.
        """
        row = self._conn.execute(
            "SELECT publication_date, extra_json FROM publications WHERE id=?",
            (publication_id,),
        ).fetchone()
        if row is None or row["publication_date"] is not None:
            return False
        extra = json.loads(row["extra_json"] or "{}")
        if source:
            extra["publication_date_source"] = source
        self._conn.execute(
            "UPDATE publications SET publication_date=?, extra_json=?, updated_at=? WHERE id=?",
            (iso(date), json.dumps(extra), iso(now_utc()), publication_id),
        )
        self._conn.commit()
        return True

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
    # Discovery campaign report layer (run lifecycle + result snapshot)
    # ------------------------------------------------------------------

    def start_discovery_run(
        self,
        run_id: str,
        banks,
        pid: int | None = None,
        date_start: str | None = None,
        date_end: str | None = None,
        sources_total: int | None = None,
    ) -> None:
        """Record that a discovery campaign started (status ``running``).

        ``pid`` is the OS process running the campaign (for the desktop GUI's
        pause / resume / stop controls); it is optional so CLI-driven campaigns
        stay recordable too. ``date_start`` / ``date_end`` are the campaign's
        publication-date window (ISO), persisted as a property of that run.
        ``sources_total`` is the number of sources the campaign will discover
        (fixed at launch so the GUI can show ``0 / N`` immediately);
        ``sources_completed`` starts at 0 and is advanced by the Core via
        :meth:`set_discovery_progress` as each source actually finishes.

        Only one campaign may be active (``running`` or ``paused``) at a time.
        The claim is taken in a write-locked transaction so two launchers
        racing (e.g. two GUI launches) cannot both start: the second one raises
        :class:`ActiveDiscoveryError`.
        """
        started = iso(now_utc())
        banks_json = json.dumps(list(banks or ()))
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            existing = self._conn.execute(
                "SELECT run_id FROM discovery_runs "
                "WHERE status IN ('running', 'paused') LIMIT 1"
            ).fetchone()
            if existing is not None:
                raise ActiveDiscoveryError(
                    f"a discovery campaign is already active: {existing['run_id']}"
                )
            self._conn.execute(
                """
                INSERT INTO discovery_runs
                    (run_id, started_at, status, candidates, banks_json, pid, date_start, date_end,
                     sources_total, sources_completed)
                VALUES (?, ?, 'running', 0, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(run_id) DO UPDATE SET
                    started_at=excluded.started_at,
                    status='running',
                    candidates=0,
                    error=NULL,
                    finished_at=NULL,
                    banks_json=excluded.banks_json,
                    pid=excluded.pid,
                    date_start=excluded.date_start,
                    date_end=excluded.date_end,
                    sources_total=excluded.sources_total,
                    sources_completed=0
                """,
                (run_id, started, banks_json, pid, date_start, date_end, sources_total or 0),
            )
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise

    def set_discovery_progress(self, run_id: str, *, completed: int, total: int) -> None:
        """Persist the Core-driven source progression of an active campaign.

        Called on the campaign's serialized writer thread (never from a worker):
        it only updates the two counters of an existing ``discovery_runs`` row,
        so ``sources_completed`` reflects real source terminations as they
        happen and ``sources_total`` stays fixed at its launch value. A run that
        no longer exists is a no-op.
        """
        self._conn.execute(
            "UPDATE discovery_runs SET sources_completed=?, sources_total=? WHERE run_id=?",
            (completed, total, run_id),
        )
        self._conn.commit()

    def set_discovery_run_control(self, run_id: str, status: str) -> None:
        """Flip the status of a *running/paused* campaign (pause / resume) from
        the controlling process; the campaign process itself is not involved
        (a SIGSTOPped process cannot write to the store)."""
        self._conn.execute(
            "UPDATE discovery_runs SET status=? WHERE run_id=?", (status, run_id)
        )
        self._conn.commit()

    def clear_discovery_cache(self) -> tuple[int, int]:
        """Drop the discovery *candidate cache*, preserving campaign history.

        Only ``discovery_candidates`` is a cache (per-run result snapshots).
        ``discovery_runs`` is campaign history (timing, status, scope, window)
        and is preserved; its cached candidate count is zeroed so the report
        never claims snapshots that no longer exist. Publications, documents,
        facts and every other pipeline table are untouched.
        """
        candidates = self._conn.execute("DELETE FROM discovery_candidates").rowcount
        self._conn.execute("UPDATE discovery_runs SET candidates=0")
        self._conn.commit()
        runs = self._conn.execute("SELECT COUNT(*) FROM discovery_runs").fetchone()[0]
        return int(runs), candidates

    def finish_discovery_run(
        self,
        run_id: str,
        *,
        status: str,
        candidates: list[dict] | None = None,
        error: str | None = None,
    ) -> None:
        """Finalize a discovery campaign: status + per-candidate snapshot.

        ``candidates`` is the *report* of what the run discovered (provenance
        fields such as method and new/known are computed by the caller from the
        publications the Core's discovery returned). The ``publications`` table
        stays the single source of truth for the pipeline itself.
        """
        rows = candidates or []
        finished = iso(now_utc())
        self._conn.execute(
            "DELETE FROM discovery_candidates WHERE run_id=?", (run_id,)
        )
        for position, c in enumerate(rows):
            self._conn.execute(
                """
                INSERT INTO discovery_candidates
                    (run_id, position, publication_id, central_bank, bank_name,
                     title, url, source_id, method, is_new, discovered_at, publication_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    position,
                    c.get("publication_id"),
                    c.get("bank_id"),
                    c.get("bank_name"),
                    c.get("title"),
                    c.get("url"),
                    c.get("source_id"),
                    c.get("method"),
                    1 if c.get("is_new") else 0,
                    c.get("discovered_at"),
                    c.get("publication_date"),
                ),
            )
        self._conn.execute(
            "UPDATE discovery_runs SET finished_at=?, status=?, candidates=?, error=? WHERE run_id=?",
            (finished, status, len(rows), error, run_id),
        )
        self._conn.commit()

    def _run_from_row(self, row: sqlite3.Row) -> dict | None:
        if row is None:
            return None
        run = {
            "run_id": row["run_id"],
            "status": row["status"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "error": row["error"],
            "candidates": row["candidates"] or 0,
            "banks": json.loads(row["banks_json"] or "[]"),
            "pid": row["pid"],
            "date_start": row["date_start"],
            "date_end": row["date_end"],
            "sources_total": row["sources_total"] or 0,
            "sources_completed": row["sources_completed"] or 0,
        }
        new, known = self.discovery_candidate_counts(row["run_id"])
        run["new"] = new
        run["known"] = known
        return run

    def discovery_candidate_counts(self, run_id: str) -> tuple[int, int]:
        """(new, known) candidate counts for a run's snapshot (0 when none)."""
        row = self._conn.execute(
            "SELECT COALESCE(SUM(is_new), 0) AS n, COALESCE(SUM(1 - is_new), 0) AS k "
            "FROM discovery_candidates WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return int(row["n"]), int(row["k"])

    def get_discovery_run(self, run_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM discovery_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        return self._run_from_row(row)

    def latest_discovery_run(self) -> dict | None:
        """The most recently started discovery campaign, if any."""
        row = self._conn.execute(
            "SELECT * FROM discovery_runs ORDER BY started_at DESC, run_id DESC LIMIT 1"
        ).fetchone()
        return self._run_from_row(row)

    def list_discovery_candidates(self, run_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM discovery_candidates WHERE run_id=? ORDER BY position",
            (run_id,),
        ).fetchall()
        return [
            {
                "publication_id": r["publication_id"],
                "bank_id": r["central_bank"],
                "bank_name": r["bank_name"],
                "title": r["title"],
                "url": r["url"],
                "source_id": r["source_id"],
                "method": r["method"],
                "is_new": bool(r["is_new"]),
                "discovered_at": r["discovered_at"],
                "publication_date": r["publication_date"],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Overview counters (read-only aggregates for the GUI)
    # ------------------------------------------------------------------

    def count_publications(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM publications").fetchone()["n"])

    def count_documents(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"])

    def count_normalized_documents(self) -> int:
        return int(
            self._conn.execute("SELECT COUNT(*) AS n FROM normalized_documents").fetchone()["n"]
        )

    def count_facts(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM facts").fetchone()["n"])

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
            PeriodKind,
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
                FactPeriod(
                    kind=PeriodKind(row["period_kind"]),
                    value=row["period_value"],
                    label=row["period_label"],
                )
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

    # ------------------------------------------------------------------
    # Phase 5 — fact changes
    # ------------------------------------------------------------------
    def save_change(self, change) -> None:
        """Persist one ``FactChange``, upserting by its deterministic id.

        Idempotent: re-saving the same change overwrites the row in place,
        preserving ``created_at``.
        """
        from .changes.base import ChangeType, FactChange

        if not isinstance(change, FactChange):
            raise TypeError(f"expected FactChange, got {type(change).__name__}")
        change_id = change.resolve_id()
        now_iso = iso(now_utc())
        existing = self._conn.execute(
            "SELECT created_at FROM fact_changes WHERE change_id = ?", (change_id,)
        ).fetchone()
        created_at = existing["created_at"] if existing else now_iso
        self._conn.execute(
            """
            INSERT INTO fact_changes
                (change_id, previous_fact_id, current_fact_id, change_type,
                 central_bank, subject, predicate, value_kind,
                 previous_value_json, current_value_json, delta_json,
                 identity_qualifier,
                 previous_period_kind, previous_period_value, previous_period_label,
                 current_period_kind, current_period_value, current_period_label,
                 previous_publication_id, current_publication_id,
                 previous_document_id, current_document_id,
                 previous_effective_date, current_effective_date,
                 previous_source_text, current_source_text,
                 analysis_version, analyzed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(change_id) DO UPDATE SET
                previous_fact_id=excluded.previous_fact_id,
                current_fact_id=excluded.current_fact_id,
                change_type=excluded.change_type,
                central_bank=excluded.central_bank,
                subject=excluded.subject,
                predicate=excluded.predicate,
                value_kind=excluded.value_kind,
                previous_value_json=excluded.previous_value_json,
                current_value_json=excluded.current_value_json,
                delta_json=excluded.delta_json,
                identity_qualifier=excluded.identity_qualifier,
                previous_period_kind=excluded.previous_period_kind,
                previous_period_value=excluded.previous_period_value,
                previous_period_label=excluded.previous_period_label,
                current_period_kind=excluded.current_period_kind,
                current_period_value=excluded.current_period_value,
                current_period_label=excluded.current_period_label,
                previous_publication_id=excluded.previous_publication_id,
                current_publication_id=excluded.current_publication_id,
                previous_document_id=excluded.previous_document_id,
                current_document_id=excluded.current_document_id,
                previous_effective_date=excluded.previous_effective_date,
                current_effective_date=excluded.current_effective_date,
                previous_source_text=excluded.previous_source_text,
                current_source_text=excluded.current_source_text,
                analysis_version=excluded.analysis_version,
                analyzed_at=excluded.analyzed_at,
                updated_at=excluded.updated_at
            """,
            (
                change_id,
                change.previous_fact_id,
                change.current_fact_id,
                change.change_type.value,
                change.central_bank,
                change.subject,
                change.predicate,
                change.value_kind,
                json.dumps(change.previous_value.to_dict()) if change.previous_value else None,
                json.dumps(change.current_value.to_dict()) if change.current_value else None,
                json.dumps(change.delta.to_dict()) if change.delta else None,
                change.identity_qualifier,
                change.previous_period.kind.value if change.previous_period else None,
                change.previous_period.value if change.previous_period else None,
                change.previous_period.label if change.previous_period else None,
                change.current_period.kind.value if change.current_period else None,
                change.current_period.value if change.current_period else None,
                change.current_period.label if change.current_period else None,
                change.previous_publication_id,
                change.current_publication_id,
                change.previous_document_id,
                change.current_document_id,
                iso(change.previous_effective_date),
                iso(change.current_effective_date),
                change.previous_source_text,
                change.current_source_text,
                change.analysis_version,
                iso(change.analyzed_at),
                created_at,
                now_iso,
            ),
        )
        self._conn.commit()

    def save_changes(self, changes) -> int:
        """Persist a list of ``FactChange`` (or a ``FactChangeResult``). Returns count."""
        if hasattr(changes, "changes"):
            changes = changes.changes
        count = 0
        for change in changes:
            self.save_change(change)
            count += 1
        return count

    def get_change(self, change_id: str):
        row = self._conn.execute(
            "SELECT * FROM fact_changes WHERE change_id = ?", (change_id,)
        ).fetchone()
        return self._change_from_row(row) if row else None

    def get_changes(
        self,
        *,
        bank: str | tuple[str, ...] | None = None,
        subject: str | None = None,
        change_type: str | None = None,
        previous_fact_id: str | None = None,
        current_fact_id: str | None = None,
        publication_id: str | None = None,
        limit: int | None = None,
    ) -> list:
        query = "SELECT * FROM fact_changes"
        clauses: list[str] = []
        params: list = []
        if bank is not None:
            banks = (bank,) if isinstance(bank, str) else tuple(bank)
            if banks:
                clauses.append(f"central_bank IN ({','.join('?' * len(banks))})")
                params.extend(banks)
        if subject is not None:
            clauses.append("subject = ?")
            params.append(subject)
        if change_type is not None:
            clauses.append("change_type = ?")
            params.append(change_type)
        if previous_fact_id is not None:
            clauses.append("previous_fact_id = ?")
            params.append(previous_fact_id)
        if current_fact_id is not None:
            clauses.append("current_fact_id = ?")
            params.append(current_fact_id)
        if publication_id is not None:
            clauses.append("(previous_publication_id = ? OR current_publication_id = ?)")
            params.extend((publication_id, publication_id))
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY subject, previous_publication_id, current_publication_id, change_id"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [self._change_from_row(r) for r in rows]

    def delete_changes_for_document(self, document_id: str) -> int:
        """Delete every change involving a document (either side)."""
        cursor = self._conn.execute(
            "DELETE FROM fact_changes WHERE previous_document_id = ? OR current_document_id = ?",
            (document_id, document_id),
        )
        self._conn.commit()
        return cursor.rowcount

    def delete_changes_for_publication(self, publication_id: str) -> int:
        """Delete every change involving a publication (either side)."""
        cursor = self._conn.execute(
            "DELETE FROM fact_changes WHERE previous_publication_id = ? OR current_publication_id = ?",
            (publication_id, publication_id),
        )
        self._conn.commit()
        return cursor.rowcount

    def delete_changes(self, *, bank: str | None = None) -> int:
        """Delete every change of a bank (or of the whole store)."""
        if bank is not None:
            cursor = self._conn.execute(
                "DELETE FROM fact_changes WHERE central_bank = ?", (bank,)
            )
        else:
            cursor = self._conn.execute("DELETE FROM fact_changes")
        self._conn.commit()
        return cursor.rowcount

    def rebuild_changes(self, changes, *, bank: str | None = None) -> int:
        """Replace a bank's (or the store's) changes with ``changes`` in one
        transaction.

        ``changes`` is a list of ``FactChange`` or a ``FactChangeResult``.
        ``fact_changes`` is derived data: the bank scope is fully recomputed
        each time, so re-analysis is idempotent, an empty result clears the
        scope, and no stale change survives the facts it relates.
        """
        from .changes.base import FactChange

        if hasattr(changes, "changes"):
            changes = changes.changes
        try:
            if bank is not None:
                self._conn.execute("DELETE FROM fact_changes WHERE central_bank = ?", (bank,))
            else:
                self._conn.execute("DELETE FROM fact_changes")
            count = 0
            for change in changes:
                if not isinstance(change, FactChange):
                    raise TypeError(f"expected FactChange, got {type(change).__name__}")
                change_id = change.resolve_id()
                now_iso = iso(now_utc())
                self._conn.execute(
                    """
                    INSERT INTO fact_changes
                        (change_id, previous_fact_id, current_fact_id, change_type,
                         central_bank, subject, predicate, value_kind,
                         previous_value_json, current_value_json, delta_json,
                         identity_qualifier,
                         previous_period_kind, previous_period_value, previous_period_label,
                         current_period_kind, current_period_value, current_period_label,
                         previous_publication_id, current_publication_id,
                         previous_document_id, current_document_id,
                         previous_effective_date, current_effective_date,
                         previous_source_text, current_source_text,
                         analysis_version, analyzed_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        change_id,
                        change.previous_fact_id,
                        change.current_fact_id,
                        change.change_type.value,
                        change.central_bank,
                        change.subject,
                        change.predicate,
                        change.value_kind,
                        json.dumps(change.previous_value.to_dict()) if change.previous_value else None,
                        json.dumps(change.current_value.to_dict()) if change.current_value else None,
                        json.dumps(change.delta.to_dict()) if change.delta else None,
                        change.identity_qualifier,
                        change.previous_period.kind.value if change.previous_period else None,
                        change.previous_period.value if change.previous_period else None,
                        change.previous_period.label if change.previous_period else None,
                        change.current_period.kind.value if change.current_period else None,
                        change.current_period.value if change.current_period else None,
                        change.current_period.label if change.current_period else None,
                        change.previous_publication_id,
                        change.current_publication_id,
                        change.previous_document_id,
                        change.current_document_id,
                        iso(change.previous_effective_date),
                        iso(change.current_effective_date),
                        change.previous_source_text,
                        change.current_source_text,
                        change.analysis_version,
                        iso(change.analyzed_at),
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
    def _change_from_row(row: sqlite3.Row):
        from .changes.base import ChangeType, FactChange
        from .facts.base import FactPeriod, FactValue, PeriodKind

        return FactChange(
            change_id=row["change_id"],
            previous_fact_id=row["previous_fact_id"],
            current_fact_id=row["current_fact_id"],
            change_type=ChangeType(row["change_type"]),
            central_bank=row["central_bank"],
            subject=row["subject"],
            predicate=row["predicate"],
            value_kind=row["value_kind"],
            previous_value=(
                FactValue.from_dict(json.loads(row["previous_value_json"]))
                if row["previous_value_json"]
                else None
            ),
            current_value=(
                FactValue.from_dict(json.loads(row["current_value_json"]))
                if row["current_value_json"]
                else None
            ),
            delta=FactValue.from_dict(json.loads(row["delta_json"])) if row["delta_json"] else None,
            identity_qualifier=row["identity_qualifier"] or "",
            previous_period=(
                FactPeriod(
                    kind=PeriodKind(row["previous_period_kind"]),
                    value=row["previous_period_value"],
                    label=row["previous_period_label"],
                )
                if row["previous_period_kind"]
                else None
            ),
            current_period=(
                FactPeriod(
                    kind=PeriodKind(row["current_period_kind"]),
                    value=row["current_period_value"],
                    label=row["current_period_label"],
                )
                if row["current_period_kind"]
                else None
            ),
            previous_publication_id=row["previous_publication_id"],
            current_publication_id=row["current_publication_id"],
            previous_document_id=row["previous_document_id"],
            current_document_id=row["current_document_id"],
            previous_effective_date=from_iso(row["previous_effective_date"]),
            current_effective_date=from_iso(row["current_effective_date"]),
            previous_source_text=row["previous_source_text"],
            current_source_text=row["current_source_text"],
            analysis_version=row["analysis_version"],
            analyzed_at=from_iso(row["analyzed_at"]),
        )

    # ------------------------------------------------------------------
    # Phase 6 — temporal relationships (legacy table name "policy_reactions")
    # ------------------------------------------------------------------
    def save_temporal_relationship(self, reaction) -> None:
        """Persist one :class:`TemporalRelationship`, upserting by its deterministic id.

        Idempotent: re-saving the same reaction overwrites the row in place,
        preserving ``created_at``.
        """
        from .temporal_relationships.base import TemporalRelationship

        if not isinstance(reaction, TemporalRelationship):
            raise TypeError(f"expected TemporalRelationship, got {type(reaction).__name__}")
        reaction_id = reaction.resolve_id()
        now_iso = iso(now_utc())
        existing = self._conn.execute(
            "SELECT created_at FROM policy_reactions WHERE reaction_id = ?", (reaction_id,)
        ).fetchone()
        created_at = existing["created_at"] if existing else now_iso
        self._conn.execute(
            """
            INSERT INTO policy_reactions
                (reaction_id, central_bank, inferred,
                 condition_change_id, condition_subject, condition_predicate,
                 condition_value_kind, condition_previous_value_json,
                 condition_current_value_json,
                 condition_period_kind, condition_period_value, condition_period_label,
                 condition_publication_id, condition_document_id,
                 condition_effective_date, condition_source_text, condition_observed_at,
                 policy_change_id, policy_subject, policy_predicate,
                 policy_value_kind, policy_previous_value_json,
                 policy_current_value_json,
                 policy_period_kind, policy_period_value, policy_period_label,
                 policy_publication_id, policy_document_id,
                 policy_effective_date, policy_source_text, policy_observed_at,
                 lag_days, max_lag_days, formulation,
                 analysis_version, analyzed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(reaction_id) DO UPDATE SET
                central_bank=excluded.central_bank,
                inferred=excluded.inferred,
                condition_change_id=excluded.condition_change_id,
                condition_subject=excluded.condition_subject,
                condition_predicate=excluded.condition_predicate,
                condition_value_kind=excluded.condition_value_kind,
                condition_previous_value_json=excluded.condition_previous_value_json,
                condition_current_value_json=excluded.condition_current_value_json,
                condition_period_kind=excluded.condition_period_kind,
                condition_period_value=excluded.condition_period_value,
                condition_period_label=excluded.condition_period_label,
                condition_publication_id=excluded.condition_publication_id,
                condition_document_id=excluded.condition_document_id,
                condition_effective_date=excluded.condition_effective_date,
                condition_source_text=excluded.condition_source_text,
                condition_observed_at=excluded.condition_observed_at,
                policy_change_id=excluded.policy_change_id,
                policy_subject=excluded.policy_subject,
                policy_predicate=excluded.policy_predicate,
                policy_value_kind=excluded.policy_value_kind,
                policy_previous_value_json=excluded.policy_previous_value_json,
                policy_current_value_json=excluded.policy_current_value_json,
                policy_period_kind=excluded.policy_period_kind,
                policy_period_value=excluded.policy_period_value,
                policy_period_label=excluded.policy_period_label,
                policy_publication_id=excluded.policy_publication_id,
                policy_document_id=excluded.policy_document_id,
                policy_effective_date=excluded.policy_effective_date,
                policy_source_text=excluded.policy_source_text,
                policy_observed_at=excluded.policy_observed_at,
                lag_days=excluded.lag_days,
                max_lag_days=excluded.max_lag_days,
                formulation=excluded.formulation,
                analysis_version=excluded.analysis_version,
                analyzed_at=excluded.analyzed_at,
                updated_at=excluded.updated_at
            """,
            (
                reaction_id,
                reaction.central_bank,
                1 if reaction.inferred else 0,
                reaction.condition_change_id,
                reaction.condition_subject,
                reaction.condition_predicate,
                reaction.condition_value_kind,
                json.dumps(reaction.condition_previous_value.to_dict()) if reaction.condition_previous_value else None,
                json.dumps(reaction.condition_current_value.to_dict()) if reaction.condition_current_value else None,
                reaction.condition_period.kind.value if reaction.condition_period else None,
                reaction.condition_period.value if reaction.condition_period else None,
                reaction.condition_period.label if reaction.condition_period else None,
                reaction.condition_publication_id,
                reaction.condition_document_id,
                iso(reaction.condition_effective_date),
                reaction.condition_source_text,
                iso(reaction.condition_observed_at),
                reaction.policy_change_id,
                reaction.policy_subject,
                reaction.policy_predicate,
                reaction.policy_value_kind,
                json.dumps(reaction.policy_previous_value.to_dict()) if reaction.policy_previous_value else None,
                json.dumps(reaction.policy_current_value.to_dict()) if reaction.policy_current_value else None,
                reaction.policy_period.kind.value if reaction.policy_period else None,
                reaction.policy_period.value if reaction.policy_period else None,
                reaction.policy_period.label if reaction.policy_period else None,
                reaction.policy_publication_id,
                reaction.policy_document_id,
                iso(reaction.policy_effective_date),
                reaction.policy_source_text,
                iso(reaction.policy_observed_at),
                reaction.lag_days,
                reaction.max_lag_days,
                reaction.formulation,
                reaction.analysis_version,
                iso(reaction.analyzed_at),
                created_at,
                now_iso,
            ),
        )
        self._conn.commit()

    def save_temporal_relationships(self, reactions) -> int:
        """Persist a list of :class:`TemporalRelationship` (or a
        :class:`TemporalRelationshipResult`). Returns count."""
        if hasattr(reactions, "reactions"):
            reactions = reactions.reactions
        count = 0
        for reaction in reactions:
            self.save_reaction(reaction)
            count += 1
        return count

    def get_temporal_relationship(self, relationship_id: str):
        row = self._conn.execute(
            "SELECT * FROM policy_reactions WHERE reaction_id = ?", (relationship_id,)
        ).fetchone()
        return self._temporal_relationship_from_row(row) if row else None

    # legacy alias
    def get_reaction(self, reaction_id: str):
        """Legacy alias for :meth:`get_temporal_relationship`."""
        return self.get_temporal_relationship(reaction_id)

    def get_temporal_relationships(
        self,
        *,
        bank: str | tuple[str, ...] | None = None,
        condition_change_id: str | None = None,
        policy_change_id: str | None = None,
        subject: str | None = None,
        limit: int | None = None,
    ) -> list:
        query = "SELECT * FROM policy_reactions"
        clauses: list[str] = []
        params: list = []
        if bank is not None:
            banks = (bank,) if isinstance(bank, str) else tuple(bank)
            if banks:
                clauses.append(f"central_bank IN ({','.join('?' * len(banks))})")
                params.extend(banks)
        if condition_change_id is not None:
            clauses.append("condition_change_id = ?")
            params.append(condition_change_id)
        if policy_change_id is not None:
            clauses.append("policy_change_id = ?")
            params.append(policy_change_id)
        if subject is not None:
            clauses.append("(condition_subject = ? OR policy_subject = ?)")
            params.extend((subject, subject))
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY condition_subject, policy_subject, reaction_id"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [self._temporal_relationship_from_row(r) for r in rows]

    def delete_temporal_relationships(self, *, bank: str | None = None) -> int:
        """Delete every temporal relationship of a bank (or of the whole store)."""
        if bank is not None:
            cursor = self._conn.execute(
                "DELETE FROM policy_reactions WHERE central_bank = ?", (bank,)
            )
        else:
            cursor = self._conn.execute("DELETE FROM policy_reactions")
        self._conn.commit()
        return cursor.rowcount

    def delete_temporal_relationships_for_document(self, document_id: str) -> int:
        """Delete every temporal relationship involving a document (either side)."""
        cursor = self._conn.execute(
            "DELETE FROM policy_reactions WHERE condition_document_id = ? OR policy_document_id = ?",
            (document_id, document_id),
        )
        self._conn.commit()
        return cursor.rowcount

    def delete_temporal_relationships_for_publication(self, publication_id: str) -> int:
        """Delete every temporal relationship involving a publication (either side)."""
        cursor = self._conn.execute(
            "DELETE FROM policy_reactions WHERE condition_publication_id = ? OR policy_publication_id = ?",
            (publication_id, publication_id),
        )
        self._conn.commit()
        return cursor.rowcount

    def rebuild_temporal_relationships(self, reactions, *, bank: str | None = None) -> int:
        """Replace a bank's (or the store's) temporal relationships with ``reactions`` in one
        transaction.

        ``reactions`` is a list of :class:`TemporalRelationship` or a
        :class:`TemporalRelationshipResult`. ``policy_reactions`` is derived data: the bank
        scope is fully recomputed each time, so re-analysis is idempotent, an
        empty result clears the scope, and no stale reaction survives the
        changes it relates.
        """
        from .temporal_relationships.base import TemporalRelationship

        if hasattr(reactions, "reactions"):
            reactions = reactions.reactions
        try:
            if bank is not None:
                self._conn.execute(
                    "DELETE FROM policy_reactions WHERE central_bank = ?", (bank,)
                )
            else:
                self._conn.execute("DELETE FROM policy_reactions")
            count = 0
            for reaction in reactions:
                if not isinstance(reaction, TemporalRelationship):
                    raise TypeError(
                        f"expected TemporalRelationship, got {type(reaction).__name__}"
                    )
                reaction_id = reaction.resolve_id()
                now_iso = iso(now_utc())
                self._conn.execute(
                    """
                    INSERT INTO policy_reactions
                        (reaction_id, central_bank, inferred,
                         condition_change_id, condition_subject, condition_predicate,
                         condition_value_kind, condition_previous_value_json,
                         condition_current_value_json,
                         condition_period_kind, condition_period_value, condition_period_label,
                         condition_publication_id, condition_document_id,
                         condition_effective_date, condition_source_text, condition_observed_at,
                         policy_change_id, policy_subject, policy_predicate,
                         policy_value_kind, policy_previous_value_json,
                         policy_current_value_json,
                         policy_period_kind, policy_period_value, policy_period_label,
                         policy_publication_id, policy_document_id,
                         policy_effective_date, policy_source_text, policy_observed_at,
                         lag_days, max_lag_days, formulation,
                         analysis_version, analyzed_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        reaction_id,
                        reaction.central_bank,
                        1 if reaction.inferred else 0,
                        reaction.condition_change_id,
                        reaction.condition_subject,
                        reaction.condition_predicate,
                        reaction.condition_value_kind,
                        json.dumps(reaction.condition_previous_value.to_dict()) if reaction.condition_previous_value else None,
                        json.dumps(reaction.condition_current_value.to_dict()) if reaction.condition_current_value else None,
                        reaction.condition_period.kind.value if reaction.condition_period else None,
                        reaction.condition_period.value if reaction.condition_period else None,
                        reaction.condition_period.label if reaction.condition_period else None,
                        reaction.condition_publication_id,
                        reaction.condition_document_id,
                        iso(reaction.condition_effective_date),
                        reaction.condition_source_text,
                        iso(reaction.condition_observed_at),
                        reaction.policy_change_id,
                        reaction.policy_subject,
                        reaction.policy_predicate,
                        reaction.policy_value_kind,
                        json.dumps(reaction.policy_previous_value.to_dict()) if reaction.policy_previous_value else None,
                        json.dumps(reaction.policy_current_value.to_dict()) if reaction.policy_current_value else None,
                        reaction.policy_period.kind.value if reaction.policy_period else None,
                        reaction.policy_period.value if reaction.policy_period else None,
                        reaction.policy_period.label if reaction.policy_period else None,
                        reaction.policy_publication_id,
                        reaction.policy_document_id,
                        iso(reaction.policy_effective_date),
                        reaction.policy_source_text,
                        iso(reaction.policy_observed_at),
                        reaction.lag_days,
                        reaction.max_lag_days,
                        reaction.formulation,
                        reaction.analysis_version,
                        iso(reaction.analyzed_at),
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
    def _temporal_relationship_from_row(row: sqlite3.Row):
        from .facts.base import FactPeriod, PeriodKind, FactValue
        from .temporal_relationships.base import TemporalRelationship

        return TemporalRelationship(
            reaction_id=row["reaction_id"],
            central_bank=row["central_bank"],
            inferred=bool(row["inferred"]),
            condition_change_id=row["condition_change_id"],
            condition_subject=row["condition_subject"],
            condition_predicate=row["condition_predicate"],
            condition_value_kind=row["condition_value_kind"],
            condition_previous_value=(
                FactValue.from_dict(json.loads(row["condition_previous_value_json"]))
                if row["condition_previous_value_json"]
                else None
            ),
            condition_current_value=(
                FactValue.from_dict(json.loads(row["condition_current_value_json"]))
                if row["condition_current_value_json"]
                else None
            ),
            condition_period=(
                FactPeriod(
                    kind=PeriodKind(row["condition_period_kind"]),
                    value=row["condition_period_value"],
                    label=row["condition_period_label"],
                )
                if row["condition_period_kind"]
                else None
            ),
            condition_publication_id=row["condition_publication_id"],
            condition_document_id=row["condition_document_id"],
            condition_effective_date=from_iso(row["condition_effective_date"]),
            condition_source_text=row["condition_source_text"],
            condition_observed_at=from_iso(row["condition_observed_at"]),
            policy_change_id=row["policy_change_id"],
            policy_subject=row["policy_subject"],
            policy_predicate=row["policy_predicate"],
            policy_value_kind=row["policy_value_kind"],
            policy_previous_value=(
                FactValue.from_dict(json.loads(row["policy_previous_value_json"]))
                if row["policy_previous_value_json"]
                else None
            ),
            policy_current_value=(
                FactValue.from_dict(json.loads(row["policy_current_value_json"]))
                if row["policy_current_value_json"]
                else None
            ),
            policy_period=(
                FactPeriod(
                    kind=PeriodKind(row["policy_period_kind"]),
                    value=row["policy_period_value"],
                    label=row["policy_period_label"],
                )
                if row["policy_period_kind"]
                else None
            ),
            policy_publication_id=row["policy_publication_id"],
            policy_document_id=row["policy_document_id"],
            policy_effective_date=from_iso(row["policy_effective_date"]),
            policy_source_text=row["policy_source_text"],
            policy_observed_at=from_iso(row["policy_observed_at"]),
            lag_days=row["lag_days"],
            max_lag_days=row["max_lag_days"],
            formulation=row["formulation"],
            analysis_version=row["analysis_version"],
            analyzed_at=from_iso(row["analyzed_at"]),
        )

    # ------------------------------------------------------------------
    # Legacy "reaction" method aliases (delegate to the canonical names)
    # ------------------------------------------------------------------
    def save_reaction(self, reaction) -> None:
        """Legacy alias for :meth:`save_temporal_relationship`."""
        return self.save_temporal_relationship(reaction)

    def save_reactions(self, reactions) -> int:
        """Legacy alias for :meth:`save_temporal_relationships`."""
        return self.save_temporal_relationships(reactions)

    def get_reactions(
        self,
        *,
        bank: str | tuple[str, ...] | None = None,
        condition_change_id: str | None = None,
        policy_change_id: str | None = None,
        subject: str | None = None,
        limit: int | None = None,
    ) -> list:
        """Legacy alias for :meth:`get_temporal_relationships`."""
        return self.get_temporal_relationships(
            bank=bank,
            condition_change_id=condition_change_id,
            policy_change_id=policy_change_id,
            subject=subject,
            limit=limit,
        )

    def delete_reactions(self, *, bank: str | None = None) -> int:
        """Legacy alias for :meth:`delete_temporal_relationships`."""
        return self.delete_temporal_relationships(bank=bank)

    def delete_reactions_for_document(self, document_id: str) -> int:
        """Legacy alias for :meth:`delete_temporal_relationships_for_document`."""
        return self.delete_temporal_relationships_for_document(document_id)

    def delete_reactions_for_publication(self, publication_id: str) -> int:
        """Legacy alias for :meth:`delete_temporal_relationships_for_publication`."""
        return self.delete_temporal_relationships_for_publication(publication_id)

    def rebuild_reactions(self, reactions, *, bank: str | None = None) -> int:
        """Legacy alias for :meth:`rebuild_temporal_relationships`."""
        return self.rebuild_temporal_relationships(reactions, bank=bank)

    @staticmethod
    def _reaction_from_row(row: sqlite3.Row):
        """Legacy alias for :meth:`_temporal_relationship_from_row`."""
        return Store._temporal_relationship_from_row(row)

    # ------------------------------------------------------------------
    # Phase 7 — monetary policy states
    # ------------------------------------------------------------------
    def save_policy_state(self, state) -> None:
        """Persist one ``MonetaryPolicyState``, upserting by its deterministic
        id.

        Idempotent: re-saving the same state overwrites the row in place,
        preserving ``created_at``.
        """
        from .states.base import MonetaryPolicyState

        if not isinstance(state, MonetaryPolicyState):
            raise TypeError(f"expected MonetaryPolicyState, got {type(state).__name__}")
        state_id = state.resolve_id()
        now_iso = iso(now_utc())
        existing = self._conn.execute(
            "SELECT created_at FROM monetary_policy_states WHERE state_id = ?", (state_id,)
        ).fetchone()
        created_at = existing["created_at"] if existing else now_iso
        self._conn.execute(
            """
            INSERT INTO monetary_policy_states
                (state_id, central_bank, synthesized, source_change_id,
                 dimension_key, subject, predicate, value_kind, qualifier,
                 period_kind, period_value, period_label, publication_type,
                 value_json, previous_value_json, observed_at,
                 publication_id, document_id, effective_date, source_text,
                 analysis_version, analyzed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(state_id) DO UPDATE SET
                central_bank=excluded.central_bank,
                synthesized=excluded.synthesized,
                source_change_id=excluded.source_change_id,
                dimension_key=excluded.dimension_key,
                subject=excluded.subject,
                predicate=excluded.predicate,
                value_kind=excluded.value_kind,
                qualifier=excluded.qualifier,
                period_kind=excluded.period_kind,
                period_value=excluded.period_value,
                period_label=excluded.period_label,
                publication_type=excluded.publication_type,
                value_json=excluded.value_json,
                previous_value_json=excluded.previous_value_json,
                observed_at=excluded.observed_at,
                publication_id=excluded.publication_id,
                document_id=excluded.document_id,
                effective_date=excluded.effective_date,
                source_text=excluded.source_text,
                analysis_version=excluded.analysis_version,
                analyzed_at=excluded.analyzed_at,
                updated_at=excluded.updated_at
            """,
            (
                state_id,
                state.central_bank,
                1 if state.synthesized else 0,
                state.source_change_id,
                state.dimension_key,
                state.subject,
                state.predicate,
                state.value_kind,
                state.qualifier,
                state.period.kind.value if state.period else None,
                state.period.value if state.period else None,
                state.period.label if state.period else None,
                state.publication_type,
                json.dumps(state.value.to_dict()) if state.value else None,
                json.dumps(state.previous_value.to_dict()) if state.previous_value else None,
                iso(state.observed_at),
                state.publication_id,
                state.document_id,
                iso(state.effective_date),
                state.source_text,
                state.analysis_version,
                iso(state.analyzed_at),
                created_at,
                now_iso,
            ),
        )
        self._conn.commit()

    def save_policy_states(self, states) -> int:
        """Persist a list of ``MonetaryPolicyState`` (or a
        ``MonetaryPolicyStateResult``). Returns count."""
        if hasattr(states, "states"):
            states = states.states
        count = 0
        for state in states:
            self.save_policy_state(state)
            count += 1
        return count

    def get_policy_state(self, state_id: str):
        row = self._conn.execute(
            "SELECT * FROM monetary_policy_states WHERE state_id = ?", (state_id,)
        ).fetchone()
        return self._state_from_row(row) if row else None

    def get_policy_states(
        self,
        *,
        bank: str | tuple[str, ...] | None = None,
        subject: str | None = None,
        predicate: str | None = None,
        source_change_id: str | None = None,
        publication_id: str | None = None,
        limit: int | None = None,
    ) -> list:
        query = "SELECT * FROM monetary_policy_states"
        clauses: list[str] = []
        params: list = []
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
        if source_change_id is not None:
            clauses.append("source_change_id = ?")
            params.append(source_change_id)
        if publication_id is not None:
            clauses.append("publication_id = ?")
            params.append(publication_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY subject, observed_at, state_id"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [self._state_from_row(r) for r in rows]

    def get_policy_state_as_of(self, bank: str, as_of=None) -> list:
        """Return the latest state entry per dimension with ``observed_at ≤
        as_of`` (the "state at a date" view, no look-ahead). ``as_of=None``
        means no upper bound. ``observed_at`` is the temporal reference of the
        current-side publication (meeting_date else publication_date);
        ``effective_date`` is never used as an observation time."""
        if as_of is not None:
            rows = self._conn.execute(
                "SELECT * FROM monetary_policy_states "
                "WHERE central_bank = ? AND observed_at <= ?",
                (bank, iso(as_of)),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM monetary_policy_states WHERE central_bank = ?",
                (bank,),
            ).fetchall()
        latest: dict[str, tuple] = {}
        for row in rows:
            key = row["dimension_key"]
            if key not in latest:
                latest[key] = row
                continue
            current, candidate = latest[key], row
            if candidate["observed_at"] > current["observed_at"]:
                latest[key] = candidate
            elif candidate["observed_at"] == current["observed_at"] and candidate["state_id"] < current["state_id"]:
                latest[key] = candidate
        return [self._state_from_row(r) for r in sorted(latest.values(), key=lambda r: r["state_id"])]

    def delete_policy_states(self, *, bank: str | None = None) -> int:
        """Delete every state of a bank (or of the whole store)."""
        if bank is not None:
            cursor = self._conn.execute(
                "DELETE FROM monetary_policy_states WHERE central_bank = ?", (bank,)
            )
        else:
            cursor = self._conn.execute("DELETE FROM monetary_policy_states")
        self._conn.commit()
        return cursor.rowcount

    def delete_policy_states_for_document(self, document_id: str) -> int:
        """Delete every state established from a document."""
        cursor = self._conn.execute(
            "DELETE FROM monetary_policy_states WHERE document_id = ?", (document_id,)
        )
        self._conn.commit()
        return cursor.rowcount

    def delete_policy_states_for_publication(self, publication_id: str) -> int:
        """Delete every state established from a publication."""
        cursor = self._conn.execute(
            "DELETE FROM monetary_policy_states WHERE publication_id = ?", (publication_id,)
        )
        self._conn.commit()
        return cursor.rowcount

    def rebuild_policy_states(self, states, *, bank: str | None = None) -> int:
        """Replace a bank's (or the store's) states with ``states`` in one
        transaction.

        ``states`` is a list of ``MonetaryPolicyState`` or a
        ``MonetaryPolicyStateResult``. ``monetary_policy_states`` is derived
        data: the bank scope is fully recomputed each time, so re-analysis is
        idempotent, an empty result clears the scope, and no stale state
        survives the change it summarizes.
        """
        from .states.base import MonetaryPolicyState

        if hasattr(states, "states"):
            states = states.states
        try:
            if bank is not None:
                self._conn.execute(
                    "DELETE FROM monetary_policy_states WHERE central_bank = ?", (bank,)
                )
            else:
                self._conn.execute("DELETE FROM monetary_policy_states")
            count = 0
            for state in states:
                if not isinstance(state, MonetaryPolicyState):
                    raise TypeError(
                        f"expected MonetaryPolicyState, got {type(state).__name__}"
                    )
                state_id = state.resolve_id()
                now_iso = iso(now_utc())
                self._conn.execute(
                    """
                    INSERT INTO monetary_policy_states
                        (state_id, central_bank, synthesized, source_change_id,
                         dimension_key, subject, predicate, value_kind, qualifier,
                         period_kind, period_value, period_label, publication_type,
                         value_json, previous_value_json, observed_at,
                         publication_id, document_id, effective_date, source_text,
                         analysis_version, analyzed_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state_id,
                        state.central_bank,
                        1 if state.synthesized else 0,
                        state.source_change_id,
                        state.dimension_key,
                        state.subject,
                        state.predicate,
                        state.value_kind,
                        state.qualifier,
                        state.period.kind.value if state.period else None,
                        state.period.value if state.period else None,
                        state.period.label if state.period else None,
                        state.publication_type,
                        json.dumps(state.value.to_dict()) if state.value else None,
                        json.dumps(state.previous_value.to_dict()) if state.previous_value else None,
                        iso(state.observed_at),
                        state.publication_id,
                        state.document_id,
                        iso(state.effective_date),
                        state.source_text,
                        state.analysis_version,
                        iso(state.analyzed_at),
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
    def _state_from_row(row: sqlite3.Row):
        from .facts.base import FactPeriod, PeriodKind, FactValue
        from .states.base import MonetaryPolicyState

        return MonetaryPolicyState(
            state_id=row["state_id"],
            central_bank=row["central_bank"],
            synthesized=bool(row["synthesized"]),
            source_change_id=row["source_change_id"],
            dimension_key=row["dimension_key"],
            subject=row["subject"],
            predicate=row["predicate"],
            value_kind=row["value_kind"],
            qualifier=row["qualifier"] or "",
            period=(
                FactPeriod(
                    kind=PeriodKind(row["period_kind"]),
                    value=row["period_value"],
                    label=row["period_label"],
                )
                if row["period_kind"]
                else None
            ),
            publication_type=row["publication_type"],
            value=(
                FactValue.from_dict(json.loads(row["value_json"]))
                if row["value_json"]
                else None
            ),
            previous_value=(
                FactValue.from_dict(json.loads(row["previous_value_json"]))
                if row["previous_value_json"]
                else None
            ),
            observed_at=from_iso(row["observed_at"]),
            publication_id=row["publication_id"],
            document_id=row["document_id"],
            effective_date=from_iso(row["effective_date"]),
            source_text=row["source_text"],
            analysis_version=row["analysis_version"],
            analyzed_at=from_iso(row["analyzed_at"]),
        )
    # ------------------------------------------------------------------
    # Phase 8 — forex fundamentals
    # ------------------------------------------------------------------
    def save_forex_fundamental(self, fundamental) -> None:
        """Persist one ``ForexFundamental``, upserting by its deterministic id.

        Idempotent: re-saving the same fundamental overwrites the row in place,
        preserving ``created_at``.
        """
        from .forex.base import ForexFundamental

        if not isinstance(fundamental, ForexFundamental):
            raise TypeError(
                f"expected ForexFundamental, got {type(fundamental).__name__}"
            )
        fundamental_id = fundamental.resolve_id()
        now_iso = iso(now_utc())
        existing = self._conn.execute(
            "SELECT created_at FROM forex_fundamentals WHERE fundamental_id = ?",
            (fundamental_id,),
        ).fetchone()
        created_at = existing["created_at"] if existing else now_iso
        self._conn.execute(
            """
            INSERT INTO forex_fundamentals
                (fundamental_id, currency, synthesized, source_kind, source_id,
                 central_bank, dimension_key, lineage_key, subject, predicate,
                 value_kind, qualifier, period_kind, period_value, period_label,
                 publication_type, value_json, observed_at, publication_id,
                 document_id, effective_date, source_text, analysis_version,
                 analyzed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fundamental_id) DO UPDATE SET
                currency=excluded.currency,
                synthesized=excluded.synthesized,
                source_kind=excluded.source_kind,
                source_id=excluded.source_id,
                central_bank=excluded.central_bank,
                dimension_key=excluded.dimension_key,
                lineage_key=excluded.lineage_key,
                subject=excluded.subject,
                predicate=excluded.predicate,
                value_kind=excluded.value_kind,
                qualifier=excluded.qualifier,
                period_kind=excluded.period_kind,
                period_value=excluded.period_value,
                period_label=excluded.period_label,
                publication_type=excluded.publication_type,
                value_json=excluded.value_json,
                observed_at=excluded.observed_at,
                publication_id=excluded.publication_id,
                document_id=excluded.document_id,
                effective_date=excluded.effective_date,
                source_text=excluded.source_text,
                analysis_version=excluded.analysis_version,
                analyzed_at=excluded.analyzed_at,
                updated_at=excluded.updated_at
            """,
            (
                fundamental_id,
                fundamental.currency,
                1 if fundamental.synthesized else 0,
                fundamental.source_kind,
                fundamental.source_id,
                fundamental.central_bank,
                fundamental.dimension_key,
                fundamental.lineage_key,
                fundamental.subject,
                fundamental.predicate,
                fundamental.value_kind,
                fundamental.qualifier,
                fundamental.period.kind.value if fundamental.period else None,
                fundamental.period.value if fundamental.period else None,
                fundamental.period.label if fundamental.period else None,
                fundamental.publication_type,
                json.dumps(fundamental.value.to_dict()) if fundamental.value else None,
                iso(fundamental.observed_at),
                fundamental.publication_id,
                fundamental.document_id,
                iso(fundamental.effective_date),
                fundamental.source_text,
                fundamental.analysis_version,
                iso(fundamental.analyzed_at),
                created_at,
                now_iso,
            ),
        )
        self._conn.commit()

    def save_forex_fundamentals(self, fundamentals) -> int:
        """Persist a list of ``ForexFundamental`` (or a
        ``ForexFundamentalResult``). Returns count."""
        if hasattr(fundamentals, "fundamentals"):
            fundamentals = fundamentals.fundamentals
        count = 0
        for fundamental in fundamentals:
            self.save_forex_fundamental(fundamental)
            count += 1
        return count

    def get_forex_fundamental(self, fundamental_id: str):
        row = self._conn.execute(
            "SELECT * FROM forex_fundamentals WHERE fundamental_id = ?",
            (fundamental_id,),
        ).fetchone()
        return self._fundamental_from_row(row) if row else None

    def get_forex_fundamentals(
        self,
        *,
        currency: str | tuple[str, ...] | None = None,
        source_kind: str | None = None,
        source_id: str | None = None,
        subject: str | None = None,
        predicate: str | None = None,
        publication_id: str | None = None,
        limit: int | None = None,
    ) -> list:
        query = "SELECT * FROM forex_fundamentals"
        clauses: list[str] = []
        params: list = []
        if currency is not None:
            currencies = (currency,) if isinstance(currency, str) else tuple(currency)
            if currencies:
                clauses.append(f"currency IN ({','.join('?' * len(currencies))})")
                params.extend(currencies)
        if source_kind is not None:
            clauses.append("source_kind = ?")
            params.append(source_kind)
        if source_id is not None:
            clauses.append("source_id = ?")
            params.append(source_id)
        if subject is not None:
            clauses.append("subject = ?")
            params.append(subject)
        if predicate is not None:
            clauses.append("predicate = ?")
            params.append(predicate)
        if publication_id is not None:
            clauses.append("publication_id = ?")
            params.append(publication_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY subject, observed_at, fundamental_id"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [self._fundamental_from_row(r) for r in rows]

    def get_fundamentals_as_of(self, currency: str, as_of=None) -> list:
        """Return the latest fundamental per lineage with ``observed_at ≤
        as_of`` (the "fundamentals at a date" view, no look-ahead).
        ``as_of=None`` means no upper bound. ``observed_at`` is the temporal
        reference of the source publication (meeting_date else
        publication_date); ``effective_date`` and ``period`` are never used as
        observation times."""
        if as_of is not None:
            rows = self._conn.execute(
                "SELECT * FROM forex_fundamentals "
                "WHERE currency = ? AND observed_at <= ?",
                (currency, iso(as_of)),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM forex_fundamentals WHERE currency = ?",
                (currency,),
            ).fetchall()
        latest: dict[str, tuple] = {}
        for row in rows:
            key = row["lineage_key"]
            if key not in latest:
                latest[key] = row
                continue
            current, candidate = latest[key], row
            if candidate["observed_at"] > current["observed_at"]:
                latest[key] = candidate
            elif candidate["observed_at"] == current["observed_at"] and candidate["fundamental_id"] < current["fundamental_id"]:
                latest[key] = candidate
        return [
            self._fundamental_from_row(r)
            for r in sorted(latest.values(), key=lambda r: r["fundamental_id"])
        ]

    def delete_forex_fundamentals(self, *, currency: str | None = None) -> int:
        """Delete every fundamental of a currency (or of the whole store)."""
        if currency is not None:
            cursor = self._conn.execute(
                "DELETE FROM forex_fundamentals WHERE currency = ?", (currency,)
            )
        else:
            cursor = self._conn.execute("DELETE FROM forex_fundamentals")
        self._conn.commit()
        return cursor.rowcount

    def delete_forex_fundamentals_for_document(self, document_id: str) -> int:
        """Delete every fundamental established from a document."""
        cursor = self._conn.execute(
            "DELETE FROM forex_fundamentals WHERE document_id = ?", (document_id,)
        )
        self._conn.commit()
        return cursor.rowcount

    def delete_forex_fundamentals_for_publication(self, publication_id: str) -> int:
        """Delete every fundamental established from a publication."""
        cursor = self._conn.execute(
            "DELETE FROM forex_fundamentals WHERE publication_id = ?",
            (publication_id,),
        )
        self._conn.commit()
        return cursor.rowcount

    def rebuild_forex_fundamentals(
        self, fundamentals, *, currency: str | None = None
    ) -> int:
        """Replace a currency's (or the store's) fundamentals with ``states`` in
        one transaction.

        ``fundamentals`` is a list of ``ForexFundamental`` or a
        ``ForexFundamentalResult``. ``forex_fundamentals`` is derived data: the
        currency scope is fully recomputed each time, so re-analysis is
        idempotent, an empty result clears the scope, and no stale fundamental
        survives the observation it summarizes.
        """
        from .forex.base import ForexFundamental

        if hasattr(fundamentals, "fundamentals"):
            fundamentals = fundamentals.fundamentals
        try:
            if currency is not None:
                self._conn.execute(
                    "DELETE FROM forex_fundamentals WHERE currency = ?", (currency,)
                )
            else:
                self._conn.execute("DELETE FROM forex_fundamentals")
            count = 0
            for fundamental in fundamentals:
                if not isinstance(fundamental, ForexFundamental):
                    raise TypeError(
                        f"expected ForexFundamental, got {type(fundamental).__name__}"
                    )
                if currency is not None and fundamental.currency != currency:
                    continue
                fundamental_id = fundamental.resolve_id()
                now_iso = iso(now_utc())
                self._conn.execute(
                    """
                    INSERT INTO forex_fundamentals
                        (fundamental_id, currency, synthesized, source_kind,
                         source_id, central_bank, dimension_key, lineage_key,
                         subject, predicate, value_kind, qualifier, period_kind,
                         period_value, period_label, publication_type, value_json,
                         observed_at, publication_id, document_id, effective_date,
                         source_text, analysis_version, analyzed_at, created_at,
                         updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fundamental_id,
                        fundamental.currency,
                        1 if fundamental.synthesized else 0,
                        fundamental.source_kind,
                        fundamental.source_id,
                        fundamental.central_bank,
                        fundamental.dimension_key,
                        fundamental.lineage_key,
                        fundamental.subject,
                        fundamental.predicate,
                        fundamental.value_kind,
                        fundamental.qualifier,
                        fundamental.period.kind.value if fundamental.period else None,
                        fundamental.period.value if fundamental.period else None,
                        fundamental.period.label if fundamental.period else None,
                        fundamental.publication_type,
                        json.dumps(fundamental.value.to_dict()) if fundamental.value else None,
                        iso(fundamental.observed_at),
                        fundamental.publication_id,
                        fundamental.document_id,
                        iso(fundamental.effective_date),
                        fundamental.source_text,
                        fundamental.analysis_version,
                        iso(fundamental.analyzed_at),
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
    def _fundamental_from_row(row: sqlite3.Row):
        from .facts.base import FactPeriod, PeriodKind, FactValue
        from .forex.base import ForexFundamental

        return ForexFundamental(
            fundamental_id=row["fundamental_id"],
            currency=row["currency"],
            synthesized=bool(row["synthesized"]),
            source_kind=row["source_kind"],
            source_id=row["source_id"],
            central_bank=row["central_bank"],
            dimension_key=row["dimension_key"],
            lineage_key=row["lineage_key"],
            subject=row["subject"],
            predicate=row["predicate"],
            value_kind=row["value_kind"],
            qualifier=row["qualifier"] or "",
            period=(
                FactPeriod(
                    kind=PeriodKind(row["period_kind"]),
                    value=row["period_value"],
                    label=row["period_label"],
                )
                if row["period_kind"]
                else None
            ),
            publication_type=row["publication_type"],
            value=(
                FactValue.from_dict(json.loads(row["value_json"]))
                if row["value_json"]
                else None
            ),
            observed_at=from_iso(row["observed_at"]),
            publication_id=row["publication_id"],
            document_id=row["document_id"],
            effective_date=from_iso(row["effective_date"]),
            source_text=row["source_text"],
            analysis_version=row["analysis_version"],
            analyzed_at=from_iso(row["analyzed_at"]),
        )

    # ------------------------------------------------------------------
    # Phase 8 — forex differentials
    # ------------------------------------------------------------------
    def save_forex_differential(self, differential) -> None:
        """Persist one ``ForexDifferential``, upserting by its deterministic id.

        Idempotent: re-saving the same differential overwrites the row in
        place, preserving ``created_at``.
        """
        from .forex.base import ForexDifferential

        if not isinstance(differential, ForexDifferential):
            raise TypeError(
                f"expected ForexDifferential, got {type(differential).__name__}"
            )
        differential_id = differential.resolve_id()
        now_iso = iso(now_utc())
        existing = self._conn.execute(
            "SELECT created_at FROM forex_differentials WHERE differential_id = ?",
            (differential_id,),
        ).fetchone()
        created_at = existing["created_at"] if existing else now_iso
        self._conn.execute(
            """
            INSERT INTO forex_differentials
                (differential_id, base_currency, quote_currency, synthesized,
                 dimension_key, subject, predicate, value_kind, qualifier,
                 period_kind, period_value, period_label, publication_type,
                 base_fundamental_id, base_source_kind, base_source_id,
                 base_central_bank, base_value_json, base_observed_at,
                 base_publication_id, base_document_id, base_effective_date,
                 base_source_text, quote_fundamental_id, quote_source_kind,
                 quote_source_id, quote_central_bank, quote_value_json,
                 quote_observed_at, quote_publication_id, quote_document_id,
                 quote_effective_date, quote_source_text, value_json,
                 formulation, analysis_version, analyzed_at, created_at,
                 updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(differential_id) DO UPDATE SET
                base_currency=excluded.base_currency,
                quote_currency=excluded.quote_currency,
                synthesized=excluded.synthesized,
                dimension_key=excluded.dimension_key,
                subject=excluded.subject,
                predicate=excluded.predicate,
                value_kind=excluded.value_kind,
                qualifier=excluded.qualifier,
                period_kind=excluded.period_kind,
                period_value=excluded.period_value,
                period_label=excluded.period_label,
                publication_type=excluded.publication_type,
                base_fundamental_id=excluded.base_fundamental_id,
                base_source_kind=excluded.base_source_kind,
                base_source_id=excluded.base_source_id,
                base_central_bank=excluded.base_central_bank,
                base_value_json=excluded.base_value_json,
                base_observed_at=excluded.base_observed_at,
                base_publication_id=excluded.base_publication_id,
                base_document_id=excluded.base_document_id,
                base_effective_date=excluded.base_effective_date,
                base_source_text=excluded.base_source_text,
                quote_fundamental_id=excluded.quote_fundamental_id,
                quote_source_kind=excluded.quote_source_kind,
                quote_source_id=excluded.quote_source_id,
                quote_central_bank=excluded.quote_central_bank,
                quote_value_json=excluded.quote_value_json,
                quote_observed_at=excluded.quote_observed_at,
                quote_publication_id=excluded.quote_publication_id,
                quote_document_id=excluded.quote_document_id,
                quote_effective_date=excluded.quote_effective_date,
                quote_source_text=excluded.quote_source_text,
                value_json=excluded.value_json,
                formulation=excluded.formulation,
                analysis_version=excluded.analysis_version,
                analyzed_at=excluded.analyzed_at,
                updated_at=excluded.updated_at
            """,
            (
                differential_id,
                differential.base_currency,
                differential.quote_currency,
                1 if differential.synthesized else 0,
                differential.dimension_key,
                differential.subject,
                differential.predicate,
                differential.value_kind,
                differential.qualifier,
                differential.period.kind.value if differential.period else None,
                differential.period.value if differential.period else None,
                differential.period.label if differential.period else None,
                differential.publication_type,
                differential.base_fundamental_id,
                differential.base_source_kind,
                differential.base_source_id,
                differential.base_central_bank,
                json.dumps(differential.base_value.to_dict()) if differential.base_value else None,
                iso(differential.base_observed_at),
                differential.base_publication_id,
                differential.base_document_id,
                iso(differential.base_effective_date),
                differential.base_source_text,
                differential.quote_fundamental_id,
                differential.quote_source_kind,
                differential.quote_source_id,
                differential.quote_central_bank,
                json.dumps(differential.quote_value.to_dict()) if differential.quote_value else None,
                iso(differential.quote_observed_at),
                differential.quote_publication_id,
                differential.quote_document_id,
                iso(differential.quote_effective_date),
                differential.quote_source_text,
                json.dumps(differential.value.to_dict()) if differential.value else None,
                differential.formulation,
                differential.analysis_version,
                iso(differential.analyzed_at),
                created_at,
                now_iso,
            ),
        )
        self._conn.commit()

    def save_forex_differentials(self, differentials) -> int:
        """Persist a list of ``ForexDifferential`` (or a
        ``ForexFundamentalResult``). Returns count."""
        if hasattr(differentials, "differentials"):
            differentials = differentials.differentials
        count = 0
        for differential in differentials:
            self.save_forex_differential(differential)
            count += 1
        return count

    def get_forex_differential(self, differential_id: str):
        row = self._conn.execute(
            "SELECT * FROM forex_differentials WHERE differential_id = ?",
            (differential_id,),
        ).fetchone()
        return self._differential_from_row(row) if row else None

    def get_forex_differentials(
        self,
        *,
        base_currency: str | None = None,
        quote_currency: str | None = None,
        subject: str | None = None,
        predicate: str | None = None,
        limit: int | None = None,
    ) -> list:
        query = "SELECT * FROM forex_differentials"
        clauses: list[str] = []
        params: list = []
        if base_currency is not None:
            clauses.append("base_currency = ?")
            params.append(base_currency)
        if quote_currency is not None:
            clauses.append("quote_currency = ?")
            params.append(quote_currency)
        if subject is not None:
            clauses.append("subject = ?")
            params.append(subject)
        if predicate is not None:
            clauses.append("predicate = ?")
            params.append(predicate)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY subject, base_observed_at, differential_id"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [self._differential_from_row(r) for r in rows]

    def get_differential_as_of(
        self,
        base_currency: str,
        quote_currency: str,
        subject: str,
        as_of=None,
    ) -> list:
        """Return the latest differential per lineage with ``base_observed_at ≤
        as_of`` for the given ordered pair and subject (the "differential at a
        date" view, no look-ahead). ``as_of=None`` means no upper bound. The
        differential timeline is base-anchored (``base_observed_at``)."""
        if as_of is not None:
            rows = self._conn.execute(
                "SELECT * FROM forex_differentials "
                "WHERE base_currency = ? AND quote_currency = ? AND subject = ? "
                "AND base_observed_at <= ?",
                (base_currency, quote_currency, subject, iso(as_of)),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM forex_differentials "
                "WHERE base_currency = ? AND quote_currency = ? AND subject = ?",
                (base_currency, quote_currency, subject),
            ).fetchall()
        latest: dict[str, tuple] = {}
        for row in rows:
            key = row["dimension_key"]
            if key not in latest:
                latest[key] = row
                continue
            current, candidate = latest[key], row
            if candidate["base_observed_at"] > current["base_observed_at"]:
                latest[key] = candidate
            elif candidate["base_observed_at"] == current["base_observed_at"] and candidate["differential_id"] < current["differential_id"]:
                latest[key] = candidate
        return [
            self._differential_from_row(r)
            for r in sorted(latest.values(), key=lambda r: r["differential_id"])
        ]

    def delete_forex_differentials(
        self, *, base_currency: str | None = None, quote_currency: str | None = None
    ) -> int:
        """Delete every differential of a base currency (and/or a quote
        currency), or of the whole store."""
        if base_currency is not None and quote_currency is not None:
            cursor = self._conn.execute(
                "DELETE FROM forex_differentials "
                "WHERE base_currency = ? AND quote_currency = ?",
                (base_currency, quote_currency),
            )
        elif base_currency is not None:
            cursor = self._conn.execute(
                "DELETE FROM forex_differentials WHERE base_currency = ?",
                (base_currency,),
            )
        elif quote_currency is not None:
            cursor = self._conn.execute(
                "DELETE FROM forex_differentials WHERE quote_currency = ?",
                (quote_currency,),
            )
        else:
            cursor = self._conn.execute("DELETE FROM forex_differentials")
        self._conn.commit()
        return cursor.rowcount

    def delete_forex_differentials_for_document(self, document_id: str) -> int:
        """Delete every differential established from a document (either side)."""
        cursor = self._conn.execute(
            "DELETE FROM forex_differentials "
            "WHERE base_document_id = ? OR quote_document_id = ?",
            (document_id, document_id),
        )
        self._conn.commit()
        return cursor.rowcount

    def delete_forex_differentials_for_publication(self, publication_id: str) -> int:
        """Delete every differential established from a publication (either
        side)."""
        cursor = self._conn.execute(
            "DELETE FROM forex_differentials "
            "WHERE base_publication_id = ? OR quote_publication_id = ?",
            (publication_id, publication_id),
        )
        self._conn.commit()
        return cursor.rowcount

    def rebuild_forex_differentials(
        self, differentials, *, currencies: tuple[str, ...] | None = None
    ) -> int:
        """Replace the differentials involving the given currencies (or the
        store's) with ``differentials`` in one transaction.

        ``differentials`` is a list of ``ForexDifferential`` or a
        ``ForexFundamentalResult``. ``forex_differentials`` is derived data:
        the scope is fully recomputed each time, so re-analysis is idempotent,
        an empty result clears the scope, and no stale differential survives
        the observations it summarizes.
        """
        from .forex.base import ForexDifferential

        if hasattr(differentials, "differentials"):
            differentials = differentials.differentials
        try:
            if currencies is not None:
                placeholders = ",".join("?" * len(currencies))
                self._conn.execute(
                    "DELETE FROM forex_differentials "
                    "WHERE base_currency IN ({0}) OR quote_currency IN ({0})".format(
                        placeholders
                    ),
                    tuple(currencies) * 2,
                )
            else:
                self._conn.execute("DELETE FROM forex_differentials")
            count = 0
            for differential in differentials:
                if not isinstance(differential, ForexDifferential):
                    raise TypeError(
                        f"expected ForexDifferential, got {type(differential).__name__}"
                    )
                if currencies is not None and not (
                    differential.base_currency in currencies
                    or differential.quote_currency in currencies
                ):
                    continue
                differential_id = differential.resolve_id()
                now_iso = iso(now_utc())
                self._conn.execute(
                    """
                    INSERT INTO forex_differentials
                        (differential_id, base_currency, quote_currency,
                         synthesized, dimension_key, subject, predicate,
                         value_kind, qualifier, period_kind, period_value,
                         period_label, publication_type, base_fundamental_id,
                         base_source_kind, base_source_id, base_central_bank,
                         base_value_json, base_observed_at, base_publication_id,
                         base_document_id, base_effective_date, base_source_text,
                         quote_fundamental_id, quote_source_kind, quote_source_id,
                         quote_central_bank, quote_value_json, quote_observed_at,
                         quote_publication_id, quote_document_id,
                         quote_effective_date, quote_source_text, value_json,
                         formulation, analysis_version, analyzed_at, created_at,
                         updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        differential_id,
                        differential.base_currency,
                        differential.quote_currency,
                        1 if differential.synthesized else 0,
                        differential.dimension_key,
                        differential.subject,
                        differential.predicate,
                        differential.value_kind,
                        differential.qualifier,
                        differential.period.kind.value if differential.period else None,
                        differential.period.value if differential.period else None,
                        differential.period.label if differential.period else None,
                        differential.publication_type,
                        differential.base_fundamental_id,
                        differential.base_source_kind,
                        differential.base_source_id,
                        differential.base_central_bank,
                        json.dumps(differential.base_value.to_dict()) if differential.base_value else None,
                        iso(differential.base_observed_at),
                        differential.base_publication_id,
                        differential.base_document_id,
                        iso(differential.base_effective_date),
                        differential.base_source_text,
                        differential.quote_fundamental_id,
                        differential.quote_source_kind,
                        differential.quote_source_id,
                        differential.quote_central_bank,
                        json.dumps(differential.quote_value.to_dict()) if differential.quote_value else None,
                        iso(differential.quote_observed_at),
                        differential.quote_publication_id,
                        differential.quote_document_id,
                        iso(differential.quote_effective_date),
                        differential.quote_source_text,
                        json.dumps(differential.value.to_dict()) if differential.value else None,
                        differential.formulation,
                        differential.analysis_version,
                        iso(differential.analyzed_at),
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
    def _differential_from_row(row: sqlite3.Row):
        from .facts.base import FactPeriod, PeriodKind, FactValue
        from .forex.base import ForexDifferential

        return ForexDifferential(
            differential_id=row["differential_id"],
            base_currency=row["base_currency"],
            quote_currency=row["quote_currency"],
            synthesized=bool(row["synthesized"]),
            dimension_key=row["dimension_key"],
            subject=row["subject"],
            predicate=row["predicate"],
            value_kind=row["value_kind"],
            qualifier=row["qualifier"] or "",
            period=(
                FactPeriod(
                    kind=PeriodKind(row["period_kind"]),
                    value=row["period_value"],
                    label=row["period_label"],
                )
                if row["period_kind"]
                else None
            ),
            publication_type=row["publication_type"],
            base_fundamental_id=row["base_fundamental_id"],
            base_source_kind=row["base_source_kind"],
            base_source_id=row["base_source_id"],
            base_central_bank=row["base_central_bank"],
            base_value=(
                FactValue.from_dict(json.loads(row["base_value_json"]))
                if row["base_value_json"]
                else None
            ),
            base_observed_at=from_iso(row["base_observed_at"]),
            base_publication_id=row["base_publication_id"],
            base_document_id=row["base_document_id"],
            base_effective_date=from_iso(row["base_effective_date"]),
            base_source_text=row["base_source_text"],
            quote_fundamental_id=row["quote_fundamental_id"],
            quote_source_kind=row["quote_source_kind"],
            quote_source_id=row["quote_source_id"],
            quote_central_bank=row["quote_central_bank"],
            quote_value=(
                FactValue.from_dict(json.loads(row["quote_value_json"]))
                if row["quote_value_json"]
                else None
            ),
            quote_observed_at=from_iso(row["quote_observed_at"]),
            quote_publication_id=row["quote_publication_id"],
            quote_document_id=row["quote_document_id"],
            quote_effective_date=from_iso(row["quote_effective_date"]),
            quote_source_text=row["quote_source_text"],
            value=(
                FactValue.from_dict(json.loads(row["value_json"]))
                if row["value_json"]
                else None
            ),
            formulation=row["formulation"],
            analysis_version=row["analysis_version"],
            analyzed_at=from_iso(row["analyzed_at"]),
        )
