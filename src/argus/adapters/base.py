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
    item_selector=None,
    lookback_window_days=None,
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
            item_selector=item_selector,
            lookback_window_days=lookback_window_days,
        ),
        priority=priority,
        publication_types=types,
    )