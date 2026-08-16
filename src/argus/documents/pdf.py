from __future__ import annotations

import re
from io import BytesIO

from ..normalize import now_utc
from .base import (
    METHOD_PDF_TEXT,
    METHOD_PDF_UNAVAILABLE,
    WARNING_EMPTY_TEXT,
    WARNING_SCANNED_PDF,
    DocumentPage,
    DocumentParser,
    DocumentSection,
    NormalizedDocument,
)
from ._util import make_unavailable, strip_noise_lines


def collapse_digit_spaces(text: str) -> str:
    """Collapse a space/tab sitting between two digits — an extraction artifact.

    pypdf sometimes emits a spurious space inside a number (e.g. a 4-digit
    year "202 6" or a rate "1.0 5"). A space/tab between two digits is almost
    always such an artifact; a line break between digits is preserved.
    """
    return re.sub(r"(?<=\d)[ \t]+(?=\d)", "", text)


class PdfParser(DocumentParser):
    kind = "pdf"
    label = "Generic PDF text extraction (pypdf, page-aware)"
    extraction_method = METHOD_PDF_TEXT

    def parse(self, document) -> NormalizedDocument:
        if document.local_path is None:
            return make_unavailable(document)
        try:
            from pathlib import Path

            data = Path(document.local_path).read_bytes()
        except OSError:
            return make_unavailable(document)

        if not data[:5] == b"%PDF-":
            return make_unavailable(document, warnings=["parse_error"], title=document.url)

        try:
            from pypdf import PdfReader, errors as pypdf_errors

            reader = PdfReader(BytesIO(data), strict=False)
        except pypdf_errors.PdfReadError as exc:
            doc = make_unavailable(document, warnings=["parse_error"])
            doc.metadata["parse_error"] = f"pypdf: {exc}"
            return doc
        except Exception as exc:
            doc = make_unavailable(document, warnings=["parse_error"])
            doc.metadata["parse_error"] = f"{exc.__class__.__name__}: {exc}"
            return doc

        pages: list[DocumentPage] = []
        sections: list[DocumentSection] = []
        try:
            page_count = len(reader.pages)
        except Exception:
            page_count = 0

        for index in range(page_count):
            try:
                page_text = reader.pages[index].extract_text() or ""
            except Exception:
                page_text = ""
            page_text = strip_noise_lines(page_text)
            # pypdf sometimes inserts spurious spaces inside numbers (e.g. a
            # 4-digit year "202 6" or a rate "1.0 5"); collapse digit-separated
            # spaces. A line break between digits is preserved.
            page_text = collapse_digit_spaces(page_text)
            page_number = index + 1
            pages.append(DocumentPage(number=page_number, text=page_text))
            sections.append(
                DocumentSection(
                    order=index,
                    heading="",
                    level=0,
                    text=page_text,
                    page=page_number,
                )
            )

        non_blank = [p for p in pages if p.text.strip()]
        blank = [p for p in pages if not p.text.strip()]
        text_parts = [p.text for p in non_blank]

        metadata: dict = {"page_count": page_count, "blank_pages": [p.number for p in blank]}
        pdf_meta = {}
        try:
            meta = reader.metadata
            if meta is not None:
                for key in ("title", "author", "subject", "creator", "producer"):
                    value = getattr(meta, key, None)
                    if value:
                        pdf_meta[key] = value
        except Exception:
            pass
        if pdf_meta:
            metadata["pdf_metadata"] = pdf_meta

        title = pdf_meta.get("title") or document.url
        warnings: list[str] = []

        # A PDF whose pages carry no extracted text but do carry images is, in
        # practice, a scanned document. We refuse to invent OCR text.
        if not non_blank:
            scanned = False
            try:
                scanned = any(len(page.images) > 0 for page in reader.pages)
            except Exception:
                scanned = False
            if scanned or blank:
                doc = make_unavailable(
                    document,
                    method=METHOD_PDF_UNAVAILABLE,
                    warnings=[WARNING_SCANNED_PDF],
                    title=title,
                )
                doc.metadata["image_count"] = str(scanned)
                doc.metadata["page_count"] = page_count
                doc.metadata["blank_pages"] = [p.number for p in pages]
                return doc
            warnings.append(WARNING_EMPTY_TEXT)

        text = "\n\n".join(text_parts)
        if not text:
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
            sections=sections,
            pages=pages,
            metadata=metadata,
            extraction_method=self.extraction_method,
            extraction_warnings=warnings,
            normalized_at=now_utc(),
        )