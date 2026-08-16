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
        search_provider=None,
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
        self.search_provider = search_provider
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
            found: list[models.Publication] = []
            strategy = None
            native_error: Exception | None = None
            try:
                strategy = create_strategy(source, self.client, now=self._now)
                found = strategy.discover()
            except Exception as exc:
                native_error = exc
                found = []

            source_ok = True
            source_error: str | None = None
            if native_error is not None:
                # Native discovery was unavailable → run the search fallback when
                # the source is configured for it; always log the native error so
                # operators know native discovery failed.
                self._log_error(source, strategy, native_error, run_id)
                if self._search_configured(source):
                    found = self._search_fallback(source)
                if not found:
                    source_ok = False
                    source_error = f"{native_error.__class__.__name__}: {native_error}"
            elif not found and self._search_configured(source) and source.discovery.search_fallback_on_empty:
                # Native succeeded but produced no results, and the source
                # explicitly opts in to a search fallback on empty.
                found = self._search_fallback(source)

            for publication in found:
                publications.append(self.store.upsert_publication(publication))
            self.store.record_source_result(
                source.id, ok=source_ok, error=source_error
            )
        return publications

    def _search_configured(self, source) -> bool:
        return bool(source.discovery.search_query) and self.search_provider is not None

    def _search_fallback(self, source) -> list[models.Publication]:
        from .discovery.search import SearchDiscovery

        return SearchDiscovery(source, self.search_provider, now=self._now).discover()

    def _log_error(self, source, strategy, exc: Exception, run_id: str) -> None:
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
            bank=self._effective_banks(banks),
            statuses=statuses, date_start=date_start, date_end=date_end,
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

    def _effective_banks(self, banks) -> tuple[str, ...] | None:
        """The banks to operate on: the caller's explicit selection, or — when
        not restricted — the active (enabled) banks of the registry, so disabled
        banks are never scheduled for operational work."""
        if banks is not None:
            return tuple(banks)
        active = [b.id for b in self.registry.active_banks]
        return tuple(active) if active else None

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