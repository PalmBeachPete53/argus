from .. import models


class BankAdapter:
    def __init__(self) -> None:
        bank, sources = self._build()
        self._bank = bank
        self._sources = list(sources)

    def _build(self):
        raise NotImplementedError

    @property
    def bank(self) -> models.CentralBank:
        return self._bank

    @property
    def sources(self) -> list[models.Source]:
        return self._sources


def rss_source(
    id_: str,
    bank: str,
    name: str,
    url: str,
    *,
    priority: int = 100,
    types=(),
    include=(),
    exclude=(),
    lookback_window_days=None,
    search_query=None,
    search_domain=None,
    search_engines=(),
    search_fallback_on_empty=False,
) -> models.Source:
    return models.Source(
        id=id_,
        central_bank=bank,
        name=name,
        discovery=models.DiscoverySpec(
            kind="rss",
            url=url,
            include=include,
            exclude=exclude,
            lookback_window_days=lookback_window_days,
            search_query=search_query,
            search_domain=search_domain,
            search_engines=tuple(search_engines or ()),
            search_fallback_on_empty=search_fallback_on_empty,
        ),
        priority=priority,
        publication_types=types,
    )


def sitemap_source(
    id_: str,
    bank: str,
    name: str,
    url: str,
    *,
    priority: int = 100,
    types=(),
    include=(),
    exclude=(),
    lookback_window_days=None,
    search_query=None,
    search_domain=None,
    search_engines=(),
    search_fallback_on_empty=False,
) -> models.Source:
    return models.Source(
        id=id_,
        central_bank=bank,
        name=name,
        discovery=models.DiscoverySpec(
            kind="sitemap",
            url=url,
            include=include,
            exclude=exclude,
            lookback_window_days=lookback_window_days,
            search_query=search_query,
            search_domain=search_domain,
            search_engines=tuple(search_engines or ()),
            search_fallback_on_empty=search_fallback_on_empty,
        ),
        priority=priority,
        publication_types=types,
    )


def html_source(
    id_: str,
    bank: str,
    name: str,
    url: str,
    *,
    priority: int = 100,
    types=(),
    include=(),
    exclude=(),
    scope_prefixes=(),
    pagination_urls=(),
    allow_future=False,
    title_from_url=True,
    keep_documents=False,
    item_selector=None,
    lookback_window_days=None,
    search_query=None,
    search_domain=None,
    search_engines=(),
    search_fallback_on_empty=False,
) -> models.Source:
    return models.Source(
        id=id_,
        central_bank=bank,
        name=name,
        discovery=models.DiscoverySpec(
            kind="html",
            url=url,
            include=include,
            exclude=exclude,
            scope_prefixes=scope_prefixes,
            pagination_urls=pagination_urls,
            allow_future=allow_future,
            title_from_url=title_from_url,
            keep_documents=keep_documents,
            item_selector=item_selector,
            lookback_window_days=lookback_window_days,
            search_query=search_query,
            search_domain=search_domain,
            search_engines=tuple(search_engines or ()),
            search_fallback_on_empty=search_fallback_on_empty,
        ),
        priority=priority,
        publication_types=types,
    )