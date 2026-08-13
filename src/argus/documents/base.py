from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..models import Document

# Extraction method values (documented vocabulary).
METHOD_HTML = "html"
METHOD_PDF_TEXT = "pdf_text"
METHOD_PDF_UNAVAILABLE = "pdf_unavailable"
METHOD_DOCX = "docx"
METHOD_XLSX = "xlsx"
METHOD_CSV = "csv"
METHOD_TXT = "txt"
METHOD_UNAVAILABLE = "unavailable"

# Warning codes (documented vocabulary).
WARNING_SCANNED_PDF = "scanned_pdf"
WARNING_UNSUPPORTED_KIND = "unsupported_kind"
WARNING_MISSING_FILE = "missing_file"
WARNING_PARSE_ERROR = "parse_error"
WARNING_DECODING = "decoding_error"
WARNING_EMPTY_TEXT = "empty_text"

EXTRACTION_METHODS = (
    METHOD_HTML,
    METHOD_PDF_TEXT,
    METHOD_PDF_UNAVAILABLE,
    METHOD_DOCX,
    METHOD_XLSX,
    METHOD_CSV,
    METHOD_TXT,
    METHOD_UNAVAILABLE,
)


@dataclass
class DocumentSection:
    """A heading + the text that follows it, in document order."""

    order: int
    heading: str
    level: int = 1
    text: str = ""
    page: int | None = None
    id: int | None = None


@dataclass
class DocumentTable:
    """A structured table extracted from a document."""

    order: int
    name: str
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    page: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    id: int | None = None

    def render(self, sep: str = " | ") -> str:
        lines: list[str] = []
        if self.headers:
            lines.append(sep.join(self.headers))
        for row in self.rows:
            lines.append(sep.join(str(cell or "") for cell in row))
        return "\n".join(lines)


@dataclass
class DocumentPage:
    number: int
    text: str


@dataclass
class NormalizedDocument:
    """Structured, traceable representation of a raw document's content.

    This is the output of a ``DocumentParser``. It is deliberately
    content-preserving: no summarization, translation or interpretation is
    applied. Every element can be traced back to the raw ``Document`` it was
    produced from via ``document_id`` / ``publication_id`` / ``source_url`` /
    ``local_path`` and the ``extraction_method``.
    """

    publication_id: str
    document_id: str
    source_url: str
    local_path: str | None
    document_kind: str
    mime_type: str | None = None

    title: str | None = None
    text: str = ""

    sections: list[DocumentSection] = field(default_factory=list)
    tables: list[DocumentTable] = field(default_factory=list)
    pages: list[DocumentPage] = field(default_factory=list)

    metadata: dict[str, Any] = field(default_factory=dict)
    extraction_method: str = METHOD_UNAVAILABLE
    extraction_warnings: list[str] = field(default_factory=list)
    normalized_at: datetime | None = None

    @property
    def ok(self) -> bool:
        return self.extraction_method != METHOD_UNAVAILABLE

    @property
    def sections_text(self) -> str:
        """Full text reassembled from sections when available."""
        if not self.sections:
            return self.text
        blocks = []
        for section in self.sections:
            if section.heading:
                blocks.append(section.heading)
            if section.text:
                blocks.append(section.text)
        return "\n\n".join(block for block in blocks if block)


class DocumentParser(ABC):
    """Transforms the bytes of a raw document into a NormalizedDocument.

    A parser is tightly coupled to one ``Document.kind`` and performs no
    economic interpretation whatsoever. It only extracts the structure of the
    content (text, sections, tables, metadata) and stays traceable.
    """

    kind: str = ""
    label: str = ""
    extraction_method: str = METHOD_UNAVAILABLE

    def __init__(self) -> None:
        if not self.kind or not self.label:
            raise TypeError(f"{self.__class__.__name__} must define kind and label")

    @abstractmethod
    def parse(self, document: Document) -> NormalizedDocument:  # pragma: no cover
        raise NotImplementedError