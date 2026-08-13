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

The vertical slice is deliberate and small:

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
5.0.0`).

Extracts only what the source states, with provenance:

| subject | predicate | value | source |
|---|---|---|---|
| `monetary_policy_decision` | `date` | ISO date (e.g. `2026-07-23`) | leading date paragraph |
| `main_refinancing_rate` | `value` | percentage | the three-rate enumeration or a per-instrument sentence |
| `marginal_lending_rate` | `value` | percentage | same |
| `deposit_facility_rate` | `value` | percentage | same |
| `{rate}` | `change` | basis_points (sign preserved) | explicit "lower(ed) … by 25 basis points" |

Every Fact also carries:

- `effective_date` — parsed from "with effect from …" when explicitly stated
  (never assumed);
- `source_location` — section index inside the normalized document;
- `source_text` — the verbatim supporting passage / matched value wording;
- `extraction_method = regex`, `extraction_version = 5.0.0`, `confidence`.

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

### Not covered (by design)

- hawkish/dovish assessment, forex interpretation, temporal/cross-publication
  analysis — later phases;
- other banks (their extractors are Phase 5 follow-up work);
- LLM extraction — prohibited by invariant 8.

## Canonical subject / predicate vocabulary (extensions)

Controlled-vocabulary additions made next to the Phase 4 core set (documented
here and in `src/argus/decisions/ecb.py`):

- subjects: `monetary_policy_decision`, `main_refinancing_rate`,
  `marginal_lending_rate`, `deposit_facility_rate`
- predicates added: `date` (decision date), alongside the existing
  `value` / `change`.

The identity (`fact_id`) follows Phase 4: SHA-256 over publication_id,
document_id, subject, predicate, period and effective_date. Values are excluded,
so corrected extractions update rows in place. Two facts differing only by their
effective date remain distinct facts.

## Golden test

`tests/fixtures/documents/ecb_decision.html` (modeled on the real ECB page:
date paragraph, decision section, "Key ECB interest rates" section with the
enumeration + "with effect from 2 August 2026", APP/PEPP sections).
`tests/test_decisions.py` runs the normalizer → extractor → store slice and
asserts the seven facts (date, three levels, three changes) with their
provenance, plus idempotence and classification gating.