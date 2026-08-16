"""Search Discovery — turn SearchProvider results into publication candidates.

This is a *fallback* discovery strategy: it is only used when a source is
explicitly configured with a search query and native discovery was
unavailable (or, if configured, produced no results). It never fetches or
returns document content — it only produces candidate publication URLs that
then go through the normal publication -> classification -> Fetcher ->
normalization -> extraction pipeline.
"""

from __future__ import annotations

from urllib.parse import urlparse

from .. import models
from ..normalize import now_utc, parse_datetime, title_from_url
from ..search import SearchProvider

SEARCH_PROVENANCE_KEYS = (
    "discovery_method",
    "search_provider",
    "search_query",
    "search_rank",
    "search_result_url",
)


def _matches_domain(url: str, domain: str) -> bool:
    """True when ``url``'s host is ``domain`` or a subdomain of it."""
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    domain = domain.lower().strip().lstrip("*.")
    if not domain or not host:
        return False
    return host == domain or host.endswith("." + domain)


class SearchDiscovery:
    """Discovery strategy producing publication candidates from a SearchProvider.

    The source must declare ``DiscoverySpec.search_query``; results are filtered
    to ``DiscoverySpec.search_domain`` when set, and each candidate keeps search
    provenance in ``Publication.extra``.
    """

    kind = "search"

    def __init__(self, source: models.Source, provider: SearchProvider, *, now=None) -> None:
        self.source = source
        self.provider = provider
        self._now = now or now_utc
        self.now = self._now()

    def discover(self) -> list[models.Publication]:
        query = self.source.discovery.search_query
        if not query:
            return []
        results = self.provider.search(
            query, engines=self.source.discovery.search_engines or ()
        )
        domain = self.source.discovery.search_domain
        candidates: list[models.Publication] = []
        for result in results:
            if domain and not _matches_domain(result.url, domain):
                continue
            candidates.append(self._make_publication(result))
        return candidates

    def _make_publication(self, result) -> models.Publication:
        type_hint = self.source.publication_types
        extra: dict = {
            "discovery_method": "search",
            "search_provider": self.provider.name,
            "search_query": self.source.discovery.search_query,
            "search_rank": result.rank,
            "search_result_url": result.url,
        }
        if type_hint:
            extra["type_hint"] = list(type_hint)
        return models.Publication(
            central_bank=self.source.central_bank,
            title=(result.title or title_from_url(result.url)).strip(),
            url=result.url,
            source_id=self.source.id,
            source_url=self.source.discovery.url,
            publication_date=parse_datetime(result.published_date) if result.published_date else None,
            extra=extra,
        )
