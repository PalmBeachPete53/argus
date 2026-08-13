from __future__ import annotations

import xml.etree.ElementTree as ET

from .. import models
from ..normalize import absolutize, parse_datetime
from .base import DiscoveryStrategy

RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
RSS1 = "{http://purl.org/rss/1.0/}"
DC = "{http://purl.org/dc/elements/1.1/}"
ATOM = "{http://www.w3.org/2005/Atom}"

ACCEPT = "application/rss+xml, application/atom+xml, application/xml, text/xml"


def _child(element, tag):
    return element.find(tag)


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_rss(item):
    title_el = _child(item, "title")
    link_el = _child(item, "link")
    date_el = _child(item, "pubDate")
    guid_el = _child(item, "guid")
    desc_el = _child(item, "description")
    enclosure = _child(item, "enclosure")
    return {
        "title": title_el.text if title_el is not None else None,
        "link": link_el.text if link_el is not None else None,
        "date": date_el.text if date_el is not None else None,
        "guid": guid_el.text if guid_el is not None else None,
        "description": (desc_el.text or "").strip() if desc_el is not None else "",
        "enclosure": (enclosure.get("url") if enclosure is not None else None),
    }


def _parse_rdf(item):
    return {
        "title": _child(item, f"{RSS1}title").text if _child(item, f"{RSS1}title") is not None else None,
        "link": _child(item, f"{RSS1}link").text if _child(item, f"{RSS1}link") is not None else None,
        "date": _child(item, f"{DC}date").text if _child(item, f"{DC}date") is not None else None,
        "guid": None,
        "description": (
            (_child(item, f"{RSS1}description").text or "").strip()
            if _child(item, f"{RSS1}description") is not None
            else ""
        ),
        "enclosure": None,
    }


def _parse_atom(entry):
    link = None
    link_el = None
    for candidate in entry.findall(f"{ATOM}link"):
        rel = candidate.get("rel")
        if rel in (None, "", "alternate"):
            link = candidate.get("href")
            link_el = candidate
            break
    published = _child(entry, f"{ATOM}published")
    updated = _child(entry, f"{ATOM}updated")
    date = (published.text if published is not None else None) or (
        updated.text if updated is not None else None
    )
    return {
        "title": _child(entry, f"{ATOM}title").text if _child(entry, f"{ATOM}title") is not None else None,
        "link": link,
        "date": date,
        "guid": _child(entry, f"{ATOM}id").text if _child(entry, f"{ATOM}id") is not None else None,
        "description": (
            (_child(entry, f"{ATOM}summary").text or "").strip()
            if _child(entry, f"{ATOM}summary") is not None
            else ""
        ),
        "enclosure": None,
    }


class RSSDiscovery(DiscoveryStrategy):
    kind = "rss"

    def discover(self) -> list[models.Publication]:
        response = self.client.get(self.spec.url, headers={"Accept": ACCEPT})
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            from ..errors import DiscoveryError

            raise DiscoveryError(
                self.source.id, self.kind, self.spec.url, f"feed parse failed: {exc}"
            ) from exc
        parser = self._parser_for(root)
        if parser is None:
            from ..errors import DiscoveryError

            raise DiscoveryError(
                self.source.id, self.kind, self.spec.url, f"unsupported feed root <{_localname(root.tag)}>"
            )
        publications: list[models.Publication] = []
        for item in parser(root):
            raw = item
            date = parse_datetime(raw["date"]) if raw["date"] else None
            if not self._in_window(date):
                continue
            link = absolutize(self.spec.url, raw["link"]) if raw["link"] else ""
            if link and not self._allowed(link):
                continue
            document_urls = []
            if raw["enclosure"]:
                document_urls.append(absolutize(self.spec.url, raw["enclosure"]))
            extra = {
                "feed_title": raw["title"],
                "feed_date": raw["date"],
                "feed_guid": raw["guid"],
                "feed_description": raw["description"],
            }
            publications.append(
                self._make(
                    url=link,
                    title=raw["title"],
                    publication_date=date,
                    document_urls=document_urls,
                    extra=extra,
                )
            )
        return publications

    def _parser_for(self, root):
        name = _localname(root.tag)
        if name == "rss":
            return self._iter_rss
        if name == "feed":
            return self._iter_atom
        if name == "RDF":
            return self._iter_rdf
        return None

    def _iter_rss(self, root):
        channel = root.find("channel")
        if channel is None:
            return
        for item in channel.findall("item"):
            yield _parse_rss(item)

    def _iter_rdf(self, root):
        for item in root.findall(f"{RSS1}item"):
            yield _parse_rdf(item)

    def _iter_atom(self, root):
        for entry in root.findall(f"{ATOM}entry"):
            yield _parse_atom(entry)