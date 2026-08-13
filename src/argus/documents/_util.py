from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from .base import (
    METHOD_UNAVAILABLE,
    WARNING_MISSING_FILE,
    WARNING_PARSE_ERROR,
    NormalizedDocument,
)

if TYPE_CHECKING:
    from ..models import Document


def read_bytes(document: Document) -> bytes | None:
    if not document.local_path:
        return None
    path = Path(document.local_path)
    if not path.exists() or not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def decode_text(data: bytes) -> tuple[str, list[str]]:
    """Decode raw bytes to UTF-8 text, trying common encodings."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace"), []
    warnings: list[str] = []
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(encoding), warnings
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), ["decoding_error"]


def collapse_whitespace(text: str) -> str:
    return re.sub(r"[ \t\f\v]+", " ", text)


def strip_noise_lines(text: str) -> str:
    """Remove empty leading/trailing lines and normalize line breaks."""
    lines = text.split("\n")
    stripped = [line.strip() for line in lines]
    cleaned = []
    for line in stripped:
        if not line and cleaned and not cleaned[-1]:
            continue
        cleaned.append(line)
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    while cleaned and not cleaned[0]:
        cleaned.pop(0)
    return "\n".join(cleaned)


def make_unavailable(
    document: Document,
    *,
    warnings: list[str] | None = None,
    method: str = METHOD_UNAVAILABLE,
    title: str | None = None,
) -> NormalizedDocument:
    warnings = list(warnings or [])
    if not document.local_path or not Path(document.local_path).exists():
        if WARNING_MISSING_FILE not in warnings:
            warnings.append(WARNING_MISSING_FILE)
    return NormalizedDocument(
        publication_id=document.publication_id,
        document_id="",
        source_url=document.url,
        local_path=document.local_path,
        document_kind=document.kind,
        mime_type=document.content_type,
        title=title,
        text="",
        extraction_method=method,
        extraction_warnings=warnings,
        metadata={"parse_error": warnings},
    )


def safe(func):
    """Wrap a parser body so any exception becomes an unavailable document."""

    def wrapper(document: Document, *_args, **_kwargs):
        try:
            return func(document)
        except Exception as exc:
            doc = make_unavailable(document, warnings=[WARNING_PARSE_ERROR])
            doc.metadata["parse_error"] = f"{exc.__class__.__name__}: {exc}"
            doc.extraction_warnings = [WARNING_PARSE_ERROR]
            return doc

    return wrapper


def page_boundary_sniff(data: bytes, limit: int = 1024) -> str | None:
    """Crude sniff for common container formats (used as a dispatch fallback)."""
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "zip"
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "txt"
    return None
