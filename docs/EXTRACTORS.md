# Type-Specific Extractors (Phase 5)

This document is the reference for the first type-specific extractor: the
**ECB Monetary Policy Decision extractor**. It builds on the Phase 4 contract
(`docs/DATA_MODEL.md`, `src/argus/facts/`).

## Pipeline

```
NormalizedDocument                      ← Phase 2
    ↓  DecisionExtractor.extract(publication, document)
ExtractionResult(publication_id, document_id, facts, warnings)
    ↓  Store.rebuild_facts_for_document (delete + insert, idempotent)
facts table
```

- a classified **ECB monetary policy decision** publication
- its normalized document(s)
- the ECB extractor producing structured **Facts**
- an `ExtractionResult` handed to the existing `Store`

No aggregation, no temporal analysis, no interpretation (Phases 12+).

## Extractor contract

`src/argus/decisions/base.py`:

```python
class DecisionExtractor(ABC):
    bank: str
    extraction_version: str
    def extract(self, publication, document) -> ExtractionResult: ...
```

- One extractor per bank (bank-specific wording stays encapsulated, invariant
  10); the generic code only dispatches on `central_bank`.
- Extractors are **pure**: they read the normalized document and return an
  `ExtractionResult`; persistence is a caller concern.
- `extract_decision(store, publication, *, document=None)` runs the right
  extractor and persists facts through
  `Store.rebuild_facts_for_document` (delete + insert), keeping re-runs
  idempotent and stale facts impossible.
- Extraction is **gated on classification**: a publication classified as
  anything other than `monetary_policy_decision` (authoritatively in the
  `classifications` table; the denormalized cache is only a fallback) is never
  mined for decision facts.

## ECB extractor

`src/argus/decisions/ecb.py` — `EcbDecisionExtractor` (`extraction_version
5.2.0`). It answers *"what did the Governing Council explicitly decide or
announce as part of the decision?"*.

### Supported facts

| subject | predicate | value | source |
|---|---|---|---|
| `monetary_policy_decision` | `date` | ISO date (e.g. `2026-07-23`) | leading date paragraph |
| `main_refinancing_rate` | `value` | percentage | the three-rate enumeration or a per-instrument sentence |
| `marginal_lending_rate` | `value` | percentage | same |
| `deposit_facility_rate` | `value` | percentage | same |
| `{rate}` | `change` | basis_points (sign preserved) | explicit "lower(ed) … by 25 basis points" |
| `monetary_policy_decision` | `statement` | text (verbatim) | decision wording: every "… decided (today) to …" sentence |
| `asset_purchase` | `decision` | text (verbatim) | APP / PEPP / TLTRO sections (reinvestment, cessation, continuation, maturity handling) |
| `policy_guidance` | `statement` | text (verbatim) | explicit prospective policy statements in the decision body |

Every Fact also carries:

- `effective_date` — parsed from "with effect from …" when explicitly stated
  (never assumed);
- `source_location` — section index inside the normalized document;
- `source_text` — the verbatim supporting passage / matched value wording;
- value-level `source_text` — verbatim wording next to the normalized value;
- `extraction_method = regex`, `extraction_version = 5.2.0`, `confidence`.

#### Decision wording

The "The Governing Council … decided to / decided that …" sentences of the
decision section are kept **verbatim** as `monetary_policy_decision /
statement` facts. They are source information only: they are **never** recast as
hawkish/dovish, tightening/easing, bullish/bearish, or any other interpreted
stance. Interpretation belongs to later phases.

#### Asset purchases & balance-sheet decisions

The APP, PEPP (and, when present, TLTRO) sections state explicit programme
decisions — reinvestment, cessation of reinvestment, continuation, maturity
handling. Each such sentence becomes an `asset_purchase / decision` fact:

- programme identity is preserved in `identity_qualifier` (e.g. `app:0`,
  `pepp:0`, `tltro:0`);
- a relevant period stated by the source (e.g. "during the first half of
  2027") is preserved as `Fact.period` (`semester:2027-H1`) with the verbatim
  label;
- the decision itself is preserved verbatim as both `value` and `source_text`.

Absence of a programme statement never becomes an invented "no change"/"no
action" fact.

#### Forward guidance

Explicit prospective **policy** statements that are part of the decision are
kept verbatim as `policy_guidance / statement` facts (e.g. "will keep the key
ECB interest rates … for as long as necessary", "stands ready to adjust all of
its instruments within its mandate"). Narrow anchors only — they never capture
macro-economic analysis. Guidance is **not** classified and **not** turned into
an interpreted policy stance.

### Supported wording

- Enumeration, e.g. *"… will be decreased to 2.00%, 2.25% and 1.75%
  respectively"* (instrument order is read from the sentence; the canonical ECB
  order main-refinancing → marginal lending → deposit is the fallback).
- Per-instrument sentence, e.g. *"… lower the deposit facility rate to 1.75 per
  cent."* — used to fill any rate missing from the enumeration.
- Hold wording, e.g. *"… remain at 2.00%, 2.25% and 1.75% respectively"* —
  levels are extracted; **no** `change` fact is produced (a delta is never
  invented).
- Direction: `lower/decrease/reduce/cut/…` ⇒ negative basis points,
  `increase/raise/hike/…` ⇒ positive.

### Not covered — ECB decision limitations

- **Votes.** ECB Monetary Policy Decisions do not report individual votes (no
  unanimity/dissent counts on the decision page). Vote extraction is therefore
  **unsupported for ECB Decision documents** and a vote fact is never
  fabricated. Votes/dissents belong to Minutes / Meeting Accounts (Phase 8).
- **Risk assessment.** The decision document carries no risk assessment; risk
  language on the ECB website belongs to the separate Monetary Policy
  Statement, the press conference and the Economic Bulletin. Risk assessment is
  therefore **not extracted from ECB decisions** — it is Phase 6 territory
  (see boundary below).
- **Inflation/growth/employment analysis.** The macro-economic justification is
  **deferred to Phase 6** even when it appears on the same page.
- hawkish/dovish assessment, forex interpretation, temporal/cross-publication
  analysis — later phases;
- LLM extraction — prohibited by invariant 8.

### Phase 5 / Phase 6 boundary

Phase 5 answers *"what did the central bank explicitly decide or announce as
part of the decision?"*. Content belonging to the separate **Monetary Policy
Statement** (macro-economic justification, inflation/growth/employment
analysis, full risk framework, formulation-change analysis, temporal
comparison) is Phase 6 and is **not** extracted. Concretely:

- content under a heading normalized to `monetary policy statement` is **not**
  mined for Phase 5 facts: the same sentence that is a source of forward
  guidance inside the decision body is **not** extracted when it sits in the
  statement section (regression-tested);

## Canonical subject / predicate vocabulary (extensions)

Controlled-vocabulary additions made next to the Phase 4 core set (documented
here and in `src/argus/decisions/ecb.py`):

- subjects: `monetary_policy_decision`, `main_refinancing_rate`,
  `marginal_lending_rate`, `deposit_facility_rate`, `asset_purchase`,
  `policy_guidance`
- predicates added: `date`, `statement`, `decision` (alongside the existing
  `value` / `change`).

`identity_qualifier` disambiguates the rare multi-fact cases in one document:
programme (`app`/`pepp`/`tltro`) + ordinal for asset-purchase facts, sentence
ordinal for decision-wording and forward-guidance facts.

The identity (`fact_id`) follows Phase 4: SHA-256 over publication_id,
document_id, subject, predicate, period and effective_date (plus
identity_qualifier). Values are excluded, so corrected extractions update rows
in place. Two facts differing only by their effective date or programme remain
distinct facts.

## Golden tests

`tests/fixtures/documents/ecb_decision*.html` (modeled on the real ECB page:
date paragraph, decision section, "Key ECB interest rates" section with the
enumeration + "with effect from …", APP/PEPP/TLTRO sections, forward guidance,
decoy dates, and a "Monetary policy statement" section used to prove the Phase 6
boundary). `tests/test_decisions.py` runs the normalizer → extractor → store
slice and asserts, per fixture:

- the exact expected facts (date, levels, changes, decision wording, asset-
  purchase decisions, forward guidance) with values, effective dates and
  warnings;
- no invented facts — no fabricated `vote`, no `risk_assessment`, nothing for
  absent optional categories;
- verbatim provenance: each `fact.source_text` and `fact.value.source_text` is
  a substring of the referenced section;
- deterministic extraction and idempotent Store persistence
  (`rebuild_facts_for_document` re-run is a no-op);
- phase-6 boundary: guidance inside the statement section is not extracted.

---

# Type-Specific Extractors — Phase 6 (Monetary Policy Statement)

This section is the reference for the **ECB Monetary Policy Statement
extractor**, built on the same Phase 4 contract and following the Phase 5
pattern (`src/argus/statements/`).

## Pipeline

```
NormalizedDocument                       ← Phase 2
    ↓  StatementExtractor.extract(publication, document)
ExtractionResult(publication_id, document_id, facts, warnings)
    ↓  Store.rebuild_facts_for_document (delete + insert, idempotent)
facts table
```

- a classified **ECB monetary policy statement** publication
- its normalized document(s)
- the ECB statement extractor producing structured **Facts**
- an `ExtractionResult` handed to the existing `Store`

## Extractor contract

`src/argus/statements/base.py`:

```python
class StatementExtractor(ABC):
    bank: str
    extraction_version: str
    def extract(self, publication, document) -> ExtractionResult: ...
```

- One extractor per bank; the generic code only dispatches on `central_bank`.
- Extractors are **pure**: they read the normalized document and return an
  `ExtractionResult`; persistence is a caller concern.
- `extract_statement(store, publication, *, document=None)` runs the right
  extractor and persists facts through `Store.rebuild_facts_for_document`,
  keeping re-runs idempotent.
- Extraction is **gated on classification**: a publication classified as
  anything other than `monetary_policy_statement` (authoritatively in the
  `classifications` table) is never mined for statement facts.

## ECB statement extractor

`src/argus/statements/ecb.py` — `EcbMonetaryPolicyStatementExtractor`
(`extraction_version 6.0.0`). It answers *"what does the Governing Council
explicitly state about the economy and its policy stance in the statement?"*.

### Supported facts

| subject | predicate | value | source |
|---|---|---|---|
| `monetary_policy` | `rationale` | text (verbatim) | justification sentences ("are based on", "in order to", "to ensure that", "consistent with", …) |
| `policy_guidance` | `statement` | text (verbatim) | forward guidance ("stands ready to adjust", "for as long as necessary", "will be guided by", …) |
| `inflation` | `value` / `assessment` | percentage (+ period) / text | inflation section |
| `core_inflation` | `value` / `assessment` | percentage (+ period) / text | same |
| `inflation_expectations` | `assessment` | text (verbatim) | same |
| `growth` | `assessment` | text (verbatim) | growth / activity section |
| `gdp` | `value` | percentage (+ period) | quantitative growth claims ("projected to grow by 1.4% in 2027") |
| `labour_market` | `assessment` | text (verbatim) | labour market section |
| `unemployment` | `value` | percentage (+ period) | explicit unemployment claims |
| `wages` | `value` | percentage (+ period) | explicit wage-growth claims |
| `financial_conditions` | `assessment` | text (verbatim) | financial / financing conditions section |
| `risk` | `assessment` | categorical or text | risk assessment section |
| `inflation_risk` | `assessment` | categorical (upside/downside/balanced) or text | same |
| `growth_risk` | `assessment` | categorical (upside/downside/balanced) or text | same |

Every Fact also carries:

- `source_location` — section index inside the normalized document;
- `source_text` — the verbatim supporting passage / matched value wording;
- value-level `source_text` — verbatim wording next to the normalized value;
- `extraction_method = regex`, `extraction_version = 6.0.0`, `confidence`;
- `effective_date = None` (statement facts carry no effective date);
- `identity_qualifier` — per-subject ordinal (`inflation:0`, `guidance:0`, …)
  that keeps `fact_id`s unique for multi-value / multi-sentence subjects.

### Routing

Content is routed deterministically by **section heading** (risk → inflation →
growth → labour market → financial conditions → forward guidance). A narrow
content-first fallback (guidance > risk > rationale) applies only to sections
whose heading carries no signal (intro, closing remarks, heading-less text), so
cross-category phrasing inside a mapped section is never double-counted.

### Risk assessment

A risk sentence yields a **categorical** orientation fact when an explicit
orientation word is present — `upside`, `downside`, or `balanced`
(`two-sided` / `symmetric` are normalized to `balanced`) — with the sentence
kept verbatim as `source_text`. A risk sentence with no orientation ("… subject
to heightened uncertainty") becomes a verbatim text assessment instead. The
risk target is read from the wording (`inflation` → `inflation_risk`,
`growth`/`activity`/`gdp` → `growth_risk`, otherwise `risk`). Orientations are
**never inferred** from absence: a statement with no risk section emits a
`no_risk_assessment` warning and no risk fact.

### Quantitative values

Values are extracted only from sentences with an explicit value claim
("projected / expected … to average / stand at / be …", "stood at …", "declined
to …"), so "the 2% target" or "converging towards 2%" is never read as a value.
A percentage immediately followed by a reference period keeps it as
`FactPeriod` (year, or month when a month is named, e.g. "6.4% in June 2026" →
`month:2026-06`); a bare percentage is kept without a period.

### Confidence

- `HIGH` — quantitative percentages and categorical risk orientations (explicit
  source wording);
- `MEDIUM` — verbatim qualitative assessments (sentence-level category
  identification).

### Not covered — Phase 6 boundaries

- **The decision itself** (wording, rates, changes, effective date) stays Phase
  5, gated on decision publications; the decision sentences inside a statement
  ("… decided to lower the three key ECB interest rates by 25 basis points") are
  never mined.
- **Formulation-change analysis** (old wording vs new wording) is Phase 12;
  Phase 6 only preserves the current wording verbatim so Phase 12 can diff it.
- **Votes, hawkish/dovish or stance interpretation, forex fundamentals** —
  none of these is ever produced here.
- An absent optional section (no risk assessment, no forward guidance) never
  becomes an invented "balanced" / "no guidance" fact; it is surfaced as a
  warning (`no_risk_assessment`, `no_forward_guidance`).

### Phase 5 / Phase 6 boundary (statement side)

Phase 6 reuses the `policy_guidance` subject introduced in Phase 5 for
statement-level forward guidance: the content type is the same, only the
publication type differs (gating keeps the two extractors disjoint). The
decision extractor never mines the statement's own section (heading normalized
to `monetary policy statement`), regression-tested.

## Golden tests

`tests/fixtures/documents/ecb_statement*.html` (modeled on the ECB statement
layout: date paragraph, "Monetary policy statement" intro, economic activity,
inflation, labour market, financial conditions, risk assessment, forward
guidance). `tests/test_statements.py` runs the normalizer → extractor → store
slice and asserts, per fixture:

- the exact expected facts (rationale, guidance, assessments, quantitative
  values with their reference periods, risk orientations) with warnings;
- no invented facts — no Phase 5 decision subjects, no vote, no
  hawkish/dovish label, nothing for absent optional categories;
- verbatim provenance: each `fact.source_text` and `fact.value.source_text` is
  a substring of the referenced section;
- deterministic extraction and idempotent Store persistence;
- classification gating (`extract_statement` skips non-statement
  publications) and Phase 5/6 coexistence.