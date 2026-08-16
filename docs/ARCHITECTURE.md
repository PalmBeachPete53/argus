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
- `FactChange` (Phase 5) — change_id (deterministic SHA-256 over previous fact
  id + current fact id + change type), previous_fact_id → current_fact_id,
  change_type (numeric/qualitative/text_changed), denormalized provenance of
  **both** source Facts (publication, document, value, period, effective date,
  verbatim source text), delta (numeric only), analysis_version, analyzed_at
- `TemporalRelationship` (Phase 6, legacy class name `PolicyReaction`) —
  reaction_id (deterministic SHA-256 over central_bank + condition_change_id +
  policy_change_id), inferred (constant `True`), earlier side + later side (each
  a denormalized `FactChange` provenance, stored as condition_*/policy_*),
  lag_days, max_lag_days, non-causal formulation, analysis_version
- `MonetaryPolicyState` (Phase 7) — state_id (deterministic SHA-256 over
  central_bank + source_change_id), synthesized (constant `True`), one policy
  dimension (subject, predicate, value_kind, qualifier, period, authoritative
  publication type), the observed level (current side of the source
  `FactChange`, verbatim — never invented or converted), observed_at
  (meeting_date, else publication_date; `effective_date` kept separate),
  denormalized current-side provenance, analysis_version
- `ForexFundamental` (Phase 8) — fundamental_id (deterministic SHA-256 over
  currency + source_kind + source_id), synthesized (constant `True`), one
  fundamental dimension of one economy (currency, resolved from the canonical
  `CentralBank.currency` mapping), one source observation (a Phase 7
  `MonetaryPolicyState` — monetary dimensions — or a Phase 4 `Fact` — macro
  dimensions), the observed level copied verbatim, observed_at, currency-scoped
  `dimension_key` + currency-independent `lineage_key`, denormalized
  provenance, analysis_version
- `ForexDifferential` (Phase 8) — differential_id (deterministic SHA-256 over
  base_currency + quote_currency + subject + predicate + base_source_id +
  quote_source_id), synthesized (constant `True`), an ordered same-dimension
  pair (base/quote convention never silently inverted), the arithmetic
  difference `base_value - quote_value` (same unit/kind, no conversion),
  full denormalized provenance on **both** sides, base-anchored quote
  (latest with `observed_at ≤ base_observed_at`, no look-ahead),
  purely descriptive formulation, analysis_version

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

Classification is **collision-corrected** where a new official document family
shares a title token with an existing one: the SNB "Summary of the monetary
policy assessment discussion" (minutes-like, published since September 2025)
used to classify `monetary_policy_decision` because the SNB decision title rule
was the bare `monetary policy assessment`. The decision rule was narrowed to
`monetary policy assessment of <day> ` and a bank-specific SNB `minutes` rule
(URL `zus_\d{8}`, title `summary of discussion`) was added, so the summary
classifies `minutes` while the `pre_\d{8}` URL rule keeps every real decision
`monetary_policy_decision`. Discovery adds the declared-type `snb_summaries`
source (`types=("minutes",)`) — the Tier‑1 type-hint is the strongest signal —
and the URL/title contradiction tier lets the explicit summary title win over a
`pre_<date>` RSS URL. The Minutes family has **no** SNB extractor, so the
summary is intentionally classified-but-unextracted (document-only); the
`minutes` classification also gates the SNB Decision extractor off it. See
`docs/EXTRACTORS.md` (SNB discussion-summary subsection) and
`tests/test_classification_snb_summaries.py`.

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

## Type-specific extractor families (Phases 4.1–4.7, 4.x)

Each **publication type** is mined by its own extractor family — decisions,
statements, press conferences, minutes/meeting accounts, economic projections,
monetary policy **reports**, and speeches — and extraction is **gated on
classification** (the `classifications` table is the single source of truth),
so the families are disjoint and never cross-mine. Within a family, extraction
is **bank-specific**: one extractor per bank, dispatched generically on
`central_bank` (`get_extractor(bank)`), because bank wording, section labels
and report structures differ materially. Only **structural** helpers (heading
normalization, sentence splitting, the explicit value-claim gate, a
deterministic provenance-carrying fact emitter with within-run deduplication)
are shared between a family's extractors (`src/argus/reports/_shared.py`);
**no bank-specific semantics** live in those helpers. The press-conference
family (`src/argus/press_conferences/`) follows the same split: ECB (Phase 4.3
reference implementation, conserved), Fed and BoE extractors are dispatched
generically on `central_bank`, and only structural mechanics are shared
(`press_conferences/_shared.py`). BoE press conferences are classified via the
declared-type source `boe_mpc_press_conference` (`METHOD_SOURCE_TYPE_HINT`) —
the MPR transcript PDF URL matches no URL/title TypeRule.

A **monetary policy report is a publication type** (`monetary_policy_report`),
not a Fact: the pipeline is `official source → publication → classification →
bank-specific Report extractor → canonical Facts`. Report extractors never
produce downstream semantic Facts (no hawkish/dovish, stance, directional or
forex interpretation) and never mutate source objects. The Report family
currently covers ECB, Norges, BoE, BoC, RBA, RBNZ and Riksbank; Fed, BoJ and
SNB are documented as not-applicable / represented by another family (see
`docs/EXTRACTORS.md`, `docs/REPORTS.md`).

## Phase 5 — Temporal / Cross-Publication Analysis (`changes/`)

`FactChangeAnalyzer` relates consecutive observations of the same lineage
(same central bank, subject, predicate, value kind, canonical period,
qualifier and **authoritative** publication type — from the `classifications`
table, never the denormalized cache). Facts are ordered by their publication
temporal reference (`meeting_date`, else `publication_date`), chained
consecutively (F1→F2→F3, never a fixed baseline), and each adjacent pair is
evaluated independently: identical values or an incomparable pair produce **no
change** and are never bridged over. A `FactChange` is strictly descriptive —
never an economic interpretation. It is derived data: `fact_changes` is
rebuilt idempotently per bank (`rebuild_changes`), empty results clear the
scope, and source `Fact`s are never modified. See `docs/CHANGES.md`.

## Phase 6 — Temporal Relationships (`temporal_relationships/`, legacy module name `reactions/`)

`TemporalRelationshipAnalyzer` (legacy class name `PolicyReactionAnalyzer`)
derives **inferred, non-causal temporal associations** between Phase 5
`FactChange`s: an earlier change (antecedent vocabulary: inflation,
core_inflation, inflation_expectations, gdp, growth, unemployment, wages,
labour_market, financial_conditions, fiscal_policy) temporally followed by a
later change (subsequent vocabulary: policy_rate, main_refinancing_rate,
deposit_facility_rate, marginal_lending_rate, policy_guidance, asset_purchase,
risk, inflation_risk, growth_risk). The observation time of each change is the
temporal reference of its current-side publication (`meeting_date`, else
`publication_date`). The `central_bank` used to group changes is a property of
the `FactChange`, never resolved from the publication (a change without one is
skipped with `unplaced_change`). Pairing is per central bank, requires **no
look-ahead** (`earlier_observed_at ≤ later_observed_at`) and a lag within the
documented window (`DEFAULT_MAX_LAG_DAYS = 180`); every eligible pair yields
exactly one Temporal Relationship. A relationship is never a Fact, never causal,
never a central-bank reaction function, and carries no stance/trading
interpretation. It is derived data: `policy_reactions` is rebuilt idempotently
per bank (`rebuild_temporal_relationships`), empty results clear the scope, and
source `Fact`s / `FactChange`s are never modified. See
`docs/TEMPORAL_RELATIONSHIPS.md`.

## Phase 7 — Monetary Policy State (`states/`)

`MonetaryPolicyStateAnalyzer` synthesizes a **derived, dated** state of the
**observable policy dimensions** of each central bank from Phase 5
`FactChange`s. Each eligible policy change (subject in `STATE_SUBJECTS`, which
is exactly Phase 6's reaction vocabulary; predicate not in
`STATE_EXCLUDED_PREDICATES = {"projection"}`) yields exactly one state entry:
the current side of the change is the newest known level of that dimension,
observed at the temporal reference of the current-side publication
(`meeting_date`, else `publication_date`). The `central_bank` is a property of
the `FactChange`, never resolved from the publication (`unplaced_change`
warning otherwise). The observed value is copied verbatim — policy rates are
never reduced to a single rate, never invented, never converted; guidance /
asset purchase / risk assessments are kept as observed with **no directional
interpretation**. `synthesized` is a constant `True` (state synthesis is
authorized, economic/market interpretation is not). The state at an instant T
is answered by `get_policy_state_as_of(bank, T)` — the latest entry per
dimension with `observed_at ≤ T` (no look-ahead; a dimension with no observed
change is simply absent). It is derived data: `monetary_policy_states` is
rebuilt idempotently per bank (`rebuild_policy_states`), empty results clear
the scope, and source `Fact`s / `FactChange`s are never modified. See
`docs/MONETARY_POLICY_STATE.md`.

## Phase 8 — Forex Fundamentals (`forex/`)

`ForexFundamentalsAnalyzer` synthesizes a **derived, dated, cross-economy**
layer from two existing sources only: Phase 7 `MonetaryPolicyState` entries
(monetary dimensions: `MONETARY_SUBJECTS`, Phase 7's reaction vocabulary) and
Phase 4 `Fact`s (macro dimensions: `MACRO_SUBJECTS`, Phase 6's condition
vocabulary). Each eligible source observation yields exactly one
`ForexFundamental`: one fundamental dimension of one economy, whose currency is
resolved from the canonical `CentralBank.currency` mapping (an economy is a
currency; a bank absent from the mapping is skipped with `unknown_currency`).
The observed level is copied verbatim — rates are never reconstructed from
documents, never reduced to a single rate, never invented, never converted.
The observation time is the temporal reference of the source publication
(`meeting_date`, else `publication_date`); `effective_date` and `period` are
never observation times. Macro facts with `predicate` in
`FUNDAMENTAL_EXCLUDED_PREDICATES = {"projection", "change", "date"}` are out of
scope (an expectation, a delta and a date are not levels).

`ForexDifferential`s compare two fundamentals of **two different economies** on
the **same** currency-independent lineage (subject, predicate, value_kind,
canonical period, qualifier, publication_type), anchored on the base
observation: the quote is the latest observation of that lineage with
`observed_at ≤ base_observed_at` (no look-ahead). The value is the **arithmetic
difference** `base_value − quote_value` in the same unit/kind — no conversion,
no interpretation; text/qualitative/date/boolean/range dimensions are observed
but by nature not differentiable (documented property); a unit mismatch is an
`incomparable_differential` warning; a base observation with no eligible quote
is a `missing_side` warning. Both orientations (A−B and B−A) are generated
with distinct identities and the convention is never silently inverted.
Instruments are never merged (ECB `deposit_facility_rate` vs Fed `policy_rate`
are different lineages → no comparison). `synthesized` is a constant `True`:
a state synthesis and an arithmetic difference are authorized, economic/market
interpretation is not — no hawkish/dovish, no stance, no forecast, no fair
value, no trading signal, no ranking, no causality. The tables
`forex_fundamentals` / `forex_differentials` are derived data, rebuilt
idempotently per currency (rebuilds read the full dataset so differentials are
correct; the scope limits what is persisted), empty results clear the scope,
and sources are never modified. See `docs/FOREX_FUNDAMENTALS.md`.

## Search Discovery fallback (`discovery/search.py`, `search/`)

Native discovery (RSS / HTML / sitemap) is the primary mechanism. **Search
Discovery** is an optional, per-source **fallback** that uses a `SearchProvider`
(SearXNG) to produce official publication candidates when native discovery is
unavailable:

- it is configured per source via `DiscoverySpec.search_query`,
  `search_domain`, `search_engines`, `search_fallback_on_empty`;
- it only yields candidate URLs — it **never** fetches or returns document
  content; the Fetcher remains the only document-ingestion path;
- search-discovered publications keep provenance
  (`discovery_method=search`, provider, query, rank, result URL) and reuse the
  existing deduplication / publication identity;
- SearXNG is optional and never assumed to run locally (environment config:
  `SEARCH_PROVIDER`, `SEARXNG_BASE_URL`, `SEARXNG_ENGINES`).

Configured fallback sources today: RBA (`rba_media_releases_rss`) and RBNZ
(`rbnz_ocr_decisions`). See `docs/SEARCH_DISCOVERY.md`.

## Bank enable/disable toggle (`config.py`)

`BANKS_ENABLED` in `src/argus/config.py` is the single source of truth for which
of the 10 banks participate in operational executions. A bank set to `False`
remains fully defined (adapter, sources, discovery, classification, extractors,
fixtures, golden, unit tests) but is excluded from every integrated execution
path (discovery / fetch / normalize / classify / extract) and from parametrized
E2E scenarios — `--bank` does not bypass the toggle. Environment overrides:
`ARGUS_BANKS_DISABLED` (additional exclusions) and `ARGUS_BANKS_ENABLED`
(complete, authoritative allow-list — the only way to run a default-OFF bank).
`filter_enabled(banks)` applies the toggle uniformly to any selection. RBNZ is
currently OFF. See `docs/BANKS.md`.

## Golden corpus (`tests/golden/`)

Real captured official sources, versioned and replayed offline through the L4
harness (`tests/l4_harness.py`). Each case stores the discovery artifact and the
document with SHA-256 and provenance in `tests/golden/manifest.json`; the
capture tool (`scripts/capture_golden.py`) supports native, search and manual
modes. Coverage is currently **9/10 banks** (Fed, ECB, BoE, BoJ, SNB, BoC, RBA
via Search Discovery, Norges, Riksbank); RBNZ has no real capture yet and stays
at 9/10 — no synthetic golden exists.

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

The bank is enabled by default (unknown banks are treated as enabled by the
toggle); to keep a bank out of integrated executions, set it `False` in
`BANKS_ENABLED` (see "Bank enable/disable toggle" above). If native access is
unreliable, configure a Search Discovery fallback on the relevant source.

No core code changes are required.