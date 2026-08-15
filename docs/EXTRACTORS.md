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
  idempotent and stale facts impossible. Rebuild runs for **every** valid
  normalized document — **including when the extraction yields zero facts** — so
  the current extraction result is the source of truth for the persisted state
  and a re-extraction that now produces nothing clears the stale facts of that
  document instead of leaving them behind.
- Extraction is **gated on classification**: the `classifications` table is the
  **single source of truth**. A publication is mined for decision facts only
  when an authoritative classification record exists and its `publication_type`
  is `monetary_policy_decision`. A publication classified as anything else, a
  publication with **no** classification record, and the denormalized
  `publication.publication_type` cache (which **never** authorizes extraction on
  its own) are all refused — classification runs first
  (classification → extraction).

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

In addition, `tests/test_decisions.py` holds dedicated **hardening regression
tests** (no new fixtures, documents built inline):

- **Classification gating**: the `classifications` table is the single source
  of truth — a `monetary_policy_decision` classification allows extraction;
  any other classification (e.g. `minutes`, `monetary_policy_report`) refuses
  it; an **absent** classification refuses it; and the denormalized
  `publication.publication_type` cache **alone** (set to
  `monetary_policy_decision`) never authorizes extraction. The batch entry
  point (`extract_decision_batch`) respects the same gating and never persists
  facts for an unauthorized publication.
- **Empty-result persistence**: re-extracting a persisted document with an
  extractor that now yields zero facts clears that document's stale facts
  (idempotently on repeated runs), while facts of other documents under the
  same publication are preserved.

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
  keeping re-runs idempotent. Rebuild runs for **every** valid normalized
  document — **including when the extraction yields zero facts** — so the
  current extraction result is the source of truth for the persisted state and
  a re-extraction that now produces nothing clears the stale facts of that
  document instead of leaving them behind (scoped to the requested document;
  facts of other documents/publications are untouched).
- Extraction is **gated on classification**: the `classifications` table is the
  **single source of truth**. A publication is mined for statement facts only
  when an authoritative classification record exists and its `publication_type`
  is `monetary_policy_statement`. A publication classified as anything else, a
  publication with **no** classification record, and the denormalized
  `publication.publication_type` cache (which **never** authorizes extraction on
  its own) are all refused — classification runs first
  (classification → extraction). A gating refusal never deletes facts that an
  earlier authorized extraction persisted (pipeline-wide classification changes
  are out of Phase 6's scope).

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

Content is routed deterministically by **section heading**, and routing is
**exact identity on the cleaned heading** (case / numbering / punctuation /
leading "the" normalized away, then compared verbatim against controlled sets:
"Monetary policy statement", "Risk assessment", "Forward guidance", "Economic
activity", "Inflation", "Labour market", "Financial conditions", …). Substring
coincidence is never enough: "Risk management" does not route to the risk
section and "Non-economic developments" does not route to a growth section —
both fall through to the narrow content-first fallback. The fallback
(guidance > risk > rationale) applies only to sections whose heading carries no
signal (intro, closing remarks, heading-less text), so cross-category phrasing
inside a mapped section is never double-counted.

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

The risk **anchor** is the same controlled set used by Phases 10/11
(`risks? to/for/around/…`, `downside/upside/two-sided/… risks`,
`uncertain/uncertainty/uncertainties`, `tilted`): a word merely carrying the
"risk" prefix ("risky", "risk-free", "riskiness") is never a risk anchor, and a
risk section is mined sentence-by-sentence through those anchors — precision
over recall.

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

In addition, `tests/test_statements.py` holds dedicated **hardening regression
tests** (no new fixtures, documents built inline):

- **Classification precedence**: a `monetary_policy_statement` classification
  wins against a contradictory `publication.publication_type` cache; a
  `minutes` / unknown classification with a statement cache is refused; an
  absent classification (even with a statement cache) is refused; a gating
  refusal never deletes facts that an earlier authorized extraction persisted;
  every other phase's publication type (decision, press conference, minutes,
  report, speech) refuses statement extraction.
- **Batch/single consistency**: `extract_statement_batch` applies exactly the
  same gating — a mixed store (one classified, one unclassified) extracts only
  the classified publication.
- **Empty-result persistence**: re-extracting a persisted document with an
  extractor that now yields zero facts clears that document's stale facts
  (idempotently on repeated runs), while facts of other documents **and of
  other publications** are preserved.
- **Routing & content classification**: an unknown heading with pure category
  content is never mined (`UNKNOWN ≠ ECONOMIC`); the narrow content-first
  fallback fires only for guidance / risk / rationale anchors; the documented
  priority (guidance > risk > rationale) is deterministic for ambiguous
  sentences; target/expectation phrasing without an explicit value claim
  ("will return to the 2% target", "remains solid") never becomes a value fact.

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

Content is routed deterministically by **section heading**, and routing is
**exact identity on the cleaned heading** (case / numbering / punctuation /
leading "the" normalized away, then compared verbatim against controlled sets):
the heading `introductory statement` (or a known ECB synonym — `opening
statement`, `introductory remarks`, `opening remarks`) is treated as **remarks**
(collective Governing Council communication); the heading `questions and
answers` (or a synonym — `questions`, `question`, `answers`, `answers to
questions`, `q&a`) is treated as **Q&A** (individual speakers). Substring
coincidence is never enough: `introductory note` is not an introductory
statement and `questions and answers on monetary policy` is not the Q&A
heading. A narrow content-first fallback (`_mode_from_text` scanning for the
labelled `Question:` / `Answer:` markers) applies when the heading carries no
signal. Only the colon-labelled lines are Q&A markers — a natural sentence
beginning with "Question marks remain …" is never read as one.

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
the "Minutes of …" title form. An unknown future section is never assumed to
be economic.

**Routing is exact identity on the cleaned heading** (case / numbering /
punctuation / leading "the" normalized away, then compared verbatim against
controlled sets: "Monetary policy stance and policy considerations",
"Economic analysis", "External environment", "Real economy", "Prices and
costs", "Money, credit and financial conditions", "Risk assessment", "Policy
conclusions", …). Substring coincidence is never enough: "Non-economic
developments" shares the word "economic" with "Economic analysis" but routes
to **ignore**, never to a mined section.

The known non-economic headings are matched the same way — **exact identity**
for `legal notice`, `statistical annex`, `copyright`, `imprint`, `disclaimer`,
`external monetary policy` — plus the two explicit **title-style prefix
families** `Account of the monetary policy meeting …` (the dated meeting-account
title) and `Minutes of …`. There is **no substring matching anywhere in heading
routing**: a heading that merely *contains* a known phrase ("External monetary
policy developments", "Statistical annexes", "Copyright notice", "Disclaimer
and legal notice") is neither that controlled heading nor an economic one — it
is an unknown heading and is ignored (0 facts, `UNKNOWN ≠ ECONOMIC`).

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
an explicit orientation, otherwise a verbatim text assessment. The risk anchor
is the same controlled set as Phases 6/7/10/11 (`risks? to/for/around/…`,
`downside/upside/two-sided/… risks`, `uncertain/uncertainty/uncertainties`,
`tilted`) — "risky", "risk-free", "riskiness" are never anchors. Confidence:
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
- **the unit is identified at the table level**, from the table's own caption
  (real ECB projection tables state it in the title line, e.g.
  "(annual percentage changes)"). A table whose unit is missing, unknown or
  incompatible is **ignored as a whole** — a value is never assumed to be a
  percentage, and the unit of one table never authorises the numbers of another;
- a cell becomes a Fact **only** when it is identified by a recognised variable
  (row label) + a year (column header) + an explicit unit.

A bare number without that identity is never a Fact (`UNKNOWN ≠ PROJECTION`):
header-less tables, unlabelled rows, scenario columns without years
("Baseline / Adverse / Severe"), the **Technical assumptions** table (oil
price, exchange rates, interest rates — assumptions, not projections) and the
methodology / disclaimer / legal-notice sections all yield nothing.

The unit gate checks the caption in a fixed order: *share* units
(`% of GDP`, `percentage of total`) are rejected first (ratios, not
changes), then percentage markers are matched (`annual percentage changes`,
whole-word `percent`/`per cent`, `% growth`, `(%)`, … — so the real caption
"(annual percentage changes; revisions in percentage points)" still
authorises the table), then incompatible units (`index`, `points`, currencies,
energy units) are rejected. `\bpercent\b` (whole word) deliberately does not
match "percentage", so "(percentage points)" alone never authorises a table.

Variables are identified by **exact canonical label matching** (row label
normalised, footnote markers stripped, then compared verbatim against the
controlled vocabulary): near-misses ("GDP deflator", "GDP per capita", "HICP
excluding energy", "HICP excluding energy, food and changes in indirect
taxes", …) never match a core variable and are ignored, while the real ECB
wording ("Real GDP", "GDP growth", "HICP", "HICP excluding energy and food")
keeps working.

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
  A projection value is produced **only when** its table's unit is explicitly
  recognised as a percentage — never assumed from the value itself, and the
  unit gate applies to revision columns too.
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
- the unit gate: an explicit percentage caption is extracted; a missing,
  unknown or incompatible unit (`% of GDP`, `index`, `(percentage points)`,
  `USD/barrel`) is ignored and never assumed/converted; percentage variants
  (`percent`, `per cent`, `(%)`, `% growth`, `annual growth rates`) are
  accepted; a unit never leaks across tables; revision columns are gated by
  the same percentage unit;
- variable near-miss protection: `Real GDP` / `GDP growth` / `Real GDP growth`
  and `HICP` / core-HICP canonical labels are extracted, while "GDP deflator",
  "GDP per capita", "GDP price deflator", "HICP excluding energy", "HICP
  excluding energy, food and changes in indirect taxes", … are ignored;
- integrity: variable × period × value × unit → one Fact; no unit → no Fact;
  a unit in another table cannot authorise; two number columns are never a
  revision without an explicit `Revisions vs` block;
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

---

# Type-Specific Extractors — Phase 10 (Monetary Policy Report / Reports)

## Pipeline

```
NormalizedDocument                          ← Phase 2
    ↓  ReportsExtractor.extract(publication, document)
ExtractionResult(publication_id, document_id, facts, warnings)
    ↓  Store.rebuild_facts_for_document (delete + insert, idempotent)
facts table
```

- a classified **monetary policy report** publication (`monetary_policy_report`)
- its normalized document(s)
- the ECB report extractor producing structured **Facts**
- an `ExtractionResult` handed to the existing `Store`

A monetary policy report is a large **narrative** document: economic outlook,
inflation drivers, growth outlook, labour market, financial conditions, fiscal
developments, risks and policy rationale. The ECB's report-like publication of
the euro area is the **Economic Bulletin**; the extractor models a
report-shaped document generically so it applies to any bank's analogous
report.

## Extractor contract

`src/argus/reports/base.py`:

```python
class ReportsExtractor(ABC):
    bank: str
    extraction_version: str
    def extract(self, publication, document) -> ExtractionResult: ...
```

- One extractor per bank; the generic code only dispatches on `central_bank`.
- Extractors are **pure**: they read the normalized document and return an
  `ExtractionResult`; persistence is a caller concern.
- `extract_report(store, publication, *, document=None)` runs the right
  extractor and persists facts through `Store.rebuild_facts_for_document`,
  keeping re-runs idempotent (delete + insert, **including empty results**).
- Extraction is **gated on classification**: a publication classified as
  `monetary_policy_report` (authoritatively in the `classifications` table) is
  mined; anything else — and an absent classification — is refused
  (`REPORT_PUBLICATION_TYPES = ("monetary_policy_report",)`).

## ECB report extractor

`src/argus/reports/ecb.py` — `EcbReportsExtractor` (`extraction_version
10.0.0`). It answers *"what does the report explicitly state about the economy,
its risks and about monetary policy?"*.

Phase 10 is the most over-extraction-prone phase, so its cardinal rule is
**precision over recall**: a Fact is only produced from a known economic
section (conservative routing) + an explicit economic assertion with sufficient
identity (subject + predicate + value + unit + period when applicable) +
provenance. An unknown section — even one full of economic-looking sentences —
is never mined (`UNKNOWN ≠ ECONOMIC`).

### Conservative section routing — exact controlled headings

Section routing uses **exact / controlled normalized labels for both economic
and ignored sections** — there is no substring matching anywhere in the
routing. A section is mined only when its normalized heading is one of the
**controlled exact economic headings** below; every other heading is either a
**known non-economic heading** (exact membership in the controlled ignore
vocabulary → IGNORE) or an **unknown heading** (UNKNOWN), and neither is mined
("absence of proof → absence of extraction"; `UNKNOWN ≠ ECONOMIC`).

Ignored headings (exact normalized strings; every supported variant is listed
explicitly): the report title (`economic bulletin`, `monetary policy report`),
`foreword`, `editorial`, `contents`, `acknowledgements`, `abbreviations`,
`legal notice`, `disclaimer`, `copyright`, `imprint`, `glossary`,
`references`, `bibliography`, `appendix`, `technical appendix`, `statistics`,
`statistical annex`, `annex`, `methodology` and `note`. Analytical **boxes**
("Box 1 — …") are deliberately never mined: they are interpretive essays.

Heading normalization is **controlled**: lowercase, collapsed whitespace,
leading numbering ("2 Economic activity 1)"), footnote markers, a leading "the"
and trailing punctuation are stripped — nothing else. Matching is then
**exact**: a word or marker inside a heading is never a match. "Risk
management" is never read as `risk`, "Non-economic developments" never as
`economic`, "Fiscal institutions" never as `fiscal`, "Employment policy" never
as `employment`, "Output developments" never as `output`, "Financial
institutions" never as `financial`, "Core developments" never as `core`,
"Economic history" never as `economic`, and "annexation of …" / "legal
framework for monetary policy" are never `annex` / `legal notice` (all
regression-tested near-misses, each yielding 0 facts).

Mined economic headings (exact normalized strings):

- **policy**: `monetary policy developments`, `monetary policy`, `policy
  considerations`, `policy decisions`, `policy stance`, `monetary policy
  stance`;
- **risk**: `risk assessment`, `risks`, `risk`;
- **inflation**: `prices and costs`, `price developments`, `inflation`;
- **labour**: `labour market`, `labor market`, `employment`, `wages`, `wage
  developments`;
- **financial**: `financial developments`, `financial conditions`, `financial
  markets`, `money and credit`, `monetary and financial`, `financial system`;
- **growth**: `economic activity`, `real economy`, `growth`, `output`,
  `economic outlook`, `euro area economy`;
- **fiscal**: `fiscal developments`, `fiscal policy`, `fiscal`, `public
  finances`;
- **general**: `external environment`, `international environment`, `economic
  and monetary developments`, `economic analysis`, `overview`, `summary`,
  `executive summary`, `world economy`, `global economy`, `economic`.

### Content-first classification

Sentence classification is **content-first for every mined section** with a
fixed deterministic precedence: **guidance > policy > risk > financial >
inflation > labour > growth > fiscal**. The heading only gates *whether* the
section is mined, never *how* a sentence is classified. An unmatched sentence
produces no fact (reliability over coverage).

Content anchors are **context-specific markers, never bare generic tokens**
(the near-miss policy: a single lexical match is insufficient):

- `core` only fires as `core inflation` / `core hicp` ("core developments" is
  not an inflation signal);
- `output` only fires as `real output` / `industrial output` / `output growth`
  / `output gap(s)` / `potential output` ("output of financial institutions"
  is not a growth signal); `activity` fires as `(economic) activity`;
- `gdp` never fires for a near-miss measure: "GDP deflator", "GDP per capita"
  and "per capita GDP" are not GDP growth — they neither anchor a growth
  sentence nor leak a GDP value, even when a sentence also mentions real growth
  ("Real GDP growth held steady while the GDP deflator rose by 2.1%" yields no
  GDP fact);
- `lending` only as `bank lending` / `lending to …` / `lending rates|growth|
  conditions`; `spreads` only with an instrument (`yield|credit|sovereign|
  bond|rate spreads`); `transmission` only as `monetary policy transmission`;
  `funding` only as `funding conditions|costs|markets|constraints|gaps` or
  `bank funding`;
- policy requires a **monetary-specific term** (`monetary policy`,
  `interest rates`, `policy rates`, `governing council`, `eurosystem`,
  `policy instruments`, …) — bare `policy` / `rate` are never policy signals
  ("policy implementation" is not mined);
- risk requires a connector (`risks to / for / around / surrounding / from /
  of / are / were / remain …`), a directional qualifier (`downside / upside /
  two-sided / symmetric / broadly balanced risks`), `uncertainty` /
  `uncertainties` or `tilted` — a bare "risk management framework" is not a
  risk signal;
- `employment` / `wage(s)` exclude a following `policy` ("employment policy"
  is not a labour signal).

The result is deterministic, local and explainable: no fuzzy matching, no
embeddings, no semantic similarity, no LLM.

### Supported facts

| subject | predicate | value | source |
|---|---|---|---|
| `policy_guidance` | `statement` | text (verbatim) | forward guidance ("stood ready to adjust … within its mandate", "for as long as necessary", "would be guided by the incoming data", …) |
| `monetary_policy` | `statement` | text (verbatim) | policy sentences ("The Governing Council decided to keep its key interest rates unchanged") |
| `inflation` | `value` / `assessment` | percentage (+ period) / text | prices and costs / overview |
| `core_inflation` | `value` / `assessment` | percentage (+ period) / text | same |
| `inflation_expectations` | `assessment` | text (verbatim) | same |
| `growth` | `assessment` | text (verbatim) | economic activity |
| `gdp` | `value` | percentage (+ period) | quantitative growth claims ("projected to increase from 1.2% in 2026 to 1.4% in 2027") |
| `labour_market` | `assessment` | text (verbatim) | labour market |
| `unemployment` | `value` | percentage (+ period) | explicit unemployment claims |
| `wages` | `value` | percentage (+ period) | explicit wage-growth claims |
| `financial_conditions` | `value` / `assessment` | percentage (+ period) / text | financial developments ("bank lending rates … reached 4.2% in June 2026") |
| `fiscal_policy` | `assessment` | text (verbatim) | fiscal developments |
| `risk` | `assessment` | categorical or text | risk assessment |
| `inflation_risk` | `assessment` | categorical (upside/downside/balanced) or text | same |
| `growth_risk` | `assessment` | categorical (upside/downside/balanced) or text | same |

`fiscal_policy` is the Phase 10 addition to the controlled subject vocabulary.

### Quantitative values — explicit value claims only

A percentage becomes a Fact only from a sentence with an **explicit value
claim verb** ("projected / expected / forecast to average / stand at / be /
reach / grow by / increase from …", "stood at / averaged / reached / rose to /
fell to / remained at …"). So target phrasing ("converging towards 2%",
"the 2% target") is never read as a value, and "Inflation is 2.4% in 2025" (no
claim verb) yields nothing.

- **Units are explicit.** A value is kept as `percentage` only when the
  sentence states a percentage; **share/ratio units** ("% of GDP", "% of total",
  "% of disposable income") are **never** converted into percentage facts.
- **Periods come from the wording** (year, named month, "first/second/…
  quarter of <year>" → `year:` / `month:` / `quarter:`), never guessed from
  proximity. `Fact.effective_date` is always `None`.
- **A forecast value without an explicit reference period is ignored** —
  "Inflation is projected to average 2.4%." is under-determined, so no value
  (and no downgraded assessment) is produced. A non-forecast percentage without
  a stated period (e.g. "Inflation stood at 2.4%.") is kept without one.
- Basis points, index levels and currency/energy units are never percentages.

### Risk assessment

Identical to Phases 6–8: a categorical orientation fact (`upside` /
`downside` / `balanced`, with `two-sided` / `symmetric` normalized to
`balanced`) **only when an explicit orientation word is present**; otherwise a
verbatim text assessment ("risks remained elevated" is never forced into a
category). The risk target is read from the wording (`inflation` →
`inflation_risk`, `growth`/`activity`/`gdp` → `growth_risk`, otherwise
`risk`). Absence never becomes an invented orientation — it is surfaced as a
`no_risk_assessment` warning.

### Tables — variable × year × value × unit integrity

The extractor also reads economic data tables (same value-gate philosophy as
Phase 9):

- **columns** are years (`20xx` header cells);
- **rows** are variables, identified by **exact canonical label matching**
  (`_clean_row_label` normalizes the label, strips footnote markers and
  parentheticals, then matches verbatim): `hicp` / `hicp inflation` /
  `inflation` → `inflation`; `core inflation` / `hicp excluding energy and
  food` / `hicpx` → `core_inflation`; `real gdp` / `gdp growth` → `gdp`;
  `unemployment (rate)` → `unemployment`; `employment` → `labour_market`;
  `wages` / `wage growth` / `compensation per employee` → `wages`; near-misses
  never match;
- **the unit is read from the table's own caption** — share units
  (`% of GDP`, …) are rejected first, then percentage markers
  (`annual percentage changes`, whole-word `percent` / `per cent`, `% growth`,
  `(%)`, …), then incompatible units (`index`, `points`, `usd`, `eur`,
  `mwh`, `barrel`, …). A table with a missing, unknown or incompatible unit is
  ignored **as a whole**, and a unit never authorises another table's numbers;
- a cell becomes a Fact only when it is identified by a recognised variable +
  a year + an explicit percentage unit. Placeholder cells (`–`, `…`, `n.a.`)
  and unrecognised rows (private consumption, technical assumptions, …) are
  ignored.

### Deduplication

The same assertion stated twice (overview + detail section, prose + table,
two risk paragraphs with the same wording) is emitted **once**, within a run:
a quantitative duplicate is defined by subject + predicate + period + value; a
qualitative one by subject + predicate + period + normalized verbatim wording.
Sections are processed before tables, so a prose value claim is the provenance
that survives when the same value appears in a table.

### Provenance — no invented identities

Every Fact carries `source_location` (`LocationKind.SECTION` with the section
index, or `LocationKind.TABLE` with `table` / `row` / `column`), `source_text`
(the verbatim supporting sentence or the exact row of the source table),
`value.source_text` (the verbatim token/cell), `extraction_method`
(`regex` for prose, `table_extraction` for tables),
`extraction_version = 10.0.0`, `confidence`, `effective_date = None` and:

- **`speaker` is always `None`** — a monetary policy report is a collective
  institutional publication; "the Governing Council" is never turned into an
  individual;
- **`identity_qualifier`** — `report:{subject}:{ordinal}` (ordinal per
  subject + predicate + period), which keeps `fact_id`s unique across a
  document without inventing identity.

### Confidence

- `HIGH` — quantitative percentages and categorical risk orientations
  (explicit source wording);
- `MEDIUM` — verbatim qualitative assessments (sentence-level category
  identification).

### Warnings (fixed order)

`no_sections` (early return), `no_economic_sections` (no recognized economic
section was mined and no table produced a fact), `no_risk_assessment`,
`no_forward_guidance`.

### Not covered — Phase 10 boundaries

- **No interpretation**: hawkish/dovish, bullish/bearish, stance or market
  reaction, forex or trading logic — never.
- **The decision itself** (wording, rates, changes, effective date) stays
  Phase 5, gated on decision publications; the report's *narrative* of policy
  is kept verbatim (`monetary_policy / statement`), never priced, and
  "unchanged" is never recast as a categorical outcome.
- **The structured economic projections tables** stay Phase 9, gated on
  `economic_projections`; prose forecasts inside a report are kept as value
  claims only when they carry an explicit reference period.
- **Phases 11** (speeches) — not this layer.
- **No LLM** (invariant 8); an absent optional section (no risk assessment, no
  forward guidance) never becomes an invented fact, only a warning.

### Phase 5 / 6 / 7 / 8 / 9 / 10 boundary

The six extractors are disjoint by **publication type** (classification
gating): decisions, statements, press conferences, minutes, economic
projections and monetary policy reports are never cross-mined.
`get_extractor` dispatches on `central_bank`, and each extractor refuses
publications whose authoritative classification is not its own type.

## Golden tests

`tests/fixtures/documents/ecb_report*.html` (modeled on the ECB Economic
Bulletin layout: title, foreword, external environment, economic activity,
prices and costs, financial developments, fiscal developments, monetary policy
developments, risk assessment, boxes, data tables, legal notice):

- `ecb_report.html` — the full narrative fixture: growth assessments, GDP
  values (quarter + year periods), inflation / core inflation /
  inflation_expectations, financial conditions (value + assessment),
  fiscal policy, a monetary policy statement, forward guidance and risk
  orientations (17 facts, no warnings);
- `ecb_report_tables.html` — prose value claims plus an "annual percentage
  changes" data table (HICP / core HICP / Real GDP / unemployment / wages ×
  2024–2026), a unit-less scenario table and a "% of GDP" table (16 facts,
  `no_risk_assessment` + `no_forward_guidance`; the unit of one table never
  authorises another, and a prose value deduplicates with the identical table
  value);
- `ecb_report_risks.html` — inflation value + balanced / upside / downside
  risk orientations + a verbatim "elevated" risk assessment (5 facts,
  `no_forward_guidance`);
- `ecb_report_unknown.html` — economic-looking content under unknown /
  appendix / methodology headings → 0 facts + all three warnings
  (`UNKNOWN ≠ ECONOMIC`);
- `ecb_report_minimal.html` — a single inflation value (1 fact,
  `no_risk_assessment` + `no_forward_guidance`).

`tests/test_reports.py` runs the normalizer → extractor → store slice and
asserts, per fixture:

- the exact expected facts (per-subject value sets with periods, and verbatim
  texts) with warnings;
- conservative routing (known economic headings mined, non-economic / unknown
  headings and analytical boxes ignored — including economic content under an
  unknown heading → 0 facts), content-first precedence (guidance > policy >
  risk > financial > inflation > labour > growth > fiscal);
- Phase 10 hardening near-misses: exact heading routing (the nine near-miss
  headings — `Non-financial developments`, `Non-economic developments`,
  `Financial institutions`, `Core developments`, `Output developments`,
  `Risk management`, `Fiscal institutions`, `Employment policy`, `Economic
  history` — each yield 0 facts while their legitimate counterparts are still
  mined; heading normalization covers case, numbering, punctuation and a
  leading "the") and context-specific content anchors (per-category near-miss
  sentences yield 0 facts while the contextual positives are mined with the
  correct subject, predicate, value and source_text);
- Phase 10 final hardening: the **IGNORE routing is exact too** — every
  controlled non-economic heading (including normalized variants such as
  "3. LEGAL NOTICE.") yields 0 facts, headings that merely share a word with
  an ignore heading ("Legal framework for monetary policy", "Annexation of
  financial conditions", "Statistical outlook") are never classified by
  substring — they fall to UNKNOWN (0 facts, `UNKNOWN ≠ ECONOMIC`), and
  economic headings that share words with an ignore heading ("Monetary policy
  developments" vs `monetary policy report`, "Economic activity" vs `economic
  bulletin`) are still mined;
- the value gate: explicit claim verbs only, share units ("% of GDP") never
  percentages, basis points never percentages, forecasts without a period
  ignored, periods from wording (year / month / quarter);
- table extraction: row × column × year × value × unit integrity, unit from the
  table's own caption, unit leakage prevented, unrecognised rows and
  placeholder cells ignored;
- within-run deduplication (repeated assertion across sections → one fact;
  identical prose + table value → one fact);
- no invented facts — no Phase 5/6/7/8/9 subjects, `speaker` always `None`,
  no hawkish/dovish / stance / forex interpretation;
- verbatim provenance: each `fact.source_text` and `fact.value.source_text` is
  a substring of the referenced section/row;
- deterministic extraction and idempotent Store persistence (including
  empty-result persistence and the `monetary_policy_report` gating),
  classification gating and Phase 5/6/7/8/9 coexistence.
# Type-Specific Extractors — Phase 11 (Speeches / Remarks / Address)

## Pipeline

```
NormalizedDocument                          ← Phase 2
    ↓  SpeechExtractor.extract(publication, document)
ExtractionResult(publication_id, document_id, facts, warnings)
    ↓  Store.rebuild_facts_for_document (delete + insert, idempotent)
facts table
```

- a classified **speech** publication (`speech`)
- its normalized document(s)
- the ECB speech extractor producing structured **Facts**
- an `ExtractionResult` handed to the existing `Store`

A speech is the **individual** communication of one central bank official
(remarks / address / keynote). Its cardinal rule, like Phase 10, is
**precision over recall**: a Fact is only produced from an explicit economic
assertion with sufficient identity (subject + predicate + value + unit + period
when applicable) + provenance, and the speaker's own words are never confused
with personal anecdote, biography, ceremonial thanks, historical narrative or
quoted authors. Interviews (`interview`) are a separate publication type with
their own treatment — out of Phase 11 scope (`SPEECH_PUBLICATION_TYPES =
("speech",)`).

## Extractor contract

`src/argus/speeches/base.py`:

```python
class SpeechExtractor(ABC):
    bank: str
    extraction_version: str
    def extract(self, publication, document) -> ExtractionResult: ...
```

- One extractor per bank; the generic code only dispatches on `central_bank`.
- Extractors are **pure**: they read the normalized document and return an
  `ExtractionResult`; persistence is a caller concern.
- `extract_speech(store, publication, *, document=None)` runs the right
  extractor and persists facts through `Store.rebuild_facts_for_document`,
  keeping re-runs idempotent (delete + insert, **including empty results**).
- Extraction is **gated on classification**: a publication classified as
  `speech` (authoritatively in the `classifications` table) is mined; anything
  else — and an absent classification — is refused. The denormalized
  `publication.publication_type` cache never authorizes extraction.

## ECB speech extractor

`src/argus/speeches/ecb.py` — `EcbSpeechExtractor` (`extraction_version
11.0.0`). It answers *"what does this official explicitly state about the
economy, its risks and about monetary policy?"*.

### Speaker attribution — explicit only

A speech is an individual communication, so the speaker is preserved verbatim
in `Fact.speaker` **only when the source states one**:

- a `Speaker: <label>` line in the document body wins over an explicit author
  field in the document metadata (`author` / `dc.creator`), which is the
  fallback;
- the speaker is **never inferred**: "the President", "the ECB", "the Governing
  Council" are never turned into a person, and a name in the prose ("Christine
  Lagarde was born in Paris", "Christine Lagarde said …") is never read as the
  speaker;
- a sentence framed as a **quotation of another person** ("As John Maynard
  Keynes once put it, …", "according to X", "X said that …", "quoting X") is
  never mined and never attributed to the speech's speaker — the document is
  flagged `quoted_content_skipped`. The speaker quoting their own past words
  ("As I said a year ago, …", "As Christine Lagarde has said, …") is never
  treated as a quotation of another person;
- **metadata isolation**: the title, subtitle, event, conference, location and
  author metadata never create facts — they only ever feed the provenance
  attribution above.

### Conservative section routing — exact controlled headings

Routing uses **exact / controlled normalized labels**, same normalization as
Phase 10 (lowercase, collapsed whitespace, stripped numbering / footnote
markers / leading "the" / trailing punctuation; matching is exact, never
substring). A heading is one of three categories:

- **`CAT_ECONOMIC`** — a known economic heading, mined in full (content-first
  sentence classification): `monetary policy`, `monetary policy stance`,
  `monetary policy transmission`, `policy considerations`, `policy stance`,
  `risk assessment`, `risks`, `risk`, `inflation`, `prices and costs`, `price
  developments`, `price stability`, `labour market`, `labor market`,
  `employment`, `wages`, `wage developments`, `financial stability`,
  `financial conditions`, `financial developments`, `financial markets`,
  `money and credit`, `monetary and financial`, `financial system`,
  `economic outlook`, `economic activity`, `real economy`, `growth`,
  `economic growth`, `output`, `euro area economy`, `external environment`,
  `international environment`, `economic and monetary developments`,
  `economic analysis`, `overview`, `summary`, `executive summary`, `world
  economy`, `global economy`, `economic`;
- **`CAT_IGNORE`** — a known non-economic heading, skipped entirely: speech
  mastheads (`speech`, `speech by`, `remarks`, `address`, `keynote speech`,
  `keynote address`), `about the speaker`, `speaker biography`, `biography`,
  `biographical note`, `acknowledgements`, `thanks`, `thank you`, `closing
  remarks`, `concluding remarks`, `closing`, the **Q&A** of a speech document
  (`questions and answers`, `questions`, `question`, `answers`, `q&a`,
  `questions from`), `references`, `bibliography`, `further reading`, `notes`,
  `endnotes`, `annex`, `appendix`, `legal notice`, `disclaimer`, `copyright`,
  `imprint`, `glossary`, and every analytical **box** ("Box N — …");
- **`CAT_UNKNOWN`** — any other heading, **strictly mined**: only explicit
  assertions pass — a quantitative value claim, a categorical risk orientation,
  a guidance or a policy sentence. A bare qualitative assessment, a personal
  anecdote, biography, ceremonial thanks, history without explicit values and
  quoted authors are never facts. `UNKNOWN` sections are therefore still a
  source of explicit facts (unlike Phase 10), but strictness keeps precision:
  an assertion is never *assumed*.

Heading-less sections (title masthead, publication date) are `CAT_IGNORE`.

### Content-first classification

Sentence classification is content-first with a fixed deterministic precedence:
**guidance > policy > risk > financial > inflation > labour > growth**. The
heading only gates *whether* the section is mined in full; an unmatched
sentence produces no fact.

- **guidance** — forward-looking commitment anchors ("stands ready to", "would
  be prepared to", "for as long as necessary", "will keep interest rates",
  "data-dependent", "meeting by meeting", "future policy decisions", "policy
  path", "will continue to monitor/assess", …) → `policy_guidance / statement`;
- **policy** — a compound signal (a stance word **and** a policy term: "the
  Governing Council decided to …", "the monetary policy stance remained
  appropriately calibrated") or an unambiguous stance phrase → `monetary_policy
  / statement`. A bare token ("policy", "rate") is never a policy signal, and a
  neutral sentence ("The Governing Council will assess the outlook at its next
  meeting.") produces no fact;
- **risk** — `_RISK_ANCHORS` ("risks to/for/around", "downside/upside risks",
  "tilted", "uncertainty") → categorical orientation (`upside` / `downside` /
  `balanced`, `HIGH`) when an explicit orientation word is present, otherwise a
  verbatim text assessment (`MEDIUM`) in known economic sections only; the
  target (`inflation_risk` / `growth_risk` / `risk`) is read from the wording;
- **financial / inflation / labour / growth** — context-specific anchors,
  never bare tokens, resolving to the Phase 10 subject vocabulary: inflation
  (`inflation` / `core_inflation` / `inflation_expectations`), growth
  (qualitative `growth` / quantitative `gdp`), labour market
  (`unemployment` / `wages` / `labour_market`), `financial_conditions`.
  The Phase 10 GDP near-miss policy applies identically: "GDP deflator",
  "GDP per capita" and "per capita GDP" never anchor a growth sentence and
  never leak a GDP value ("Real GDP growth held steady while the GDP
  deflator rose by 2.1%" yields no fact).
  Phase 11 hardening keeps the generic vocabulary out of the anchors: `credit`
  fires only as a contextual credit-conditions marker ("credit growth", "credit
  standards", …), `lending` as `bank lending` / `lending to …` / `lending
  rates|growth|conditions|standards`, `demand` only as a qualified demand
  (domestic / aggregate /
  global / external / private / overall / total), `production` only as
  sector-specific production (industrial / manufacturing / energy / oil /
  steel / automotive), bare `output` is a growth marker, and `recovery`,
  `recession`, `slowdown` and `expansion` were removed entirely.

### Qualitative fact gate — Phase 11 hardening

An anchor only *selects* a candidate sentence; a qualitative fact also requires
an **explicit economic assertion** (`_is_economic_assertion`). An anchor + a
generic assertion verb is **never sufficient** — the predicate must actually
describe the economic subject. The gate is layered and each layer is
deterministic, lexical and explainable:

1. **assertion signal** — a change/state verb ("increased", "weakened",
   "accelerated", "remained", "continues", …) or an expected / projected /
   estimated / forecast / likely-to construction must be present;
2. **platitude rejection** — copular rhetoric is always rejected: "X is
   important / essential / central", "X matters", "X is a priority", "X remains
   a key priority", "X remains an important challenge", "X continues to be
   central to our mandate", "X is an important part of our mandate";
3. **transitive-object rejection** — a change verb acting on a possessive object
   ("improved our understanding", "expanded our role", "tightened our
   procedures") describes an institutional action, not an economic state;
4. **possessor rejection** — a change verb whose subject is an "of <anchor>"
   possessor ("our understanding of the economy improved") describes the
   possessor, not the economy.

Economic vocabulary alone ("the economy", "credit", "investment",
"consumption", "growth") is never enough, and a state/property assertion that
really qualifies the economic subject is preserved: "Inflation remains
elevated", "Credit conditions remain tight", "Economic activity remains
subdued", "Economic activity continues to weaken", "The output gap narrowed",
"Credit spreads widened", "Growth picked up", "Employment gained momentum". The
gate applies to the qualitative assessment paths (growth, inflation, labour,
financial conditions) and to the verbatim risk fallback; guidance and policy
statements — already precise compound signals — are not gated, and a value
claim or a categorical risk orientation passes regardless. Unknown sections
remain stricter still (no qualitative fact at all).

### Value gate

Same value gate as Phase 10: explicit value claim verbs only ("projected /
expected / forecast to average / stand at / reach / …"), a percentage is only
kept with an explicit reference period (year / month / quarter from the
wording), a forecast without a period is under-determined and ignored, share
units ("% of GDP", "% of total") are never percentages, and basis points are
never extracted. Percentage tokens cover `%`, "per cent" and "percent".

### Deduplication

The same assertion stated twice is emitted **once** within a run: a
quantitative duplicate is subject + predicate + period + value; a qualitative
one is subject + predicate + period + normalized verbatim wording.

### Provenance — individual identity preserved

Every Fact carries `source_location` (`LocationKind.SECTION` with the section
index), `source_text` (the verbatim supporting sentence), `value.source_text`
(the verbatim token), `extraction_method = regex`, `extraction_version =
11.0.0`, `effective_date = None`, `confidence` and:

- **`speaker`** — the verbatim speaker label, when the source states one, on
  **every** Fact of the speech (a speech is never mistaken for a collective
  decision);
- **`identity_qualifier`** — `speech:{subject}:{ordinal}` (ordinal per subject
  + predicate + period), keeping `fact_id`s unique across a document without
  inventing identity.

### Confidence

- `HIGH` — quantitative percentages and categorical risk orientations;
- `MEDIUM` — verbatim qualitative assessments and policy/guidance statements.

### Warnings (fixed order)

`no_sections` (early return), `no_risk_assessment`, `no_forward_guidance`,
`quoted_content_skipped`.

### Not covered — Phase 11 boundaries

- **No interpretation**: hawkish/dovish, bullish/bearish, market sentiment,
  forex or trading logic — never.
- **Policy decisions** (wording, rates, changes, votes, effective date) stay
  Phases 5/8, gated on their own publication types; a speech's *narrative* of
  policy is kept verbatim (`monetary_policy / statement`), never priced.
- **The Q&A of a speech document** (journalist content) is skipped; the
  press-conference Q&A is Phase 7.
- **Fiscal analysis** stays Phase 10, **structured projections tables** stay
  Phases 9/10 — a speech is mined for prose assertions only, and no Phase 5–10
  subject is ever emitted here.
- **No LLM** (invariant 8); an absent optional section (no risk assessment, no
  forward guidance) never becomes an invented fact, only a warning.

### Phase 5 / 6 / 7 / 8 / 9 / 10 / 11 boundary

The seven extractors are disjoint by **publication type** (classification
gating): decisions, statements, press conferences, minutes, economic
projections, monetary policy reports and speeches are never cross-mined.
`get_extractor` dispatches on `central_bank`, and each extractor refuses
publications whose authoritative classification is not its own type.

### Phase 12 — extraction vs temporal analysis boundary

Phase 12 (`src/argus/changes/`) is **not** an extractor: it never creates
Facts, never reads source documents, and never invents content. It is a
strictly **descriptive** analytic layer that compares **existing Facts** over
time (previous → current) and records *that* an observation changed — never
*what* the change means. Because the two layers are separate, no `Fact` is
ever mutated by the analysis, and every `FactChange` keeps the identities of
both source Facts (with their documents, publications, periods and
`effective_date`) so the extraction provenance of both sides stays intact.
The exact matching rules are documented in `docs/CHANGES.md`.

## Golden tests

`tests/fixtures/documents/ecb_speech*.html` (modeled on the ECB Speech layout:
title masthead, `Speaker:` line, introduction, economic outlook, inflation,
labour market, financial stability, monetary policy, risks, closing remarks):

- `ecb_speech.html` — the full narrative fixture with a body `Speaker:
  Christine Lagarde` line (meta author "Isabel Schnabel" is overridden by the
  body label): growth assessment, GDP values, inflation / core inflation /
  inflation_expectations, unemployment / wages, financial conditions
  (assessment + value), a monetary policy statement, forward guidance and
  balanced / upside risk orientations (16 facts, no warnings, every fact
  carries `speaker = "Christine Lagarde"`);
- `ecb_speech_unknown.html` — economic content under **unknown headings**:
  explicit value + risk-orientation + GDP facts pass, a bare qualitative
  assessment does not, the Keynes quotation is skipped (3 facts,
  `no_forward_guidance` + `quoted_content_skipped`; speaker falls back to the
  metadata author);
- `ecb_speech_personal.html` — biography, personal anecdote, history without
  explicit values, thanks and closing remarks → 0 facts (`no_risk_assessment` +
  `no_forward_guidance`);
- `ecb_speech_minimal.html` — a single inflation value, no speaker (1 fact,
  `no_risk_assessment` + `no_forward_guidance`, `speaker = None`);
- `ecb_speech_adversarial.html` — Phase 11 hardening fixture: explicit
  assertions and value claims pass (GDP 2.4 / 2027 inflation 2.1 / financial
  conditions 3.0, growth assessments, wages assessment, a guidance statement
  and a downside risk orientation — 8 facts) while platitudes ("The economy is
  important …", "Investment is essential …", "The economy matters."), bare
  topic mentions ("Credit is important.", "Inflation is important.", "There are
  risks."), the removed generic anchors ("The recovery of trust is important.",
  "Recession is important.", "The expansion …", "The production of goods …"),
  anecdote and the Keynes quotation all yield nothing (`quoted_content_skipped`).

`tests/test_speeches.py` runs the normalizer → extractor → store slice and
asserts, per fixture:

- the exact expected facts (per-subject value sets with periods, verbatim texts
  and orientations) with warnings;
- conservative routing (known economic headings mined, known non-economic
  headings and Q&A ignored, unknown headings strictly mined, boxes and
  heading-less sections never mined), exact-identity heading matching and
  normalization (case / numbering / punctuation / leading "the");
- speaker attribution: body `Speaker:` line wins over metadata author, metadata
  author is the fallback, the speaker is never inferred (even when a name
  appears in the prose), labels are preserved verbatim, quoted authors are
  skipped and never attributed to the speaker, self-quotations are not flagged;
- content-first precedence (guidance > policy > risk > financial > inflation >
  labour > growth), the policy compound signal, and neutral sentences producing
  no fact;
- the value gate (explicit claim verbs, periods from wording, forecast without
  a period ignored, share units and basis points never percentages), categorical
  risk orientations only when explicit (verbatim otherwise, in known sections
  only);
- within-run deduplication, verbatim provenance (`speaker` preserved, `speech:`
  identity qualifiers, `effective_date` always `None`), no Phase 5–10 subjects,
  no hawkish/dovish interpretation;
- the Phase 11 hardening matrix: platitudes and topic mentions rejected, generic
  anchors removed, preserved assertions still extracted, guidance/policy
  ungated, contextual `credit`, known and unknown sections both precise, and no
  period contamination across sentences;
- deterministic extraction and idempotent Store persistence (including
  empty-result persistence and the `speech` gating), classification gating and
  Phase 5/6/7/8/9/10 coexistence.

---

# Multi-Bank Extractor Coverage Summary (Phase 4 Hardening Pass)

This table summarizes the **actual implementation state** after the Phase 4
hardening pass (commit 96e81de → hardened). The key distinction is:

| State | Meaning |
|-------|---------|
| **implemented** | Extractor class exists, registered in generic dispatch, fixtures exist, integration tests pass |
| **not implemented** | No extractor class exists for this bank/publication type |
| **not applicable** | Bank does not publish this publication type (per taxonomy) |
| **intentionally represented by another type** | Bank fuses this content into a different publication type |

## Coverage Table

| Bank | Decision | Statement | Minutes | Projections | Report/Mixed | Real Corpus |
|------|----------|-----------|---------|-------------|--------------|-------------|
| ECB | implemented | implemented | implemented (meeting_account) | implemented | implemented (Economic Bulletin) | ✅ (multiple fixtures) |
| Fed | implemented | implemented | implemented (minutes) | implemented (SEP) | not applicable | ✅ (fixtures) |
| BoE | implemented | implemented | not implemented | not applicable | not implemented | ⚠️ (fixtures only) |
| BoJ | intentionally represented by Statement | implemented | not implemented | not implemented | not applicable | ⚠️ (fixtures only) |
| SNB | implemented | implemented | not applicable | not applicable | not applicable | ⚠️ (fixtures only) |
| BoC | implemented | implemented | not applicable | not applicable | not implemented | ⚠️ (fixtures only) |
| RBA | implemented | implemented | not applicable | not applicable | not implemented | ⚠️ (fixtures only) |
| RBNZ | implemented | implemented | not applicable | not applicable | not implemented | ⚠️ (fixtures only) |
| Norges | implemented | not applicable | not implemented | not applicable | implemented (Monetary Policy Report + mixed) | ⚠️ (fixtures only) |
| Riksbank | implemented | implemented | not implemented | not applicable | not applicable | ⚠️ (fixtures only) |

### BoJ Decision Coverage — Explicit Resolution

**Status**: `intentionally represented by another extractor`

**Why**: The Bank of Japan fuses its monetary policy decision and statement into a single publication type: `monetary_policy_statement` (the "Statement on Monetary Policy"). The classification taxonomy (`src/argus/classification/bank_rules.py`) has no `monetary_policy_decision` rule for BoJ — only `monetary_policy_statement`, `economic_projections`, and `minutes`.

**Authoritative publication type**: `monetary_policy_statement`

**Facts extracted by BoJ Statement extractor** (`src/argus/statements/boj.py`):
- Decision date
- Short-term policy target (uncollateralized overnight call rate "formed at around X percent")
- Decision wording + explicit vote sentence ("At the Monetary Policy Meeting held today, the Policy Board decided to …", "The vote was …")
- Forward guidance (e.g., "will continue with monetary easing … as long as necessary")
- Price/growth/risk assessment (quantitative value claims with reference periods, categorical risk orientations)

**Not extracted** (by design, Phase 6 boundary):
- No separate `monetary_policy_decision` subject facts are fabricated beyond the vote/decision wording above
- Outlook for Economic Activity and Prices (projections) → Phase 9
- Individual member opinions / dissents beyond verbatim vote sentence → Phase 8

### Implementation vs. Coverage Distinction

| Coverage Type | Meaning |
|---------------|---------|
| **Implementation coverage** | Extractor class exists + registered + integration tests pass |
| **Fixture coverage** | Local HTML fixtures exist for testing |
| **Real corpus coverage** | Extractor validated against real historical documents |
| **Historical validation** | End-to-end pipeline tested on multi-year real data |

**Current state**: All 10 banks have **implementation coverage** for their applicable publication types. **Fixture coverage** exists for all implemented extractors (golden tests). **Real corpus coverage** is substantial for ECB (multiple decision/statement/minutes/projection/report fixtures modeled on real documents); other banks have fixture coverage only. **Historical validation** (Phase 16) remains `NOT STARTED`.

### Registry Integration Verified

Generic dispatch (`get_extractor(bank)`) works for all implemented extractors:

```python
# Decisions (9 banks)
from argus.decisions import get_extractor as get_decision
assert get_decision("ecb").__class__.__name__ == "EcbDecisionExtractor"
# ... fed, boe, boc, snb, rba, rbnz, riksbank, norges
assert get_decision("boj") is None  # intentionally no decision type

# Statements (9 banks)
from argus.statements import get_extractor as get_statement
assert get_statement("boj").__class__.__name__ == "BojStatementExtractor"
# ... ecb, fed, boe, boc, snb, rba, rbnz, riksbank
assert get_statement("norges") is None  # no statement type

# Minutes (2 representative banks)
from argus.minutes import get_extractor as get_minutes
assert get_minutes("ecb").__class__.__name__ == "EcbMinutesExtractor"
assert get_minutes("fed").__class__.__name__ == "FedMinutesExtractor"

# Projections (2 representative banks)
from argus.projections import get_extractor as get_projections
assert get_projections("ecb").__class__.__name__ == "EcbProjectionsExtractor"
assert get_projections("fed").__class__.__name__ == "FedSepExtractor"

# Reports (2 representative banks)
from argus.reports import get_extractor as get_reports
assert get_reports("ecb").__class__.__name__ == "EcbReportsExtractor"
assert get_reports("norges").__class__.__name__ == "NorgesReportExtractor"
```

### Test Results (Hardening Pass)

- **All tests pass**: 957 tests × 3 runs = deterministic
- **compileall**: clean
- **New integration tests added**:
  - Decision generic dispatch: 9 banks × 1 fixture each
  - Statement generic dispatch: 9 banks × 1 fixture each
  - Minutes generic dispatch: 2 banks × 1 fixture each
  - Projections generic dispatch: 2 banks × 1 fixture each
  - Reports generic dispatch: 2 banks × 1 fixture each
- **No regressions**: All existing golden tests, gating tests, idempotence tests, phase coexistence tests pass

### Phase 16 Status

Phase 16 (Historical Validation) remains **DEFERRED** — this hardening pass does not mark it complete or started.
