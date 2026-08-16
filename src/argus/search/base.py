"""SearchProvider — abstract search discovery backend.

A SearchProvider answers a textual query with a list of candidate ``SearchResult``
URLs. It is a *discovery* mechanism only: it never fetches or returns document
content. A result URL found here must go through the normal publication ->
classification -> Fetcher -> normalization -> extraction pipeline.

Argus does not depend on any concrete search engine: the provider is injected,
which keeps tests offline and lets operators run SearXNG separately (e.g. in
Docker).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SearchResult:
    """A single search hit, retaining enough provenance for the discovery trace."""

    url: str
    title: str = ""
    snippet: str = ""
    published_date: str | None = None
    engine: str = ""
    rank: int = 0


class SearchProvider(ABC):
    """Abstract search backend producing candidate URLs for a query."""

    name: str = ""

    @abstractmethod
    def search(self, query: str, *, engines: tuple[str, ...] = ()) -> list[SearchResult]:
        """Return search results for ``query``, best-ranked first."""
        raise NotImplementedError
