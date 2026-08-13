# Argus — Official G10 central bank publication collector

Infrastructure for discovering, registering and retrieving official monetary-policy
publications from the G10 central banks, up to the "raw documents", normalization
and classification layers of the analytical pipeline. No economic interpretation or
LLM analysis is performed at this stage.

The authoritative evolution plan is `docs/ROADMAP.md`; phases 0–3 are complete,
phase 4 (Fact Model) is next.

## Pipeline scope

Status follows `docs/ROADMAP.md` (`COMPLETE` / `NEXT` / `NOT STARTED`):

```
Official Sources
      ↓
Source Registry        ✅  (Phase 1)
      ↓
Publication Discovery  ✅  (Phase 1)
      ↓
Publication Metadata   ✅  (Phase 1)
      ↓
Document Fetching      ✅  (Phase 1)
      ↓
Raw Documents          ✅  (Phase 1)
      ↓
Document Normalization ✅  (Phase 2: PDF/DOCX/XLSX/CSV/TXT/HTML → structured text+tables)
      ↓
Publication Classification ✅  (Phase 3: deterministic evidence-tier engine)
      ↓
Fact Model                    (Phase 4 — NEXT)
Type-Specific Extraction      (Phases 5–11 — NOT STARTED)
Temporal / Analysis           (Phases 12–16 — NOT STARTED)
Trading / Signal Layer        (Phase 17 — out of scope for now)
```

Stage note: **normalization** (`documents/`) turns raw bytes into structured,
traceable text. **classification** (`classification/`) assigns a canonical
`publication_type` from an explainable rule engine — no model calls, no fabricated
labels.

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

## Phases 2 & 3 — normalize & classify

Normalization parses raw documents on disk into structured text + tables (no
network), and classification assigns a canonical `publication_type` from a
deterministic rule engine:

```bash
# Parse all fetched documents (PDF/DOCX/XLSX/CSV/TXT/HTML) into the store
.venv/bin/python -m argus.cli --store data/argus.db --raw-root data/raw --normalize

# Re-run a single publication's normalization, overwriting previous output
.venv/bin/python -m argus.cli --store data/argus.db --raw-root data/raw --publication <id> --normalize --force

# Classify publications (optionally scoped per bank) and persist the result
.venv/bin/python -m argus.cli --store data/argus.db --normalize --classify --bank ecb

# Read-only store summary (publications, raw/normalized docs, classifications)
.venv/bin/python -m argus.cli --store data/argus.db --report

# In-process equivalent
from argus.documents import Normalizer
from argus.classification import PublicationClassifier

normalizer = Normalizer(store=collector.store, raw_root="data/raw")
docs = normalizer.normalize_all(force=False)

classifier = PublicationClassifier(store=collector.store)
results = classifier.classify_all()
```

## Tests

```bash
.venv/bin/python -m pytest
```

Unit tests use local fixtures and a fake HTTP transport; they never depend on a live
website. Binary fixtures (PDF / DOCX / XLSX) are generated at test time by
`tests/fixture_docs.py`, so no binary blobs are committed.

## Documentation

- `docs/ROADMAP.md` — official roadmap: vision, phases 0–17, invariants,
  architectural notes and current position.
- `docs/ARCHITECTURE.md` — generic core, discovery abstractions, fetcher, storage,
  deduplication and lifecycle design.
- `docs/SOURCES.md` — the verified research matrix of official sources per bank
  (RSS / sitemap / HTML archives / calendars / APIs), with the source IDs used by
  each adapter.