# Argus — Monetary Policy Report Extraction Contract (Phase 4.x)

> **Phase boundary.** This document describes the **Phase 4.x — Multi-Bank
> Report Extraction Extension** of the **Phase 4 — Fact Extraction** layer.
> It is **not** a new standalone phase: Argus is not gaining a "Phase 10/11",
> and no existing phase is renumbered. Report extraction is
> document-to-`Fact` extraction — it lives entirely **upstream** of the
> Phase 5+ derived layers (`FactChange` → `PolicyReaction` →
> `MonetaryPolicyState` → `ForexFundamentals`). This work ends at canonical
> `Fact`s.

```
Official publications
        ↓ Classification (classifications table = single source of truth)
        ↓ Phase 4 Fact Extraction (Report extractor family)
        ↓ canonical Facts
        ↓ Phase 5+ (separate, downstream, never touched here)
```

Phase 9 — Historical Validation — remains **DEFERRED**. Nothing here adds a
synthetic historical corpus or claims real historical coverage.

---

## 1. Scope

Argus extracts **observable factual claims** from official monetary policy
**report** publications: the ECB Economic Bulletin (the euro-area
report-like publication), the Norges Bank Monetary Policy Report (a
mixed-content document), and the BoE, BoC, RBA, RBNZ and Riksbank reports.
A monetary policy report is a **publication type** (`monetary_policy_report`,
gated on the `classifications` table), not a Fact.

Each bank's extractor is **bank-specific** (heading vocabulary, terminology,
section layout), sharing only genuinely **structural** mechanics via
`src/argus/reports/_shared.py` (heading normalization, sentence splitting,
the explicit value-claim gate, a deterministic provenance-carrying fact
emitter with within-run deduplication). **No bank-specific semantics live in
the shared helper.**

A report extractor may capture, only when the source states it explicitly:

- explicit numerical values (percentage value claims with an explicit
  reference period — year / month / quarter read from the wording);
- explicit economic assessments (verbatim qualitative);
- explicit policy statements (verbatim — the report's *narrative* of policy);
- explicit forward-guidance statements (verbatim);
- explicit risks (categorical orientation when stated, otherwise verbatim);
- the Norges policy-rate path (explicit numeric policy-rate levels in future
  years, Norges-specific); the Riksbank MPR decision narrative is **never**
  priced (see §3).

## 2. Epistemic boundary

Report extraction MUST **never** infer, emit, or label:

- hawkish / dovish stance, sentiment or market expectations;
- a policy **decision** (level, change, votes) — Phase 4.1/8, gated on
  `monetary_policy_decision` / minutes publications;
- structured economic projection **tables** — Phase 4.5, gated on
  `economic_projections`;
- causal relationships, trading signals, ranking, conviction;
- a policy-rate **path** for banks other than Norges (no artificial symmetry:
  the canonical vocabulary is not extended per bank without justification).

Precision over recall: prefer **no Fact** over an **invented Fact**.
`UNKNOWN ≠ ECONOMIC`. "Absence of proof → absence of extraction."

## 3. Phase 4.1 / 9 boundaries inside a report

A report is a large narrative that may *mention* decisions and projections
without being them. The family keeps the boundaries hard:

- the **decision narrative** ("The Executive Board decided to cut the policy
  rate by 0.25 percentage points to 2.5 per cent") is preserved **verbatim**
  as a `monetary_policy/statement` and **never priced** (no `policy_rate`
  value Fact) — the current level/change is Phase 4.1 territory;
- the **forecast/statistical tables** sections (Riksbank "Forecast tables",
  ECB macroeconomic data captions) belong to Phase 4.5; a prose footnote about
  them is not mined. Prose **forecasts** inside a report are kept as value
  Facts only when they carry an explicit reference period; a forecast without
  a period is under-determined and ignored;
- share/ratio units ("% of GDP", "% of total") are never converted into
  percentage Facts; a bare statement ("Inflation is 2.4 per cent in 2025")
  yields no value (an explicit value-claim verb is required);
- GDP near-misses ("GDP deflator", "GDP per capita", "per capita GDP") never
  anchor a growth sentence and never leak a GDP value;
- `Fact.speaker` is always `None`, `Fact.effective_date` is always `None`,
  `identity_qualifier` is `report:{subject}:{ordinal}`;
- verbatim provenance preserved on every Fact; within-run deduplication;
  deterministic output; source objects never mutated.

## 4. Canonical subjects & predicates

Subjects (controlled vocabulary — reuses the Phase 4.2/7/8 vocabulary, with
`fiscal_policy` and, for the Riksbank, the shared `core_inflation`):

```
inflation, core_inflation, inflation_expectations, growth, gdp,
labour_market, unemployment, wages, financial_conditions, fiscal_policy,
risk, inflation_risk, growth_risk, monetary_policy, policy_guidance
(policy_rate_projection — Norges-specific, documented in docstrings)
```

Predicates: `assessment` (qualitative / categorical / risk), `statement`
(policy & guidance, verbatim), `value` (quantitative, `ValueKind.PERCENTAGE`,
with a `FactPeriod` from the wording).

## 5. Bank-specific coverage

| Bank | Extractor | Publication | v |
|------|-----------|-------------|---|
| ECB | `EcbReportsExtractor` | Economic Bulletin | 10.0.0 |
| Norges | `NorgesReportExtractor` | Monetary Policy Report (+ policy-rate path) | 10.1.0 |
| BoE | `BoeReportExtractor` | Monetary Policy Report | 10.2.0 |
| BoC | `BocReportExtractor` | Monetary Policy Report | 10.3.0 |
| RBA | `RbaReportExtractor` | Statement on Monetary Policy | 10.4.0 |
| RBNZ | `RbnzReportExtractor` | Monetary Policy Statement | 10.5.0 |
| Riksbank | `RiksbankReportExtractor` | Monetary Policy Report | 10.6.0 |

Fed, BoJ and SNB are documented `not applicable` / represented by another
family (see `docs/EXTRACTORS.md`); the shared generic dispatch
(`get_extractor(bank)`) resolves only the banks above.

### ECB — `EcbReportsExtractor` (v10.0.0)

Publication: the **Economic Bulletin** (the euro-area report-like publication)
at `/press/economic-bulletin/html/eb<YYYYMM>.en.html`. Classification (Phase
4.x): the ECB-specific `monetary_policy_report` rule (`economic-bulletin`) maps
the bulletin issue pages to `monetary_policy_report`; they are discovered via
`ecb_publications_rss`. The fixture `tests/fixtures/documents/ecb_report.html`
models the report body (inflation, GDP, core inflation, unemployment, wages,
financial conditions, fiscal policy, monetary-policy statement, forward
guidance, risks) and extracts 19 canonical Facts; it also contains a non-claim
sentence and a narrative reference to a previous rate decision that must not
become a decision/projection fact. A real-source integration check on bulletin
5/2026 verified discovery → `monetary_policy_report` classification → dispatch
→ persistence; live extraction is conservative (0 facts) because the live HTML
landing page is a JS-rendered single-section page and the linked PDFs carry
uncontrolled section headings — no fact is invented (`UNKNOWN ≠ ECONOMIC`).
This is real-source verification, not Phase 9 historical validation.

### BoC — `BocReportExtractor` (v10.3.0)

Publication: the Bank of Canada quarterly **Monetary Policy Report** at
`/publications/mpr/mpr-<date>/`. Discovery is the dedicated official MPR feed
`boc_mpr_feed` (`/content_type/mpr/feed/`, RDF/RSS 1.0) typed
`monetary_policy_report`; the decision-typed `boc_key_interest_rate_schedule`
source excludes `/publications/mpr/`, so the MPR is never classified
`monetary_policy_decision`. The official MPR publication classifies
`monetary_policy_report` (Tier‑1 source hint; without a hint, the generic
`mpr[_-]\d{4}` report URL rule), and `get_extractor("boc")` dispatches
`BocReportExtractor`. Mined sections include `executive summary`,
`growth in canada and abroad`, `the outlook for inflation`, `the labour
market`, `financial conditions`, `monetary policy`, `risks`. A live
integration check on the July 2026 MPR produced 7 canonical Facts (growth,
inflation, risk, inflation_risk) with verbatim provenance, `extraction_version
10.3.0`. Synthetic fixture `tests/fixtures/documents/boc_report.html` (12
facts, no warnings; includes a non-claim sentence yielding no fact); dedicated
suite `tests/test_reports_boc_mpr.py`.

### Riksbank — `RiksbankReportExtractor` (v10.6.0)

Publication: the Riksbank quarterly **Monetary Policy Report**. Its
distinctive vocabulary is the **CPIF** (target measure → `inflation`),
underlying inflation / CPIF excluding energy (→ `core_inflation`), the
Executive Board as decision body, and the `Forecast tables` section (never
mined — Phase 4.5 boundary). Mined sections include `summary`, `monetary policy
in sweden — the riksbank's strategy`, `the economic outlook for the coming
years`, `the labour market`, `inflation`, `financial conditions`, `monetary
policy analysis`, `uncertainty, risks and alternative scenarios`. Section-title
dash glyphs (`—` `–` `-` `−`) are normalized so heading identity never depends
on the exact dash. The decision narrative stays verbatim
`monetary_policy/statement`, never priced. Fixture:
`tests/fixtures/documents/riksbank_report.html` (15 facts, no warnings).

## 6. Validation

Each bank's suite (`tests/test_reports.py`, `tests/test_reports_multibank.py`,
`tests/test_reports_riksbank.py`, `tests/test_reports_boc_mpr.py`) verifies:
golden facts with provenance,
contract fields, conservative routing (known headings mined, known
non-economic and **unknown** headings ignored), dash/numbering normalization,
the value gate, categorical risk orientations only when explicit, no
downstream semantics, within-run dedup, deterministic output (repetition +
order independence), source immutability, classification gating and the full
publication → classification → extractor → Fact → persistence → retrieval
slice, idempotently.