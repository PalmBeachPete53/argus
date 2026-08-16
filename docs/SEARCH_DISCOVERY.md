# Search Discovery fallback (SearXNG)

Argus's primary discovery is **native** (RSS / HTML / sitemap). Search Discovery
is an optional, per-source **fallback** used only when a source is explicitly
configured for it and native discovery is unavailable (or, if configured, empty).

SearXNG is a **discovery mechanism only**: it produces candidate publication
URLs. It never fetches or returns document content, and it is not a replacement
for the Fetcher. A URL found via search goes through the normal pipeline:

```
SearchDiscovery → publication candidate → upsert → classification
→ Fetcher → normalization → gated dispatch → extraction → persistence
```

There is exactly one document-ingestion pipeline.

## Architecture

```
SourceRegistry
    │
    ▼
Discovery layer
    ├── Native Discovery (RSS / HTML / sitemap) — primary
    └── Search Discovery (fallback, per source)
            │
            ▼
        SearchProvider            (argus.search.SearchProvider)
            │
            ▼
        SearxngSearchProvider     (argus.search.SearxngSearchProvider)
```

## Configuration

Argus works fully without SearXNG. A search fallback is enabled only when both:

1. the source declares a `search_query` (see the RBA / RBNZ adapters); and
2. a `SearchProvider` is injected into the collector.

### CLI / environment

```bash
export SEARCH_PROVIDER=searxng
export SEARXNG_BASE_URL=http://localhost:8080/
export SEARXNG_ENGINES=google,startpage   # optional
export SEARXNG_LANGUAGE=en                # optional
python -m argus.cli --bank rba --discover-only
```

Without `SEARCH_PROVIDER` (or without `SEARXNG_BASE_URL`), Argus runs exactly as
before and the fallback is never invoked.

### Per-source configuration

The fallback is declared on the source's `DiscoverySpec`:

- `search_query` — the query (constrained to the official domain, e.g.
  `site:rba.gov.au "Monetary Policy Decision"`);
- `search_domain` — only results whose host is this domain (or a subdomain) are
  accepted;
- `search_engines` — optional SearXNG engines;
- `search_fallback_on_empty` — if `True`, the fallback also runs when native
  discovery succeeds but yields no results; otherwise an empty native result is
  a valid state and no search happens.

Configured sources today: **RBA** (`rba_media_releases_rss`) and **RBNZ**
(`rbnz_ocr_decisions`). Their official domains block automated native access
from some environments (HTTP 403); the search queries are constrained to the
official domains and target the real publication wording.

## Fallback semantics

1. Try native discovery.
2. If it produces results → use them (search never runs).
3. If native discovery **raises** and the source has a `search_query` and a
   provider is configured → run the search fallback.
4. If native discovery succeeds but yields **nothing** → this is a valid state
   unless `search_fallback_on_empty` is set.
5. If no provider is configured → the native error is recorded and no fallback
   runs.

## Provenance

A publication discovered via search carries in `Publication.extra`:

```python
{
  "discovery_method": "search",
  "search_provider": "searxng",
  "search_query": "site:rba.gov.au \"Monetary Policy Decision\"",
  "search_rank": 1,
  "search_result_url": "https://www.rba.gov.au/...",
}
```

This answers "how did Argus discover this publication?" and survives the store
round-trip.

## Deduplication

A publication found both natively and via search is a single business object:
the existing `dedup_key` / canonical URL / `upsert_publication` identity is
reused. No second deduplication mechanism exists.

## SearXNG (optional, Docker)

SearXNG is run separately (typically in Docker) and must expose its **JSON
API** (`format=json`). A minimal compose service:

```yaml
# docker-compose.searxng.yml
services:
  searxng:
    image: searxng/searxng:latest
    ports:
      - "8080:8080"
    environment:
      - SEARXNG_BASE_URL=http://localhost:8080/
    volumes:
      - ./searxng:/etc/searxng
```

Ensure `/etc/searxng/settings.yml` enables the JSON format
(`search.formats: [html, json]`). Argus never assumes SearXNG is running
locally and does not depend on it for native sources.
