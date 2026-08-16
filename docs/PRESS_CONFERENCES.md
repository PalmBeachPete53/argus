# Argus — Press Conference Extraction Contract (Phase 4.x)

> **Phase boundary.** This document describes the **Phase 4.x — Multi-Bank
> Press Conference Extraction Extension** of the **Phase 4 — Fact Extraction**
> layer. It is **not** a new standalone phase: Argus is not gaining a
> "Phase 10/11", and no existing phase is renumbered. Press conference is a
> **publication type** (`press_conference`), not a `Fact`. This work extends the
> Phase 4.3 press-conference family (ECB, the reference implementation) with the
> **Fed** press conference extractor. It is document-to-`Fact` extraction that
> lives entirely **upstream** of the Phase 5+ derived layers
> (`FactChange` → `PolicyReaction` → `MonetaryPolicyState` →
> `ForexFundamentals`). This work ends at canonical `Fact`s.

```
Official publications
        ↓ Classification (classifications table = single source of truth)
        ↓ Phase 4 Fact Extraction (Press Conference extractor family)
        ↓ canonical Facts
        ↓ Phase 5+ (separate, downstream, never touched here)
```

Phase 9 — Historical Validation — remains **DEFERRED**. Nothing here adds a
synthetic historical corpus or claims real historical coverage.

---

## 1. Scope

Argus extracts **observable factual claims** from official central-bank press
conference transcripts. A press conference transcript differs from a
decision/statement: it mixes the collective **introductory statement / opening
remarks** with the **individual answers** of officials to journalists'
questions. The Phase 4.3 extractors therefore keep, per `Fact`, the attribution
context (remarks vs Q&A answer, the Q&A turn, and the verbatim official speaker
when the document labels one) in `identity_qualifier` and the `Fact.speaker`
attribute — an individual's words are never presented as a collective decision
(roadmap Phase 4.3 criterion).

The **ECB press conference extractor** (`src/argus/press_conferences/ecb.py`,
`EcbPressConferenceExtractor` v7.0.0) is the **reference implementation** and is
**left untouched**. It answers *"what did the President and the Vice-President
explicitly state about the economy and their policy stance during the press
conference?"*.

This extension adds the **Fed** press conference extractor
(`src/argus/press_conferences/fed.py`, `FedPressConferenceExtractor`). It
answers *"what does the Chair (or other Fed officials) explicitly state about
the economy and their policy stance during the FOMC press conference?"*,
caps N official facts from the Fed transcript, and never mines the
journalists' questions.

It also adds the **BoE** press conference extractor
(`src/argus/press_conferences/boe.py`, `BoEPressConferenceExtractor`). It
answers *"what do the MPC members (Governor and Deputy Governors) explicitly
state about the economy and their policy stance during the MPR press
conference?"*, preserves each official's verbatim name label, and never mines
the journalists' questions.

## 2. Epistemic boundary

Press conference extraction MUST **never** infer, emit, or label:

- hawkish / dovish stance;
- sentiment;
- market expectations;
- causal relationships;
- trading signals;
- conviction / importance;
- surprise;
- rate path (a verbatim guidance sentence is kept verbatim, never converted
  into a "rate hike expected" fact);
- implied policy reaction.

A press conference is **evidence**, not interpreted policy. It is never
converted into a Phase 6 reaction, a Phase 7 state dimension, or a Phase 8
forex fundamental. The boundary is hard:

```
Publication / Document
        ↓ Press Conference extractor
        ↓ canonical Fact
        ↑ END OF THIS WORK
```

Precision over recall: prefer **no Fact** over an **invented Fact**.

## 3. Fed transcript structure

Fed press conference transcripts are published by the Federal Reserve Board
under two URL shapes:

- the **transcript PDF**: `https://www.federalreserve.gov/mediacenter/files/
  FOMCpresconf<date>.pdf` — e.g. `FOMCpresconf20260617.pdf`;
- the **event page**: `https://www.federalreserve.gov/newsevents/
  pressconferences/fomc-press-conference-<date>.htm`.

The transcript body is a **turn-based dialog**: each turn is a plain ALL-CAPS
speaker label followed by that speaker's words. There are **no**
`Question:` / `Answer:` colon markers as in the ECB transcript; the Fed
transcript is:

```
CHAIRMAN WARSH.
Good afternoon, everyone. Before turning to your questions…
(unattributed paragraph continues the same speaker's turn)
CHRIS RUGABER.
Mr. Chairman, is the Committee still considering another rise in the federal
funds rate this year?
CHAIRMAN WARSH.
We will decide meeting by meeting as new data arrive.
VICE CHAIR DONALD LERNER.
Let me add that inflation expectations remain well anchored.
```

Speaker roles are read from the label itself (see `§4`). The first Fed-official
turn before any journalist turn is the **opening remarks** (collective FOMC
communication, `Fact.speaker = None`); every Fed-official turn after a
journalist's label is a **Q&A answer** (attributed). The Q&A turn counter
increments at each non-Fed ALL-CAPS label.

## 4. Speaker attribution — explicit only

Fed press conference labels identify the speaker by **role word + name**:

- Fed officials: `CHAIRMAN` / `CHAIRWOMAN` / `CHAIR` / `VICE CHAIR` /
  `GOVERNOR` / `PRESIDENT` followed by a name (e.g. `CHAIRMAN WARSH`,
  `VICE CHAIR DONALD LERNER`, `GOVERNOR ADRIANA MONTES`).
- Journalists / moderators: any other ALL-CAPS name label (`MICHELLE SMITH`,
  `CHRIS RUGABER`, `STEVE LIESMAN`, …).

Rules:

- A Fed-official label preserves its **verbatim** label (ALL-CAPS, trailing
  punctuation stripped) in `Fact.speaker`. It is **never inferred** and never
  reformatted: `"CHAIRMAN WARSH"` stays `"CHAIRMAN WARSH"`.
- A journalist label is **never** mined and **never** attributed: the turn
  content after a journalist label is the questioner's words, and `speaker`
  stays `None` for anything the Fed official then says unless that official's
  own label appears on the answer.
- An ambiguous label (`MR. POWELL.`, `MS. YELLEN.`, a `Mr.`/`Ms.`-form label)
  is treated as **non-Fed**: it is a conservative identity, the turn is treated
  as a journalist boundary, and no speaker is invented.
- `speaker = None` is always used for remarks facts (collective FOMC
  communication — never attributed to an individual chair).

Attribution context is carried deterministically in `identity_qualifier`
mirroring the Phase 4.3 ECB contract:

- `remarks:{n}` for remarks facts (`n` = ordinal within the remarks);
- `answer:{turn}:{n}` for Q&A answer facts (`turn` = 1-based Q&A turn, `n` =
  per-turn ordinal).

This is what distinguishes *"what the FOMC communicated"* (remarks) from
*"what one official said to a journalist"* (individual attribution) — the Phase 4.3 validation criterion.

## 5. Classification

Fed press-conference URLs do **not** match the generic `press[_-]conference`
URL rule:

- `…/FOMCpresconf20260617.pdf` → `presconf` ≠ `press[_-]conference`;
- `…/pressconferences/pressconf20260617.htm` (older event URL) → same gap;
- `…/pressconferences/fomc-press-conference-20260617.htm` **does** already
  match the generic rule;
- the title "Transcript of Mr. Chairman Warsh's Press Conference" **does**
  already match the generic title rule.

A Fed-specific `press_conference` TypeRule is therefore added in
`src/argus/classification/bank_rules.py` with Fed-scoped patterns:

- `url=(r"presconf", r"press-?conference")` — `presconf` closes the FOMC
  transcript PDF and the older event-URL gap; `press-?conference` keeps the
  `fomc-press-conference-<date>` shape classified even if the generic rule
  order changes;
- banned-classification safety verified: `presconf` never collides with Fed
  statement (`pressreleases/monetary\d{8}`), minutes (`fomcminutes`),
  projections (`fomcprojtabl`), speeches (`/newsevents/speeches/`) or calendar
  (`fomccalendars`) URLs.

Extraction stays **gated on classification**: the `classifications` table is
the single source of truth, `PRESS_CONFERENCE_PUBLICATION_TYPE =
"press_conference"`, and the Fed extractor refuses any publication whose
authoritative classification is not `press_conference`.

## 6. Supported facts

The Fed extractor emits the same canonical subjects / predicates as the ECB
Phase 4.3 extractor:

| subject | predicate | value | source |
|---|---|---|---|
| `monetary_policy` | `statement` | text (verbatim) | remarks — policy sentences |
| `policy_guidance` | `statement` | text (verbatim) | explicit prospective policy statements (Fed vernacular in `§7`) |
| `inflation` / `core_inflation` | `value` / `assessment` | percentage (+ period) / text | inflation statements |
| `inflation_expectations` | `assessment` | text (verbatim) | same |
| `growth` | `assessment` | text (verbatim) | growth / activity statements |
| `gdp` | `value` | percentage (+ period) | quantitative growth claims |
| `labour_market` | `assessment` | text (verbatim) | labour market statements |
| `unemployment` | `value` | percentage (+ period) | explicit unemployment claims |
| `wages` | `value` | percentage (+ period) | explicit wage-growth claims |
| `financial_conditions` | `assessment` | text (verbatim) | financial conditions statements |
| `risk` | `assessment` | categorical or text | risk statements |
| `inflation_risk` | `assessment` | categorical (upside/downside/balanced) or text | same |
| `growth_risk` | `assessment` | categorical (upside/downside/balanced) or text | same |

Content is classified sentence-by-sentence with the same deterministic
precedence as Phase 4.3 (guidance > policy > risk > financial > inflation >
labour > growth). An unmatched sentence produces no fact (reliability over
coverage). No `rationale`, no `change`, no decision facts, no other phase's
subjects.

Value facts follow the identical gate to Phase 4.2/7/8/11: a percentage becomes
a `Fact` only behind an **explicit value-claim** verb ("projected / expected /
forecast to average / stand at / be …", "stood at …"), so "the 2 percent
target" / "close to 2 percent" is never read as a value; a percentage with an
explicit reference period (year / month / quarter from the wording) keeps a
`FactPeriod`; a **forecast value without a reference period is ignored**
(under-determined); share units ("percent of GDP") are never percentages.

Risk facts are categorical orientations (`upside` / `downside` / `balanced`,
with `two-sided` / `symmetric` normalized to `balanced`) **only** when the
source states one, otherwise a verbatim text assessment; the target
(`inflation_risk` / `growth_risk` / `risk`) is read from the wording. Absence
never becomes an invented orientation — it is surfaced as a
`no_risk_assessment` warning.

## 7. Fed guidance & policy vocabulary

The Fed uses its own forward-guidance vernacular (same structural "guidance"
slot as Phase 4.3). Fed guidance anchors (bank-specific, in `fed.py`):

- `as appropriate`
- `will be patient` / `will be patient in considering`
- `meeting by meeting`
- `data dependent` / `depends on the data`
- `will not hesitate to`
- `stand(?:s|ing)? ready to`
- `for as long as necessary` / `as long as needed`
- `will continue to monitor|assess|evaluate`
- `future meetings?|decisions?`
- `will take into account`

Fed policy sentences require a compound signal: a policy stance word
(`stance` / `decided to` / `appropriate` / `restrictive` / `accommodative` /
`tightening` / `easing`) **and** a Fed policy term (`monetary policy`, `the
FOMC`, `the Committee`, `the Federal Reserve`, `the federal funds rate`,
`interest rates`, `policy rates`). A bare `policy` / `rate` is never a policy
signal.

The generic English financial / inflation / labour / growth / risk anchors are
structural and shared (bank-agnostic). Fed inflation vocabulary adds `PCE` /
`consumer prices` on top of the generic `inflation` anchor.

## 8. Observations & warnings

- `Fact.speaker` — verbatim Fed-official label (labels preserved ALL-CAPS),
  never inferred; `None` for remarks and for anything the questioner says.
- `effective_date` is always `None`.
- `identity_qualifier` — `remarks:{n}` / `answer:{turn}:{n}`.
- Provenance: `source_location` (section index), `source_text` (verbatim
  supporting passage), value-level `source_text`, `extraction_method = regex`,
  `extraction_version`, `confidence` (`HIGH` for percentages and categorical
  orientations, `MEDIUM` for verbatim text).
- Within-run deduplication: quantitative duplicates are subject + predicate +
  period + value; qualitative ones subject + predicate + period + normalized
  verbatim wording.
- Warnings (fixed order): `no_sections` (early return), `no_remarks`, `no_qna`,
  `no_risk_assessment`, `no_forward_guidance`.

## 9. Journalist content is never mined

The Fed extractor walks the transcript line by line. Any line that is an
ALL-CAPS label that is **not** a recognised Fed-official role is a **journalist
/ moderator turn boundary**: it starts a new Q&A turn, its content is **never**
mined (a market-fact sentence in a journalist's question is never attributed to
the bank), and any following Fed-official content is emitted as that turn's
answer. An unprefixed content line continues the current speaker's turn (a
Fed-official turn may span multiple paragraphs). A content line with **no**
Fed-official label present is **unattributed and never mined** (conservative
skip — an unattributed economic-looking line is never turned into a fact).

## 10. Shared structural mechanics (`press_conferences/_shared.py`)

`src/argus/press_conferences/_shared.py` holds **bank-agnostic structural
mechanics** (the per-family convention already used by `minutes/_shared.py`,
`reports/_shared.py`, `speeches/_shared.py` — per-family duplication is the
convention, no code import graph between families):

- heading normalization (`clean_heading`), box detection (`is_box`), sentence
  splitting (`split_sentences`);
- the explicit-value-claim gate, period parsing (year / month / quarter),
  share-unit rejection, the GDP near-miss guard, `token_value` /
  `is_share` / `period_for` / `sentence_label`;
- the qualitative-assertion gate (`is_economic_assertion` — platitude,
  transitive-object and possessor rejection) applied to the financial /
  inflation / labour / growth verbatim-assessment paths;
- a deterministic, provenance-carrying `PressConferenceReporter` with within-run
  deduplication and the `remarks:` / `answer:` ordinal qualifiers;
- the canonical Phase 4.3 subject/predicate constants and the generic English
  financial / inflation / labour / growth / risk anchor sets.

Bank-specific semantics live **only** in `fed.py` and `boe.py` (bank transcript
labels, bank turn parsing, bank guidance / policy vocabulary). No
`if bank == "…":` dispatch anywhere.

## 11. Bank-specific extractor — Fed

`src/argus/press_conferences/fed.py` — `FedPressConferenceExtractor`.
Bank-specific logic:

- `_FED_ROLE_RE` — `CHAIRMAN|CHAIRWOMAN|CHAIR|VICE CHAIR|GOVERNOR|PRESIDENT`
  role words;
- `_ALL_CAPS_LABEL_RE` — full ALL-CAPS label line detection (`^[A-Z][A-Z .'’-]{2,}\.$`);
- turn parsing: the first Fed-official turn before any journalist label is
  remarks; each journalist label increments the turn counter; subsequent
  Fed-official turns are Q&A answers;
- the Fed guidance anchors and Fed policy compound vocabulary (`§7`);
- Fed inflation additions (`PCE`, `consumer prices`).

Registration: `FedPressConferenceExtractor` is added to `_EXTRACTORS` in
`src/argus/press_conferences/base.py` (generic dispatch `get_extractor("fed")`)
and exported from `src/argus/press_conferences/__init__.py`.

## 12. Bank-specific extractor — BoE

`src/argus/press_conferences/boe.py` — `BoEPressConferenceExtractor`.
Bank-specific logic:

### 12.1 Source & discovery

BoE publishes the MPC press conference transcript as a **PDF** on the MPR issue
page (`…/monetary-policy-report/<yyyy>/<month>-<yyyy>`, link *"Press conference
transcript (PDF)"*). Discovery is a declared-type source
`boe_mpc_press_conference` (priority 6, `types=("press_conference",)`,
`keep_documents=True`). Classification flows through the source **type_hint**
(`METHOD_SOURCE_TYPE_HINT`, `HIGH` confidence) — BoE has **no** URL/title
TypeRule for press conferences (the transcript PDF `mpr-press-conference-transcript-*`
does not match the generic `press[_-]conference` rule). The opening remarks are
a separate PDF; the transcript is a **pure Q&A** document that opens with the
tail of the Governor's closing remarks.

### 12.2 Transcript structure & label detection

The transcript body is a **turn-based dialog** of standalone capitalized name
labels (e.g. `Andrew Bailey`, `Clare Lombardelli`, `Dave Ramsden`), each
followed by that speaker's wrapped words. There are no ALL-CAPS role labels and
no `Question:` / `Answer:` markers. Speaker labels are **not** a fixed corpus:
the conservative official identity is the known MPC membership as of 2026
(Governor + Deputy Governors + external members, `_BOE_MPC_MEMBERS`); any other
label is a journalist / moderator boundary.

Label acceptance is deliberately conservative (`_is_label_line`): a label must
be a multi-word capitalized name that either is a known MPC member or sits at a
clean turn boundary (start of the transcript, directly after a sentence-ending
line, or directly after another label). This rejects the two BoE PDF artifacts
seen in the real transcript:

- a **single-word interjection** (`Yeah.`) — never becomes a journalist
  boundary, so the answer it opens is still mined;
- a **wrapped PDF fragment** (`Charter Act.` from `1844 Bank Charter Act.`) —
  treated as content, so the enclosing answer is not split.

### 12.3 Turn parsing & speaker attribution

Turn parsing mirrors the Fed model: the first MPC-member turn before any
journalist label is **remarks** (collective, `Fact.speaker = None`); each
journalist label increments the Q&A turn counter and is **never mined**; every
MPC-member turn after a journalist label is an **answer** attributed verbatim
to the member's label. A content line with no preceding label is unattributed
and never mined.

Because the PDF is page-based and `document.sections` are pages, the walker
accumulates a turn's wrapped lines across page sections (`_RunState.pending`)
and mines the joined paragraph once the next label arrives, so a turn spanning a
page break keeps its speaker and its provenance. A sentence split across pages
is only guaranteed to be contiguous in the **full document text** (whitespace
normalized), not in a single page section — the provenance contract for BoE
tests normalizes whitespace accordingly.

`identity_qualifier` follows the Phase 4.3 contract: `remarks:{n}` and
`answer:{turn}:{n}`.

### 12.4 BoE vocabulary

- `_GUIDANCE_ANCHORS` — BoE forward-guidance phrasing: `stand ready to`,
  `will not hesitate to`, `for as long as necessary`, `meeting by meeting`,
  `data dependent`, `will be guided by`, `depends on the (incoming) data`,
  `will continue to monitor|assess|evaluate`, `will take into account`,
  `will decide`, `future (policy) decisions`, `there will be a decision`,
  `will form the judgment`, `will keep (monetary) policy under review`.
- `_POLICY_TERM` × `_POLICY_STANCE` compound: a BoE policy sentence needs a
  stance word (`stance`, `decided to`, `decision(s)`, `appropriate`,
  `restrictive`, `accommodative`, `tightening`, `easing`, `unchanged`, `hold`,
  `primary tool`) **and** a BoE term (`Bank Rate`, `monetary (policy)`,
  `interest rates`, `the MPC`, `the Bank of England`, `the Bank`, `policy
  rates`, `quantitative tightening`).
- `_INFLATION_EXTRAS` — `CPI`, `disinflation`, food/energy prices, second-round
  effects, inflationary; `_FINANCIAL_EXTRAS` — gilts, yields, term premia, QT,
  balance sheet, reserves; `_RISK_EXTRAS` — `distribution of risk`,
  `on the upside|downside`; `_INFLATION_DRIVER` — driven/driving/drivers,
  owing to, boosted by, weighed on, energy/food/oil/gas/services prices.
- The shared `_VALUE_GATE` in `_shared.py` gained an approximation-qualifier
  alternation (`… was | were | is | are | stood | stands | standing | averaged |
  running | remain(s|ed|ing)? [at] about|around|roughly|approximately …`) so
  the real July 2026 sentence *"So it was about 0.1% growth in the last
  quarter."* is mined as an explicit GDP value. The gate change is
  bank-agnostic and was verified not to disturb the ECB/Fed press-conference
  suites.

### 12.5 Canonical facts

BoE emits the same canonical subjects / predicates as the Fed extractor
(`§6`). On the real July 2026 transcript the extractor emits 27 facts with no
warnings: risk (11), monetary_policy (5), inflation_risk (4), policy_guidance
(2), plus inflation, inflation_driver, gdp (value 0.1%, from "was about 0.1%
growth in the last quarter"), financial_conditions and growth — speaker-attributed
across the Governor and both Deputy Governors, with the risks-tilted-to-upside
and "10-year gilt yields have risen by about 350 basis points" claims preserved
verbatim. Currency claims ("around £4 billion a year") and basis-point claims
that are not percentages are never value facts.

### 12.6 Registration & tests

Registration mirrors Fed: `BoEPressConferenceExtractor` is added to
`_EXTRACTORS` in `base.py` (`get_extractor("boe")`) and exported from
`press_conferences/__init__.py` (`BOE_EXTRACTION_VERSION`). Tests live in
`tests/test_press_conferences_boe.py` (dispatch, golden facts, contract fields,
speaker attribution, Q&A boundary / turn numbering, page-spanning accumulation,
value gate incl. the approximation-qualifier regression, negative epistemic,
provenance round-trip via `Store`, gating, idempotent persistence, batch
extraction, and BoE classification) against the synthetic fixture
`tests/fixtures/documents/boe_press_conf.txt` (a multi-page turn-based dialog
that reproduces the label interjection and wrapped-fragment cases) and inline
synthetic documents. A live-source verification was run against the real July
2026 transcript (discovery → fetch → normalize `pdf_text` → classify
`press_conference`/`source_type_hint` → extract → persist, 27 facts retrieved).

## 13. Final output

The Press Conference family (ECB conserved + Fed and BoE added) emits canonical
`Fact`s only. Nothing here introduces Phases 5–8 semantics, no LLM, no network
calls, no fuzzy inference, and no new top-level roadmap phase. Phase 9 remains
`DEFERRED`; nothing here starts Phase 10.