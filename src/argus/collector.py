from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import models
from .discovery import create as create_strategy
from .fetcher import Fetcher
from .http import HttpClient, HttpConfig
from .models import CollectError, FetchResult, RunResult
from .registry import SourceRegistry
from .store import Store

DEFAULT_STORE_PATH = "data/argus.db"
DEFAULT_RAW_ROOT = "data/raw"

# Bounded discovery worker pool: at most this many *sources* are discovered
# simultaneously. Discovery is network/I-O bound, so a handful of threads is
# enough to saturate the available parallelism without hammering hosts or the
# store. Configurable per process via ARGUS_DISCOVERY_WORKERS (a future GUI
# setting can reuse the same hook).
DISCOVERY_WORKERS = 6
_WORKERS_ENV = "ARGUS_DISCOVERY_WORKERS"

# Bounded collection worker pool: at most this many *publications* are collected
# simultaneously. Collection is network/I-O bound, so a handful of threads
# saturates it. The Store stays a single SQLite connection: workers only do
# network + raw-file work and every Store mutation happens on the caller's
# serialized writer. Configurable per process via ARGUS_COLLECTION_WORKERS (a
# future GUI setting can reuse the same hook).
COLLECTION_WORKERS = 6
_COLLECTION_WORKERS_ENV = "ARGUS_COLLECTION_WORKERS"


def _worker_pool_size() -> int:
    raw = os.environ.get(_WORKERS_ENV, "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return DISCOVERY_WORKERS


def _collection_worker_pool_size() -> int:
    """The bounded collection pool size, configurable via ``ARGUS_COLLECTION_WORKERS``.

    Mirrors :func:`_worker_pool_size`: a positive integer override wins, any
    invalid / non-positive / missing value falls back to ``COLLECTION_WORKERS``.
    The pool is always bounded — never an unbounded thread count.
    """
    raw = os.environ.get(_COLLECTION_WORKERS_ENV, "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return COLLECTION_WORKERS

# The statuses a collection campaign considers "needs work" and therefore
# selects by default. This is the self-repair contract:
#
# * ``DISCOVERED`` — never collected yet, fetch it;
# * ``UPDATED``    — rediscovery changed metadata / added document URLs, collect
#   the new bits (existing FETCHED documents are still skipped);
# * ``PARTIAL``    — some documents failed last time, retry the failed ones;
# * ``FAILED``     — every document failed, retry them.
#
# Fetched-only publications (``FETCHED``) are *not* selected: idempotence relies
# on skipping already-fetched documents, and blindly re-selecting everything
# would re-download the world on every pass. ``force=True`` keeps its meaning —
# it re-collects the documents of an otherwise-selected publication even when
# they are already FETCHED — it does not re-select FETCHED publications.
COLLECTION_STATUSES = (
    models.PublicationStatus.DISCOVERED,
    models.PublicationStatus.UPDATED,
    models.PublicationStatus.PARTIAL,
    models.PublicationStatus.FAILED,
)


def _worker_pool_size() -> int:
    raw = os.environ.get(_WORKERS_ENV, "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return DISCOVERY_WORKERS


@dataclass
class _SourceDiscoveryResult:
    """What one worker produced for one source — never touches the Store.

    The scheduler collects these and performs every Store mutation serially
    (the Store owns a single connection and is not thread-safe).
    """

    source: models.Source
    publications: list[models.Publication]
    ok: bool
    error: str | None
    strategy: object | None
    native_error: Exception | None


class CollectionStopped(Exception):
    """Raised inside a collection campaign when a stop (SIGTERM) is requested.

    Mirrors the Discovery lifecycle: SIGTERM is a normal lifecycle control,
    never a "failed" signal — the campaign finalizes itself as ``cancelled`` so
    the GUI shows exactly what happened. With a parallel pool, the stop is
    polled on the campaign's main thread (the only thread allowed to touch the
    Store); in-flight workers may be interrupted at the process level as
    before.
    """


@dataclass
class _CollectionWorkerResult:
    """What one collection worker produced for one publication — never the Store.

    The worker only performs the network fetch, content validation and atomic
    raw-file writes; this result carries the documents to persist and the
    computed status. The scheduler (main thread) applies every Store mutation
    serially through ``persist`` / ``collect_errors``, exactly like discovery's
    ``_SourceDiscoveryResult``.
    """

    publication: models.Publication
    fetch_result: FetchResult | None = None
    status: models.PublicationStatus | None = None
    error: Exception | None = None


def in_bounds(pub, start, end) -> bool:
    """Publication-date window check (start-inclusive, end-exclusive, None = open).

    The single definition of a discovery/fetch date range in the Core — shared
    by the CLI and the GUI bridge so a window is always applied the same way. A
    publication without a publication date is out of bounds once a window is
    set.
    """
    if start is None and end is None:
        return True
    if pub.publication_date is None:
        return False
    if start is not None and pub.publication_date < start:
        return False
    if end is not None and pub.publication_date >= end:
        return False
    return True


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
        # An explicitly injected client (tests / callers) is shared as-is by the
        # parallel workers; a collector-owned client is replaced by one fresh
        # HttpClient per worker so a `requests.Session` is never used
        # concurrently from several threads.
        self._injected_client = client is not None
        self._http_config = http_config
        self.client = client or HttpClient(http_config)
        self._injected_fetcher = fetcher is not None
        self.fetcher = fetcher or Fetcher(
            self.client,
            self.store,
            raw_root or Path(DEFAULT_RAW_ROOT),
        )
        self.search_provider = search_provider
        self._now = now
        self.errors: list[CollectError] = []

    def _new_client(self, *, rate_limiter: "RateLimiter | None" = None) -> HttpClient:
        """A client for one discovery/collection worker.

        Collector-owned clients are per-worker (own session, own robots cache)
        but share the injected ``rate_limiter`` when one is given (the campaign
        limiter) so per-host spacing is global across the campaign — a single
        ``requests.Session`` is still never used from several threads. An
        injected client is the test double, shared as-is.
        """
        if self._injected_client:
            return self.client
        return HttpClient(self._http_config, rate_limiter=rate_limiter)

    def _new_fetcher(self, client: HttpClient) -> Fetcher:
        """A Fetcher bound to one worker's client (own session / rate limiter).

        Collection workers never touch the Store, so the per-worker Fetcher is
        built with the real store reference but only its network/raw-file path
        (``collect``) is ever called; every Store mutation stays on the
        scheduler thread via :meth:`_apply_collection_result`. An injected
        fetcher is the test double, shared.
        """
        if self._injected_fetcher:
            return self.fetcher
        f = self.fetcher
        return Fetcher(
            client,
            self.store,
            f.raw_root,
            page_doc_extraction=f.page_doc_extraction,
            max_page_documents=f.max_page_documents,
            max_retries=f.max_retries,
        )

    def _sync_sources(self) -> None:
        for source in self.registry.sources:
            self.store.upsert_source(source)

    def discover_all(
        self,
        *,
        banks: tuple[str, ...] | list[str] | None = None,
        source_ids: tuple[str, ...] | list[str] | None = None,
        run_id: str | None = None,
        date_start=None,
        date_end=None,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[models.Publication]:
        """Discover and persist publications across the enabled sources.

        The enabled sources are discovered by a **bounded pool of workers** (one
        worker per source at a time, at most ``DISCOVERY_WORKERS``). Workers only
        perform the network discovery and candidate generation — every Store
        mutation is performed here, serially, on the main thread, because the
        Store owns a single SQLite connection and is not thread-safe.

        ``date_start`` / ``date_end`` (datetime) restrict *what enters the
        store* to the publication-date window (start-inclusive, end-exclusive,
        see :func:`in_bounds`); every worker receives the same bounds. With no
        bounds the behaviour is unchanged: every discovered publication is
        persisted. The window lives here, in the Core, so the CLI and the GUI
        bridge apply it identically. An error on one source never fails the
        campaign: it is recorded (logged + source result) and the other
        sources proceed.

        ``progress(completed, total)`` is called (on the caller's thread) as
        soon as each source *actually finishes* — ``completed`` / ``total`` are
        source counts, so a future GUI progress bar needs no prior knowledge of
        the candidate count. Progress follows worker completion order (a slow
        source never stalls the progress of already-finished sources), while
        the returned publications keep their logical source order.

        When a ``run_id`` is given (the GUI campaign always passes one), the
        Core also persists the progression into the store's ``discovery_runs``
        row through the *same serialized writer* as every other Store mutation
        — ``sources_total`` is fixed to the enabled-source count before the
        pool starts (``0 / N`` is immediately observable) and
        ``sources_completed`` is advanced on the caller's thread as each source
        finishes. A failing or empty source still counts as one completed step.
        """
        self._sync_sources()
        run_id = run_id or self.store.run_stamp()
        sources = self.registry.enabled_sources(banks=banks, source_ids=source_ids)
        publications: list[models.Publication] = []
        if not sources:
            return publications

        # Persisted progression (serialized writer thread): N is fixed at the
        # start, so 0 / N is readable before any source finishes.
        self.store.set_discovery_progress(run_id, completed=0, total=len(sources))

        def _on_progress(completed: int, total: int) -> None:
            # Worker completions arrive on the caller's thread via
            # `as_completed` — the same thread that owns the serialized Store
            # writes, so this is never a concurrent SQLite mutation.
            self.store.set_discovery_progress(run_id, completed=completed, total=total)
            if progress is not None:
                progress(completed, total)

        outcomes = self._run_sources(
            sources, date_start=date_start, date_end=date_end, progress=_on_progress
        )

        # Serialized Store writes, in source order (dedup makes the result
        # independent of worker completion order).
        for outcome in outcomes:
            if outcome.native_error is not None:
                # Native discovery was unavailable → run the search fallback when
                # the source is configured for it; always log the native error so
                # operators know native discovery failed.
                self._log_error(outcome.source, outcome.strategy, outcome.native_error, run_id)
            for publication in outcome.publications:
                publications.append(self.store.upsert_publication(publication))
            self.store.record_source_result(
                outcome.source.id, ok=outcome.ok, error=outcome.error
            )
        return publications

    def _discover_source_worker(
        self,
        source: models.Source,
        date_start=None,
        date_end=None,
    ) -> _SourceDiscoveryResult:
        """Discover **one arbitrary source** (never bank- or source-specific).

        Pure network/candidate work: this must not touch the Store. Any error —
        strategy, search fallback, or unexpected — is isolated into the result
        so a failing source cannot kill the other workers.
        """
        client = self._new_client()
        try:
            found: list[models.Publication] = []
            strategy = None
            native_error: Exception | None = None
            try:
                strategy = create_strategy(source, client, now=self._now)
                found = strategy.discover()
            except Exception as exc:
                native_error = exc
                found = []

            source_ok = True
            source_error: str | None = None
            if native_error is not None:
                # Native discovery was unavailable → run the search fallback when
                # the source is configured for it (the native error is logged by
                # the serialized writer).
                if self._search_configured(source):
                    found = self._search_fallback(source)
                if not found:
                    source_ok = False
                    source_error = f"{native_error.__class__.__name__}: {native_error}"
            elif not found and self._search_configured(source) and source.discovery.search_fallback_on_empty:
                # Native succeeded but produced no results, and the source
                # explicitly opts in to a search fallback on empty.
                found = self._search_fallback(source)

            in_window = [p for p in found if in_bounds(p, date_start, date_end)]
            return _SourceDiscoveryResult(
                source=source,
                publications=in_window,
                ok=source_ok,
                error=source_error,
                strategy=strategy,
                native_error=native_error,
            )
        except Exception as exc:  # pragma: no cover - defensive isolation
            return _SourceDiscoveryResult(
                source=source,
                publications=[],
                ok=False,
                error=f"{exc.__class__.__name__}: {exc}",
                strategy=None,
                native_error=exc,
            )

    def _run_sources(self, sources, *, date_start=None, date_end=None, progress=None):
        """Run the bounded worker pool over ``sources``.

        Progress and results are decoupled on purpose:

        * ``progress(completed, total)`` is invoked on the caller's thread as
          soon as each worker *really* finishes (``as_completed``), so the
          remontée of progress follows the real completion order and a slow
          source can never stall the progress of sources already done;
        * the returned results keep the source *submission* order, so the
          serialized Store writes (and the logical result order / dedup) are
          unchanged by the completion order.

        A stop request (``DiscoveryStopped`` raised on the main thread by the
        SIGTERM handler, or any other unexpected failure) shuts the pool down
        without waiting for in-flight workers and propagates — the campaign
        lifecycle (Stop / app close) must never wait for a stuck worker, and
        never fabricate a ``progress(total, total)`` for interrupted work.
        """
        executor = ThreadPoolExecutor(max_workers=_worker_pool_size())
        total = len(sources)
        try:
            future_to_index = {
                executor.submit(
                    self._discover_source_worker, source, date_start, date_end
                ): index
                for index, source in enumerate(sources)
            }
            results: list[_SourceDiscoveryResult | None] = [None] * total
            completed = 0
            for future in as_completed(future_to_index):
                # The worker isolates every Exception; a BaseException escaping
                # it propagates here and shuts the pool down (never counted as a
                # successful completion).
                results[future_to_index[future]] = future.result()
                completed += 1
                if progress is not None:
                    progress(completed, total)
        except BaseException:
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        executor.shutdown(wait=True)
        return [result for result in results if result is not None]

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

    def plan_collection(
        self,
        *,
        banks=None,
        force: bool = False,
        date_start=None,
        date_end=None,
    ) -> list[models.Publication]:
        """The publications a collection pass will operate on (selected first).

        Selection is the single source of truth for what a collection campaign
        covers: the currently enabled banks, the self-repair statuses
        (:data:`COLLECTION_STATUSES`) and the optional publication-date window.
        ``force`` does *not* widen the selection (it only re-downloads
        already-fetched documents of a selected publication), so the plan is
        stable across identical inputs.
        """
        effective = self._effective_banks(banks)
        if not effective:
            return []
        return self.store.list_publications(
            bank=effective,
            statuses=COLLECTION_STATUSES,
            date_start=date_start,
            date_end=date_end,
        )

    def collect_campaign(
        self,
        *,
        banks: tuple[str, ...] | list[str] | None = None,
        force: bool = False,
        date_start=None,
        date_end=None,
        run_id: str | None = None,
        progress: Callable[[int, int], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        publications: list[models.Publication] | None = None,
    ) -> list[FetchResult]:
        """Run a **parallel** collection campaign over the selected publications.

        The lifecycle-aware counterpart of :meth:`fetch_all`, mirroring
        :meth:`discover_all`: the campaign owns a ``run_id``, fixes its scope
        before the pool starts, and reports progression through the store's
        ``collection_runs`` row via the serialized writer.

        **Frozen plan / total coherence.** The campaign works on a *logical
        snapshot* of its plan: either ``publications`` (explicitly handed in —
        the bridge passes the exact list whose length it recorded at launch), or
        ``plan_collection()`` evaluated once here. ``publications_total`` is
        fixed to the length of that frozen list *before* the pool starts
        (``0 / N`` is immediately observable) and can always be reached unless
        the campaign is stopped — a publication added/modified by a concurrent
        Discovery *after* the plan is frozen never appears in the total nor in
        the workers of this campaign.

        Publications are collected by a **bounded pool of workers**
        (``COLLECTION_WORKERS`` / ``ARGUS_COLLECTION_WORKERS``) — one worker per
        publication. Workers only perform the network fetch, content validation
        and atomic raw-file writes; every Store mutation (``upsert_document``,
        ``set_publication_status``, ``collect_errors``, progress) happens here,
        serially, on the caller's thread, because the Store owns a single SQLite
        connection and is not thread-safe. All workers share one campaign-scoped
        per-host ``RateLimiter`` (own sessions/clients, one limiter).

        ``progress(completed, total)`` fires on the caller's thread in **real
        completion order** (``as_completed``), so a slow publication never
        stalls the progress of already-finished ones. ``completed`` /
        ``total`` are publication counts, and a failing publication still counts
        as one completed step. ``should_stop`` is polled before submissions and
        after each completion; when it returns True (or a SIGTERM raises
        :class:`CollectionStopped`), the pool is shut down without waiting for
        the remaining publications and the campaign aborts — the caller (the
        bridge) finalizes the run as ``cancelled``, never fabricating
        ``completed == total``.

        Returns the per-publication ``FetchResult`` list in **selection order**
        (a worker that failed before producing a result is logged but absent).
        """
        run_id = run_id or self.store.run_stamp()
        if publications is None:
            publications = self.plan_collection(
                banks=banks, force=force, date_start=date_start, date_end=date_end
            )
        total = len(publications)
        if run_id:
            self.store.set_collection_progress(run_id, completed=0, total=total)

        def _on_progress(completed: int, campaign_total: int) -> None:
            # Always on the campaign's own thread: the same thread that owns the
            # serialized Store writes.
            if run_id:
                self.store.set_collection_progress(run_id, completed=completed, total=campaign_total)
            if progress is not None:
                progress(completed, campaign_total)

        # A single campaign-scoped per-host rate limiter shared by every worker's
        # client: concurrent downloads to the same host are spaced globally while
        # different hosts remain parallel. (A per-client limiter would let N
        # workers hit one host simultaneously.)
        from .http import RateLimiter

        min_interval = self._http_config.min_interval if self._http_config else HttpConfig().min_interval
        rate_limiter = RateLimiter(min_interval)

        return self._run_collection(
            publications,
            force=force,
            run_id=run_id,
            progress=_on_progress,
            should_stop=should_stop,
            rate_limiter=rate_limiter,
        )

    def _collect_publication_worker(
        self,
        publication: models.Publication,
        existing_by_url: dict,
        force: bool,
        rate_limiter,
    ) -> _CollectionWorkerResult:
        """Collect **one arbitrary publication** (never bank- or source-specific).

        Pure network/raw-file work: this must not touch the Store. Any error is
        isolated into the result so a failing publication cannot kill the other
        workers. ``existing_by_url`` is the snapshot of the publication's stored
        documents taken by the scheduler (the serialized writer) before
        submission — it is read here, never written. ``rate_limiter`` is the
        campaign's single per-host limiter, shared by every worker's client.
        """
        fetcher = self._new_fetcher(self._new_client(rate_limiter=rate_limiter))
        try:
            fetch_result, status = fetcher.collect(publication, existing_by_url, force=force)
            return _CollectionWorkerResult(
                publication=publication,
                fetch_result=fetch_result,
                status=status,
            )
        except CollectionStopped:
            raise
        except Exception as exc:  # pragma: no cover - defensive isolation
            return _CollectionWorkerResult(
                publication=publication,
                error=exc,
            )

    def _apply_collection_result(self, worker_result: _CollectionWorkerResult, run_id: str) -> None:
        """Serialized Store writes for one worker's result (never in a worker)."""
        publication = worker_result.publication
        if worker_result.error is not None:
            exc = worker_result.error
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
            return
        fetch_result = worker_result.fetch_result
        if fetch_result is not None:
            self.fetcher.persist(publication.id or "", fetch_result.documents, worker_result.status)

    def _run_collection(
        self,
        publications: list[models.Publication],
        *,
        force: bool,
        run_id: str,
        progress: Callable[[int, int], None] | None,
        should_stop: Callable[[], bool] | None,
        rate_limiter=None,
    ) -> list[FetchResult]:
        """Run the bounded worker pool over ``publications``.

        Progress and results are decoupled on purpose, exactly like discovery:

        * ``progress(completed, total)`` is invoked on the caller's thread as
          soon as each worker *really* finishes (``as_completed``), so the
          remontée of progress follows the real completion order and a slow
          publication can never stall the progress of publications already done;
        * the returned results keep the publication *submission* order, so the
          serialized Store writes (and the logical result order / dedup) are
          unchanged by the completion order.

        Every worker shares ``rate_limiter`` (the campaign's single per-host
        limiter), so concurrent downloads to the same host are spaced globally
        while different hosts stay parallel.

        A stop request (``CollectionStopped`` raised on the main thread by the
        stop poll or SIGTERM handler) shuts the pool down without waiting for
        in-flight workers and propagates — the campaign lifecycle (Stop / app
        close) must never wait for a stuck worker, and never fabricate a
        ``progress(total, total)`` for interrupted work.
        """
        if not publications:
            return []
        executor = ThreadPoolExecutor(max_workers=_collection_worker_pool_size())
        total = len(publications)
        try:
            future_to_index: dict = {}
            for index, publication in enumerate(publications):
                if should_stop is not None and should_stop():
                    raise CollectionStopped("collection cancelled by user")
                existing_by_url = {d.url: d for d in self.store.list_documents(publication.id or "")}
                future = executor.submit(
                    self._collect_publication_worker, publication, existing_by_url, force, rate_limiter
                )
                future_to_index[future] = index
            results: list[FetchResult | None] = [None] * total
            completed = 0
            for future in as_completed(future_to_index):
                if should_stop is not None and should_stop():
                    raise CollectionStopped("collection cancelled by user")
                # The worker isolates every Exception; a BaseException escaping
                # it propagates here and shuts the pool down (never counted as a
                # successful completion).
                worker_result = future.result()
                self._apply_collection_result(worker_result, run_id)
                if worker_result.fetch_result is not None:
                    results[future_to_index[future]] = worker_result.fetch_result
                completed += 1
                if progress is not None:
                    progress(completed, total)
        except BaseException:
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        executor.shutdown(wait=True)
        return [result for result in results if result is not None]

    def fetch_all(
        self,
        *,
        banks: tuple[str, ...] | list[str] | None = None,
        statuses: tuple = COLLECTION_STATUSES,
        force: bool = False,
        date_start=None,
        date_end=None,
        run_id: str | None = None,
    ) -> list[FetchResult]:
        """Fetch the selected publications (see :data:`COLLECTION_STATUSES`).

        The default selection is the self-repair contract: DISCOVERED (never
        collected), UPDATED (metadata changed), PARTIAL (some documents failed)
        and FAILED (all documents failed) publications are selected — so a
        partially or entirely failed collection is automatically retried on the
        next pass. Already-FETCHED documents of a selected publication are
        skipped (no network, idempotent). ``force=True`` re-downloads the
        documents of a selected publication even when already fetched.

        A per-publication error never kills the pass: it is recorded in
        ``collect_errors`` and the loop continues with the next publication.
        """
        run_id = run_id or self.store.run_stamp()
        effective = self._effective_banks(banks)
        if not effective:
            return []  # selection filtered to zero enabled banks
        publications = self.store.list_publications(
            bank=effective,
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

    def _effective_banks(self, banks) -> tuple[str, ...]:
        """The banks to operate on, filtered by the toggle.

        A caller's explicit selection is still filtered to the currently enabled
        banks, so a disabled bank is never scheduled for operational work (the
        only way to run it is to re-enable it first, e.g. via
        ``ARGUS_BANKS_ENABLED``). When no selection is given, the registry's
        active (enabled) banks are used. May return an empty tuple — the caller
        must treat an empty result as "nothing to do" (never as "no filter")."""
        if banks is not None:
            from .config import filter_enabled

            return filter_enabled(banks)
        active = [b.id for b in self.registry.active_banks]
        return tuple(active)

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