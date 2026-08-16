"""Authoritative publication-date extraction from normalized document metadata.

Temporal provenance contract (see the Phase 4 / discovery design):

- ``discovered_at`` / ``fetched_at`` are collection signals and are never a
  publication date.
- A sitemap ``lastmod`` is a last-modification / crawl signal, never a
  publication date.
- A publication date is only established by an *authoritative* source inside
  the publication itself, in order of trust:

  1. structured JSON-LD (``datePublished`` / ``dateCreated`` / ``published``);
  2. HTML OpenGraph / Article metadata (``article:published_time`` /
     ``og:published_time``);
  3. Dublin-Core style metadata (``dcterms.created`` / ``dc.date`` / ``date``);
  4. an explicit ``<time datetime=…>`` element (lowest structured signal).

Only the first match in that order is returned, with its source recorded for
provenance. When none is available the publication stays undated — a date is
never invented.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..normalize import parse_datetime

_JSON_LD_DATE_KEYS = (
    "datePublished",
    "dateCreated",
    "published",
    "uploadDate",
)

_META_DATE_KEYS = (
    "article:published_time",
    "og:published_time",
    "article:modified_time",
    "og:updated_time",
    "dcterms.created",
    "dc.date",
    "date",
)


def _json_ld_candidates(metadata: dict[str, Any]):
    for obj in metadata.get("json_ld") or ():
        if not isinstance(obj, dict):
            continue
        for key in _JSON_LD_DATE_KEYS:
            value = obj.get(key)
            if isinstance(value, list):
                value = value[0] if value else None
            if value:
                parsed = parse_datetime(str(value))
                if parsed is not None:
                    yield key, parsed


def _meta_candidates(metadata: dict[str, Any]):
    meta = metadata.get("html_meta") or {}
    if not isinstance(meta, dict):
        return
    for key in _META_DATE_KEYS:
        value = meta.get(key)
        if value:
            parsed = parse_datetime(str(value))
            if parsed is not None:
                yield key, parsed


def _time_candidates(metadata: dict[str, Any]):
    for value in metadata.get("dates") or ():
        parsed = parse_datetime(str(value))
        if parsed is not None:
            yield "time", parsed


def extract_publication_date_from_metadata(
    metadata: dict[str, Any],
) -> tuple[datetime | None, str | None]:
    """Return ``(datetime, source)`` of the most authoritative publication date
    present in a normalized document's metadata, or ``(None, None)``.

    ``source`` is a short provenance tag (``json_ld:datePublished``,
    ``meta:article:published_time``, ``time:time``) suitable for
    ``Store.set_publication_date_if_missing``.
    """
    for source, dt in _json_ld_candidates(metadata):
        return dt, f"json_ld:{source}"
    for source, dt in _meta_candidates(metadata):
        return dt, f"meta:{source}"
    for source, dt in _time_candidates(metadata):
        return dt, f"time:{source}"
    return None, None
