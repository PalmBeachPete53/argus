from __future__ import annotations

from pathlib import Path

from . import models
from .discovery import create as create_strategy
from .fetcher import Fetcher
from .http import HttpClient, HttpConfig
from .models import CollectError, FetchResult, RunResult
from .registry import SourceRegistry
from .store import Store

DEFAULT_STORE_PATH = "data/argus.db"
DEFAULT_RAW_ROOT = "data/raw"


class CentralBankCollector:
    def __init__(
        self,
        store: Store | str | None = None,
        registry: SourceRegistry | None = None,
        *,
        client: HttpClient | None = None,
        http_config: HttpConfig | None = None,
        raw_root: Path | str | None = None,
        fetcher: Fetcher | None = None,
        now=None,
    ) -> None:
        self.store = store if isinstance(store, Store) else Store(store or DEFAULT_STORE_PATH)
        self.registry = registry or SourceRegistry()
        self.client = client or HttpClient(http_config)
        self.fetcher = fetcher or Fetcher(
            self.client,
            self.store,
            raw_root or Path(DEFAULT_RAW_ROOT),
        )
        self._now = now
        self.errors: list[CollectError] = []

    def _sync_sources(self) -> None:
        for source in self.registry.sources:
            self.store.upsert_source(source)

    def discover_all(
        self,
        *,
        banks: tuple[str, ...] | list[str] | None = None,
        source_ids: tuple[str, ...] | list[str] | None = None,
        run_id: str | None = None,
    ) -> list[models.Publication]:
        self._sync_sources()
        run_id = run_id or self.store.run_stamp()
        publications: list[models.Publication] = []
        for source in self.registry.enabled_sources(banks=banks, source_ids=source_ids):
            strategy = None
            try:
                strategy = create_strategy(source, self.client, now=self._now)
                found = strategy.discover()
                source_ok = True
                source_error: str | None = None
                for publication in found:
                    persisted = self.store.upsert_publication(publication)
                    publications.append(persisted)
            except Exception as exc:
                source_ok = False
                source_error = f"{exc.__class__.__name__}: {exc}"
                error = CollectError(
                    bank_id=source.central_bank,
                    source_id=source.id,
                    strategy=source.discovery.kind,
                    url=getattr(strategy, "spec", None).url if strategy else source.discovery.url,
                    error_type=exc.__class__.__name__,
                    message=str(exc),
                    status_code=getattr(exc, "status_code", None),
                    run_id=run_id,
                )
                self.store.log_error(error)
                self.errors.append(error)
            self.store.record_source_result(
                source.id, ok=source_ok, error=source_error
            )
        return publications

    def fetch_all(
        self,
        *,
        banks: tuple[str, ...] | list[str] | None = None,
        statuses: tuple = (
            models.PublicationStatus.DISCOVERED,
            models.PublicationStatus.UPDATED,
        ),
        force: bool = False,
        date_start=None,
        date_end=None,
        run_id: str | None = None,
    ) -> list[FetchResult]:
        run_id = run_id or self.store.run_stamp()
        publications = self.store.list_publications(
            bank=banks, statuses=statuses, date_start=date_start, date_end=date_end
        )
        results: list[FetchResult] = []
        for publication in publications:
            try:
                result = self.fetcher.fetch(publication, force=force)
                results.append(result)
            except Exception as exc:
                error = CollectError(
                    bank_id=publication.central_bank,
                    source_id=publication.source_id,
                    strategy="fetch",
                    url=publication.url,
                    error_type=exc.__class__.__name__,
                    message=str(exc),
                    status_code=getattr(exc, "status_code", None),
                    run_id=run_id,
                )
                self.store.log_error(error)
                self.errors.append(error)
        return results

    def fetch(
        self,
        publications: list[models.Publication],
        *,
        force: bool = False,
        run_id: str | None = None,
    ) -> list[FetchResult]:
        run_id = run_id or self.store.run_stamp()
        results: list[FetchResult] = []
        for publication in publications:
            try:
                results.append(self.fetcher.fetch(publication, force=force))
            except Exception as exc:
                error = CollectError(
                    bank_id=publication.central_bank,
                    source_id=publication.source_id,
                    strategy="fetch",
                    url=publication.url,
                    error_type=exc.__class__.__name__,
                    message=str(exc),
                    run_id=run_id,
                )
                self.store.log_error(error)
                self.errors.append(error)
        return results

    def run(
        self,
        *,
        banks: tuple[str, ...] | list[str] | None = None,
        discover=True,
        fetch=True,
        force=False,
        date_start=None,
        date_end=None,
    ) -> RunResult:
        self.errors = []
        run_id = self.store.run_stamp()
        publications: list[models.Publication] = []
        fetch_results: list[FetchResult] = []
        if discover:
            publications = self.discover_all(banks=banks, run_id=run_id)
        if fetch:
            fetch_results = self.fetch_all(
                banks=banks, force=force, date_start=date_start, date_end=date_end,
                run_id=run_id,
            )
        return RunResult(
            run_id=run_id,
            publications=publications,
            fetch_results=fetch_results,
            errors=self.errors,
        )