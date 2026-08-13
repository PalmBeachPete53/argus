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