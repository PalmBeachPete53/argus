from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "ref",
}

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y%m%d",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%Y/%m/%d",
)


def absolutize(base_url: str, href: str) -> str:
    if not href:
        return base_url
    return urljoin(base_url, href.strip())


def hostname(url: str) -> str | None:
    parsed = urlparse(url)
    if not parsed.netloc:
        return None
    return (parsed.netloc or "").split("@")[-1].split(":")[0].lower()


def is_same_host(url: str, base: str) -> bool:
    a, b = hostname(url), hostname(base)
    return bool(a and b and a == b)


def canonical_url(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower() or "https"
    host = parsed.netloc.split("@")[-1].lower()
    port = ""
    if ":" in host and host.rsplit(":", 1)[1].isdigit():
        head, maybe_port = host.rsplit(":", 1)
        if (scheme == "https" and maybe_port == "443") or (
            scheme == "http" and maybe_port == "80"
        ):
            host = head
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS]
    query = sorted(query)
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    qs = urlencode(query)
    result = f"{scheme}://{host}{path}"
    if qs:
        result += f"?{qs}"
    return result


def normalize_title(title: str) -> str:
    normalized = unicodedata.normalize("NFKD", title)
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", normalized).strip().lower()


def title_from_url(url: str, fallback: str = "Untitled") -> str:
    parsed = urlparse(url)
    if not parsed.path or parsed.path in ("/", ""):
        return fallback
    segments = [s for s in parsed.path.split("/") if s and s not in ("en", "index.htm", "index.html")]
    if not segments:
        return fallback
    last = segments[-1]
    for stem in (".pdf", ".html", ".htm", ".xml", ".docx", ".xlsx", ".csv"):
        if last.lower().endswith(stem):
            last = last[: -len(stem)]
    last = re.sub(r"[\-_+]+", " ", last)
    last = re.sub(r"\s+", " ", last).strip()
    return last or fallback


def slugify(value: str, max_len: int = 80) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value[:max_len].rstrip("-") or "untitled"


def _fallback_key(bank: str, title: str | None, date: datetime | None) -> str:
    date_part = date.strftime("%Y-%m-%d") if date else "no-date"
    return f"t|{bank}|{normalize_title(title or '')}|{date_part}"


def compute_dedup_key(
    bank: str,
    url: str | None = None,
    title: str | None = None,
    date: datetime | None = None,
) -> str:
    """The deterministic identity of a publication, used as its dedup key.

    A publication is primarily identified by its **canonical URL**: two
    publications whose URLs canonicalize to the same value (query order,
    fragments, tracking parameters, default ports, trailing slash — see
    :func:`canonical_url`) are the *same* publication and coalesce into one row.
    Only when a publication has no URL at all does it fall back to a
    title+bank+date identity.

    The URL identity is the **only** dedup contract: two different URLs — even
    when they point at the same physical document — are two distinct
    publications (``URL A → publication X``, ``URL B → publication Y``). This
    phase deliberately does *not* attempt semantic document deduplication; a
    source that knows two URLs are the same publication must say so by supplying
    ``dedup_key`` / ``canonical_url`` on the Publication.
    """
    if url:
        canon = canonical_url(url)
        payload = f"u|{canon}"
    else:
        payload = _fallback_key(bank, title, date)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(value, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    value_compact = re.sub(r"\s+", " ", value)
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(value_compact, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(value_compact)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
