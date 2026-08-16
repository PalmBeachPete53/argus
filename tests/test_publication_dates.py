"""Regression tests for publication temporal provenance (F1 fix).

Covers the root cause and the correction:

- a sitemap ``<lastmod>`` is a crawl signal, never a ``publication_date``;
- ``upsert_publication`` never blanks a known date with ``None``;
- the HTML normalizer captures authoritative date metadata (JSON-LD,
  OpenGraph/Article, ``<time datetime>``);
- ``documents.dates`` extracts the publication date along a documented trust
  hierarchy;
- the ``Normalizer`` refines an undated publication's date from its document
  metadata (and never overwrites an existing date);
- undated publications are excluded from a dated fetch window.

These reproduce the real 2025-campaign findings (Norges / Riksbank historical
pages artificially stamped with their crawl date).
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from argus.documents import Normalizer
from argus.documents.dates import extract_publication_date_from_metadata
from argus.models import Document, DocumentStatus, Publication
from argus.store import Store

from conftest import make_store


def _doc(tmp_path, html: str, *, publication_id: str, name: str) -> Document:
    path = tmp_path / name
    path.write_text(html, encoding="utf-8")
    return Document(
        publication_id=publication_id,
        url=f"https://x.test/{name}",
        kind="html",
        status=DocumentStatus.FETCHED,
        local_path=str(path),
    )


def _pub(*, url="https://x.test/a", publication_date=None) -> Publication:
    return Publication(
        central_bank="x",
        title="t",
        url=url,
        source_id="s",
        source_url="https://x.test",
        publication_date=publication_date,
    )


# ---------------------------------------------------------------------------
# sitemap <lastmod> is a crawl signal
# ---------------------------------------------------------------------------


def test_upsert_never_blanks_known_date(tmp_path):
    store = make_store(tmp_path)
    saved = store.upsert_publication(_pub(publication_date=datetime(2023, 6, 29, tzinfo=timezone.utc)))
    # A later discovery with no date (e.g. a sitemap entry) must not blank it.
    store.upsert_publication(_pub(publication_date=None))
    got = store.get_publication(saved.id)
    assert got is not None
    assert got.publication_date == datetime(2023, 6, 29, tzinfo=timezone.utc)


def test_upsert_keeps_date_when_rediscovered_with_same_date(tmp_path):
    store = make_store(tmp_path)
    date = datetime(2023, 6, 29, tzinfo=timezone.utc)
    store.upsert_publication(_pub(publication_date=date))
    store.upsert_publication(_pub(publication_date=date))
    pubs = store.list_publications()
    assert len(pubs) == 1
    assert pubs[0].publication_date == date


# ---------------------------------------------------------------------------
# document metadata capture
# ---------------------------------------------------------------------------


def test_html_normalizer_captures_date_metadata(tmp_path):
    html = """<html><head>
      <meta property="og:published_time" content="2023-06-29T09:30:35Z"/>
      <meta name="dcterms.created" content="2023-06-29"/>
      <script type="application/ld+json">{"@type":"NewsArticle","datePublished":"2023-06-29T09:30:35"}</script>
    </head><body><article><h1>June 2023 report</h1><p>Some body text.</p></article></body></html>"""
    normalized = Normalizer().parse(_doc(tmp_path, html, publication_id="pub", name="r.html"))
    assert normalized.metadata["html_meta"]["og:published_time"] == "2023-06-29T09:30:35Z"
    assert normalized.metadata["html_meta"]["dcterms.created"] == "2023-06-29"
    assert normalized.metadata["json_ld"][0]["datePublished"] == "2023-06-29T09:30:35"


def test_html_normalizer_captures_time_element(tmp_path):
    html = """<html><head><title>r</title></head><body>
      <article><h1>Decision</h1><time datetime="2023-06-29T09:30:00">29 June 2023</time><p>text</p></article>
    </body></html>"""
    normalized = Normalizer().parse(_doc(tmp_path, html, publication_id="pub", name="t.html"))
    assert "2023-06-29T09:30:00" in normalized.metadata.get("dates", [])


# ---------------------------------------------------------------------------
# trust hierarchy extraction
# ---------------------------------------------------------------------------


def test_extract_json_ld_wins_over_meta():
    metadata = {
        "html_meta": {"article:published_time": "2023-06-30T00:00:00"},
        "json_ld": [{"@type": "NewsArticle", "datePublished": "2023-06-29T09:30:35"}],
        "dates": ["2023-06-28"],
    }
    dt, source = extract_publication_date_from_metadata(metadata)
    assert source == "json_ld:datePublished"
    assert dt.date().isoformat() == "2023-06-29"


def test_extract_meta_wins_over_time():
    metadata = {"html_meta": {"article:published_time": "2023-06-30T00:00:00"}, "dates": ["2023-06-28"]}
    dt, source = extract_publication_date_from_metadata(metadata)
    assert source == "meta:article:published_time"
    assert dt.date().isoformat() == "2023-06-30"


def test_extract_time_as_last_resort():
    metadata = {"dates": ["2023-06-28T00:00:00"]}
    dt, source = extract_publication_date_from_metadata(metadata)
    assert source == "time:time"
    assert dt.date().isoformat() == "2023-06-28"


def test_extract_none_when_no_authoritative_metadata():
    assert extract_publication_date_from_metadata({}) == (None, None)
    assert extract_publication_date_from_metadata({"html_meta": {"description": "x"}}) == (None, None)
    # sitemap_lastmod / crawl signals are never a publication date
    assert extract_publication_date_from_metadata({"sitemap_lastmod": "2025-04-09"}) == (None, None)


# ---------------------------------------------------------------------------
# normalizer refinement
# ---------------------------------------------------------------------------


def test_normalizer_sets_date_when_publication_undated(tmp_path):
    store = make_store(tmp_path)
    saved = store.upsert_publication(_pub(publication_date=None))
    html = """<html><head>
      <script type="application/ld+json">{"@type":"Report","datePublished":"2023-06-29T09:30:35"}</script>
    </head><body><article><h1>June 2023</h1><p>body</p></article></body></html>"""
    doc = _doc(tmp_path, html, publication_id=saved.id, name="mpr.html")
    Normalizer(store=store).normalize(doc)
    got = store.get_publication(saved.id)
    assert got is not None
    assert got.publication_date is not None
    assert got.publication_date.date().isoformat() == "2023-06-29"
    assert got.extra.get("publication_date_source") == "json_ld:datePublished"


def test_normalizer_never_overwrites_existing_date(tmp_path):
    store = make_store(tmp_path)
    saved = store.upsert_publication(_pub(publication_date=datetime(2020, 1, 1, tzinfo=timezone.utc)))
    html = """<html><head>
      <meta property="og:published_time" content="2023-06-29T09:30:35"/>
    </head><body><article><h1>x</h1><p>body</p></article></body></html>"""
    doc = _doc(tmp_path, html, publication_id=saved.id, name="a.html")
    Normalizer(store=store).normalize(doc)
    got = store.get_publication(saved.id)
    assert got.publication_date == datetime(2020, 1, 1, tzinfo=timezone.utc)


def test_reference_case_riksbank_june_2023(tmp_path):
    # The real Riksbank "monetary policy report june 2023" page carries
    # JSON-LD datePublished = 2023-06-29 (verified on the collected data).
    # It must be dated 2023 — never the crawl date (2025-09-02) that the
    # previous sitemap lastmod promotion produced.
    store = make_store(tmp_path)
    saved = store.upsert_publication(_pub(publication_date=None))
    html = """<html><head>
      <title>Monetary Policy Report, June 2023 | Sveriges Riksbank</title>
      <script type="application/ld+json">{"@type":"Report","datePublished":"2023-06-29T09:30:35","dateModified":"2023-06-29T09:30:35"}</script>
    </head><body><article><h1>Monetary Policy Report, June 2023</h1><p>body</p></article></body></html>"""
    doc = _doc(tmp_path, html, publication_id=saved.id, name="mpr-june-2023.html")
    Normalizer(store=store).normalize(doc)
    got = store.get_publication(saved.id)
    assert got.publication_date.date().isoformat() == "2023-06-29"
    assert got.publication_date.year == 2023  # never 2025


# ---------------------------------------------------------------------------
# window semantics: undated historical pubs leave the dated window
# ---------------------------------------------------------------------------


def test_undated_publication_excluded_from_dated_window(tmp_path):
    store = make_store(tmp_path)
    store.upsert_publication(_pub(url="https://x.test/undated", publication_date=None))
    store.upsert_publication(_pub(url="https://x.test/in2025", publication_date=datetime(2025, 4, 10, tzinfo=timezone.utc)))
    in_window = store.list_publications(
        date_start=datetime(2025, 1, 1, tzinfo=timezone.utc),
        date_end=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    urls = [p.url for p in in_window]
    assert "https://x.test/undated" not in urls
    assert "https://x.test/in2025" in urls
