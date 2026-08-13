# Architecture

`argus` is a generic collection layer for official central bank publications.
The guiding principle is **abstraction of responsibilities, specialisation of
sources**: generic discovery/fetch/storage infrastructure shared by all banks, and
thin, declarative bank adapters that only encode *where* each bank exposes
publications.

```
src/argus/
├── models.py        CentralBank, Source, DiscoverySpec, Publication, Document, states
├── registry.py      SourceRegistry — aggregates all bank adapters
├── collector.py     CentralBankCollector — discover_all / fetch_all / run
├── http.py          HttpClient — retries, backoff, jitter, per-host rate limiting,
│                    cache-friendly user agent, robots.txt enforcement (RobotsGate)
├── robots.py        Minimal robots.txt parser (RFC 9309-style longest-match)
├── store.py         SQLite persistence, dedup, idempotence, error log, lifecycle
├── fetcher.py       Fetcher — downloads a publication's documents + linked docs
├── discovery/
│   ├── base.py      DiscoveryStrategy (abstract) + publication builder
│   ├── rss.py       RSS 2.0 / RSS 1.0 (RDF) / Atom
│   ├── sitemap.py   sitemap.xml + sitemap index recursive walk
│   └── html.py      HTML archive / calendar listing (+ pagination, date sniffing)
├── documents/       Phase 2A — parsers + normalization (no network, no economics)
│   ├── base.py      NormalizedDocument / DocumentSection / DocumentTable /
│   │                DocumentPage + shared extraction-method & warning vocabulary
│   ├── registry.py  ParserRegistry — dispatch by kind / content-type / byte sniffing
│   ├── normalizer.py Normalizer — parse + persist, idempotent, store-backed
│   ├── html.py      HTML parser (main-content isolation, sections, tables, links)
│   ├── pdf.py       PDF parser via pypdf (text + pages + scanned/blank detection)
│   ├── docx.py      DOCX parser (zipfile + OOXML XML — headings, tables, core props)
│   ├── spreadsheet.py XLSX/CSV/TXT parsers (structured tables per sheet/file)
│   └── _util.py     shared helpers (make_unavailable, sniffing)
├── classification/  Phase 2B — deterministic, explainable publication typing
│   ├── base.py      PublicationClassification, Confidence, method & vocabulary
│   ├── rules.py     generic regex TypeRules + canonical_types()
│   ├── bank_rules.py bank-specific rules, one declarative block per bank
│   └── classifier.py PublicationClassifier — evidence-tier engine (no model calls)
├── facts/          Phase 4 — the Fact model (contract for future extractors)
│   ├── base.py     Fact, FactValue, FactPeriod, FactLocation, ExtractionResult,
│   │               value-kind & extraction-method vocabulary
│   └── identity.py deterministic fact_id (SHA-256 over semantic+provenance)
└── adapters/        One BankAdapter per central bank (10x), fully declarative
```

## Separation of responsibilities

### 1. SourceRegistry → declarative bank/source catalogue

A `Source` declares *where* a bank exposes publications (mechanism + URL + filters)
and a priority. Sources never download anything:

```python
Source(
    id="rba_media_releases_rss",
    central_bank="rba",
    name="RBA media releases RSS",
    discovery=DiscoverySpec(kind="rss", url="https://www.rba.gov.au/rss/rss-cb-media-releases.xml"),
    priority=1,
    fallback_for=(...),   # optional, declarative
)
```

`SourceRegistry` loads 10 `BankAdapter`s, each returning a `CentralBank` and its
`Source`s, and exposes `sources`, `sources_for_bank(bank)`, `enabled_sources(...)`.
All bank-specific URLs live in `src/argus/adapters/*.py` — nothing is spread through
the core.

### 2. Discovery → "what publications exist?"

`DiscoveryStrategy` turns a `Source` into `Publication` records. All strategies
produce the **same** model, so downstream code never knows whether a publication was
found via RSS, sitemap or HTML. Supported mechanisms:

| kind      | notes |
|-----------|-------|
| `rss`     | RSS 2.0, RSS 1.0/RDF (`dc:date`), Atom; enclosures recorded as document URLs |
| `sitemap` | follows sitemap index + urlset; URL include/exclude regex filters |
| `html`    | archive/calendar listing pages; same-host + scope filtering; optional `pagination_urls`; date sniffed from anchor context; `allow_future` for calendars |

Filters (`include`/`exclude` regexes, `scope_prefixes`, `lookback_window_days`,
`allow_future`) are data, not code — an adapter for a new bank only describes these.

Discovery does **no** economic interpretation. Raw feed/HTML context is preserved in
`Publication.extra` (`feed_title`, `feed_guid`, `html_anchor_text`, `type_hint`, …)
so later classification can use it without re-fetching.

### 3. Fetcher → raw documents

`Fetcher` is agnostic of bank and discovery mechanism. For a `Publication` it:

1. fetches each `document_urls` (or the publication page itself),
2. classifies kind + extension from content type / URL extension,
3. stores bytes under `data/raw/<bank>/<YYYY>/<MM>/<slug>-<sha8>.<ext>`,
4. records SHA-256, size, content type, retrieved-at, local path in the store,
5. when the page is HTML, extracts linked documents (PDF/XLSX/DOCX/…) from the page
   — this handles aggregator pages (e.g. Riksbank decision pages linking many PDFs).

Idempotent: an already-fetched URL is not re-downloaded (unless `force`). Failed
documents are re-tried on later runs up to a per-document retry budget.

## Data model

- `CentralBank` — id, name, currency, official_domain
- `Source` — id, central_bank, name, discovery (DiscoverySpec), priority, enabled,
  publication_types, fallback_for
- `Publication` — central_bank, title, url, source_id, source_url, publication_date,
  meeting_date, publication_type (optional/unknown), language, document_urls, extra
  (provenance preserved: every record can be traced to an official source)
- `Document` — publication_id, url, kind, status, local_path, sha256, content_type,
  size, retrieved_at, retries, error
- `NormalizedDocument` — publication_id, document_id, source_url, local_path,
  document_kind, mime_type, title, text, `sections[]`, `tables[]`, `pages[]`,
  metadata, extraction_method, extraction_warnings, normalized_at
- `DocumentSection` — order, heading, level, text, page
- `DocumentTable` — order, name, headers, rows, page, metadata
- `DocumentPage` — number, text
- `PublicationClassification` — publication_id, central_bank, publication_type,
  confidence, method, evidence (list of reasons), classified_at
- `Fact` (Phase 4) — fact_id (deterministic identity), publication_id,
  document_id, central_bank, subject, predicate, value/previous_value/change
  (`FactValue`: kind + numeric/string value + unit + verbatim `source_text`),
  period (`FactPeriod`), effective_date, source_location (`FactLocation`),
  source_text, extraction_method/version, confidence, extracted_at
- `ExtractionResult` (Phase 4) — publication_id, document_id, facts[], warnings[]
  (contract returned by future type-specific extractors)

## Phase 2A — Normalization (`documents/`)

`Normalizer` turns a raw `Document` (already fetched to disk, `local_path`) into a
`NormalizedDocument`. It is strictly content-preserving: no summarization,
translation or economic interpretation. Parsers never touch the network — they only
read `Document.local_path`, so normalization can be re-run on stored bytes at any
time.

Dispatch (`ParserRegistry`) picks a parser from `Document.kind`, then the content
type, and finally byte sniffing (`%PDF-`, OLE header, `PK\x03\x04`, …). Each parser
produces the same dataclasses, so downstream code never depends on the source format.

- **HTML** — isolates `<main>` (site header/nav/footer are excluded), builds sections
  from headings, captures tables (`<table>` → headers+rows) and outbound links
  (`linked_documents` metadata) without following them.
- **PDF** — page-by-page text via pypdf with page boundaries; a PDF whose pages have
  no text but contain images is reported as `scanned_pdf` (method `pdf_unavailable`)
  instead of hallucinating OCR text; blank-but-textless PDFs emit `empty_text`.
- **DOCX** — OOXML via `zipfile`/`ElementTree`: heading levels, paragraph order,
  tables, and core properties (`dc:title`, …).
- **XLSX / CSV / TXT** — structured tables per sheet/file (header detection,
  delimiter auto-detection, trailing-empty-row trimming); TXT stays a single text
  blob.

Every `NormalizedDocument` carries `extraction_method` and `extraction_warnings`
(shared codes such as `scanned_pdf`, `unsupported_kind`, `missing_file`,
`parse_error`, `empty_text`) so failures are explicit and auditable. Documents
normalized with a `Store` are persisted (`normalized_documents` +
`document_sections` + `document_tables`) keyed by the SHA-256 `document_id`, with
idempotence: re-running without `force` skips already-normalized documents.

## Phase 2B — Classification (`classification/`)

`PublicationClassifier` assigns one `publication_type` from a fixed canonical
vocabulary (`PUBLICATION_TYPES`) to each publication, deterministically and
without any model call. It evaluates evidence tiers in order and stops at the first
tier that yields a single unambiguous candidate:

1. `source_type_hint` — a single canonical declared type from the **live**
   `Source.publication_types` (or, for unregistered sources, the stored
   `extra["type_hint"]`) ⇒ HIGH confidence. Broad "press releases" feeds declare
   *no* type — adapters only tag genuinely type-specific sources — and the live
   declaration is authoritative over any stale stored hint.
2. `url_pattern` — bank-specific and generic URL regexes.
3. `title_pattern` — same, on the title.
4. `document_metadata` / `content_heuristic` — from the normalized document when
   available (heuristic content matches are LOW confidence).

A later single signal that contradicts the current tier's only hit makes the tier
unreliable and it is skipped (e.g. a generic URL slug vs an explicit
"Minutes of the Federal Open Market Committee" title). Confidence is HIGH when the
winner also appears in the source hint set, MEDIUM otherwise. Unresolved
publications are returned as `type="unknown"` with a non-empty `evidence` trail, and
a classification never fabricates a type.

The content heuristic (fifth tier) only looks at a bounded, configurable window of
the normalized text — `content_window` (default 20 000 chars) with
`content_scope="first_n_chars"` — chosen so long 150-page reports are not scanned
end-to-end by default; raise `content_window` when the distinguishing passage sits
deeper in a document.

### Single source of truth for classification

The `classifications` table is the **authoritative** record of a publication's type:
it stores `publication_type`, `confidence`, `method` and `evidence`, upserted by
`Store.set_classification()`. `publications.publication_type` is a **denormalized
cache** duplicated onto the quick-filter column **in the same transaction**, so both
always agree by construction. Downstream code that needs the authoritative type +
reasoning must read via `Store.get_classification()` / `list_classifications()`;
the `Publication` field is only a lightweight cache for listing/filtering. Batch entry points persist results in the
`classifications` table and update `publications.publication_type`.

All bank-specific knowledge lives declaratively in `bank_rules.py`; the engine in
`classifier.py` never branches on bank id.

## Phase 4 — Facts (`facts/`)

`Fact` is the canonical, structured representation of information **explicitly
present** in a source document — never an interpretation (see
`docs/DATA_MODEL.md`). A Fact carries full provenance: `publication_id` +
`document_id` (reusing the existing chain to `Source`/`CentralBank`),
`source_text` (verbatim wording), `source_location` (`section`/`table`/`page`/
`offset`, format-independent) and `extraction_method` + `extraction_version`
for auditability. Values are typed (`FactValue` kinds: number, percentage,
basis_points, currency, date, boolean, categorical, text, range, null) and
never stored as opaque strings; `FactPeriod` keeps forecast/reference periods
(year, quarter, month, range) canonical and sortable, distinct from
`effective_date` and the publication/meeting dates.

Identity is deterministic: `fact_id` = SHA-256 over stable semantic +
provenance fields (publication, document, subject, predicate, period,
effective_date) — the
extracted value is excluded so corrections update the row in place. Persistence
in the `facts` table is idempotent (`save_fact` upserts by `fact_id`;
`rebuild_facts_for_document` replaces a document's facts in one transaction).

Future type-specific extractors return an `ExtractionResult(publication_id,
document_id, facts, warnings)`. No extractor exists yet: this phase only
defines the contract and the data model.

## Deduplication

Stable identity is derived per `Publication`:

1. if a `url` exists → `sha256("u|" + canonical_url)` (normalization removes
   fragments, tracking params, sorts remaining query params, strips default ports,
   lowercases host);
2. otherwise → `sha256("t|" + bank + "|" + normalized_title + "|" + date)`.

This handles the common G10 case where the same publication is met via RSS, sitemap
and an HTML archive with slightly different URL spellings. It is deterministic and
unit-tested.

## Idempotence & lifecycle

`Store` upserts publications by dedup key: `first_seen_at` is preserved, document
URLs and `extra` metadata are merged, `last_seen_at` is refreshed. Re-running the
collector therefore never duplicates records.

Publication lifecycle:

```
DISCOVERED ──fetch──▶ FETCHED      (all documents retrieved)
    │                  PARTIAL     (some documents failed, some succeeded)
    │                  FAILED      (all documents failed)
    └──re-discovery──▶ UPDATED     (a previously fetched publication changed)
```

## Isolation & errors

`CentralBankCollector.discover_all`/`fetch_all` run each source inside its own
try/except. A failing bank is logged as a `CollectError` (bank, source, strategy,
url, HTTP status, error type, message, run id, timestamp) and the other banks keep
running. Errors are persisted in the `collect_errors` table.

## Respect for websites

- robots.txt is honoured (per-origin cache, our token falls back to `*`; behaviour
  on fetch failure is configurable);
- per-host minimum request interval with a globally unique `User-Agent`
  (`ArgusCollector/0.1 …`);
- timeouts, exponential backoff + jitter, retries on transient statuses
  (408/429/5xx) and transport errors;
- discovery of already-known publications does not refetch documents.

## Adding a new central bank

1. Add a `CentralBank` definition,
2. declare one or more `Source`s (RSS / sitemap / HTML — with filters if needed),
3. only write a custom discovery path if the site truly requires it (none of the
   current G10 banks do — all ten use the generic strategies).

No core code changes are required.