# Data Model — Forex Fundamentals (Phase 8)

This document is the authoritative reference for the **forex fundamentals
layer** of Argus (`src/argus/forex/`). It defines what a `ForexFundamental`
and a `ForexDifferential` are, what they are **not**, and every design decision
behind the analyzer, the identity scheme, the comparability rules, the temporal
semantics and the persistence.

It was written **before** the implementation (Phase 6 and 14 are frozen;
Phase 9+ are not started). Anything in this document is normative; any
divergence found during implementation must be resolved here first.

## Where Forex Fundamentals sit in the pipeline

```
Type-Specific Extractor                 ← Phases 4.1–4.7
    ↓
Fact                                    ← Phase 4 (src/argus/facts/)
    ↓
Temporal / Cross-Publication Analysis   ← Phase 5 (src/argus/changes/)
    ↓
Policy Reaction Function                ← Phase 6 (src/argus/reactions/)  [FROZEN]
    ↓
Monetary Policy State                   ← Phase 7 (src/argus/states/)     [FROZEN]
    ↓
Forex Fundamentals                      ← Phase 8 (src/argus/forex/, THIS layer)
    ↓
Historical Validation / Trading Layer   ← Phases 9–10 (NOT STARTED)
```

Phase 8 is the **first cross-economy layer**. It transforms the already
structured monetary states (Phase 7) and the macro observations (Phase 4
`Fact`s) of each economy into a layer of **structured, comparable, traceable
forex fundamentals**, and it is the first place where a **descriptive
comparison between two central banks / economies is allowed**.

The central question Phase 8 answers:

> **Quels sont les fondamentaux macroéconomiques observables d'une devise, et
> comment leur état peut-il être comparé à celui d'une autre devise ?**

It is **not** a trading system, **not** a buy/sell signal, **not** a forecast,
**not** a valuation/fair-value model, **not** a conviction score, **not** a
positioning engine.

## Epistemic boundary (the core principle)

```
SOURCE                      observed     (official publication / document)
→ OBSERVED FACT             observed     (Phase 4)
→ OBSERVED CHANGE           observed     (Phase 5)
→ TEMPORAL RELATIONSHIP     INFERRED     (Phase 6)
→ MONETARY POLICY STATE     SYNTHESIZED  (Phase 7)
→ FOREX FUNDAMENTAL         SYNTHESIZED  (Phase 8 — THIS layer)
→ FOREX DIFFERENTIAL        SYNTHESIZED  (Phase 8 — THIS layer)
```

- `Fact` / `FactChange` are **observed**.
- `PolicyReaction` is **inferred** (`inferred=True` constant).
- `MonetaryPolicyState` is **synthesized** (`synthesized=True` constant).
- A `ForexFundamental` and a `ForexDifferential` are **synthesized**:
  `synthesized` is always `True`. They are derived, dated summaries of
  observable dimensions and their arithmetic differences. A **state synthesis
  and an arithmetic difference are authorized**; an **economic/market
  interpretation is not**.

Every `ForexFundamental` / `ForexDifferential` carries `synthesized=True` (a
constant, never `False`) and a purely descriptive `formulation`.

## Sources — what feeds this layer

Phase 8 consumes **two** existing sources and nothing else:

1. **Monetary dimensions** come from **`MonetaryPolicyState`** (Phase 7) —
   the observable monetary policy state. The policy rates are **never
   reconstructed from documents**, and Phase 7's logic is **never
   duplicated**.
2. **Macro dimensions** come from **`Fact`** (Phase 4) — the canonical
   observed macro data (latest-known-observation model).

**Never** used as a value source:

- `PolicyReaction` (Phase 6) — an inferred temporal relation, never a
  fundamental value.
- `FactChange` (Phase 5) deltas — a change is a variation, not a level; the
  state layer already consumes them.
- Raw documents / network / LLM / fuzzy / semantic logic.

## Central bank → currency / economy

The canonical bank→currency relation already exists and is **reused**:
`CentralBank.currency` (defined in `src/argus/adapters/`), exposed by
`SourceRegistry`. A bank maps to exactly one ISO currency code, and in Phase 8
the **currency is the economy identifier** (e.g. `EUR` = Euro area). The store
entry point builds the mapping from `SourceRegistry`; the pure analyzer takes
it as a parameter (never hardcoded, never duplicated).

A bank is **not** assumed to correspond to a single dimension or a single
rate: an economy may observe several instruments (e.g. ECB
`main_refinancing_rate`, `deposit_facility_rate`, `marginal_lending_rate`),
each preserved as its own dimension.

## What a ForexFundamental is

A `ForexFundamental` is a **derived, dated observation of ONE fundamental
dimension of ONE economy (currency)**, established by ONE source observation:

```
source observation (a MonetaryPolicyState entry  OR  a macro Fact)
        ↓
ForexFundamental
   currency     = ISO code of the economy (from central_bank → currency)
   dimension    = subject, predicate, value_kind, canonical period, qualifier,
                  publication_type   (currency-independent)
   value        = the observed level, copied verbatim (never invented)
   observed_at  = temporal reference of the source publication
                  (meeting_date, else publication_date)
   provenance   = source_kind ("monetary_state" | "fact"), source_id
                  (state_id | fact_id), plus the denormalized current side
                  (publication, document, effective date, verbatim source text)
```

Each eligible source observation produces **exactly one** `ForexFundamental`.
The history of an economy+dimension is the ordered list of its fundamentals;
the fundamental at `T` is the latest one with `observed_at ≤ T`.

### Dimensions

```
MONETARY (from MonetaryPolicyState — Phase 7 STATE_SUBJECTS):
    policy_rate, main_refinancing_rate, deposit_facility_rate,
    marginal_lending_rate, policy_guidance, asset_purchase,
    risk, inflation_risk, growth_risk

MACRO (from Facts — Phase 6 CONDITION_SUBJECTS):
    inflation, core_inflation, inflation_expectations, gdp, growth,
    unemployment, wages, labour_market, financial_conditions, fiscal_policy
```

`FUNDAMENTAL_SUBJECTS = MACRO_SUBJECTS ∪ MONETARY_SUBJECTS`, both re-exported
from the canonical Phase 6/7 vocabularies so the layers never drift.

**Excluded observation kinds** (macro facts): predicate in
`FUNDAMENTAL_EXCLUDED_PREDICATES = {"projection", "change", "date"}`.

- `projection` — an expectation of a future value, not the current observed
  level (same rationale as Phase 7's `STATE_EXCLUDED_PREDICATES`).
- `change` — a delta (variation), not an absolute level.
- `date` — a meta observation, not a level.

**Documented gaps (not structured by current data, never invented):**
`consumption`, `investment`, `trade`, `current_account`, `productivity`,
`labour_risk`, `fiscal_stance`, `yield_curves`, `market_pricing`,
`expectations` beyond `inflation_expectations`. Phase 8 records these as
gaps; it never approximates them.

## What a ForexDifferential is

A `ForexDifferential` is a **derived, dated arithmetic comparison of two
fundamentals of two different economies, on an explicitly declared dimension**:

```
base_observation (currency B, dimension D)   ← latest known at the anchor
        ↓  differential = base_value − quote_value
quote_observation (currency Q, dimension D)  ← latest known ≤ base.observed_at
```

- The pair is **ordered** (`base_currency`, `quote_currency`); the convention
  is stable and **never silently inverted**: `EUR/USD` ⇒ base `EUR`, quote
  `USD`, differential = EUR value − USD value; `USD/EUR` is a different object
  with a different identity.
- **Both sides declare their dimension explicitly** (`subject`, `predicate`,
  `value_kind`, `qualifier`, `period`, `publication_type` on the differential).
  The two observations are never merged, and a "unique policy rate" is never
  implied — Phase 7's instruments stay distinct.
- `differential = base_value − quote_value`, an **arithmetic difference in the
  same unit/kind**, with both source values preserved (no conversion, no
  interpretation).
- **Both orientations are generated** (A−B and B−A) with distinct identities.

### Comparability gate

Before a differential is formed, the two observations must be **sufficiently
compatible**. The gate is strict:

1. same dimension lineage (subject, predicate, value_kind, canonical period,
   qualifier, publication_type);
2. same **unit**;
3. a **numeric** value kind (number, percentage, basis points, currency) —
   an arithmetic difference requires numbers.

When the gate fails the differential is **not** formed: a documented
`incomparable` warning is emitted for a unit mismatch, and
text/qualitative/date/boolean/range dimensions are by nature **not
differentiable** (they remain observed fundamentals, no warning — it is their
documented property, not an error).

### Cross-instrument comparisons

Comparing `deposit_facility_rate` (ECB) against `policy_rate` (Fed) is
**not** derived by Phase 8: the two sides do not share a dimension lineage,
and Phase 8 never merges instruments implicitly or explicitly. Such
comparisons require an explicit, economy-neutral instrument-family mapping,
which is deliberately **not pre-implemented** (documented gap, future phase).

## Temporal semantics

- The **observation time** of a fundamental is the temporal reference of the
  source publication: `meeting_date` when set, else `publication_date` (the
  exact reference Phase 5/6/7 use).
- **`effective_date` and `period` are never observation times.** They are kept
  separate. A value about a period is **not** treated as known during that
  period: it is known from its observation/publication date.
- **No look-ahead**: a fundamental dated `T` uses only observations with
  `observed_at ≤ T`; a differential anchored on a base observation uses the
  quote observation with `observed_at ≤ base.observed_at`.
- **Frequency differences** (event-driven rates, monthly inflation, quarterly
  GDP): **never resampled, never interpolated, never forward-predicted**. The
  alignment rule is **latest-known observation at T**, always traceable to the
  observation actually used.

## Missing data

- A dimension with no observation for an economy → **no fundamental** (absent,
  `unknown > invention`).
- A differential dimension present on only one side → **no differential**
  (documented absence, no warning).
- A base observation with **no quote observation ≤ its date** (the quote side
  exists but starts later / is temporally behind) → **no differential** with an
  explicit `missing_side` warning.
- A unit mismatch → **no differential** with an `incomparable` warning.

## Revisions — point-in-time correctness

Each observation is its own fundamental (one source observation → one row), so
the history is never silently overwritten. A revised value published at `T2`
is a distinct observation with its own provenance; as-of queries only ever use
observations with `observed_at ≤ T`. Nothing ever claims a revised value was
known before its publication. Sources are never modified.

## Identity

```
fundamental_id    = SHA-256(currency, source_kind, source_id)
differential_id   = SHA-256(base_currency, quote_currency,
                            subject, predicate,
                            base_source_id, quote_source_id)
```

Identities are derived solely from the relationship — reproducible,
self-explanatory, stable across rebuilds (idempotent persistence), and never
"invented": both sides are real source observations. The pair and the dimension
are part of the differential identity, so `EUR/USD` never collides with
`USD/EUR`. The analyzer is pure, deterministic and order-independent
(same input → same output).

## Provenance

```
ForexFundamental
    ↓  source_id + source_kind
MonetaryPolicyState (Phase 7)  or  Fact (Phase 4)
    ↓
Publication → Document

ForexDifferential
    ↓  base_source_id / quote_source_id
left ForexFundamental / right ForexFundamental
    ↓  leurs sources respectives
```

Every fundamental and every differential is **self-describing**: it carries
its own denormalized provenance (publication, document, effective date,
verbatim source text, observed dates) on both sides where applicable. A
derived value without complete provenance is invalid and never emitted.

## Persistence and rebuild

Two dedicated tables mirroring `monetary_policy_states` conventions:
`forex_fundamentals` and `forex_differentials`.

- `analyze_forex_fundamentals(store, *, bank=None, persist=True)` recomputes
  over the **full input** (all states + all facts — differentials need both
  sides) and **replaces** the requested scope: fundamentals of the bank's
  currency, and differentials involving that currency. `bank=None` rebuilds the
  whole store.
- Rebuilds are **idempotent**, an **empty result clears the scope**, and no
  derived row can survive the disappearance of the observation it summarizes.
- Deletion surface mirrors Phase 6/7: by bank/currency, by document, by
  publication.
- `created_at` is preserved across upserts; `rebuild` restores the scope in
  one transaction.
- Sources (`facts`, `fact_changes`, `policy_reactions`,
  `monetary_policy_states`, `publications`) are **never modified**.

## API surface

- `ForexFundamental`, `ForexDifferential`, `ForexFundamentalResult`
  (fundamentals + differentials + warnings).
- `ForexFundamentalsAnalyzer` — pure, deterministic, `analysis_version = "15.0.0"`.
- `analyze_forex_fundamentals(store, *, bank=None, persist=True)` — store entry point.
- `fundamental_id_of(...)`, `differential_id_of(...)` — deterministic identity.
- Store: `save_forex_fundamental(s)`, `get_forex_fundamental(s)`,
  `get_fundamentals_as_of(currency, as_of=None)`,
  `delete_forex_fundamentals`, `rebuild_forex_fundamentals`, and the
  differential equivalents including `get_differential_as_of(pair, subject, as_of=None)`.

## What Phase 8 is NOT

Phase 8 produces: observed values, arithmetic differentials, temporal
alignment, comparability metadata, provenance. It does **not** produce:

- hawkish / dovish, bullish / bearish, strong / weak currency;
- buy / sell / long / short / trade signal;
- conviction score, probability, expected return;
- fair value, target price, forecast, market prediction;
- currency ranking, positioning engine, causal claim, expected market
  reaction.

Even when an economic relation "seems obvious", it is not emitted.

## Normalization

Only normalizations that are deterministic, mathematically explicit,
documented, reversible/traceable and economically unambiguous are allowed.
Phase 8 implements **one**: the arithmetic difference of two observed values
(same unit/kind). Percentage changes, annualization, z-scores, rankings,
weighted scores and unit conversions are **not** implemented (documented
scope; not justified by the current data).

## Documented gaps left to later phases

- Cross-instrument monetary comparisons (e.g. ECB `deposit_facility_rate` vs
  Fed `policy_rate`) — require an explicit instrument-family mapping.
- Macro dimensions not yet structured: consumption, investment, trade,
  current account, productivity, labour risk, fiscal stance, yield curves,
  market pricing.
- Relative/percentage differentials, annualization, any normalization beyond
  the arithmetic difference.
- Any forecast, expectation, fair value, ranking, conviction, positioning or
  trading signal (Phases 9–10).

## Validation criteria (Phase 8 is COMPLETE only if)

1. The Phase 8 test suite is green and deterministic (run twice).
2. The full suite (Phases 0–15) is green twice; compileall passes; Phases 0–14
   are functionally unchanged (`git diff` audited).
3. `reactions/` and `states/` receive no Phase 8 logic; `store.py` changes are
   exclusively Phase 8 additions.
4. No forbidden content exists: no hawkish/dovish/bullish/bearish, no
   forecast/fair value/signal/conviction/ranking, no mutation of sources, no
   LLM/network/fuzzy/semantic logic.
5. Every behavior above has a dedicated test (golden, adversarial, negative,
   persistence, determinism, provenance, as-of, no-look-ahead).