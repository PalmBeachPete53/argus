# Argus — Speech Extraction Contract (Phase 4.x)

> **Phase boundary.** This document describes the **Phase 4.x — Multi-Bank
> Speech Extraction Extension** of the **Phase 4 — Fact Extraction** layer.
> It is **not** a new standalone phase: Argus is not gaining a "Phase 10/11",
> and no existing phase is renumbered. Speech extraction is document-to-`Fact`
> extraction — it lives entirely **upstream** of the Phase 5+ derived layers
> (`FactChange` → `PolicyReaction` → `MonetaryPolicyState` →
> `ForexFundamentals`). This work ends at canonical `Fact`s.

```
Official publications
        ↓ Classification (classifications table = single source of truth)
        ↓ Phase 4 Fact Extraction (Speech extractor family)
        ↓ canonical Facts
        ↓ Phase 5+ (separate, downstream, never touched here)
```

Phase 9 — Historical Validation — remains **DEFERRED**. Nothing here adds a
synthetic historical corpus or claims real historical coverage.

---

## 1. Scope

Argus extracts **observable factual claims** from official central-bank
speeches / remarks / addresses / keynote addresses. Each bank has its own
speech publication; each bank's extractor is **bank-specific** (heading
vocabulary, terminology, section layout, speaker/role conventions), sharing
only genuinely **structural** mechanics via
`src/argus/speeches/_shared.py` and `src/argus/speeches/_pipeline.py`.

A speech extractor may capture, only when the source states it explicitly:

- explicit numerical values (percentage value claims with an explicit period);
- explicit economic assessments (verbatim qualitative);
- explicit policy statements (verbatim);
- explicit forward-guidance statements (verbatim);
- explicit risks (categorical orientation when stated, otherwise verbatim);
- explicit forecasts/projections — only when the source clearly frames them as
  such;
- speaker identity and role, when explicitly available (`Fact.speaker`);
- explicit attribution qualifiers.

## 2. Epistemic boundary

Speech extraction MUST **never** infer, emit, or label:

- hawkish / dovish stance;
- sentiment;
- policy stance;
- market expectations;
- causal relationships;
- trading signals;
- conviction / importance;
- surprise;
- rate path;
- implied causal policy interpretation (no reaction-function / causality claim).

A speech is **evidence**, not interpreted policy. It is never converted into a
Phase 6 temporal relationship, a Phase 7 state dimension, or a Phase 8 forex
fundamental. The boundary is hard:

```
Publication / Document
        ↓ Speech extractor
        ↓ canonical Fact
        ↑ END OF THIS WORK
```

Precision over recall: prefer **no Fact** over an **invented Fact**.
*"The economy is doing well."* must not become a quantitative Fact, and
*"We remain committed to price stability."* must not be recast as directional
guidance.

## 3. Observation vs forecast

An explicit forecast/projection may be represented only when the source
clearly identifies it as one (e.g. *"GDP growth is expected to be 1.5% in
2027"*). Argus never silently converts *"we expect X"* into an observed value.
A reference **period is only assigned when the source provides one** (year,
month, quarter read from the wording — never guessed from proximity). A
forecast value claim without an explicit reference period is
under-determined and ignored.

`observed_at` (a temporal provenance concept of later phases) is never
confused with a forecast/reference `FactPeriod`.

## 4. Speaker identity

Use `Fact.speaker` **only when identity is explicitly established by the
source**: a `Speaker: <label>` line in the document body, or an explicit
author/metadata field. The speaker is **never inferred** from the URL,
institution, title, or surrounding page context, and a name appearing in the
prose is never read as the speaker. When attribution is collective or
ambiguous (e.g. *"A member of the committee noted …"*), preserve the
attribution via the `identity_qualifier` where the contract supports it and
keep `speaker=None`. A sentence framed as another person's quotation is never
mined and never attributed to the speech's speaker
(`quoted_content_skipped`); the speaker quoting their own past words is not a
quotation.

## 5. Canonical vocabulary

Speech extractors **reuse the existing canonical Fact vocabulary** — the same
subjects, predicates, value kinds, periods and qualifiers already used by
Phases 4.2–4.6: `inflation`, `core_inflation`, `inflation_expectations`, `growth`,
`gdp`, `labour_market`, `unemployment`, `wages`, `financial_conditions`,
`risk`, `inflation_risk`, `growth_risk`, `monetary_policy`,
`policy_guidance`; predicates `value` / `assessment` / `statement`;
`ValueKind.PERCENTAGE` / `CATEGORICAL` / `TEXT`; `FactPeriod` (year/month/
quarter); risk orientations `upside` / `downside` / `balanced`.

No speech-specific macro concept is created merely because wording differs
between banks. If a genuinely necessary vocabulary gap is discovered, it is
**documented first and the work stops for an architectural decision** — never
silently invented.

## 6. Provenance

Every `Fact` is traceable: `Fact` → `source_location` (section index) →
`source_text` (verbatim passage) → `document_id` → `publication_id` →
publication → document/source. Every Fact carries `extraction_method = regex`,
`extraction_version`, `confidence`, `effective_date = None`, `speaker`, and the
`speech:{subject}:{ordinal}` `identity_qualifier`.

## 7. Determinism

The same input produces identical Fact identities and identical output,
independent of:

- input / section ordering (semantically irrelevant ordering);
- dictionary ordering;
- incidental HTML ordering.

Repeated and reordered extraction are byte-for-byte equivalent.

## 8. Immutability

Extraction never mutates the `Publication`, the `Document`, an existing `Fact`
or `FactChange`, or the source payloads. Extraction is pure; persistence is the
caller's concern.

## 9. Shared structural mechanics (`_shared.py`, `_pipeline.py`)

`src/argus/speeches/_shared.py` holds **bank-agnostic structural mechanics**:

- heading normalization (`clean_heading`), analytical-box detection (`is_box`),
  sentence splitting;
- numeric value parsing, the explicit-value-claim gate, period parsing
  (year/month/quarter), share-unit rejection, the GDP near-miss guard;
- speaker-line / metadata-author detection;
- quotation detection and the qualitative-assertion gate;
- a deterministic, provenance-carrying fact `Reporter` with within-run
  deduplication and ordinal `speech:` qualifiers.

`src/argus/speeches/_pipeline.py` holds the **shared structural extraction
pipeline**: conservative section routing (`ignore` / `unknown` / `economic`),
content-first sentence categorization with fixed precedence
(guidance > policy > risk > financial > inflation > labour > growth), risk
orientation vs verbatim emission, and the run-state warnings
(`no_risk_assessment` / `no_forward_guidance` / `quoted_content_skipped`).
Its `SpeechExtractorBase` drives `extract` end-to-end and exposes the
vocabulary hooks each concrete bank overrides.

Both modules contain **no** bank-specific vocabulary, headings, terminology or
interpretation, and **no** `if bank == "…":` dispatch. The only shared
non-bank vocabulary is the genuinely generic English financial anchor set
(inflation / financial / labour / growth / risk), which contains no bank
identity.

## 10. Bank-specific extractors

The ECB extractor (`speeches/ecb.py`) is the **reference implementation**
(standalone, full fidelity). The nine minor banks (`speeches/{fed,boe,boj,snb,
boc,rba,rbnz,norges,riksbank}.py`) subclass the structural
`SpeechExtractorBase` and supply only source-specific logic:

- bank-specific economic and *ignore* heading sets (known economic → mined in
  full, known non-economic → IGNORED, unknown → **strictly** mined —
  explicit assertions only);
- bank-specific guidance anchors and policy term / stance vocabulary;
- bank-specific speaker/role conventions and any date/role extraction.

Each bank's extractor is verified against its **own** official speech source
(`COVERAGE_SOURCE` in the module); where a bank's speech URL cannot be reliably
classified by the generic rule (e.g. singular `/speech/`, the Japanese `koen`
or Swedish `tal` slugs), classification relies on the explicit title signal and
this is documented rather than forced. The goal is
`common mechanics + bank-specific extraction = canonical Facts`, never
identical parsing forced onto structurally different sources.

## 11. Final output

The Speech family (ECB + the 9 other applicable banks) emits canonical `Fact`s
only. Nothing here introduces Phases 5–8 semantics, no LLM, no network calls,
no fuzzy inference, and no new top-level roadmap phase.
