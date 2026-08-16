# Data Model — Facts (Phase 4)

This document is the authoritative reference for the **Fact layer** of Argus.
It defines what a Fact is, what it is not, and every design decision behind
`src/argus/facts/`. Future extractors (Phases 4.1–4.7) and analysis layers
(Phases 5–10) are expected to build on this contract.

## Where Facts sit in the pipeline

```
Normalized Document                      ← Phase 2
    ↓
Type-Specific Extractor                  ← Phases 4.1+ (decisions, statements, press conferences)
    ↓
Fact                                     ← Phase 4 (this layer)
    ↓
Temporal / Cross-Publication Analysis    ← Phase 5 (src/argus/changes/, see docs/CHANGES.md)
```

The Fact model is the **canonical interface** between publication-specific
extraction and temporal / cross-publication analysis / policy state / forex
fundamentals. Correctness, provenance, extensibility and explicit semantics
take priority over convenience.

## What a Fact is

A Fact is a **structured representation of information explicitly present in a
source document**, carrying enough context and provenance to independently
verify it against the source.

```
"The Bank raised the policy rate from 4.00% to 4.25%"
→ Fact(subject=policy_rate, predicate=change, value=+25 bps,
       previous_value=4.00, effective_date=…, source_text=…, …)
```

## What a Fact is NOT

- **Not an interpretation** — `"this decision is hawkish"` is not a Fact.
- **Not a prediction/judgement by Argus** — `"the EUR should appreciate"` is
  not a Fact.
- **Not an economic analysis** — no summary, no synthesis, no valuation.
- **Not the document itself** — a `NormalizedDocument` preserves *all* content;
  a Fact is a single structured assertion *derived from* it. One document
  yields many Facts.
- **Not the publication** — a `Publication` is a discovered/fetched record; a
  Fact belongs to one of its normalized documents.
- **Not a model interpretation** — `"Argus considers the central bank
  hawkish"` belongs to a later analytical layer, never here.

Interpretation belongs to later phases (5+). The Fact layer stays as close as
reasonably possible to the source material.

## Relation to existing models

| Concept | Where | Role |
|---|---|---|
| `CentralBank` | `models.py` / adapters | root of provenance |
| `Source` | `models.py` / adapters | where publications come from |
| `Publication` | `models.py` | discovered publication, `publication_id` |
| `Document` | `models.py` | raw bytes on disk, `document_id` (SHA-256) |
| `NormalizedDocument` | `documents/base.py` | structured content, sections/tables/pages |
| `PublicationClassification` | `classification/base.py` | type of the *publication* |
| `Fact` | `facts/base.py` | **this layer** — assertion inside a document |

Provenance is **reused, not duplicated**: a Fact carries `publication_id` and
`document_id`, and the existing tables provide the full chain
`Fact → NormalizedDocument → Document → Publication → Source → CentralBank`.
No parallel provenance system exists.

A `Fact` is therefore **not** a generalisation of `PublicationClassification`:
the classification answers "what *kind* of publication is this?", while a Fact
answers "what *specific thing* does this document say?".

## Anatomy of a Fact

```
Fact
├── fact_id              deterministic SHA-256 (identity, below)
├── publication_id       provenance → Publication
├── document_id          provenance → NormalizedDocument (SHA-256)
├── central_bank         denormalized convenience for filtering (filled from the
│                        publication when missing)
├── subject              canonical semantic subject, e.g. "policy_rate"
├── predicate            canonical semantic predicate, e.g. "value"
├── value                FactValue | None (the assertion's value)
├── previous_value       FactValue | None (explicit "was X" claim, if in source)
├── change               FactValue | None (explicit delta claim, if in source)
├── period               FactPeriod | None (reference/forecast period)
├── effective_date       datetime | None (when the value/decision applies)
├── source_location      FactLocation | None
├── source_text          verbatim supporting passage
├── extraction_method    how the Fact was produced (vocabulary below)
├── extraction_version   extractor version (auditability of corrections)
├── confidence           Confidence (HIGH/MEDIUM/LOW) — extraction confidence
├── identity_qualifier   optional discriminator for the identity (rare)
├── speaker              str | None — verbatim official attribution (rare)
├── extracted_at         when the Fact was produced
```

### Field semantics

- `subject` / `predicate` form the Fact's semantic identity. Together they
  answer "who/what, doing what". Examples:
  - `subject=policy_rate, predicate=value`
  - `subject=inflation, predicate=projection`
  - `subject=inflation_risk, predicate=assessment`
- `value` holds the structured assertion, never an opaque string.
- `previous_value` / `change` are **only** set when the source itself states
  the previous level or the delta ("raised from 4.00% to 4.25%", "by 25 basis
  points"). Temporal analysis can *also* derive changes later; storing the
  explicit claim preserves the source statement as made.
- `source_text` preserves the exact wording of the supporting passage. For
  categorical/numeric values the verbatim wording is also kept inside the
  `FactValue.source_text`, so the evidence is never reduced to a code.
- `speaker` preserves the verbatim official attribution of an individual
  statement when the source itself labels it (e.g. a press-conference answer by
  "President Christine Lagarde"). It is **never inferred**: an unlabelled
  statement keeps `speaker = None`, and collective communications (e.g.
  Governing Council remarks) are always `None` — an individual is never
  credited for a collective stance. `speaker` is deliberately **not** part of
  `fact_id` (it is provenance, not identity): correcting or adding an
  attribution updates the row in place; two facts that differ only in who said
  the same thing remain one fact.

## Value types (`ValueKind`)

Values are typed, not free-form strings:

| kind | machine value | meaning |
|---|---|---|
| `number` | float | plain numeric, optional `unit` (e.g. `trn`) |
| `percentage` | float | e.g. `4.25` → 4.25% |
| `basis_points` | float | sign preserved, e.g. `+25`, `-25` |
| `currency` | float + `unit` | e.g. `unit="usd"` / `"billion"` |
| `date` | ISO-8601 str | e.g. `"2026-08-14"` |
| `boolean` | bool | yes/no flags |
| `categorical` | canonical str | e.g. `"upside"`, `"moderate"`, `"restrictive"` |
| `text` | str | quoted passage (statements, forward guidance) |
| `range` | `min`/`max` | numeric interval, optional `unit` |
| `null` | None | explicitly unavailable / not disclosed |

`value_type` is denormalized into its own column for filtering ("all percentage
facts"). `source_text` on the value preserves the exact source wording next to
the normalized value, satisfying "keep the source wording alongside the
normalized value".

This is deliberately **not** a universal unit system — it is practical for
central-bank communications.

### Categorical vs. textual statements

- A **categorical** Fact is a normalized category backed by verbatim wording
  (`inflation_risk = "upside"` + `source_text=…`). This is a SOURCE statement,
  not an Argus interpretation.
- A **text** Fact keeps the wording when no canonical category applies yet
  (`forward_guidance = "the policy rate may need to remain restrictive"`).

## Time semantics

A Fact has **multiple distinct temporal dimensions** which are never collapsed
into one "date" field:

| dimension | field | example |
|---|---|---|
| publication date | `Publication.publication_date` (via `publication_id`) | 2026-08-13 |
| meeting date | `Publication.meeting_date` (via `publication_id`) | 2026-08-12 |
| effective date | `Fact.effective_date` | 2026-08-14 |
| reference/forecast period | `Fact.period` | 2027, Q4 2027, 2028 |

`FactPeriod` uses a canonical, sortable form (zero-padding makes lexical order
equal chronological order): `year` `"2027"`, `quarter` `"2027-Q4"`,
`month` `"2027-08"`, `semester` `"2027-H1"`, `range` `"2027-2028"`. The
verbatim source label (e.g. "in 2027", "2027–2028") is kept in `FactPeriod.label`.
This makes later "compare across meetings/publications" queries
straightforward.

## Changes: previous / current / change

Two consistent choices are offered, matching how the source phrases it:

- value in percentage points: `value=4.25, previous_value=4.00, change=+0.25`
  (all `percentage`)
- or in basis points: `value=4.25 (percentage), change=+25 (basis_points)`

The extractor chooses per source; the model stores what the source actually
said and never invents the missing side. If only the current level is stated,
`previous_value` and `change` are left `None` — temporal analysis can later
compute the delta from the previous publication's fact.

## Provenance

A Fact must be traceable end-to-end:

```
Fact
 ↓ source_text + source_location + extraction_method + extraction_version
NormalizedDocument
 ↓ document_id (SHA-256)
Document
 ↓ publication_id
Publication
 ↓ Source
CentralBank
```

The existing identifiers are reused. In addition to `publication_id` and
`document_id`, `source_location` pinpoints the exact place inside the document.

### `FactLocation` — format-independent provenance

| kind | meaning | formats |
|---|---|---|
| `section` | `sections[position]` (heading + following text) | HTML, DOCX, TXT |
| `table` | `tables[position]`, optional `row`/`column` | XLSX, CSV, HTML, PDF |
| `page` | `pages[page]` | PDF, DOCX |
| `offset` | `char_start`…`char_end` in the normalized text | any |

Page numbers are not assumed (HTML has none); the same model works for HTML,
PDF, DOCX, XLSX, CSV and TXT.

## Extraction method & confidence

`extraction_method` records how the Fact was produced:

`rule`, `parser`, `table_extraction`, `regex`, `structured_metadata`, `manual`,
`unknown` — and `llm`, **reserved for the future and NOT used in Phase 4**.

`extraction_version` (extractor version) identifies the version that **produced
the current canonical value** stored in `facts`. Because the `facts` table is
upserted by `fact_id`, it holds the *current* state of each fact: when an
extraction is corrected, the row is updated and the old version is **not**
retained. `extraction_version` therefore answers "which version produced the
data currently stored", **not** "which versions have historically produced this
data". A historical extraction audit log (if ever needed) would be a separate
system, deliberately out of scope for the `facts` table.

`confidence` is **extraction/structuring confidence** (HIGH/MEDIUM/LOW) — how
confident the parser is that it faithfully structured the source. It is
**never** "confidence that the economic interpretation is correct"; that
concept does not exist at this layer.

## Fact identity & deduplication

`fact_id` = `SHA-256` over stable **semantic + provenance** fields:

```
publication_id | document_id | subject | predicate | period | effective_date | identity_qualifier
```

The extracted `value`, `previous_value` and `change` are deliberately **not**
part of the identity:

- Re-running an extractor produces the same `fact_id` → the row is updated,
  never duplicated (idempotent persistence by construction).
- A corrected extraction (same subject, new value) updates the same row; the
  updated `extraction_version` + `confidence` show it was re-extracted.
- Two facts that only differ in *where* they were found collapse to one, which
  is correct: the same assertion stated twice is one Fact.

`effective_date` **is** part of the identity, by design. Rationale: it is a
stable, semantic attribute — not an extracted value. Two facts in the same
document that differ only by their effective date (e.g. a policy rate stated
for two different dates, two changes effective on different days) are genuinely
distinct facts and must not collide. Value corrections keep the "update in
place" property because `value` remains excluded. The one trade-off: correcting
an `effective_date` changes the identity and opens a new slot — stale rows are
cleared by the full re-extraction path (`rebuild_facts_for_document` /
`delete_facts_for_document`).

`identity_qualifier` is an extractor-provided discriminator for the rare case
where two distinct Facts would otherwise share `subject + predicate + period +
effective_date` in one document (e.g. a target range and a midpoint of the same
value).

Fuzzy/approximate deduplication is intentionally **not** designed now — the
immediate requirement is deterministic, idempotent persistence.

## Persistence

Facts live in the existing SQLite store (`store.py`) in the `facts` table — no
separate database, no migration framework (the project uses `CREATE TABLE IF
NOT EXISTS` schema initialization, followed for consistency):

```
facts
├── fact_id            PRIMARY KEY (deterministic)
├── publication_id     NOT NULL
├── document_id        NOT NULL
├── central_bank
├── subject / predicate
├── value_type + value_json (+ previous_value_json, change_json)
├── period_kind / period_value / period_label
├── effective_date
├── source_location_json
├── source_text
├── extraction_method / extraction_version
├── confidence
├── extracted_at
└── created_at / updated_at
```

Store operations:

| method | purpose |
|---|---|
| `save_fact(fact)` | upsert by `fact_id`, idempotent; fills `central_bank` from the publication if absent |
| `save_facts(facts | ExtractionResult)` | bulk persist, returns count |
| `get_fact(fact_id)` | single lookup |
| `get_facts(publication_id=…, document_id=…, bank=…, subject=…, predicate=…, value_type=…, limit=…)` | filtering for analysis queries |
| `delete_facts_for_document(id)` / `delete_facts_for_publication(id)` | full cleanup |
| `rebuild_facts_for_document(id, facts)` | replace a document's facts in one transaction (delete + insert), for re-extraction — stale facts never survive a rebuild |

## Extractor contract: `ExtractionResult`

Future type-specific extractors (Phase 4.1+) return one object per
`(publication, document)`:

```
ExtractionResult(
    publication_id=…,
    document_id=…,
    facts=[Fact, …],
    warnings=[…],
)
```

- An extractor produces Facts for **exactly one** `document_id`.
- `warnings` records skipped or degraded extractions (e.g. a projection table
  that could not be parsed), making quality visible rather than silent.
- The container carries no business logic.

## Subject / predicate vocabulary

`subject` and `predicate` are canonical strings. The initial curated set
follows the roadmap's conceptual subjects (`policy_rate`, `inflation`, `gdp`,
`inflation_risk`, `growth_outlook`, `forward_guidance`, …) with predicates
`value`, `change`, `projection`, `assessment`, `decision`, `statement`.

This is a **controlled vocabulary strategy**: new concepts are added by
extending the canonical strings (and documenting them here/next to the
extractor), without modifying core infrastructure. Semantic identity and
querying never depend on arbitrary free-form strings.

## Example facts

### Quantitative (percentage)

```
Fact:
  subject: policy_rate
  predicate: value
  value: 4.25 (percentage, source_text="4.25 percent")
  effective_date: 2026-08-14
  publication_id: <pub>
  document_id: <sha256>
  source_text: "the target for the policy rate is 4.25 percent"
  source_location: section=2
  extraction_method: rule
  extraction_version: 1.0.0
  confidence: high
```

### Projection

```
Fact:
  subject: gdp
  predicate: projection
  value: 1.4 (percentage, source_text="1.4 percent")
  period: year:2028 (label="in 2028")
  effective_date: 2026-09-16
  source_location: table=1, row=4, column=2
  extraction_method: table_extraction
  confidence: high
```

### Qualitative / categorical

```
Fact:
  subject: inflation_risk
  predicate: assessment
  value: upside (categorical, source_text="risks to inflation are tilted to the upside")
  source_text: "The risks to inflation remain tilted to the upside."
  source_location: section=5
  extraction_method: rule
  confidence: medium
```

### Change (basis points)

```
Fact:
  subject: policy_rate
  predicate: change
  value: +25 (basis_points, source_text="by 25 basis points")
  previous_value: 4.00 (percentage)
  effective_date: 2026-08-14
  extraction_method: rule
  confidence: high
```

## Architectural decisions (record)

- **A. Fact identity**: SHA-256 over stable semantic + provenance fields
  (subject, predicate, period, effective_date); extracted value intentionally
  excluded so value corrections update in place. `effective_date` is included
  because facts differing only by their effective date are distinct facts.
- **B. Fact vs Interpretation**: a Fact is explicitly in the source;
  interpretation is reserved for later analysis phases.
- **C. Fact vs Document**: document = full content (content-preserving); Fact =
  one structured assertion with provenance into that content.
- **D. Fact vs Provenance**: provenance chain lives in the existing tables
  (`publication_id`, `document_id`); Facts reuse rather than duplicate it.
- **E. Fact vs Publication**: one Publication may have many documents and many
  Facts; the Publication record itself is never interpreted.
- **F. Values**: typed `FactValue` kinds + verbatim `source_text`, not untyped
  strings.
- **G. Forecast periods**: `FactPeriod` with canonical sortable forms, distinct
  from dates.
- **H. Changes**: explicit `previous_value` + `change` when the source states
  them; derived deltas belong to later analysis.
- **I. Idempotence**: deterministic `fact_id` + upsert + `rebuild_facts_for_document`.
- **J. Extractors**: contract is `ExtractionResult(publication_id,
  document_id, facts, warnings)`.
- **K. Analysis queries**: `Store.get_facts(...)` with filters on bank, subject,
  predicate, value_type, period columns.