from __future__ import annotations

from zipfile import ZipFile

from ..models import Document
from .base import (
    WARNING_UNSUPPORTED_KIND,
    DocumentParser,
    METHOD_UNAVAILABLE,
    NormalizedDocument,
)
from ._util import make_unavailable, page_boundary_sniff
from .docx import DocxParser
from .html import HtmlParser
from .pdf import PdfParser
from .spreadsheet import CsvParser, XlsxParser
from .txt import TxtParser

DEFAULT_PARSERS: list[DocumentParser] = [
    HtmlParser(),
    PdfParser(),
    DocxParser(),
    XlsxParser(),
    CsvParser(),
    TxtParser(),
]

# Fallback mapping used when ``Document.kind`` is missing/lossy but the declared
# content type is reliable.
_CONTENT_TYPE_KIND = {
    "application/pdf": "pdf",
    "text/html": "html",
    "application/xhtml+xml": "html",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "text/csv": "csv",
    "text/plain": "txt",
}


def _sniff_kind(document: Document) -> str | None:
    if not document.local_path:
        return None
    try:
        with open(document.local_path, "rb") as handle:
            head = handle.read(1024)
    except OSError:
        return None
    guessed = page_boundary_sniff(head)
    if guessed == "pdf":
        return "pdf"
    if guessed == "txt":
        return "txt"
    if guessed == "zip":
        try:
            with ZipFile(document.local_path) as archive:
                names = set(archive.namelist())
        except Exception:
            return None
        if "word/document.xml" in names:
            return "docx"
        if "xl/workbook.xml" in names or any(n.startswith("xl/") for n in names):
            return "xlsx"
        return "zip"
    return None


class ParserRegistry:
    """Dispatch a raw ``Document`` to the right ``DocumentParser``."""

    def __init__(self, parsers: list[DocumentParser] | None = None) -> None:
        self.parsers = parsers or list(DEFAULT_PARSERS)
        self._by_kind: dict[str, DocumentParser] = {p.kind: p for p in self.parsers}

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(self._by_kind)

    def parser_for(self, document: Document) -> DocumentParser | None:
        kinds = []
        if document.kind:
            kinds.append(document.kind)
        if document.content_type:
            media = document.content_type.split(";", 1)[0].strip().lower()
            if media in _CONTENT_TYPE_KIND:
                kinds.append(_CONTENT_TYPE_KIND[media])
        for kind in kinds:
            parser = self._by_kind.get(kind)
            if parser is not None:
                return parser
        sniffed = _sniff_kind(document)
        if sniffed is not None:
            return self._by_kind.get(sniffed)
        return None

    def parse(self, document: Document) -> NormalizedDocument:
        parser = self.parser_for(document)
        if parser is None:
            return make_unavailable(
                document,
                warnings=[WARNING_UNSUPPORTED_KIND],
                title=document.url,
            )
        return parser.parse(document)