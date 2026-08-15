from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from .. import models
from ..normalize import absolutize, is_same_host, parse_datetime, title_from_url
from .base import DiscoveryStrategy

_DATE_PATTERNS = (
    re.compile(r"\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b"),
    re.compile(r"\b\d{1,2}-\d{1,2}-\d{4}\b"),
    re.compile(r"\b[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}\b"),
    re.compile(r"\b(?:19|20)\d{2}-\d{1,2}-\d{1,2}\b"),
)

SKIP_FRAGMENT_PREFIXES = ("#", "mailto:", "javascript:", "tel:", "data:")
DOCUMENT_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".csv", ".xml")


def search_date(text: str):
    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            parsed = parse_datetime(match.group(0))
            if parsed is not None:
                return parsed
    return None


class HTMLDiscovery(DiscoveryStrategy):
    kind = "html"

    def discover(self) -> list[models.Publication]:
        publications: list[models.Publication] = []
        seen: set[str] = set()
        pages = (self.spec.url,) + tuple(self.spec.pagination_urls)
        for page_url in pages:
            response = self.client.get(page_url)
            soup = BeautifulSoup(response.text, "html.parser")
            anchors = self._anchors(soup)
            for anchor in anchors:
                href = anchor.get("href")
                if not href or not isinstance(href, str):
                    continue
                stripped = href.strip()
                if (
                    not stripped
                    or stripped.startswith(SKIP_FRAGMENT_PREFIXES)
                    or (not self.spec.keep_documents and stripped.lower().endswith(DOCUMENT_EXTENSIONS))
                ):
                    continue
                url = absolutize(page_url, stripped)
                if not is_same_host(url, page_url):
                    continue
                if url in seen:
                    continue
                if not self._allowed(url):
                    continue
                seen.add(url)
                text = anchor.get_text(" ", strip=True) or None
                title = text if text else title_from_url(url)
                if not text and not self.spec.title_from_url:
                    continue
                date = self._detect_date(anchor, soup)
                if date is not None and not self._in_window(date):
                    continue
                publications.append(
                    self._make(
                        url=url,
                        title=title,
                        publication_date=date,
                        extra={"html_anchor_text": text, "html_page": page_url},
                    )
                )
        return publications

    def _anchors(self, soup) -> list[Tag]:
        if self.spec.item_selector:
            return soup.select(self.spec.item_selector)
        return soup.find_all("a")

    def _detect_date(self, anchor, soup):
        context = anchor.get_text(" ", strip=True)
        parsed = search_date(context)
        if parsed is not None:
            return parsed
        node = anchor.parent
        depth = 0
        while node is not None and depth < 2:
            text = node.get_text(" ", strip=True)
            parsed = search_date(text)
            if parsed is not None:
                return parsed
            node = node.parent
            depth += 1
        if self.spec.date_css:
            for element in soup.select(self.spec.date_css):
                parsed = search_date(element.get_text(" ", strip=True))
                if parsed is not None:
                    return parsed
        return None