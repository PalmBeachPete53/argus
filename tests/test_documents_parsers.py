"""End-to-end tests for the Phase 2A document parsers using local fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from argus.documents import (
    METHOD_CSV,
    METHOD_DOCX,
    METHOD_HTML,
    METHOD_PDF_TEXT,
    METHOD_PDF_UNAVAILABLE,
    METHOD_TXT,
    METHOD_XLSX,
    WARNING_EMPTY_TEXT,
    WARNING_MISSING_FILE,
    WARNING_PARSE_ERROR,
    WARNING_SCANNED_PDF,
    WARNING_UNSUPPORTED_KIND,
    Normalizer,
    document_id_of,
)
from argus.models import Document, DocumentStatus

from fixture_docs import html_document, make_docx, make_pdf, make_scanned_pdf, write_xlsx

FIXTURES = Path(__file__).parent / "fixtures" / "documents"


def make_document(tmp_path, data: bytes, *, kind: str, name: str, url: str | None = None) -> Document:
    path = tmp_path / name
    path.write_bytes(data)
    return Document(
        publication_id="pub-1",
        url=url or f"https://x.test/{name}",
        kind=kind,
        status=DocumentStatus.FETCHED,
        local_path=str(path),
    )


def normalize(data: bytes, *, kind: str, name: str, tmp_path) -> object:
    return Normalizer().parse(make_document(tmp_path, data, kind=kind, name=name))


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------


def test_html_fixture_structure(tmp_path):
    doc = make_document(tmp_path, (FIXTURES / "sample.html").read_bytes(), kind="html", name="sample.html")
    normalized = Normalizer().parse(doc)
    assert normalized.ok
    assert normalized.extraction_method == METHOD_HTML
    assert normalized.title == "Monetary Policy Report — Sample Central Bank"
    assert "keep the policy rate at 2.75" in normalized.text
    assert "Download the report" in normalized.text
    # boilerplate (header/nav/footer) is not part of the content
    assert "Home" not in normalized.text
    assert "About" not in normalized.text
    assert "Copyright Sample Central Bank" not in normalized.text


def test_html_sections_and_headings(tmp_path):
    normalized = normalize((FIXTURES / "sample.html").read_bytes(), kind="html", name="sample.html", tmp_path=tmp_path)
    headings = [(s.heading, s.level) for s in normalized.sections]
    assert ("Monetary Policy Report", 1) in headings
    assert ("Policy Decision", 2) in headings
    decision_text = next(s.text for s in normalized.sections if s.heading == "Policy Decision")
    assert "2.75" in decision_text


def test_html_links_preserved_in_metadata(tmp_path):
    normalized = normalize((FIXTURES / "sample.html").read_bytes(), kind="html", name="sample.html", tmp_path=tmp_path)
    linked = normalized.metadata.get("linked_documents", [])
    urls = [link["url"] for link in linked]
    assert any("report.pdf" in u for u in urls)


def test_html_table_structured(tmp_path):
    normalized = normalize((FIXTURES / "sample.html").read_bytes(), kind="html", name="sample.html", tmp_path=tmp_path)
    assert normalized.tables
    table = normalized.tables[0]
    assert table.headers == ["Year", "CPI", "GDP"]
    assert {"2026", "2027"} <= {row[0] for row in table.rows}


def test_html_fed_and_boe_fixtures(tmp_path):
    for name in ("fed_statement.html", "boe_minutes.html"):
        normalized = normalize(
            (FIXTURES / name).read_bytes(), kind="html", name=name, tmp_path=tmp_path
        )
        assert normalized.ok, name
        assert "Home" not in normalized.text, name


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def test_pdf_text_and_page_boundaries(tmp_path):
    doc = make_document(
        tmp_path, make_pdf(["First line of page one", "More text on page two"]), kind="pdf", name="a.pdf"
    )
    normalized = Normalizer().parse(doc)
    assert normalized.ok
    assert normalized.extraction_method == METHOD_PDF_TEXT
    assert "First line of page one" in normalized.text
    assert "More text on page two" in normalized.text
    assert {p.number for p in normalized.pages} == {1, 2}
    assert normalized.pages[0].text.startswith("First line of page one")
    assert normalized.pages[1].text.startswith("More text on page two")
    assert all(s.page in (1, 2) for s in normalized.sections)
    assert normalized.title is not None  # falls back to url when no PDF title


def test_pdf_uses_document_title(tmp_path):
    data = make_pdf(["Content of the report"])
    doc = make_document(tmp_path, data, kind="pdf", name="report.pdf", url="https://x.test/report.pdf")
    normalized = Normalizer().parse(doc)
    assert "https://x.test/report.pdf" in (normalized.title or "")


def test_pdf_scanned_document_reported(tmp_path):
    doc = make_document(tmp_path, make_scanned_pdf(page_count=2), kind="pdf", name="scan.pdf")
    normalized = Normalizer().parse(doc)
    assert normalized.extraction_method == METHOD_PDF_UNAVAILABLE
    assert WARNING_SCANNED_PDF in normalized.extraction_warnings
    assert normalized.text == ""


def test_pdf_digit_space_collapse():
    """pypdf artifact spaces inside numbers are collapsed; line breaks and
    letter spaces are preserved."""
    from argus.documents.pdf import collapse_digit_spaces

    assert collapse_digit_spaces("July 31, 202 6") == "July 31, 2026"
    assert collapse_digit_spaces("around 1.0 5 percent") == "around 1.05 percent"
    assert collapse_digit_spaces("voted by an 8-1 majority") == "voted by an 8-1 majority"
    assert collapse_digit_spaces("operati ons and V oting") == "operati ons and V oting"
    # a line break between digits is preserved (not a digit-space artifact)
    assert collapse_digit_spaces("rate 1.0\n6.0 next") == "rate 1.0\n6.0 next"


def test_pdf_missing_file(tmp_path):
    doc = Document(
        publication_id="pub-1",
        url="https://x.test/missing.pdf",
        kind="pdf",
        status=DocumentStatus.FETCHED,
        local_path=str(tmp_path / "missing.pdf"),
    )
    normalized = Normalizer().parse(doc)
    assert not normalized.ok
    assert WARNING_MISSING_FILE in normalized.extraction_warnings


# ---------------------------------------------------------------------------
# DOCX / XLSX / CSV / TXT
# ---------------------------------------------------------------------------


def test_docx_structure_and_order(tmp_path):
    docx = make_docx(
        [
            {"type": "heading", "text": "Title of Report", "level": 1},
            {"type": "para", "text": "First paragraph body."},
            {"type": "heading", "text": "Section Two", "level": 2},
            {"type": "para", "text": "Second paragraph body."},
            {"type": "table", "rows": [["Instrument", "Rate"], ["Policy", "2.75"]]},
        ],
        title="Docx Report Title",
    )
    doc = make_document(tmp_path, docx, kind="docx", name="report.docx")
    normalized = Normalizer().parse(doc)
    assert normalized.ok
    assert normalized.extraction_method == METHOD_DOCX
    assert normalized.title == "Docx Report Title"
    headings = [s.heading for s in normalized.sections]
    assert headings == ["Title of Report", "Section Two"]
    assert [s.level for s in normalized.sections] == [1, 2]
    assert normalized.text.index("First paragraph body.") < normalized.text.index("Second paragraph body.")
    assert normalized.tables[0].headers == ["Instrument", "Rate"]
    assert normalized.tables[0].rows == [["Policy", "2.75"]]


def test_docx_invalid_zip_reported(tmp_path):
    doc = make_document(tmp_path, b"this is not a zip file", kind="docx", name="bad.docx")
    normalized = Normalizer().parse(doc)
    assert not normalized.ok
    assert WARNING_PARSE_ERROR in normalized.extraction_warnings


def test_xlsx_sheets_to_tables(tmp_path):
    path = tmp_path / "data.xlsx"
    write_xlsx(path, [("Rates", [["Date", "Rate"], ["2026-06-18", "2.75"]]), ("Empty", [])])
    doc = Document(
        publication_id="pub-1", url="https://x.test/data.xlsx", kind="xlsx",
        status=DocumentStatus.FETCHED, local_path=str(path),
    )
    normalized = Normalizer().parse(doc)
    assert normalized.ok
    assert normalized.extraction_method == METHOD_XLSX
    assert len(normalized.tables) >= 1
    table = normalized.tables[0]
    assert table.name == "Rates"
    assert table.headers == ["Date", "Rate"]
    assert table.rows == [["2026-06-18", "2.75"]]


def test_csv_structured(tmp_path):
    normalized = normalize(
        (FIXTURES / "sample.csv").read_bytes(), kind="csv", name="sample.csv", tmp_path=tmp_path
    )
    assert normalized.extraction_method == METHOD_CSV
    assert normalized.tables
    table = normalized.tables[0]
    assert table.headers == ["Indicator", "Value", "Unit"]
    assert {"Policy rate", "CPI", "GDP growth"} <= {row[0] for row in table.rows}


def test_txt_plain_text(tmp_path):
    normalized = normalize(
        (FIXTURES / "sample.txt").read_bytes(), kind="txt", name="sample.txt", tmp_path=tmp_path
    )
    assert normalized.extraction_method == METHOD_TXT
    assert "The economy expanded modestly" in normalized.text


# ---------------------------------------------------------------------------
# Registry behaviour & normalizer semantics
# ---------------------------------------------------------------------------


def test_unsupported_kind_reported(tmp_path):
    doc = make_document(tmp_path, b"\x00\x01\x02", kind="exe", name="tool.exe")
    normalized = Normalizer().parse(doc)
    assert normalized.extraction_method not in (METHOD_HTML,)  # not one of the happy paths
    assert WARNING_UNSUPPORTED_KIND in normalized.extraction_warnings


def test_document_id_derived_from_sha256(tmp_path):
    import hashlib

    data = (FIXTURES / "sample.html").read_bytes()
    doc = make_document(tmp_path, data, kind="html", name="sample.html")
    assert document_id_of(doc) == hashlib.sha256(data).hexdigest()


def test_parse_is_content_preserving_and_repeatable(tmp_path):
    data = (FIXTURES / "sample.html").read_bytes()
    first = Normalizer().parse(make_document(tmp_path, data, kind="html", name="sample.html"))
    second = Normalizer().parse(make_document(tmp_path, data, kind="html", name="sample.html"))
    assert first.text == second.text
    assert [s.heading for s in first.sections] == [s.heading for s in second.sections]
    assert first.extraction_method == second.extraction_method


def test_parsers_do_not_claim_document_identity(tmp_path):
    # The Normalizer is the sole owner of a document's stable identity: parsers
    # return an empty document_id and Normalizer.parse rewrites it from SHA-256.
    from argus.documents.html import HtmlParser

    data = (FIXTURES / "sample.html").read_bytes()
    doc = make_document(tmp_path, data, kind="html", name="sample.html")

    raw = HtmlParser().parse(doc)
    assert raw.document_id == ""

    normalized = Normalizer().parse(doc)
    assert normalized.document_id == document_id_of(doc) != ""


def test_normalizer_persist_and_roundtrip(tmp_path):
    from conftest import make_store

    store = make_store(tmp_path)
    data = (FIXTURES / "boe_minutes.html").read_bytes()
    doc = make_document(tmp_path, data, kind="html", name="boe_minutes.html")

    normalized = Normalizer(store=store).normalize(doc)
    assert normalized is not None
    assert store.get_normalized_document(normalized.document_id) is not None

    # Idempotent: a second normalize without force is skipped.
    assert Normalizer(store=store).normalize(doc) is None

    # Reconstruct from the store and compare structure.
    loaded = store.get_normalized_document(normalized.document_id)
    assert loaded.text == normalized.text
    assert [s.heading for s in loaded.sections] == [s.heading for s in normalized.sections]
    assert loaded.extraction_method == METHOD_HTML


def test_page_field_ignored_for_html(tmp_path):
    normalized = normalize(
        (FIXTURES / "sample.html").read_bytes(), kind="html", name="sample.html", tmp_path=tmp_path
    )
    assert all(s.page is None for s in normalized.sections)