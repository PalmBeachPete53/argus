# Argus — Official G10 central bank monetary-policy pipeline

Argus collects, normalizes, classifies and extracts **canonical, provenance-carrying
Facts** from the official monetary-policy communications of the 10 initial G10
central banks, then derives temporal **FactChanges** and **Temporal Relationships**.
No economic interpretation, trading logic, sentiment or LLM-based analysis is
performed: every conclusion is traceable to an official source.

The authoritative evolution plan is `docs/ROADMAP.md`.

## Pipeline

```
Official Sources
      ↓
SourceRegistry (10 banks, Bank Toggle)
      ↓
Discovery
      ├── Native Discovery   (RSS / HTML / sitemap) — primary
      └── Search Discovery   (fallback, per source) — SearXNG, discovery only
      ↓
Publication upsert / deduplication (deterministic identity)
      ↓
Fetcher  (the only way document content is retrieved)
      ↓
Raw Document → Normalization (HTML/PDF/DOCX/XLSX/CSV/TXT)
      ↓
Classification (deterministic evidence-tier engine → canonical publication_type)
      ↓
Gated dispatch → Extractor (per bank, per publication family)
      ↓
Facts (typed, provenance-carrying) → Store
      ↓
FactChanges (Phase 5 — temporal comparisons)
      ↓
Temporal Relationships (Phase 6 — temporal relations between observed changes)
```

Key responsibilities:
- **discovery** (`discovery/`) produces publication candidates from official sources.
- **fetcher** (`fetcher.py`) retrieves raw documents — and is the *only* mechanism
  that ever fetches document content.
- **normalization** (`documents/`) turns raw bytes into structured, traceable text.
- **classification** (`classification/`) assigns a canonical `publication_type`
  from an explainable rule engine — no model calls.
- **facts** (`facts/`) are typed, provenance-carrying assertions extracted by
  per-bank extractors.
- **store** (`store.py`) persists publications, documents, classifications,
  facts and the derived `fact_changes` / `policy_reactions` tables.

## Banks

All 10 banks are registered in the `SourceRegistry`; a generic **Bank Toggle**
(`docs/BANKS.md`) decides which participate in operational executions:

| Bank | State |
|---|---|
| Fed | ON |
| ECB | ON |
| BoE | ON |
| BoJ | ON |
| SNB | ON |
| BoC | ON |
| RBA | ON |
| RBNZ | OFF (official source `rbnz.govt.nz` currently inaccessible from the execution environment; bank fully implemented, not removed) |
| Norges | ON |
| Riksbank | ON |

A disabled bank keeps its adapter, sources, discovery, classification,
extractors, fixtures and unit tests — it is simply excluded from integrated
runs and parametrized E2E scenarios. See `docs/BANKS.md`.

## Discovery

Native discovery (RSS / HTML / sitemap) is the primary mechanism and is
unchanged. **Search Discovery** (`docs/SEARCH_DISCOVERY.md`) is an optional,
per-source **fallback** that uses a `SearchProvider` (SearXNG) to produce
official publication candidates when native discovery is unavailable:

- it only yields candidate URLs, never document content;
- it is configured per source (`search_query`, `search_domain`);
- it preserves discovery provenance and reuses existing deduplication;
- the Fetcher remains the single document-ingestion path.

SearXNG is optional: Argus works entirely without it for native sources.
Configuration via environment: `SEARCH_PROVIDER=searxng`,
`SEARXNG_BASE_URL`, `SEARXNG_ENGINES`.

## Bank Toggle

`src/argus/config.py` is the single source of truth (`BANKS_ENABLED`).
Environment overrides: `ARGUS_BANKS_DISABLED` and `ARGUS_BANKS_ENABLED`
(allow-list, authoritative). Explicit bank selection (`--bank`) never bypasses
the toggle — the only way to run a disabled bank is to re-enable it via
`ARGUS_BANKS_ENABLED`. See `docs/BANKS.md`.

## What Argus produces

- **Facts** — canonical typed assertions (policy rates, changes, dates,
  assessments, forward guidance, …) with verbatim provenance.
- **FactChanges (Phase 5)** — temporal comparisons of the same lineage
  (same bank/subject/predicate/value kind/period/qualifier/type) between
  consecutive publications, with provenance on both sides and deterministic
  identities. Idempotent.
- **Temporal Relationships (Phase 6)** — descriptive temporal relations
  between an earlier `FactChange` and a later `FactChange`, within the
  implemented window, without look-ahead. **This is a temporal relation, not a
  causal claim, not a central-bank reaction function.** Idempotent.

## Quickstart

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"

# List the configured banks and sources, with ON/OFF toggle state
.venv/bin/python -m argus.cli --list-banks

# Discover + fetch everything for the active banks (honours robots.txt, rate-limited, idempotent)
.venv/bin/python -m argus.cli --store data/argus.db --raw-root data/raw

# Discover only, restricted to one bank
.venv/bin/python -m argus.cli --bank fed --discover-only --raw-root data/raw

# Optional period filter: restrict discovery/fetch to a month or a year
.venv/bin/python -m argus.cli --month 2026-07 --store data/argus.db --raw-root data/raw
.venv/bin/python -m argus.cli --year 2026 --discover-only

# Normalize fetched documents, then classify stored publications (offline)
.venv/bin/python -m argus.cli --store data/argus.db --raw-root data/raw --normalize
.venv/bin/python -m argus.cli --store data/argus.db --classify

# Read-only store summary
.venv/bin/python -m argus.cli --store data/argus.db --report

# Delete all collected data (store db + raw documents), keeping data/ and data/raw/
.venv/bin/python -m argus.cli --purge --store data/argus.db --raw-root data/raw
```

From Python:

```python
from argus import CentralBankCollector

collector = CentralBankCollector()          # default store ./data/argus.db
publications = collector.discover_all()     # → list[Publication] (deduped, persisted)
documents = collector.fetch_all()           # → list[FetchResult], raw docs under data/raw/
```

`collector.run()` runs discovery then fetches. Running it repeatedly is
idempotent: publications are deduplicated by a deterministic identity, and
already-fetched documents are not re-downloaded.

Raw documents are kept per bank under `data/raw/<bank>/<YYYY>/<MM>/...` with a
SHA-256 fingerprint and provenance in the SQLite store (`data/argus.db`).

## Extraction and analysis (offline)

```python
from argus.store import Store
from argus.config import enabled_banks
from argus.changes import analyze_changes      # Phase 5
from argus.reactions import analyze_reactions  # Phase 6

store = Store("data/argus.db")
for bank in enabled_banks():                          # the active banks
    analyze_changes(store, bank=bank, persist=True)      # → FactChanges
    analyze_reactions(store, bank=bank, persist=True)    # → Temporal Relationships (legacy API name)
```

Extraction is performed through the gated per-family entry points
(`extract_decision`, `extract_statement`, `extract_minutes`, …), which dispatch
the right extractor only for the classified publication type.

## Tests

```bash
.venv/bin/python -m pytest
```

- **Unit / integration tests** use local fixtures and a fake HTTP transport —
  never a live website.
- **Golden corpus** (`docs/` … see below) replays captured real official
  sources offline through the L4 harness.
- **E2E / idempotence tests** cover one representative publication per active
  bank through the full pipeline, including idempotent re-execution.
- A **2025 historical validation** campaign was run on real data for the active
  banks (see `docs/ROADMAP.md` "Current Position").

## Project state

- **Golden corpus: 9/10 banks** with real captured official sources (Fed, ECB,
  BoE, BoJ, SNB, BoC, RBA via Search Discovery, Norges, Riksbank); RBNZ has no
  real capture yet (its official domain is WAF-blocked from the capture
  environment) and stays at 9/10 — no synthetic golden exists.
- Phases 1–4 (Foundation, Source Discovery, Document Pipeline, Fact Extraction
  incl. 4.1–4.7) and Phases 5–8 are `COMPLETE`; Phase 9 (Historical
  Validation) is `DEFERRED`; Phase 10 (Trading) is `NOT STARTED`.

## Documentation

- `docs/ROADMAP.md` — official roadmap: phases, invariants, architectural notes
  and current position.
- `docs/ARCHITECTURE.md` — generic core, discovery abstractions, fetcher, storage,
  deduplication, lifecycle, Search Discovery and Bank Toggle.
- `docs/DATA_MODEL.md` — the Fact model: what a Fact is/is not, value types,
  temporal semantics, provenance, confidence, identity, persistence.
- `docs/EXTRACTORS.md` — the type-specific extractors (per family and bank).
- `docs/CHANGES.md` — Phase 5: FactChange matching and identity rules.
- `docs/TEMPORAL_RELATIONSHIPS.md` — Phase 6: Temporal Relationships (temporal
  relations between observed changes, non-causal).
- `docs/MONETARY_POLICY_STATE.md`, `docs/FOREX_FUNDAMENTALS.md` — Phase 7/8.
- `docs/SOURCES.md` — the verified research matrix of official sources per bank.
- `docs/SEARCH_DISCOVERY.md` — the SearXNG discovery fallback.
- `docs/BANKS.md` — the Bank Toggle.
- `docs/PRESS_CONFERENCES.md`, `docs/REPORTS.md`, `docs/SPEECHES.md` — family contracts.
