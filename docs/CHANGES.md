# Data Model — Fact Changes (Phase 5)

This document is the authoritative reference for the **temporal /
cross-publication change layer** of Argus (`src/argus/changes/`). It defines
what a `FactChange` is, what it is not, and every design decision behind the
analyzer, the identity scheme and the persistence.

## Where Fact Changes sit in the pipeline

```
Type-Specific Extractor                 ← Phases 4.1–4.7
    ↓
Fact                                    ← Phase 4 (src/argus/facts/)
    ↓
Temporal / Cross-Publication Analysis   ← Phase 5 (src/argus/changes/, THIS layer)
    ↓
Policy Reaction Function                ← Phase 6
Monetary Policy State                   ← Phase 7
Forex Fundamentals                      ← Phase 8
```

Phase 5 is an **analysis layer**, not an extractor. It consumes the existing
Fact history, relates Facts over time, and persists the relations. It never
creates Facts, never reads source documents and never mutates the Facts it
compares.

## What a FactChange is

A `FactChange` is an **analytic relation between two existing Facts**
(`previous_fact_id → current_fact_id`) recording the observable difference
between a previous observation and the next one, together with the complete
provenance of BOTH sides (document, publication, period, `effective_date`,
`source_text`). "Why does Argus say this value changed?" is therefore always
answerable by "Fact A in document A had X, Fact B in document B has Y".

```
Fact #1 (P1, 2026-01-15): policy_rate = 4.00 %
Fact #2 (P2, 2026-03-15): policy_rate = 4.25 %
→ FactChange(previous_fact_id=#1, current_fact_id=#2,
             change_type=numeric_changed, delta=+0.25 percentage, …)
```

## What a FactChange is NOT

- **Not a new Fact** — it does not create a fact, and the source Facts are
  never modified.
- **Not an interpretation** — no hawkish/dovish, no tightening/easing, no
  market reading. `delta` is `current − previous`, nothing more.
- **Not a judgement by Argus** — no forecast, no policy recommendation.
- **Not produced by a fuzzy/semantic/LLM comparator** — matching is exact and
  fully explainable (see below).

## Change types

| `ChangeType`           | When                              | Payload                              |
|------------------------|-----------------------------------|--------------------------------------|
| `numeric_changed`      | a numeric value changed           | `delta = current − previous`, same kind/unit, rounded to 10 decimals |
| `qualitative_changed`  | a categorical/other value changed | `delta = None` (values compared exactly) |
| `text_changed`         | a verbatim wording changed        | `delta = None` (both texts preserved) |

Identical values produce **no change** at all (see "no-change" rules below).

## Matching rules (exact, explainable, deterministic)

1. **Observation lineage.** Two Facts are candidates only when they share the
   same `(central_bank, subject, predicate, value.kind,
   period.canonical(), identity_qualifier, publication_type)`. The period is
   compared via its canonical form, so a 2027 forecast never meets a 2028
   forecast; a different `identity_qualifier` (e.g. Q&A answer 1 vs answer 2,
   minutes dissent vs members) never merges; a decision rate never meets a
   speech value.
2. **Cross-publication only.** Facts belonging to the **same publication** are
   never compared (two statements inside one document are not a change).
3. **Publication type is authoritative classification.** The type used for
   matching comes from the **`classifications` table** (source of truth), never
   from the denormalized `Publication.publication_type` cache when an
   authoritative classification is available. The production entry point
   `analyze_changes` always loads it via `Store.list_classifications`. A
   publication without any canonical classification is **skipped**
   (`missing_classification` / `unclassified_publication` warning) — the type
   is never invented and a stale cache never wins. (The pure in-memory API
   falls back to `Publication.publication_type` only when no `classifications`
   mapping is supplied — documented convenience for standalone use.)
4. **Temporal ordering.** Observations are ordered by the publication temporal
   reference — `meeting_date` when set, else `publication_date` — with ties
   broken by `publication_id`. A publication without any date is skipped
   (observability warning).
5. **Consecutive chaining, never bridging.** Only **adjacent** observations in
   the ordered lineage are compared: F1→F2, F2→F3, … A fixed baseline (F1→F3)
   is never used. Each adjacent pair is evaluated **independently**: a pair
   that yields no change (identical values) or cannot be compared (incompatible
   units) produces **no change for that pair** and is never jumped over to
   reach a later observation — F1→F3 and F2→F4 are never produced. The pair
   immediately *following* an incomparable pair is still evaluated: in
   F1→F2, F2→F3 (no change), F3→F4, the produced changes are F1→F2 and F3→F4.
   Observations of *different lineages* (e.g. a different qualifier, period or
   value kind) never interact, so the consecutive pair of a lineage is simply
   the next observation of *that* lineage — a `basis_points` observation
   between two percentage observations does not block the percentage lineage.
6. **No-change.** Equal values → no change. This keeps the table minimal and
   the signal exact.
7. **Incomparable publications / facts.** Facts whose publication is missing,
   whose canonical classification is missing or `unknown`/`other`, undated,
   valueless (`NULL`), or which carry no document id are skipped with an
   observability warning. Precision over recall: better no change than a
   spurious one.

## Central bank fallback

A `FactChange.central_bank` is **never invented**:

```
FactChange.central_bank = Fact.central_bank
                         | Publication.central_bank   (fallback)
                         | None                        (both absent — not invented)
```

`Fact.central_bank` wins; the publication's bank is the documented fallback;
a fully unplaced change keeps `None`. The matching key uses the same resolution
on both sides, so two facts that only know their bank through their
publications still group together.

## Provenance

Every `FactChange` is **bidirectionally traceable** with concrete, verifiable
values — there is no degraded or placeholder provenance:

| side | previous | current |
|---|---|---|
| fact | `previous_fact_id` | `current_fact_id` |
| document | `previous_document_id` | `current_document_id` |
| publication | `previous_publication_id` | `current_publication_id` |
| period | `previous_period` | `current_period` |
| effective date | `previous_effective_date` | `current_effective_date` |
| source text | `previous_source_text` | `current_source_text` |
| value | `previous_value` | `current_value` |

Plus `subject`, `predicate`, `value_kind`, `identity_qualifier`,
`analysis_version`. `delta` is present for `numeric_changed` only. This lets
the audit walk `Change → previous Fact → previous publication/document` and
`Change → current Fact → current publication/document` unambiguously.

`source_text` is preserved **verbatim** (byte-for-byte): never lowercased,
stripped of semantic content, paraphrased or summarized.

## Ordering reference vs period vs effective date

- The **ordering reference** is a publication-level attribute
  (`meeting_date`, else `publication_date`): it decides *which observation is
  previous*. It is purely chronological and never an economic order.
- The **period** is a Fact-level attribute (e.g. the forecast year) and is part
  of the matching key: a 2027 and a 2028 forecast are different lineages.
  2027 → 2028 is therefore **not** "2027 changed to 2028" — it is two distinct
  observations and produces no change. A period-less fact (`period=None`) is
  likewise a different lineage from a period-dated fact; a year period never
  meets a month period even for the same year.
- The **`effective_date`** is a Fact-level attribute and is preserved on both
  sides of the change, but is never used to order or match. Two facts with
  different effective dates but the same lineage are still compared (the
  effective dates are simply carried along).

## Publication type boundary

`publication_type` is part of the comparison identity — an intentional,
documented architectural decision, never widened:

| lineage | comparable? |
|---|---|
| decision → decision | yes |
| speech → speech | yes (same lineage) |
| decision → speech | **no** |
| minutes → minutes | yes (same lineage) |
| minutes → decision | **no** |

## Identity qualifier

`identity_qualifier` is part of the matching key. It is normalized the same way
the `Fact`/`Store` layer normalizes it (`None` → `""`), so `None` and `""` are
the **same** identity (no qualifier) and never split a lineage. Two distinct
non-empty qualifiers never merge:

- `answer:1:0` vs `answer:2:0` → no change
- `minutes:dissent:1` vs `minutes:members:1` → no change

## Deterministic identity

`change_id` is a SHA-256 over `previous_fact_id + current_fact_id +
change_type` (`src/argus/changes/identity.py`). The same pair of facts, seen
the same way, always yields the same id — stable across rebuilds, self
explanatory, and never "invented" (both sides are real Facts).

## Persistence (`fact_changes`)

- The `fact_changes` table is **derived data**. `analyze_changes(store, *,
  bank=None)` recomputes the full scope (one bank, or the whole store) from the
  current `facts` + `publications` + `classifications` tables and **replaces**
  it atomically (`rebuild_changes`).
- Consequences: re-analysis is **idempotent**; an empty result **clears** the
  scope; a change can never survive the disappearance of the facts it relates;
  no per-document invalidation is needed; a rebuild of one bank never touches
  the changes of another bank.
- Read filters (`get_changes`) support `bank`, `subject`, `change_type`,
  `previous_fact_id`, `current_fact_id`, `publication_id` (either side) and
  `limit`.

## Observability

`FactChangeAnalyzer.analyze` and `analyze_changes` return a `FactChangeResult`
with `changes` and `warnings`. Warnings are machine-readable so skipped facts
are never silent:

| warning | meaning |
|---|---|
| `missing_publication:<id>` | fact has no/unknown `publication_id` |
| `undocumented_fact:<id>` | fact has no `document_id` (untraceable) |
| `missing_classification:<id>` | no authoritative classification record |
| `unclassified_publication:<id>` | classified as `unknown`/`other` (uncomparable) |
| `undated_publication:<id>` | no `meeting_date` or `publication_date` |
| `valueless_fact:<id>` | value is `None`/`NULL` |