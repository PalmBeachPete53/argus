# Argus — Official G10 central bank publication collector

Infrastructure for discovering, registering and retrieving official monetary-policy
publications from the G10 central banks, up to the "raw documents" layer of the
analytical pipeline. No economic interpretation, classification or LLM analysis is
performed at this stage.

## Pipeline scope

```
Official Sources
      ↓
Source Registry        ✅
      ↓
Publication Discovery  ✅
      ↓
Publication Metadata   ✅
      ↓
Document Fetching      ✅
      ↓
Raw Documents          ✅
      ↓
Publication Classification        (next stage)
Type-Specific Extraction          (next stage)
Facts / Temporal Analysis         (next stage)
```

## Quickstart

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"

# List the configured banks and sources (declarative registry)
.venv/bin/python -m argus.cli --list-banks

# Discover + fetch everything (honours robots.txt, rate-limited; idempotent)
.venv/bin/python -m argus.cli --store data/argus.db --raw-root data/raw

# Discover only, restricted to one bank
.venv/bin/python -m argus.cli --bank fed --discover-only --raw-root data/raw

# Optional period filter: restrict discovery/fetch to a month or a year
.venv/bin/python -m argus.cli --month 2026-07 --store data/argus.db --raw-root data/raw
.venv/bin/python -m argus.cli --year 2026 --discover-only

# Delete all collected data (store db + raw documents), keeping data/ and data/raw/
.venv/bin/python -m argus.cli --purge --store data/argus.db --raw-root data/raw
```

All flags are optional: run without `--year`/`--month` to collect everything. They
combine with any subcommand (`--list-banks --month 2026-07` works too) and
`--month` takes precedence over `--year` if both are given.

Or from Python:

```python
from argus import CentralBankCollector

collector = CentralBankCollector()          # default store ./data/argus.db
publications = collector.discover_all()     # → list[Publication] (deduped, persisted)
print([(p.central_bank, p.title, p.publication_date) for p in publications])

documents = collector.fetch_all()           # → list[FetchResult], raw docs under data/raw/
```

`collector.run()` runs discovery then fetches. Running it repeatedly is idempotent:
publications are deduplicated by a deterministic identity, and already-fetched
documents are not re-downloaded.

Raw documents are kept per bank under `data/raw/<bank>/<YYYY>/<MM>/...`, each with a
SHA-256 fingerprint and full provenance (bank, source id, source url, publication
url, publication date, retrieved-at time) recorded in the SQLite store
(`data/argus.db`).

## Tests

```bash
.venv/bin/python -m pytest
```

Unit tests use local fixtures and a fake HTTP transport; they never depend on a live
website.

## Documentation

- `docs/ARCHITECTURE.md` — generic core, discovery abstractions, fetcher, storage,
  deduplication and lifecycle design.
- `docs/SOURCES.md` — the verified research matrix of official sources per bank
  (RSS / sitemap / HTML archives / calendars / APIs), with the source IDs used by
  each adapter.