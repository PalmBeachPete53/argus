"""SearXNG SearchProvider.

Talks to a SearXNG instance JSON API (``/search?q=…&format=json``). SearXNG is
an optional, operator-provided service (typically via Docker); Argus never
assumes it runs locally. All configuration is explicit and injected, and the
HTTP transport is the shared ``HttpClient`` so tests stay fully offline with
``FakeSession``.
"""

from __future__ import annotations

import json
from urllib.parse import urlencode, urljoin

from ..errors import HttpError
from ..http import HttpClient, HttpResponse
from .base import SearchProvider, SearchResult

SEARXNG_JSON_PATH = "search"


class SearxngSearchProvider(SearchProvider):
    """SearchProvider backed by a SearXNG instance JSON API."""

    name = "searxng"

    def __init__(
        self,
        base_url: str,
        *,
        client: HttpClient | None = None,
        engines: tuple[str, ...] = (),
        language: str = "",
    ) -> None:
        if not base_url:
            raise ValueError("SearxngSearchProvider requires a base_url")
        self.base_url = base_url.rstrip("/") + "/"
        self.client = client or HttpClient()
        self.engines = tuple(engines)
        self.language = language

    def _search_url(self, query: str, engines: tuple[str, ...]) -> str:
        params: dict[str, str] = {"q": query, "format": "json"}
        active_engines = tuple(engines) or self.engines
        if active_engines:
            params["engines"] = ",".join(active_engines)
        if self.language:
            params["language"] = self.language
        return urljoin(self.base_url, SEARXNG_JSON_PATH) + "?" + urlencode(params)

    def search(self, query: str, *, engines: tuple[str, ...] = ()) -> list[SearchResult]:
        response = self.client.get(self._search_url(query, engines), respect_robots=False)
        return self.parse_response(response)

    def parse_response(self, response: HttpResponse) -> list[SearchResult]:
        """Parse a SearXNG JSON API response into ``SearchResult``s.

        Separated from the HTTP call so it can be unit-tested without network.
        """
        if response.status_code >= 400:
            raise HttpError(
                response.url,
                status_code=response.status_code,
                message=f"searxng HTTP {response.status_code}",
            )
        try:
            payload = json.loads(response.text)
        except (ValueError, TypeError) as exc:
            raise HttpError(response.url, message=f"searxng invalid JSON: {exc}") from exc
        raw_results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(raw_results, list):
            raise HttpError(response.url, message="searxng response has no 'results' list")
        results: list[SearchResult] = []
        for index, item in enumerate(raw_results):
            if not isinstance(item, dict):
                continue
            url = (item.get("url") or "").strip()
            if not url:
                continue
            results.append(
                SearchResult(
                    url=url,
                    title=(item.get("title") or "").strip(),
                    snippet=(item.get("content") or "").strip(),
                    published_date=item.get("publishedDate"),
                    engine=(item.get("engine") or ""),
                    rank=index + 1,
                )
            )
        return results
