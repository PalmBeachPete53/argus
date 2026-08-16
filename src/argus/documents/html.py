from __future__ import annotations

import json
import re
from bs4 import BeautifulSoup, Tag

from ..normalize import absolutize
from .base import (
    METHOD_HTML,
    WARNING_EMPTY_TEXT,
    DocumentParser,
    DocumentSection,
    DocumentTable,
    NormalizedDocument,
)
from ._util import make_unavailable

HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
NOISE_TAGS = {
    "nav",
    "footer",
    "aside",
    "script",
    "style",
    "noscript",
    "iframe",
    "svg",
    "canvas",
    "template",
    "form",
    "button",
    "select",
    "option",
    "input",
    "textarea",
    "dialog",
    "source",
    "picture",
    "video",
    "audio",
    "object",
    "embed",
    "map",
    "area",
}
RESOURCE_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".zip")
CONTAINER_SELECTORS = (
    "main",
    "[role=main]",
    "article",
    "#content",
    "#contents",
    "#main",
    "#main-content",
    "#maincontent",
    "#body",
    "#bodytext",
    ".main-content",
    ".content",
    ".article-body",
    ".body-content",
    ".entry-content",
    ".post-content",
    ".bodytext",
    ".story-body",
    ".rich-text",
    ".text-body",
)


def _is_noise(el: Tag) -> bool:
    if el.name in NOISE_TAGS:
        return True
    if el.get("hidden") is not None or el.get("aria-hidden") == "true":
        return True
    class_id = " ".join(filter(None, [el.get("id") or "", el.get("class") and " ".join(el.get("class")) or ""]))
    return bool(re.search(r"cookie|consent-banner|skip-link|visually-hidden", class_id, re.IGNORECASE))


def _find_container(soup: BeautifulSoup) -> Tag | None:
    for selector in CONTAINER_SELECTORS:
        el = soup.select_one(selector)
        if el is not None:
            return el
    return soup.body


class _Collector:
    def __init__(self) -> None:
        self.sections: list[DocumentSection] = []
        self.tables: list[DocumentTable] = []
        self._current: DocumentSection | None = None
        self._order = 0
        self._table_order = 0

    def _ensure_section(self, heading: str = "", level: int = 0) -> None:
        self._current = DocumentSection(order=self._order, heading=heading, level=level)
        self._order += 1
        self.sections.append(self._current)

    def new_heading(self, el: Tag) -> None:
        level = int(el.name[1]) if el.name in HEADINGS else 0
        self._ensure_section(el.get_text(" ", strip=True), level)

    def append_text(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self._current is None:
            self._ensure_section()
        if self._current.text:
            self._current.text += "\n" + text
        else:
            self._current.text = text

    def capture_table(self, el: Tag) -> None:
        rows: list[list[str]] = []
        for tr in el.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            rows.append(cells)
        headers = rows[0] if rows else []
        body = rows[1:] if len(rows) > 1 else []
        caption = el.find("caption", recursive=False)
        name = caption.get_text(" ", strip=True) if caption else ""
        table = DocumentTable(order=self._table_order, name=name, headers=headers, rows=body)
        self._table_order += 1
        self.tables.append(table)


def _walk(node: Tag, collector: _Collector) -> None:
    for child in node.children:
        if isinstance(child, str):
            collector.append_text(child)
            continue
        if not isinstance(child, Tag):
            continue
        if _is_noise(child):
            child.decompose()
            continue
        if child.name in HEADINGS:
            collector.new_heading(child)
            continue
        if child.name == "table":
            collector.capture_table(child)
            continue
        if child.name in {"p", "li", "dd", "dt", "blockquote", "pre", "figcaption", "address", "hr"}:
            text = child.get_text(" ", strip=True)
            if child.name == "hr":
                continue
            collector.append_text(text)
            continue
        _walk(child, collector)


_META_KEYS = {
    "description",
    "keywords",
    "author",
    "date",
    "creation",
    "publisher",
    "dc.title",
    "dc.creator",
    "dc.date",
    "og:title",
    "og:description",
    "og:type",
    "og:site_name",
    "og:published_time",
    "og:updated_time",
    "article:published_time",
    "article:modified_time",
    "dcterms.created",
    "dcterms.modified",
    "twitter:title",
    "twitter:description",
}


def _collect_meta(soup: BeautifulSoup, metadata: dict) -> None:
    meta: dict[str, str] = {}
    for el in soup.find_all("meta"):
        key = (
            el.get("property")
            or el.get("name")
            or el.get("itemprop")
            or el.get("http-equiv")
        )
        content = el.get("content")
        if not key or content is None:
            continue
        key = str(key).strip().lower()
        if key in _META_KEYS:
            meta.setdefault(key, content)
    if meta:
        metadata["html_meta"] = meta
    lang = soup.html.get("lang") if soup.html is not None else None
    if lang:
        metadata["lang"] = lang
    dates = []
    for el in soup.find_all(attrs={"datetime": True}):
        value = el.get("datetime")
        if value:
            dates.append(value)
    if dates:
        metadata["dates"] = dates


def _collect_json_ld(soup: BeautifulSoup, metadata: dict) -> None:
    """Capture structured JSON-LD objects (e.g. ``NewsArticle.datePublished``)
    verbatim, for provenance of authoritative document-level dates."""
    found: list = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = (script.string or "").strip() or (script.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if isinstance(data, list):
            found.extend(data)
        else:
            found.append(data)
    if found:
        metadata["json_ld"] = found


def _collect_links(container: Tag, base_url: str, metadata: dict) -> None:
    links: list[dict] = []
    for anchor in container.find_all("a"):
        href = anchor.get("href")
        if not isinstance(href, str) or not href.strip():
            continue
        url = absolutize(base_url, href.strip())
        if not url.lower().endswith(RESOURCE_EXTENSIONS):
            continue
        text = anchor.get_text(" ", strip=True)
        links.append({"url": url, "title": text})
    if links:
        metadata["linked_documents"] = links


def _section_text(section: DocumentSection) -> None:
    parts = []
    for line in section.text.split("\n"):
        collapsed = re.sub(r"[ \t\f\v]+", " ", line).strip()
        if collapsed:
            parts.append(collapsed)
    section.text = "\n".join(parts)


def _title_of(soup: BeautifulSoup) -> str | None:
    if soup.title is not None and soup.title.string and soup.title.string.strip():
        return " ".join(soup.title.string.split())
    h1 = soup.find("h1")
    if h1 is not None:
        return h1.get_text(" ", strip=True)
    return None


class HtmlParser(DocumentParser):
    kind = "html"
    label = "Generic HTML parser (BeautifulSoup / main-content extraction)"
    extraction_method = METHOD_HTML

    def parse(self, document) -> NormalizedDocument:
        data = None
        if document.local_path is None:
            return make_unavailable(document, title=document.url)
        try:
            from pathlib import Path

            data = Path(document.local_path).read_bytes()
        except OSError:
            return make_unavailable(document, title=document.url)
        if not data:
            return make_unavailable(document, title=document.url)

        try:
            soup = BeautifulSoup(data, "html.parser")
        except Exception as exc:
            doc = make_unavailable(document, warnings=["parse_error"], title=document.url)
            doc.metadata["parse_error"] = f"{exc.__class__.__name__}: {exc}"
            return doc

        container = _find_container(soup)
        collector = _Collector()
        _walk(container, collector)

        for section in collector.sections:
            _section_text(section)

        title = _title_of(soup)
        text = "\n\n".join(
            (s.heading + "\n" + s.text if s.heading and s.text else s.heading or s.text)
            for s in collector.sections
            if s.heading or s.text
        )
        if not text and collector.tables:
            text = "\n\n".join(table.render() for table in collector.tables)

        metadata: dict = {"encoding": soup.original_encoding or "utf-8"}
        _collect_meta(soup, metadata)
        _collect_json_ld(soup, metadata)
        _collect_links(container, document.url, metadata)

        warnings = []
        if not text and not collector.tables:
            warnings.append(WARNING_EMPTY_TEXT)

        return NormalizedDocument(
            publication_id=document.publication_id,
            document_id="",
            source_url=document.url,
            local_path=document.local_path,
            document_kind=document.kind,
            mime_type=document.content_type,
            title=title,
            text=text,
            sections=collector.sections,
            tables=collector.tables,
            metadata=metadata,
            extraction_method=self.extraction_method,
            extraction_warnings=warnings,
        )