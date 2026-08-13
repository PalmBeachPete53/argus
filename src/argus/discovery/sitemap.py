from __future__ import annotations

import xml.etree.ElementTree as ET

from .. import models
from ..normalize import parse_datetime, title_from_url
from .base import DiscoveryStrategy

from ..normalize import absolutize, parse_datetime, title_from_url

SM = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


class SitemapDiscovery(DiscoveryStrategy):
    kind = "sitemap"
    max_depth = 3
    max_urls = 30000

    def discover(self) -> list[models.Publication]:
        urls: list[dict] = []
        self._walk(self.spec.url, depth=0, out=urls, seen=set())
        publications: list[models.Publication] = []
        seen_urls: set[str] = set()
        for entry in urls:
            url = entry["loc"]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            if not self._allowed(url):
                continue
            date = parse_datetime(entry.get("lastmod")) if entry.get("lastmod") else None
            if not self._in_window(date):
                continue
            publications.append(
                self._make(
                    url=url,
                    title=title_from_url(url),
                    publication_date=date,
                    extra={
                        "sitemap_lastmod": entry.get("lastmod"),
                        "sitemap_priority": entry.get("priority"),
                    },
                )
            )
        return publications

    def _walk(self, url: str, *, depth: int, out: list, seen: set[str]) -> None:
        if depth > self.max_depth or len(out) >= self.max_urls or url in seen:
            return
        seen.add(url)
        response = self.client.get(url, headers={"Accept": "application/xml, text/xml"})
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError:
            return
        if root.tag == f"{SM}sitemapindex":
            for entry in root.findall(f"{SM}sitemap"):
                loc = entry.find(f"{SM}loc")
                if loc is not None and loc.text:
                    self._walk(absolutize(url, loc.text), depth=depth + 1, out=out, seen=seen)
        elif root.tag == f"{SM}urlset":
            for entry in root.findall(f"{SM}url"):
                loc = entry.find(f"{SM}loc")
                if loc is None or not loc.text:
                    continue
                lastmod = entry.find(f"{SM}lastmod")
                priority = entry.find(f"{SM}priority")
                out.append(
                    {
                        "loc": absolutize(url, loc.text),
                        "lastmod": lastmod.text if lastmod is not None else None,
                        "priority": priority.text if priority is not None else None,
                    }
                )
                if len(out) >= self.max_urls:
                    return