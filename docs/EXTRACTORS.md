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

---

# Type-Specific Extractors — Phase 7 (Press Conferences)

This section is the reference for the **ECB Press Conference extractor**, built
on the same Phase 4 contract and following the Phase 5/6 patterns
(`src/argus/press_conferences/`).

## Pipeline

```
NormalizedDocument                          ← Phase 2
    ↓  PressConferenceExtractor.extract(publication, document)
ExtractionResult(publication_id, document_id, facts, warnings)
    ↓  Store.rebuild_facts_for_document (delete + insert, idempotent)
facts table
```

- a classified **ECB press conference** publication
- its normalized document(s)
- the ECB press conference extractor producing structured **Facts**
- an `ExtractionResult` handed to the existing `Store`

## Extractor contract

`src/argus/press_conferences/base.py`:

```python
class PressConferenceExtractor(ABC):
    bank: str
    extraction_version: str
    def extract(self, publication, document) -> ExtractionResult: ...
```

- One extractor per bank; the generic code only dispatches on `central_bank`.
- Extractors are **pure**: they read the normalized document and return an
  `ExtractionResult`; persistence is a caller concern.
- `extract_press_conference(store, publication, *, document=None)` runs the
  right extractor and persists facts through
  `Store.rebuild_facts_for_document`, keeping re-runs idempotent.
- Extraction is **gated on classification**: a publication classified as
  anything other than `press_conference` (authoritatively in the
  `classifications` table) is never mined for press-conference facts.

## ECB press conference extractor

`src/argus/press_conferences/ecb.py` — `EcbPressConferenceExtractor`
(`extraction_version 7.0.0`). It answers *"what does the President and the
Vice-President explicitly state about the economy and their policy stance
during the press conference?"*.

### Remarks vs. Q&A

Content is routed deterministically by **section heading**: the section whose
heading normalizes to `introductory statement` (or a known ECB synonym —
`opening statement`, `introductory remarks`, `opening remarks`) is treated as
**remarks** (collective Governing Council communication); the section whose
heading normalizes to `questions and answers` (or a synonym — `questions`,
`question`, `answers`, `answers to questions`, `q&a`) is treated as **Q&A**
(individual speakers). A narrow content-first fallback (`_mode_from_text`
scanning for `Question:` / `Answer:` markers) applies when the heading carries
no signal.

**Routing is conservative** (Phase 7 hardening): an unknown heading is mined
only when the text carries a reliable Q&A signal, and is otherwise **ignored** —
`UNKNOWN ≠ REMARKS`. "Absence of proof → absence of extraction": a future
section such as an appendix, biography, financial-stability annex, legal
notice, closing remarks or additional information is simply not mined, even
when its sentences would match the economic patterns. `closing remarks` is
deliberately **not** a remarks heading. One document contributes one remarks
mode and one Q&A mode at most.

### Supported facts

| subject | predicate | value | source |
|---|---|---|---|
| `monetary_policy` | `statement` | text (verbatim) | remarks — policy sentences ("We will decide meeting by meeting …", "We do not pre-commit …") |
| `policy_guidance` | `statement` | text (verbatim) | explicit prospective policy statements |
| `inflation` / `core_inflation` | `value` / `assessment` | percentage (+ period) / text | inflation statements |
| `inflation_driver` | `assessment` | text (verbatim) | explicit driver statements ("driven by energy prices …") |
| `growth` | `assessment` | text (verbatim) | growth / activity statements |
| `gdp` | `value` | percentage (+ period) | quantitative growth claims ("projected to grow by 1.6% in 2028") |
| `labour_market` | `assessment` | text (verbatim) | labour market statements |
| `unemployment` | `value` | percentage (+ period) | explicit unemployment claims |
| `wages` | `value` | percentage (+ period) | explicit wage-growth claims |
| `financial_conditions` | `assessment` | text (verbatim) | financial / financing conditions statements |
| `risk` | `assessment` | categorical or text | risk statements |
| `inflation_risk` | `assessment` | categorical (upside/downside/balanced) or text | same |
| `growth_risk` | `assessment` | categorical (upside/downside/balanced) or text | same |

Category precedence is deterministic: guidance > policy > risk > financial >
inflation > labour > growth. An unmatched sentence produces no fact
(reliability over coverage). No `rationale`, no `change`, no decision facts.

### Provenance — speaker attribution

Every Fact carries the Phase 5/6 provenance fields (`source_location`,
`source_text`, value-level `source_text`, `extraction_method = regex`,
`extraction_version = 7.0.0`, `confidence`). In addition:

- **`speaker`** — verbatim official role + name label when the source itself
  labels the answer (e.g. `"President Christine Lagarde"`,
  `"Vice-President Luis de Guindos"`). Never inferred: an unlabelled answer
  keeps `speaker = None`, and remarks facts are **always** `None` (collective
  Governing Council communication — never attributed to an individual).
- **`identity_qualifier`** — `remarks:{n}` for remarks facts,
  `answer:{turn}:{n}` for Q&A answer facts (`turn` = 1-based Q&A turn, `n` =
  per-turn ordinal). This is what distinguishes *"what the Governing Council
  decided"* (decision facts, Phase 5) from *"what an individual governor said"*
  (individual attribution) — the Phase 7 validation criterion.
- The journalist's **questions are never mined**: `Question:` lines start a
  turn and are skipped.

### Non-economic questions

A turn whose question is an explicit non-economic **personal** topic is
**skipped entirely** (question + answer), with a `non_economic_question_skipped`
warning. The markers are specific multi-word phrases — a memoir, personal /
private life (or matters/affairs), family life, your family, your retirement,
your spouse / children / partner, hobbies. Generic personal-language tokens
(`personal`, `personally`, `private`) are **never** triggers on their own: they
occur naturally in economic questions ("What is your personal assessment of the
inflation outlook?", "Do you personally expect inflation to return to target?")
and such answers **must** be extracted. The decision is conservative but
contextual — an economic question containing personal vocabulary is never
rejected, and the question phrasing is never reclassified by the model.

### Risk assessment

Identical to Phase 6: a categorical orientation fact (`upside` / `downside` /
`balanced`, with `two-sided` / `symmetric` normalized to `balanced`) when an
explicit orientation word is present; otherwise a verbatim text assessment.
Absence never becomes an invented orientation — it is surfaced as a
`no_risk_assessment` warning.

### Quantitative values & confidence

Identical gate to Phase 6: values are extracted only from explicit value
claims, so "the 2% target" / "close to 2%" / "converging towards 2%" is never
read as a value; a percentage with a following reference period keeps a
`FactPeriod` (year, or month when a month is named). Confidence: `HIGH` for
percentages and categorical orientations, `MEDIUM` for verbatim text.

### Warnings (fixed order)

`no_sections` (early return), `no_remarks`, `no_qna`,
`no_risk_assessment`, `no_forward_guidance`, `non_economic_question_skipped`.

### Not covered — Phase 7 boundaries

- **No collective decision from an individual statement**: a governor's remark
  is a personal statement (`speaker` set), never a `monetary_policy_decision`
  fact — that subject belongs to Phase 5 and is gated on decision
  publications.
- **No Phase 5/6 subjects**: decision wording, rates, changes, effective date,
  rationale, and decision-type forward guidance are never mined from a press
  conference (regression-tested via gating + phase-separation tests).
- No hawkish/dovish / stance / forex / trading interpretation — later phases;
  no LLM (invariant 8); multi-thematic section routing limitations inherited
  from Phase 6, documented, not fixed in this phase.

### Phase 5 / 6 / 7 boundary

The three extractors are disjoint by **publication type** (classification
gating): decisions, statements and press conferences are never cross-mined.
`get_extractor` dispatches on `central_bank`, and each extractor refuses
publications whose authoritative classification is not its own type.

## Golden tests

`tests/fixtures/documents/ecb_press_conf*.html` (modeled on the ECB press
conference layout: introductory statement, questions & answers, labelled
speakers, quantitative claims, risk language, forward guidance, decoy and
non-economic questions). `tests/test_press_conferences.py` runs the normalizer
→ extractor → store slice and asserts, per fixture:

- the exact expected facts (per-subject value sets with periods, and verbatim
  texts) with warnings;
- remarks vs. Q&A routing, speaker attribution (exact, verbatim, never
  invented), journalist content never mined, non-economic turns skipped;
- no invented facts — no Phase 5/6 subjects, no interpretation, nothing for
  absent optional categories;
- verbatim provenance: each `fact.source_text` and `fact.value.source_text` is
  a substring of the referenced section;
- deterministic extraction and idempotent Store persistence, `speaker`
  roundtrip through the store, classification gating and Phase 5/6
  coexistence.

In addition, `tests/test_press_conferences.py` holds dedicated **hardening
regression tests** (no new fixtures, documents built inline):

- **Routing**: a known remarks heading → remarks; known Q&A headings → Q&A
  (variants kept); an unknown heading with `Question:`/`Answer:` markers → Q&A;
  an unknown heading without a reliable signal → 0 facts even when its sentences
  match the economic patterns ("Additional Information / Inflation is expected
  to remain elevated." → 0 facts); `closing remarks` is not a remarks heading.
- **Question filter**: an explicitly personal question (personal life, family
  life, your retirement, your children) skips the turn; economic questions
  containing `personal`/`personally`/`private`-style vocabulary are still
  extracted, with the answer content, subject, predicate, value, period,
  `identity_qualifier`, `source_text`, `source_location` and `speaker` asserted —
  not just the fact count.
---

# Type-Specific Extractors — Phase 8 (Minutes / Meeting Accounts)

This section is the reference for the **ECB Minutes / Meeting Account
extractor**, built on the same Phase 4 contract and following the Phase 5–7
patterns (`src/argus/minutes/`).

## Pipeline

```
NormalizedDocument                          ← Phase 2
    ↓  MinutesExtractor.extract(publication, document)
ExtractionResult(publication_id, document_id, facts, warnings)
    ↓  Store.rebuild_facts_for_document (delete + insert, idempotent)
facts table
```

- a classified **ECB minutes / meeting account** publication
- its normalized document(s)
- the ECB minutes extractor producing structured **Facts**
- an `ExtractionResult` handed to the existing `Store`

## Extractor contract

`src/argus/minutes/base.py`:

```python
class MinutesExtractor(ABC):
    bank: str
    extraction_version: str
    def extract(self, publication, document) -> ExtractionResult: ...
```

- One extractor per bank; the generic code only dispatches on `central_bank`.
- Extractors are **pure**: they read the normalized document and return an
  `ExtractionResult`; persistence is a caller concern.
- `extract_minutes(store, publication, *, document=None)` runs the right
  extractor and persists facts through `Store.rebuild_facts_for_document`,
  keeping re-runs idempotent (delete + insert, **including empty results**).
- Extraction is **gated on classification**: a publication classified as
  `minutes` or `meeting_account` (authoritatively in the `classifications`
  table) is mined; anything else — and an absent classification — is refused
  (`MINUTES_PUBLICATION_TYPES = ("minutes", "meeting_account")`).

## ECB minutes extractor

`src/argus/minutes/ecb.py` — `EcbMinutesExtractor` (`extraction_version
8.0.0`). It answers *"what did the Governing Council explicitly say or discuss
during the meeting?"*.

### Section routing — conservative

A section is mined only when its normalized heading is a **known economic
section**; an **unknown heading — and the known non-economic ones — is
ignored** ("absence of proof → absence of extraction"). Ignored headings
include the "Account of the monetary policy meeting" title, `legal notice`,
`statistical annex`, `copyright`, `imprint`, `disclaimer`,
`external monetary policy` (other central banks' policy is never mined) and
`minutes of …`. An unknown future section is never assumed to be economic.

### Supported facts

| subject | predicate | value | source |
|---|---|---|---|
| `monetary_policy` | `statement` | text (verbatim) | policy stance / preferences / arguments ("Members agreed …", "The Governing Council decided to …", "Some members would have preferred …") |
| `policy_guidance` | `statement` | text (verbatim) | forward guidance in direct or indirect style ("stood ready to …", "would be guided by …", "future policy decisions would depend on …") |
| `inflation` / `core_inflation` | `value` / `assessment` | percentage (+ period) / text | prices and costs discussion |
| `inflation_expectations` | `assessment` | text (verbatim) | same |
| `growth` | `assessment` | text (verbatim) | real economy / external environment discussion |
| `gdp` | `value` | percentage (+ period) | quantitative growth claims |
| `labour_market` / `unemployment` / `wages` | `value` / `assessment` | percentage (+ period) / text | labour market discussion |
| `financial_conditions` | `assessment` | text (verbatim) | money, credit and financial conditions discussion |
| `risk` | `assessment` | categorical or text | risk assessment |
| `inflation_risk` | `assessment` | categorical (upside/downside/balanced) or text | same |
| `growth_risk` | `assessment` | categorical (upside/downside/balanced) or text | same |

Sentence classification is **content-first for every mined section** with the
same deterministic precedence as Phase 7 (guidance > policy > risk > financial
> inflation > labour > growth); the section heading only gates *whether* the
section is mined, never *how* a sentence is classified. An unmatched sentence
produces no fact (reliability over coverage).

### Discussion wording — handled faithfully

A sentence that only names the **theme of the discussion** without stating a
position is **suppressed** — no fact is invented from "they discussed X":

- "The discussion focused on inflation."
- "The topic / subject of the discussion was monetary policy."
- "Members discussed the possibility / implications / prospects of …"

A sentence that states **explicit content** is mined as usual, with its
attribution: "Members noted that inflation was expected to average 2.0% in
2027." → `inflation / value` 2.0 (`year:2027`).

### Provenance — attribution without invented identities

Every Fact carries the Phase 5–7 provenance fields (`source_location`,
`source_text`, value-level `source_text`, `extraction_method = regex`,
`extraction_version = 8.0.0`, `confidence`). Phase 8 specifics:

- **`Fact.speaker` is always `None`** — the accounts do not reliably label
  individual governors and a name is never guessed.
- **`identity_qualifier`** — `minutes:{attribution}:{n}`, where `attribution`
  is the subject label the source itself states, with precedence
  `dissent` > `one_member` > `some_members` > `members` > `council` >
  `collective` (unmarked sentences). Individual positions and dissents are
  therefore **distinguished and traced** without fabricating identities
  (roadmap Phase 8 criterion).
- A dissent is kept as the verbatim policy statement it is part of, tagged
  `minutes:dissent:` — it is **never** turned into an invented vote count or a
  `vote` subject.

### Quantitative values & risk

Identical gate to Phases 6/7: values are extracted only from explicit value
claims ("projected / expected … to average / stand at …", "stood at …"), so
target phrasing is never read as a value; a percentage with a following
reference period keeps a `FactPeriod` (year, or month when a month is named).
Risk facts are categorical (`upside` / `downside` / `balanced`, with
`two-sided` / `symmetric` normalized to `balanced`) only when the source states
an explicit orientation, otherwise a verbatim text assessment. Confidence:
`HIGH` for percentages and categorical orientations, `MEDIUM` for verbatim
text.

### Warnings (fixed order)

`no_sections` (early return), `no_risk_assessment`, `no_forward_guidance`.

### Not covered — Phase 8 boundaries

- **The decision itself** (wording, rates, changes, effective date) stays
  Phase 5, gated on decision publications; the verbatim policy statement of the
  account is kept as `monetary_policy/statement`, never as a rate value.
- **Rationale** (Phase 6), **votes** (never fabricated — a dissent is traced,
  not counted), hawkish/dovish / stance / forex / trading interpretation (later
  phases), **Phases 9–11** (projections, reports, speeches — the qualitative
  economic discussion of the account is mined, never a separate projection
  table).
- No LLM (invariant 8).

### Phase 5 / 6 / 7 / 8 boundary

The four extractors are disjoint by **publication type** (classification
gating): decisions, statements, press conferences and minutes are never
cross-mined. `get_extractor` dispatches on `central_bank`, and each extractor
refuses publications whose authoritative classification is not its own type.

## Golden tests

`tests/fixtures/documents/ecb_minutes*.html` (modeled on the ECB "Account of
the monetary policy meeting": title, monetary policy stance and policy
considerations, economic analysis / external environment / real economy /
prices and costs / money credit and financial conditions, risk assessment,
external monetary policy, policy conclusions, legal notice).
`tests/test_minutes.py` runs the normalizer → extractor → store slice and
asserts, per fixture:

- the exact expected facts (per-subject value sets with periods, and verbatim
  texts) with warnings;
- section routing (known economic headings mined, unknown and known
  non-economic headings ignored — including economic content under an unknown
  heading → 0 facts);
- discussion wording (theme-only sentences suppressed, explicit content mined
  with attribution) and attribution (dissent / some_members / members / council
  / collective qualifiers, `speaker` never set, no invented vote);
- no invented facts — no Phase 5/6 subjects, no interpretation, nothing for
  absent optional categories;
- verbatim provenance: each `fact.source_text` and `fact.value.source_text` is
  a substring of the referenced section;
- deterministic extraction and idempotent Store persistence (including
  empty-result persistence and the `minutes` / `meeting_account` gating),
  classification gating and Phase 5/6/7 coexistence.

# Type-Specific Extractors — Phase 9 (Economic Projections)

## Pipeline

```
NormalizedDocument                          ← Phase 2
    ↓  ProjectionsExtractor.extract(publication, document)
ExtractionResult(publication_id, document_id, facts, warnings)
    ↓  Store.rebuild_facts_for_document (delete + insert, idempotent)
facts table
```

- a classified **ECB / Eurosystem staff macroeconomic projections** publication
  (`economic_projections`)
- its normalized document(s)
- the ECB projections extractor producing structured **Facts**
- an `ExtractionResult` handed to the existing `Store`

## Extractor contract

`src/argus/projections/base.py`:

```python
class ProjectionsExtractor(ABC):
    bank: str
    extraction_version: str
    def extract(self, publication, document) -> ExtractionResult: ...
```

- One extractor per bank; the generic code only dispatches on `central_bank`.
- Extractors are **pure**: they read the normalized document and return an
  `ExtractionResult`; persistence is a caller concern.
- `extract_projections(store, publication, *, document=None)` runs the right
  extractor and persists facts through `Store.rebuild_facts_for_document`,
  keeping re-runs idempotent (delete + insert, **including empty results**).
- Extraction is **gated on classification**: a publication classified as
  `economic_projections` (authoritatively in the `classifications` table) is
  mined; anything else — and an absent classification — is refused
  (`PROJECTIONS_PUBLICATION_TYPES = ("economic_projections",)`).

## ECB projections extractor

`src/argus/projections/ecb.py` — `EcbProjectionsExtractor` (`extraction_version
9.0.0`). It answers *"what does the ECB staff project for each variable and
each year?"* — deterministically, from the projection **tables**.

### Table-driven extraction

Projections are table documents. The extractor works on
`NormalizedDocument.tables` (`DocumentTable`: `headers` / `rows`) and never
re-parses a flattened text blob, preserving the **variable × year × value ×
unit** integrity of the source table:

- **columns** are years (`20xx` header cells);
- **rows** are variables (the leading cell of each row);
- a cell becomes a Fact **only** when it is identified by a recognised variable
  (row label) + a year (column header) + an explicit unit.

A bare number without that identity is never a Fact (`UNKNOWN ≠ PROJECTION`):
header-less tables, unlabelled rows, scenario columns without years
("Baseline / Adverse / Severe"), the **Technical assumptions** table (oil
price, exchange rates, interest rates — assumptions, not projections) and the
methodology / disclaimer / legal-notice sections all yield nothing.

### Supported facts

| subject | predicate | value | source |
|---|---|---|---|
| `inflation` (HICP) | `projection` | percentage (+ `period` `year:`) | projection table cell |
| `core_inflation` (HICP excluding energy and food) | `projection` | percentage (+ `period` `year:`) | projection table cell |
| `gdp` (Real GDP) | `projection` | percentage (+ `period` `year:`) | projection table cell |
| `inflation` / `core_inflation` / `gdp` | `revision` | number, `unit = "pp"` (+ `period` `year:`) | explicit `Revisions vs {Month Year}` column block |

Only the three core variables are mined; any other row (private consumption,
unemployment, employment, compensation per employee, oil price, …) is ignored —
reliability over coverage. The `revision` predicate is a Phase 9 extension of
the controlled predicate vocabulary (documented here, per
`docs/DATA_MODEL.md`).

### Periods, units, revisions

- **Periods come from the table headers** (`FactPeriod` `year:2026`, label =
  the verbatim header cell) — never guessed from text position.
- **Units are kept explicitly**: projection values are `percentage` (annual
  percentage changes — a `2.1` cell stays `2.1%`, never a plain number);
  revision deltas are `number` with `unit = "pp"` (percentage points — a `-0.2`
  percentage-point revision is never turned into basis points or into `-0.2%`).
- **Revisions are only ever the explicitly stated ones.** The ECB publishes
  revisions *relative to the previous projections* as an explicit
  `Revisions vs {Month Year}` column block; those stated deltas are kept as
  `revision` facts. A revision is **never computed**: the
  `current − previous` difference between two projection tables is never
  calculated or invented.
- **Current vs previous projections** are distinguished by `identity_qualifier`:
  `projections:current` (table without an explicit as-of month),
  `projections:{yyyy-mm}` (table whose caption names its month, e.g.
  `projections:2026-03`), and `projections:revision_vs:{yyyy-mm}` for the
  explicit revision blocks.

### Provenance

Every Fact carries `source_location` (`LocationKind.TABLE`, with `table` /
`row` / `column` indexes), `source_text` = the exact verbatim row of the source
table, `value.source_text` = the exact verbatim cell, `extraction_method =
table_extraction`, `extraction_version = 9.0.0`, `confidence = HIGH`,
`effective_date = None` and `speaker = None` (projections are collective staff
output, never attributed to an individual).

### Warnings (fixed order)

`no_tables` (early return), `no_projection_table` (tables exist but none
produced a Fact — e.g. only assumptions / scenario / unlabelled tables).

### Not covered — Phase 9 boundaries

- **No interpretation**: no hawkish/dovish, stance, market impact, forex or
  trading logic — never.
- **No computed analysis**: revision magnitude, acceleration, surprise,
  deviation or `current − previous` deltas are never derived.
- **No policy facts**: decisions, rates, guidance stay Phases 5–8, gated on
  their own publication types.
- **No LLM** (invariant 8).

### Phase 5 / 6 / 7 / 8 / 9 boundary

The five extractors are disjoint by **publication type** (classification
gating): decisions, statements, press conferences, minutes and economic
projections are never cross-mined. `get_extractor` dispatches on
`central_bank`, and each extractor refuses publications whose authoritative
classification is not its own type.

## Golden tests

`tests/fixtures/documents/ecb_projections*.html` (modeled on the real ECB /
Eurosystem staff macroeconomic projections documents: title, overview, the
"Growth and inflation projections" summary table with year columns and
`Revisions vs {Month Year}` column blocks, the "Technical assumptions" box,
notes and legal notice):

- `ecb_projections.html` — current projections for HICP / core HICP / Real GDP
  over 2025–2028 (12 facts, `projections:current`);
- `ecb_projections_revisions.html` — a "March 2026 projections" table with an
  explicit `Revisions vs December 2025` block plus a reproduced "December 2025
  projections" table (18 projection + 9 explicit revision facts; current vs
  previous distinguished by qualifier, revisions proven never computed);
- `ecb_projections_ambiguous.html` — scenario columns without years, unlabelled
  rows, a technical-assumptions table and non-projection sections → 0 facts +
  `no_projection_table`;
- `ecb_projections_minimal.html` — a single clean HICP table (2 facts).

`tests/test_projections.py` runs the normalizer → extractor → store slice and
asserts, per fixture:

- the exact expected facts (per-variable value sets with year periods, and the
  explicit revision deltas) with warnings;
- table structure (row × column × year × value × unit integrity, `table` /
  `row` / `column` provenance);
- variable identity (`inflation` / `core_inflation` / `gdp`), periods from
  headers, units preserved explicitly (percentage vs `pp`), footnote markers
  stripped without inventing values;
- the value gate: a bare cell without variable + year + unit identity is never
  a Fact — scenario columns, unlabelled rows and technical assumptions produce
  nothing;
- revisions only from explicit column blocks, never computed as
  `current − previous`, current vs previous distinguished by
  `identity_qualifier`;
- no invented facts — no Phase 5/6/7/8 subjects, no interpretation, `speaker`
  always `None`;
- verbatim provenance: each `fact.source_text` is a row of the referenced
  table, `fact.value.source_text` is the exact cell;
- deterministic extraction and idempotent Store persistence (including
  empty-result persistence and the `economic_projections` gating),
  classification gating and Phase 5/6/7/8 coexistence.
