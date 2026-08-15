# Data Model — Monetary Policy State (Phase 14)

This document is the authoritative reference for the **monetary policy state
layer** of Argus (`src/argus/states/`). It defines what a `MonetaryPolicyState`
is, what it is **not**, and every design decision behind the analyzer, the
identity scheme, the temporal semantics and the persistence.

It was written **before** the implementation (Phase 13 is frozen; Phase 15 is
not started). Anything in this document is normative; any divergence found
during implementation must be resolved here first.

## Where the Monetary Policy State sits in the pipeline

```
Type-Specific Extractor                 ← Phases 5–11
    ↓
Fact                                    ← Phase 4 (src/argus/facts/)
    ↓
Temporal / Cross-Publication Analysis   ← Phase 12 (src/argus/changes/)
    ↓
Policy Reaction Function                ← Phase 13 (src/argus/reactions/)
    ↓
Monetary Policy State                   ← Phase 14 (src/argus/states/, THIS layer)
    ↓
Forex Fundamentals                      ← Phase 15 (NOT STARTED)
```

Phase 14 is an **analysis layer over the output of Phase 12** (and reuses the
**dimension vocabulary** established by Phase 13). It consumes existing
`FactChange` relations (which in turn point to existing `Fact`s) and
synthesizes, for each central bank, a **historised, dated state** of the
**observable policy dimensions** over time. It never creates Facts, never reads
source documents, never calls any network, model or fuzzy/semantic comparator,
and never mutates the `FactChange` / `Fact` / `Publication` objects it consumes.

The question Phase 14 answers, for any instant `T`:

> **Quel est l'état observable de la politique monétaire d'une banque centrale
> à un instant donné ?** — "What is the observable state of a central bank's
> monetary policy at a given instant?"

It is **not** a forecast, **not** a hawkish/dovish reading, **not** a
next-decision prediction, and **not** a forex/trading signal.

## Epistemic boundary (the core principle)

There are exactly four levels, and Phase 14 never collapses them:

```
SOURCE                      observed     (official publication / document)
→ OBSERVED FACT             observed     (Phase 4 — explicit in source)
→ OBSERVED CHANGE           observed     (Phase 12 — descriptive delta)
→ TEMPORAL RELATIONSHIP     INFERRED     (Phase 13 — empirical, non-causal)
→ MONETARY POLICY STATE     SYNTHESIZED  (Phase 14 — THIS layer)
```

- `Fact` / `FactChange` are **observed**.
- A `PolicyReaction` (Phase 13) is **inferred** (`inferred=True` constant).
- A `MonetaryPolicyState` is **synthesized**: `synthesized` is always `True`.
  It is a derived, dated summary of the observable policy dimensions — a
  **state synthesis is authorized** (ordering, selecting, dating, historising
  observations) but an **economic/market interpretation is not**.

Every `MonetaryPolicyState` carries `synthesized=True` (a constant, never
`False`) and a purely descriptive `formulation`. It is **never** presented as a
new observation, never as a stance, never as an anticipation, never as a
trading/forex signal.

### Phase 13's contribution is the vocabulary, not the values

The state's *dimensions* are exactly Phase 13's reaction-side subjects
(`REACTION_SUBJECTS`, re-exported here as `STATE_SUBJECTS`). Phase 13 is the
documented owner of that vocabulary and of the role assignment (risk
assessments are policy-side). The state's *values*, however, come **only from
observed `FactChange` current sides**. `PolicyReaction` objects themselves (the
inferred condition→policy associations) are **never** state inputs: mixing an
inferred association into an "observable state" would collapse the epistemic
boundary. This is a documented design decision.

## What a MonetaryPolicyState is

A `MonetaryPolicyState` is a **derived, dated observation of ONE policy
dimension of ONE central bank**, established by ONE `FactChange`:

```
FactChange (subject ∈ STATE_SUBJECTS, predicate ∉ excluded, current side C)
        ↓
MonetaryPolicyState
   dimension  = the change's observation lineage (bank, subject, predicate,
                value_kind, canonical period, qualifier, publication type)
   value      = C.value        (the newest known level of the dimension)
   observed_at= temporal reference of C's publication
                (meeting_date, else publication_date)
   provenance = C's publication / document / verbatim source text /
                effective date / previous value, plus source_change_id
```

The current side of a policy `FactChange` is precisely "the newest known value
of this dimension, known at `observed_at`". Each eligible change produces
**exactly one** state entry. The history of a dimension is the ordered list of
its entries; the state at `T` is the latest entry per dimension with
`observed_at ≤ T`.

## State dimensions

The observable policy dimensions (`STATE_SUBJECTS = REACTION_SUBJECTS`):

```
policy_rate, main_refinancing_rate, deposit_facility_rate, marginal_lending_rate,
policy_guidance, asset_purchase, risk, inflation_risk, growth_risk
```

- **Policy rates are never reduced to a single rate**: each rate is its own
  dimension. Institutional differences are preserved: `main_refinancing_rate`
  and `policy_rate` are separate dimensions even when a bank reports both.
- **No invented value**: a dimension's value is always a verbatim copy of the
  current side of an observed change. A missing rate is an absent dimension
  (`unknown > invention`), never a synthesized or converted rate. Rates are
  **never converted** from one instrument to another.
- **Guidance / asset purchase / risk**: kept as observed — verbatim text for
  `policy_guidance`, exact value for `asset_purchase` and for the risk
  assessments. **No directional interpretation** is attached (no "the wording
  turned hawkish", no stance label). The economic meaning of guidance wording
  is not structured by the current data and is explicitly **not derived here**
  (documented gap, not invented semantics).

### Excluded lineages

A `FactChange` whose `predicate` is in `STATE_EXCLUDED_PREDICATES =
{"projection"}` describes an **expected future value**, not the current policy
configuration. Such changes are **out of scope** for the state (Argus does not
produce its own forecast, and the state does not record the bank's projections
as "the current policy state"). They are skipped with an explicit
`out_of_scope_change:<change_id>` warning.

Changes whose `subject` is not in `STATE_SUBJECTS` (e.g. inflation, gdp) are
simply **irrelevant** to the state and are silently ignored (they are not
policy dimensions — same policy as Phase 13 for vocabulary outsiders).

## Temporal semantics

- The **observation time** of a state entry is the temporal reference of the
  current-side publication: `meeting_date` when set, else `publication_date`
  (the exact reference Phase 12 uses to order observations and Phase 13 uses to
  date reactions).
- **`effective_date` is never an observation time**: it is the date the value
  *takes effect*, a separate concept preserved verbatim on the entry. It is
  never used to order or to answer "state at T".
- **`period` is never an observation time** either: it labels the observation
  lineage (e.g. the year of a projection), not when the value was learned.
- **No look-ahead**: a state dated `T` only includes entries with
  `observed_at ≤ T`. This is enforced at query time (`get_policy_state_as_of`)
  and is deterministic — no future observation can leak into a dated state.
- **Periods without a new decision**: a dimension with no new change simply
  keeps its previous entry; "state at T" returns the latest entry ≤ T. No
  interpolation, no placeholder, no re-dating of an old observation.

## State evolution

```
dimension history (policy_rate, bank "ecb", lineage L):
  observed_at   value      source change
  -----------   -----      -------------
  Jan 15        (absent)   (nothing observed yet — dimension not asserted)
  Mar 15        4.25       change F1→F2
  May 15        4.50       change F2→F3
```

- The state of a dimension **advances** when a new `FactChange` of that
  dimension appears (its current side becomes the new level).
- Between two observations the state **persists**: `state at Apr 15 = 4.25`
  (the latest entry with `observed_at ≤ Apr 15`).
- A dimension that has **never produced a change** has **no state entry**: its
  level is not asserted (conservative; `unknown > invention`). This is the
  documented "initial state" behavior: the state timeline starts at the first
  observed change of the dimension.

## Absences and warnings

An entry is not produced, with an observability warning, when:

- the change has no `current_publication_id` → `missing_publication:<change_id>`
- the current publication is unknown to the mapping → `missing_publication:<publication_id>`
- the current publication has no temporal reference → `undated_publication:<publication_id>`
- the change has no `central_bank` → `unplaced_change:<change_id>` (the bank is
  a property of the `FactChange`, **never** resolved from the publication)
- the change's current side has no value → `valueless_change:<change_id>`
- the change is a forecast lineage → `out_of_scope_change:<change_id>`
- (authoritative mode) the current publication has no canonical classification
  → `missing_classification:<publication_id>` (mirrors Phase 12)

A dimension never observed is simply **absent** from the state (no entry, no
warning — absence is information).

## Identity

`state_id = SHA-256(central_bank, source_change_id)` — derived solely from the
source relationship, so the **same change observed the same way always yields
the same id**. It is reproducible, self-explanatory (which change established
this observation), stable across rebuilds (idempotent persistence), and never
"invented": the source is a real `FactChange`.

Determinism: the analyzer is pure; output is sorted by `state_id`; input order
is irrelevant; the same input always yields the same entries.

## Provenance

Each state entry is self-describing: `source_change_id` (→ `FactChange` →
`previous_fact_id` / `current_fact_id` → `Fact` → publications/documents), the
dimension components, the observed value, the previous value, and the verbatim
provenance of the current side (`publication_id`, `document_id`,
`effective_date`, `source_text`, `observed_at`). The state is **remontable aux
faits** (each state is traceable to the facts, publications and documents it
summarizes).

## Persistence and rebuild

- Table `monetary_policy_states` in the SQLite store, mirroring the
  `fact_changes` / `policy_reactions` conventions: derived, self-describing,
  denormalized rows keyed by `state_id`.
- `analyze_policy_state(store, *, bank=None, persist=True)` recomputes a bank
  scope (or the whole store) from the persisted `fact_changes` + publications +
  authoritative classifications, and **replaces** that scope
  (`rebuild_policy_states`): idempotent, an empty result clears the scope, and
  no state can survive the disappearance of the change it summarizes.
- Deletion surface mirrors Phase 13: by bank, by document, by publication.
- `created_at` is preserved across upserts; `rebuild` restores the scope in one
  transaction.
- Sources (`facts`, `fact_changes`, `policy_reactions`, `publications`) are
  **never modified**.

## API surface

- `MonetaryPolicyState` — the entry (dataclass).
- `MonetaryPolicyStateResult` — `states` + `warnings`.
- `MonetaryPolicyStateAnalyzer` — pure, deterministic; `analysis_version = "14.0.0"`.
- `analyze_policy_state(store, *, bank=None, persist=True)` — store entry point.
- `state_id_of(central_bank, source_change_id)` — deterministic identity.
- Store: `save_policy_state` / `save_policy_states` / `get_policy_state` /
  `get_policy_states(...)` (timeline filters) / `get_policy_state_as_of(bank,
  as_of=None)` (latest entry per dimension with `observed_at ≤ as_of`; `None`
  means no upper bound) / `delete_policy_states` /
  `delete_policy_states_for_document` / `delete_policy_states_for_publication` /
  `rebuild_policy_states`.

## What a MonetaryPolicyState is NOT

- **Not a new Fact** — it creates nothing; the source objects are never
  modified.
- **Not a stance** — no hawkish/dovish, no direction, no +1/−1, no sentiment.
- **Not a forecast / expectation** — no next-decision prediction, no expected
  path, no surprise; projection lineages are excluded.
- **Not a forex/trading signal** — no cross-bank comparison, no differentials,
  no yield/rate spreads, no entries/exits, no recommendation (Phase 15/17).
- **Not a causal claim** — the `formulation` is descriptive only.
- **Not produced by fuzzy/semantic/LLM/network logic** — synthesis is exact,
  deterministic and explainable.
- **Not a re-slice of raw facts** — it is a historised, dated synthesis of the
  *analysis* layer (Phase 12 changes), reusing Phase 13's dimension vocabulary.

## Documented gaps vs the roadmap target model

The roadmap's target "Policy State" lists `stance`, `direction`,
`rate_expectation`, `labour_risk`, `confidence` and `as_of`. Of these, only
`as_of` exists here (as `observed_at`). The others are **not structured by the
current data** — there is no observed subject or fact for a stance, a
direction, a rate expectation or a labour risk, and no confidence level is
collected. Per the project invariants (`unknown > invention`, no silent
interpretation), they are **not synthesized in Phase 14** and are recorded here
as explicit gaps for later phases, not invented here.

## Validation criteria (Phase 14 is COMPLETE only if)

1. The suite of Phase 14 tests is green and deterministic (run twice).
2. The full suite (Phases 0–14) is green twice; compileall passes; Phases 0–13
   are functionally unchanged (`git diff` audited).
3. Explicit test count before/after is reported.
4. No forbidden content exists in the state layer: no hawkish/dovish, no
   directional score, no forecast, no cross-bank comparison, no forex, no
   mutation of sources, no LLM/fuzzy/semantic/network logic.
5. Every documented behavior above has at least one dedicated test (golden,
   adversarial, negative, persistence, determinism, provenance).