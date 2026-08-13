from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

from .base import (
    METHOD_DOCX,
    WARNING_EMPTY_TEXT,
    DocumentParser,
    DocumentSection,
    DocumentTable,
    NormalizedDocument,
)
from ._util import make_unavailable

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def w(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


def _para_runs(p: ET.Element) -> str:
    parts: list[str] = []
    for node in p.iter():
        if node.tag == w("instrText"):
            continue
        if node.tag == w("t"):
            parts.append(node.text or "")
        elif node.tag == w("tab"):
            parts.append("\t")
        elif node.tag in (w("br"), w("cr")):
            parts.append("\n")
    return "".join(parts)


def _heading_level(p: ET.Element) -> int | None:
    ppr = p.find(w("pPr"))
    if ppr is None:
        return None
    outline = ppr.find(w("outlineLvl"))
    if outline is not None and outline.get(w("val")) is not None:
        try:
            return int(outline.get(w("val"))) + 1
        except (TypeError, ValueError):
            return None
    style = ppr.find(w("pStyle"))
    if style is None:
        return None
    val = style.get(w("val")) or ""
    match = re.match(r"(?:[hH]eading|HeadingNumbered)([1-9])", val)
    if match:
        return int(match.group(1))
    if val.lower() in ("title", "heading"):
        return 1
    return None


def _cell_text(cell: ET.Element) -> str:
    paragraphs = []
    for p in cell.findall(w("p")):
        text = _para_runs(p).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _capture_table(tbl: ET.Element) -> DocumentTable:
    rows: list[list[str]] = []
    for tr in tbl.findall(w("tr")):
        cells = []
        for tc in tr.findall(w("tc")):
            cells.append(_cell_text(tc))
        if cells:
            rows.append(cells)
    headers = rows[0] if rows else []
    body = rows[1:] if len(rows) > 1 else []
    name = ""
    tblpr = tbl.find(w("tblPr"))
    if tblpr is not None:
        for tag in ("tblCaption", "tblName"):
            el = tblpr.find(w(tag))
            if el is not None and el.get(w("val")):
                name = el.get(w("val"))
                break
    return DocumentTable(order=0, name=name, headers=headers, rows=body)


class DocxParser(DocumentParser):
    kind = "docx"
    label = "Generic DOCX parser (zipfile + OOXML XML)"
    extraction_method = METHOD_DOCX

    def parse(self, document) -> NormalizedDocument:
        if document.local_path is None:
            return make_unavailable(document)
        try:
            with ZipFile(document.local_path) as archive:
                if "word/document.xml" not in archive.namelist():
                    raise ValueError("word/document.xml missing from docx archive")
                root = ET.fromstring(archive.read("word/document.xml"))
                core = _read_xml(archive, "docProps/core.xml")
                app = _read_xml(archive, "docProps/app.xml")
        except (BadZipFile, KeyError, ET.ParseError, ValueError) as exc:
            doc = make_unavailable(document, warnings=["parse_error"])
            doc.metadata["parse_error"] = f"{exc.__class__.__name__}: {exc}"
            return doc

        body = root.find(w("body"))
        if body is None:
            return make_unavailable(document, warnings=["parse_error"])

        sections: list[DocumentSection] = []
        tables: list[DocumentTable] = []
        current: DocumentSection | None = None

        def ensure() -> DocumentSection:
            nonlocal current
            if current is None:
                current = DocumentSection(order=len(sections), heading="", level=0)
                sections.append(current)
            return current

        for child in body:
            if child.tag == w("p"):
                text = _para_runs(child)
                level = _heading_level(child)
                if level is not None:
                    current = DocumentSection(
                        order=len(sections), heading=text.strip(), level=level
                    )
                    sections.append(current)
                else:
                    text = re.sub(r"[ \t\f\v]+", " ", text).strip()
                    if text:
                        section = ensure()
                        section.text = f"{section.text}\n{text}".strip()
            elif child.tag == w("tbl"):
                table = _capture_table(child)
                table.order = len(tables)
                tables.append(table)

        title = None
        metadata: dict = {}
        if core is not None:
            title = _core_title(core)
            core_meta = {}
            for key in ("title", "subject", "creator", "description"):
                el = core.find(f"{{{_prop_ns()}}}{key}") or core.find(f"{{{_dc_ns()}}}{key}")
                if el is not None and el.text:
                    core_meta[key] = el.text
            if core_meta:
                metadata["docx_core"] = core_meta
        if app is not None:
            app_el = app.find(f"{{{_prop_ns()}}}Application")
            if app_el is not None and app_el.text:
                metadata["docx_app"] = app_el.text
        metadata["paragraph_count"] = sum(1 for c in body if c.tag == w("p"))
        metadata["table_count"] = len(tables)

        text = "\n\n".join(
            (s.heading + "\n" + s.text if s.heading and s.text else s.heading or s.text)
            for s in sections
            if s.heading or s.text
        )
        if not text and tables:
            text = "\n\n".join(t.render() for t in tables)

        warnings = []
        if not text and not tables:
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
            tables=tables,
            metadata=metadata,
            extraction_method=self.extraction_method,
            extraction_warnings=warnings,
        )


def _prop_ns() -> str:
    return "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"


def _dc_ns() -> str:
    return "http://purl.org/dc/elements/1.1/"


def _read_xml(archive: ZipFile, name: str) -> ET.Element | None:
    try:
        return ET.fromstring(archive.read(name))
    except (KeyError, ET.ParseError):
        return None


def _core_title(core: ET.Element) -> str | None:
    for ns in (_prop_ns(), _dc_ns()):
        el = core.find(f"{{{ns}}}title")
        if el is not None and el.text and el.text.strip():
            return " ".join(el.text.split())
    return None