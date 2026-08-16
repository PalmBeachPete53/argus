# Bank enable/disable toggle

Argus supports 10 central banks. Each bank can be **enabled (ON)** or
**disabled (OFF)** centrally, without changing any bank code.

- ON → the bank participates normally in operational executions and its
  parametrized E2E scenarios run.
- OFF → the bank is skipped by operational executions and its parametrized E2E
  scenarios are skipped — but it remains **fully defined** in the codebase:
  adapter, sources, discovery, classification, extractors, fixtures/golden and
  unit tests are all preserved.

A disabled bank is **not** removed, unsupported or invalid. The distinction:

| Concept | Meaning |
|---|---|
| Bank **disabled** | Toggle OFF: excluded from integrated operational runs and E2E scenarios, but code and unit tests remain active. |
| Bank **absent / unsupported** | Not registered at all (no adapter). |
| Bank supported but **temporarily inaccessible** | Toggle OFF for an external reason (e.g. the official source is unreachable from the execution environment); it can be re-enabled without code changes. |

## Where it is configured

Single source of truth: `src/argus/config.py` — the `BANKS_ENABLED` map.

Current state:

```
Fed       ON
ECB       ON
BoE       ON
BoJ       ON
SNB       ON
BoC       ON
RBA       ON
RBNZ      OFF   (official source rbnz.govt.nz currently inaccessible from the
                 execution environment — Cloudflare/WAF; bank fully implemented)
Norges    ON
Riksbank  ON
```

## How it works

- `argus.config.is_bank_enabled(bank_id)` / `enabled_banks()` — generic toggle.
- `SourceRegistry` still knows every bank (`registry.banks`); `active_banks` and
  `enabled_sources()` only select enabled banks, so a disabled bank's sources
  are never scheduled for discovery/fetch/classify/extract work.
- `CentralBankCollector` filters operational runs to active banks.
- CLI `--list-banks` shows each bank's ON/OFF state; operational runs default
  to the active banks.

There is **no** `if bank == "rbnz"` special-casing anywhere in the pipeline —
RBNZ is simply a disabled bank.

## Environment overrides

- `ARGUS_BANKS_DISABLED=fed,boe` — additionally disable banks.
- `ARGUS_BANKS_ENABLED=fed,ecb,…,rbnz,…` — allow-list that re-enables banks
  (including a default-OFF bank) without code changes. When set it is the
  complete allow-list and is authoritative: `ARGUS_BANKS_DISABLED` is ignored,
  and a bank present in both lists is enabled.

Explicit bank selection (e.g. `--bank`) does **not** bypass the toggle: a
disabled bank requested directly is still excluded from every integrated
execution path (discovery, fetch, normalize, classify, extract). The only way
to run it is to re-enable it first via `ARGUS_BANKS_ENABLED`.

## Re-enabling a bank

To turn RBNZ back ON once its official source is accessible:

1. set `ARGUS_BANKS_ENABLED=…,rbnz,…` (temporary), or set
   `BANKS_ENABLED["rbnz"] = True` in `src/argus/config.py` (permanent);
2. provide the real captures/golden if they do not already exist;
3. run the suite — the RBNZ E2E scenarios execute again automatically.

No adapter/extractor/classification change is needed solely because a bank was
disabled.

## Tests

- Parametrized E2E scenarios (Phase C/D) consult the toggle: a disabled bank is
  skipped with `rbnz disabled by configuration` (not removed).
- Golden corpus hooks for a disabled bank are preserved; when RBNZ is OFF no
  RBNZ golden is required (it is not in the manifest until a real capture
  exists).
- RBNZ unit tests (classification, extractor, parsing) keep running — a
  disabled bank is not an invalid bank.
- `tests/test_bank_toggle.py` covers configuration, registry, pipeline skip,
  E2E skip, golden hooks and OFF→ON reversibility.
