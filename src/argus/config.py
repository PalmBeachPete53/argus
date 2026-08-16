"""Central bank enable/disable configuration.

The single source of truth for which central banks participate in operational
executions. A bank set to ``False`` remains fully defined in the codebase
(adapter, sources, discovery, classification, extractors, fixtures, golden,
tests) — it is simply *inactive*: no discovery / fetch / classify / extract work
is scheduled for it, and parametrized E2E scenarios are skipped.

This is generic (no per-bank special-casing in the pipeline). Turning a bank
back on is a configuration change only.

Override hooks (environment):
- ``ARGUS_BANKS_DISABLED``: comma-separated bank ids to additionally disable.
- ``ARGUS_BANKS_ENABLED``: comma-separated allow-list that re-enables banks
  regardless of the default map (e.g. to temporarily turn RBNZ back on).
"""

from __future__ import annotations

import os

# Default activation state. RBNZ is currently OFF: its official domain
# (rbnz.govt.nz) is inaccessible from the current execution environment
# (Cloudflare/WAF); the bank remains fully implemented and can be re-enabled
# later without code changes.
BANKS_ENABLED: dict[str, bool] = {
    "fed": True,
    "ecb": True,
    "boe": True,
    "boj": True,
    "snb": True,
    "boc": True,
    "rba": True,
    "rbnz": False,
    "norges": True,
    "riksbank": True,
}

ENV_DISABLED = "ARGUS_BANKS_DISABLED"
ENV_ENABLED = "ARGUS_BANKS_ENABLED"


def _env_disabled() -> set[str]:
    return {b.strip().lower() for b in os.environ.get(ENV_DISABLED, "").split(",") if b.strip()}


def _env_enabled() -> set[str] | None:
    raw = os.environ.get(ENV_ENABLED, "").strip()
    if not raw:
        return None
    return {b.strip().lower() for b in raw.split(",") if b.strip()}


def is_bank_enabled(bank_id: str) -> bool:
    """True when ``bank_id`` participates in operational executions."""
    bank_id = (bank_id or "").lower()
    allow = _env_enabled()
    if allow is not None:
        # The environment allow-list is authoritative when set: it can re-enable
        # a bank that is OFF by default without any code change.
        return bank_id in allow
    if not BANKS_ENABLED.get(bank_id, True):
        return False
    if bank_id in _env_disabled():
        return False
    return True


def enabled_banks() -> tuple[str, ...]:
    """The known banks currently active (config + environment)."""
    allow = _env_enabled()
    if allow is not None:
        return tuple(bank_id for bank_id in BANKS_ENABLED if bank_id in allow)
    disabled = _env_disabled()
    return tuple(
        bank_id for bank_id, enabled in BANKS_ENABLED.items()
        if enabled and bank_id not in disabled
    )
