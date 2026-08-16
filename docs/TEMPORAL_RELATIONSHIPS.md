# Data Model — Temporal Relationships (Phase 6)

> **Legacy note.** This document was previously `docs/REACTIONS.md` and the
> layer was called **PolicyReaction / Policy Reaction Function**. During the
> renumbering/recontextualization, the concept was renamed **Temporal
> Relationships**: Phase 6 establishes *descriptive temporal relations between
> observed FactChanges* — it does **not** model a central-bank reaction function,
> a causal link, or an input→output policy response. The code module, the
> persisted table (`policy_reactions`) and the identifier (`reaction_id`) keep
> their legacy names for compatibility; the *concept* is Temporal Relationships.

This document is the authoritative reference for the **temporal-relationship
layer** of Argus (`src/argus/reactions/`, legacy module name). It defines what a
**Temporal Relationship** is, what it is **not**, and every design decision
behind the analyzer, the identity scheme and the persistence.

## Where Temporal Relationships sit in the pipeline

```
Type-Specific Extractor                 ← Phases 4.1–4.7
    ↓
Fact                                    ← Phase 4 (src/argus/facts/)
    ↓
FactChanges / history of changes        ← Phase 5 (src/argus/changes/)
    ↓
Temporal Relationships                  ← Phase 6 (src/argus/reactions/, THIS layer)
Monetary Policy State                   ← Phase 7 (COMPLETE — docs/MONETARY_POLICY_STATE.md)
Forex Fundamentals                      ← Phase 8 (COMPLETE — docs/FOREX_FUNDAMENTALS.md)
```

Phase 6 is an **analysis layer over the output of Phase 5**. It consumes
existing `FactChange` relations (which in turn point to existing `Fact`s) and
relates an **earlier change** to a **later change** observed within a defined
temporal window, when the two changes satisfy the system's matching criteria.
It never creates Facts, never reads source documents, never calls any network,
model or fuzzy/semantic comparator, and never mutates the `FactChange` / `Fact`
objects it relates.

## Epistemic boundary (the core principle)

There are exactly three levels, and Phase 6 never collapses them:

```
SOURCE                       observed   (official publication / document)
→ OBSERVED FACT              observed   (Phase 4 — explicit in source)
→ OBSERVED CHANGE            observed   (Phase 5 — descriptive delta)
→ TEMPORAL RELATIONSHIP      INFERRED   (Phase 6 — THIS layer)
```

- `Fact` and `FactChange` are **observed**. They describe what the source said
  and what changed between two observed states.
- A `TemporalRelationship` is **inferred** in the sense that it is a derived
  relation — the empirical, **non-causal** statement that a later change was
  **temporally associated** with an earlier change.
- Every relationship carries `inferred=True` (a constant, never `False`) and a
  non-causal `formulation`. It is **never** presented as an observed fact,
  never as a "true structural reaction function", and never as causality.

The relation means exactly:

> "Change B was observed after change A, within the configured temporal window,
> and the two changes satisfy the system's matching criteria."

It does **not** mean:

> "B was caused by A", "the central bank responded to A", "A is an input whose
> B is the output", or "the central bank follows a reaction function."

## What a Temporal Relationship is

A **TemporalRelationship** (legacy class name `PolicyReaction`) is a **derived
relation between two existing FactChanges**:

```
earlier change (observed at T_earlier)
        ↓  (0 ≤ later_observed_at − earlier_observed_at ≤ max_lag_days)
later change (observed at T_later)
```

It records the complete provenance of **both** sides (change ids, subjects,
predicates, values, periods, effective dates, publication ids, document ids,
verbatim source texts, observation dates), the exact temporal lag, the window
used, and a non-causal formulation.

## What a Temporal Relationship is NOT

- **Not a new Fact** — it creates nothing; the source `FactChange` / `Fact`
  objects are never modified.
- **Not a causal claim** — "a policy-rate increase was observed after an
  inflation change" is the formulation; "inflation caused the central bank to
  raise rates" is **never** produced.
- **Not a stance score** — no hawkish/dovish, no +1/−1, no sentiment.
- **Not a forecast / trading signal** — no forex, no entries/exits, no P&L, no
  recommendation. Those belong to Phase 8/10.
- **Not produced by fuzzy/semantic/LLM/network logic** — matching is exact,
  deterministic and explainable.

## The three levels, explicitly

1. **Observation** — a `Fact` exists, e.g. `policy_rate = 4.35`.
2. **Observed change** — the Fact evolves, `policy_rate: 4.35 → 4.10` at
   `T_later`; this produces a `FactChange`.
3. **Temporal relationship** — another `FactChange` exists, e.g.
   `inflation: 3.1 → 3.4` at `T_earlier`. The system can then state
   `T_earlier < T_later` and that the two changes are temporally related per
   the defined criteria — never that inflation caused the policy-rate change.

## Earlier-change vocabulary (observed changes eligible as the earlier side)

A `FactChange` is eligible as the **earlier** side when its `subject` is one of:

```
inflation, core_inflation, inflation_expectations,
gdp, growth, unemployment, wages, labour_market,
financial_conditions, fiscal_policy
```

This reuses the canonical `subject` vocabulary produced by Phases 4.1–4.7.
Projection facts (`predicate=projection` on `gdp` / `inflation` / etc.) are
eligible too, because the subject is already in the set. **No value is
reinterpreted and no qualitative fact is turned into a numeric score.**

## Later-change vocabulary (observable monetary-policy changes)

A `FactChange` is eligible as the **later** side when its `subject` is one of:

```
policy_rate, main_refinancing_rate, deposit_facility_rate, marginal_lending_rate,
policy_guidance, asset_purchase,
risk, inflation_risk, growth_risk
```

These are observable monetary-policy changes already represented by
Phases 4.1–4.7: policy-rate changes, forward-guidance wording changes,
balance-sheet / asset-purchase decisions, and risk-assessment changes.

**Documented role assignment for risk assessments.** Risk-assessment changes
(`risk`, `inflation_risk`, `growth_risk`) are ambiguous in principle — they can
be read as an earlier condition or as a later policy communication. In this
first implementation they are assigned the **later** role (monetary-policy
communication changes). They are never used as the *earlier* side. This is a
documented, deterministic choice, not a silent one.

Subjects outside both vocabularies are simply not part of a relationship (no
warning — they are irrelevant, not errors).

## Temporal rule (explicit, deterministic, inspectable)

1. **Observation time of a change.** The observation time of a `FactChange` is
   the temporal reference of its **current-side** publication — `meeting_date`
   when set, else `publication_date` (the same reference Phase 5 uses to order
   observations). This is the moment the new value became known. The `period`
   (e.g. forecast year) and the `effective_date` are **never** used as the
   observation time.
2. **No look-ahead.** A change may relate to a later change only when
   `earlier_observed_at ≤ later_observed_at`. A change observed *after* the
   other can never precede it. A change whose current publication is missing or
   undated is skipped with a warning.
3. **Central bank is a change property.** A change without a `central_bank` is
   skipped with `unplaced_change:<change_id>` — the publication's bank is never
   used to place it.
4. **Temporal window.** The lag
   `later_observed_at − earlier_observed_at` must be within a documented
   window: `0 ≤ lag_days ≤ max_lag_days`. The default `max_lag_days` is a
   documented constant (`180` days, ≈ six months, roughly spanning the ECB's
   ~6-week meeting cadence). It is **not** fitted to historical data, not
   machine-learned, and not hidden: it is an explicit parameter of the analyzer
   and each relationship records both the exact `lag_days` and the
   `max_lag_days` that matched it.
5. **Same-time boundary.** `lag_days == 0` is allowed (the later change was
   known by the earlier change's observation date) and tested explicitly.
6. **Pairing.** Every eligible `(earlier change, later change)` pair within the
   window of the same central bank produces **one** TemporalRelationship.
   Multiple earlier changes before one later change and multiple later changes
   after one earlier change are therefore both represented (each as its own
   relationship).

## Temporal distance (`lag_days`)

`lag_days` is the **temporal distance between two observed changes** — nothing
more:

```
Change A : 2025-03-15
Change B : 2025-04-02

lag_days = 18
```

This states only that **18 days separate the two observed changes**. It does
**not** state that "the central bank took 18 days to respond."

## Bank isolation

A relationship is never produced across central banks. The pairing groups
changes by `central_bank`, which is a property of the `FactChange` — it is
**never resolved or invented from the publication** (no
`Publication.central_bank` fallback). A change without a `central_bank` is
skipped with an `unplaced_change:<change_id>` warning. ECB observations never
participate in a relationship for the Fed.

## Deterministic identity

`reaction_id` (legacy name; the concept is the relationship id) is a SHA-256
over the complete semantic identity of the relationship (following the Phase 5
convention — the identity derives from the relationship itself, not from the
analysis version):

```
reaction_id = SHA-256(central_bank, earlier_change_id, later_change_id)
```

(legacy field names: `condition_change_id`, `policy_change_id`)

The same pair of changes, seen the same way, always yields the same id —
stable across rebuilds, idempotent persistence, self-explanatory, and never
"invented" (both sides are real `FactChange` objects).

## Provenance

Every TemporalRelationship is **bidirectionally traceable**:

| side | fields (legacy names) |
|---|---|
| earlier | `condition_change_id`, `condition_subject`, `condition_predicate`, `condition_value_kind`, `condition_previous_value`, `condition_current_value`, `condition_period`, `condition_effective_date`, `condition_publication_id`, `condition_document_id`, `condition_source_text`, `condition_observed_at` |
| later | `policy_change_id`, `policy_subject`, `policy_predicate`, `policy_value_kind`, `policy_previous_value`, `policy_current_value`, `policy_period`, `policy_effective_date`, `policy_publication_id`, `policy_document_id`, `policy_source_text`, `policy_observed_at` |
| relation | `lag_days`, `max_lag_days`, `formulation` |
| analysis | `reaction_id`, `central_bank`, `inferred`, `analysis_version`, `analyzed_at` |

`source_text` is preserved **verbatim** (byte-for-byte) from both changes.

## Non-causal formulation

Each relationship carries a deterministic `formulation` such as:

```
"change policy_guidance observed on 2026-03-15 followed a change inflation
 observed on 2026-01-15 within 59 days (empirical temporal association,
 not causal)"
```

The word "followed" expresses temporal association. Causality, direction of
intent, stance and market meaning are never asserted.

## Persistence (`policy_reactions`, legacy table name)

- The `policy_reactions` table is **derived data**. `analyze_reactions(store,
  *, bank=None, max_lag_days=…)` (legacy entry point) reads the persisted
  `fact_changes` of a bank (the Phase 5 output), recomputes the full
  relationship scope and **replaces** it atomically (`rebuild_reactions`).
- Consequences: re-analysis is **idempotent**; an empty result **clears** the
  scope; a relationship can never survive the disappearance of the changes it
  relates; a rebuild of one bank never touches another bank; the source
  `facts` / `fact_changes` tables are never modified.
- Read filters (`get_reactions`) support `bank`, `condition_change_id`,
  `policy_change_id`, `subject` (either side), and `limit`.
- `save_reaction` upserts by `reaction_id` and preserves `created_at`.
- `delete_reactions`, `delete_reactions_for_document`,
  `delete_reactions_for_publication` provide the same lifecycle surface as
  Phase 5.

## Analysis version

`PolicyReactionAnalyzer.analysis_version = "13.0.0"` (legacy analyzer class
name). Changing the analytical algorithm (vocabulary, window rule,
formulation) must bump this version; the version is persisted with every
relationship.

## Observability

`PolicyReactionAnalyzer.analyze` and `analyze_reactions` return a
`PolicyReactionResult` with `reactions` and `warnings`:

| warning | meaning |
|---|---|
| `missing_publication:<id>` | change's current-side publication is missing — `<id>` is the `change_id` when the change has no `current_publication_id`, else the missing publication id |
| `undated_publication:<id>` | current-side publication has no `meeting_date`/`publication_date` |
| `unplaced_change:<id>` | change has no `central_bank` (never resolved from the publication, never invented) |

Changes whose subject is in neither vocabulary are ignored silently (they are
not errors).

## Out of scope (Phase 7/8 boundaries)

Phase 6 does **not** build: policy state (stance, direction, rate_level,
rate_expectation, inflation_risk/growth_risk/labour_risk, guidance, confidence,
`as_of`), structural econometrics, causal identification, VAR/DSGE, machine
learning, Bayesian inference, forex fundamentals, cross-bank comparison, or any
trading/signal layer. It deliberately stops before those layers.
