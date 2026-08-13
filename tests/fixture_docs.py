"""Test-only builders for binary fixtures (PDF, DOCX, XLSX).

These generate small but RFC-valid documents so the end-to-end parser tests run
without committing binary blobs to the repository.
"""

from __future__ import annotations

import io
import zipfile
import xml.etree.ElementTree as ET

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CORE_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC_NS = "http://purl.org/dc/elements/1.1/"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_content(lines: list[str] | None, image: bool) -> bytes:
    if image:
        return b"q 1 0 0 1 50 50 cm /Im0 Do Q\n"
    parts = [b"BT /F1 11 Tf 12 TL 50 720 Td"]
    for line in lines or ():
        parts.append(b"(" + _escape(line).encode("latin-1", "replace") + b") Tj T*")
    parts.append(b"ET")
    return b"\n".join(parts)


def make_pdf(pages: list[str]) -> bytes:
    """Minimal PDF with one Helvetica text line per page."""
    return _build_pdf([{"text": [p] if isinstance(p, str) else p} for p in pages])


def make_scanned_pdf(page_count: int = 2) -> bytes:
    """PDF whose pages only carry an image (no text) -> scanned."""
    return _build_pdf([{"image": True} for _ in range(page_count)])


def _build_pdf(page_specs: list[dict]) -> bytes:
    img_stream = (
        b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceRGB "
        b"/BitsPerComponent 8 /Filter /ASCIIHexDecode /Length 5 >>\nstream\nffffff>\nendstream"
    )
    font_obj = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    objects: list[bytes | None] = [b"<< /Type /Catalog /Pages 1 0 R >>", None]  # 0 catalog, 1 pages placeholder
    contents: dict[int, bytes] = {}
    font_id: int | None = None
    image_id: int | None = None
    page_ids: list[str] = []

    for spec in page_specs:
        if spec.get("image"):
            if image_id is None:
                image_id = len(objects)
                objects.append(img_stream)
            resources = f"<< /XObject << /Im0 {image_id} 0 R >> >>".encode()
        else:
            if font_id is None:
                font_id = len(objects)
                objects.append(font_obj)
            resources = f"<< /Font << /F1 {font_id} 0 R >> >>".encode()
        page_index = len(objects)
        content_index = page_index + 1
        page_ids.append(f"{page_index} 0 R")
        objects.append(
            b"<< /Type /Page /Parent 1 0 R /MediaBox [0 0 612 792] /Resources "
            + resources
            + b" /Contents "
            + f"{content_index} 0 R".encode()
            + b" >>"
        )
        objects.append(None)
        contents[content_index] = _pdf_content(spec.get("text"), spec.get("image", False))

    kids = b"[" + b" ".join(p.encode() for p in page_ids) + b"]"
    objects[1] = b"<< /Type /Pages /Kids " + kids + b" /Count " + str(len(page_ids)).encode() + b" >>"

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for index, body in enumerate(objects):
        offsets.append(out.tell())
        out.write(f"{index} 0 obj\n".encode())
        stream = contents.get(index)
        if stream is not None:
            out.write(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n")
            out.write(stream)
            out.write(b"\nendstream\n")
        else:
            assert body is not None
            out.write(body)
            out.write(b"\n")
        out.write(b"endobj\n")
    xref_pos = out.tell()
    count = len(objects) + 1
    out.write(b"xref\n0 " + str(count).encode() + b"\n")
    out.write(b"0000000000 65535 f \n")
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(b"trailer\n<< /Size " + str(count).encode() + b" /Root 0 0 R >>\nstartxref\n")
    out.write(str(xref_pos).encode() + b"\n%%EOF\n")
    return out.getvalue()


def _w(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


def _docx_xml(sections: list[dict]) -> bytes:
    # sections: [{"type": "heading"|"para", "text": str, "level": int}, {"type": "table", ...}]
    body = ET.Element(_w("body"))
    for item in sections:
        if item["type"] == "heading":
            p = ET.SubElement(body, _w("p"))
            ppr = ET.SubElement(p, _w("pPr"))
            style = ET.SubElement(ppr, _w("pStyle"))
            style.set(_w("val"), f"Heading{item['level']}")
            r = ET.SubElement(p, _w("r"))
            t = ET.SubElement(r, _w("t"))
            t.text = item["text"]
        elif item["type"] == "para":
            p = ET.SubElement(body, _w("p"))
            r = ET.SubElement(p, _w("r"))
            t = ET.SubElement(r, _w("t"))
            t.text = item["text"]
        elif item["type"] == "table":
            tbl = ET.SubElement(body, _w("tbl"))
            for row in item["rows"]:
                tr = ET.SubElement(tbl, _w("tr"))
                for cell in row:
                    tc = ET.SubElement(tr, _w("tc"))
                    p = ET.SubElement(tc, _w("p"))
                    r = ET.SubElement(p, _w("r"))
                    t = ET.SubElement(r, _w("t"))
                    t.text = cell
    ET.SubElement(body, _w("sectPr"))
    document = ET.Element(_w("document"))
    document.set("xmlns:w", W_NS)
    document.append(body)
    return ET.tostring(document, encoding="utf-8", xml_declaration=True)


def make_docx(sections: list[dict], *, title: str | None = None) -> bytes:
    """Minimal valid .docx with headings, paragraphs and tables."""
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/docProps/core.xml" '
        'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '</Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        '<Relationship Id="rId2" '
        'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
        'Target="docProps/core.xml"/>'
        '</Relationships>'
    )
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties '
        'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        + (f"<dc:title>{title}</dc:title>" if title else "")
        + "</cp:coreProperties>"
    )
    core_xml = core.encode("utf-8")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", _docx_xml(sections))
        archive.writestr("docProps/core.xml", core_xml)
    return buffer.getvalue()


def write_xlsx(path, sheets: list[tuple[str, list[list]]]) -> None:
    """Write a multi-sheet workbook (openpyxl) to build XLSX fixtures."""
    import openpyxl

    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets:
        sheet = workbook.create_sheet(title=name)
        for row in rows:
            sheet.append(row)
    workbook.save(path)


def html_document(*, title="Publication title") -> str:
    return _html_template(title=title)


def _html_template(title: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>{title}</title>
  <meta name="description" content="Official publication"/>
  <meta name="og:type" content="article"/>
</head>
<body>
  <header class="site-header"><a href="/">Home</a></header>
  <nav class="main-nav">
    <ul><li><a href="/monetary-policy">Monetary policy</a></li><li>About</li></ul>
  </nav>
  <main id="content" role="main">
    <h1>{title}</h1>
    <p>Introduction paragraph that belongs to the section.</p>
    <h2>Decision</h2>
    <p>The committee decided to maintain the policy rate here.</p>
    <table class="data">
      <caption>Key rates</caption>
      <tr><th>Instrument</th><th>Level</th></tr>
      <tr><td>Policy rate</td><td>2.50</td></tr>
      <tr><td>Discount rate</td><td>3.00</td></tr>
    </table>
    <p>Download <a href="/files/full-report.pdf">the full report (PDF)</a> or
       <a href="/files/data.xlsx">the data (XLSX)</a>.</p>
  </main>
  <footer class="site-footer"><p>© Central Bank Co.</p></footer>
</body>
</html>
"""