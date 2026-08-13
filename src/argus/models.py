from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class PublicationStatus(str, Enum):
    DISCOVERED = "discovered"
    FETCHED = "fetched"
    PARTIAL = "partial"
    FAILED = "failed"
    UPDATED = "updated"


class DocumentStatus(str, Enum):
    PENDING = "pending"
    FETCHED = "fetched"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CentralBank:
    id: str
    name: str
    currency: str
    official_domain: str


@dataclass(frozen=True, slots=True)
class DiscoverySpec:
    kind: str
    url: str
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    item_selector: str | None = None
    date_css: str | None = None
    scope_prefixes: tuple[str, ...] = ()
    pagination_urls: tuple[str, ...] = ()
    allow_future: bool = False
    title_from_url: bool = False
    lookback_window_days: int | None = None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Source:
    id: str
    central_bank: str
    name: str
    discovery: DiscoverySpec
    priority: int = 100
    enabled: bool = True
    publication_types: tuple[str, ...] = ()
    fallback_for: tuple[str, ...] = ()


@dataclass
class Publication:
    central_bank: str
    title: str
    url: str
    source_id: str
    source_url: str
    publication_date: datetime | None = None
    meeting_date: datetime | None = None
    # Denormalized cache of the last classification; the authoritative record
    # (type + confidence + method + evidence) lives in the `classifications`
    # table, written atomically by `Store.set_classification`.
    publication_type: str | None = None
    language: str | None = None
    document_urls: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)
    id: str | None = None
    canonical_url: str | None = None
    dedup_key: str | None = None
    status: PublicationStatus = PublicationStatus.DISCOVERED
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def has_fetch_target(self) -> bool:
        return bool(self.url or self.document_urls)


@dataclass
class Document:
    publication_id: str
    url: str
    kind: str = "html"
    status: DocumentStatus = DocumentStatus.PENDING
    local_path: str | None = None
    sha256: str | None = None
    content_type: str | None = None
    size: int | None = None
    retrieved_at: datetime | None = None
    retries: int = 0
    error: str | None = None
    id: int | None = None


@dataclass
class FetchResult:
    publication_id: str
    documents: list[Document]
    ok: bool = False
    failed_urls: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class CollectError:
    bank_id: str
    source_id: str
    strategy: str
    url: str
    error_type: str
    message: str
    status_code: int | None = None
    run_id: str = ""
    timestamp: datetime | None = None

    def __post_init__(self) -> None:
        if self.timestamp is None:
            from datetime import timezone

            self.timestamp = datetime.now(timezone.utc)


@dataclass
class RunResult:
    run_id: str
    publications: list[Publication]
    fetch_results: list[FetchResult]
    errors: list[CollectError]

    @property
    def ok(self) -> bool:
        return not self.errors