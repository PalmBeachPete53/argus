from __future__ import annotations

from pathlib import Path

from .base import (
    METHOD_TXT,
    WARNING_EMPTY_TEXT,
    DocumentParser,
    DocumentSection,
    NormalizedDocument,
)
from ._util import decode_text, make_unavailable, strip_noise_lines


class TxtParser(DocumentParser):
    kind = "txt"
    label = "Generic plain-text parser (UTF-8 decode, noise-line strip)"
    extraction_method = METHOD_TXT

    def parse(self, document) -> NormalizedDocument:
        if document.local_path is None:
            return make_unavailable(document)
        try:
            data = Path(document.local_path).read_bytes()
        except OSError:
            return make_unavailable(document)

        body, _ = decode_text(data)
        body = strip_noise_lines(body)
        lines = [line.rstrip() for line in body.split("\n")]
        body = "\n".join(lines)
        section = DocumentSection(order=0, heading="", level=0, text=body)
        warnings = []
        if not body:
            warnings.append(WARNING_EMPTY_TEXT)
        return NormalizedDocument(
            publication_id=document.publication_id,
            document_id="",
            source_url=document.url,
            local_path=document.local_path,
            document_kind=document.kind,
            mime_type=document.content_type,
            title=None,
            text=body,
            sections=[section],
            metadata={"line_count": len(lines)},
            extraction_method=self.extraction_method,
            extraction_warnings=warnings,
        )