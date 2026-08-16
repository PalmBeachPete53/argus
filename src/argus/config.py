"""Central bank enable/disable configuration.

The single source of truth for which central banks participate in operational
executions. A bank set to ``False`` remains fully defined in the codebase
(adapter, sources, discovery, classification, extractors, fixtures, golden,
tests) — it is simply *inactive*: no discovery / fetch / classify / extract work
is scheduled for it, and parametrized E2E scenarios are skipped.

This is generic (no per-bank special-casing in the pipeline). Turning a bank
back on is a configuration change only.

Layers of truth (highest → lowest):

1. ``ARGUS_BANKS_ENABLED`` (environment) — complete allow-list, authoritative
   over everything below (documented contract, unchanged).
2. ``ARGUS_BANKS_DISABLED`` (environment) — additionally disables banks.
3. Persistent user overrides file (default ``data/argus_banks.json``,
   overridable via ``ARGUS_BANKS_CONFIG``) — written by operators / the desktop
   GUI, the only *writable* layer of the toggle.
4. ``BANKS_ENABLED`` (default map, code).

The persistent file is part of the Core configuration, not a GUI-only state:
the CLI, the pipeline and the GUI all read exactly the same
``is_bank_enabled`` / ``enabled_banks``. There is no second source of truth.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

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

# Persistent user overrides file. Defaults to the Argus data directory
# (consistent with ``collector.DEFAULT_STORE_PATH = "data/argus.db"``), resolved
# from the package location — NOT from the process working directory — so the
# file is the same whether the GUI is launched from a shell or from Finder, and
# can be redirected with ARGUS_BANKS_CONFIG.
DEFAULT_BANKS_CONFIG = str(Path(__file__).resolve().parents[2] / "data" / "argus_banks.json")
ENV_CONFIG = "ARGUS_BANKS_CONFIG"

# (path, mtime_ns, overrides) cache so the hot toggle path does not re-read the
# file on every call. Invalidated on write and whenever the file changes.
_override_cache: tuple[str, int, dict[str, bool]] | None = None


def banks_config_path() -> Path:
    """Path of the persistent user-override file (``ARGUS_BANKS_CONFIG`` wins)."""
    env = os.environ.get(ENV_CONFIG)
    if env:
        return Path(env)
    return Path(DEFAULT_BANKS_CONFIG)


def load_bank_overrides() -> dict[str, bool]:
    """Read the persistent user-override file (empty dict when absent/invalid)."""
    global _override_cache
    path = banks_config_path()
    try:
        stat = path.stat()
    except OSError:
        _override_cache = None
        return {}
    key = (str(path), stat.st_mtime_ns)
    if _override_cache is not None and (_override_cache[0], _override_cache[1]) == key:
        return _override_cache[2]
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("bank config must be an object")
        overrides = {str(k).lower(): bool(v) for k, v in data.items()}
    except (OSError, ValueError):
        _override_cache = None
        return {}
    _override_cache = (key[0], key[1], overrides)
    return overrides


def save_bank_overrides(overrides: dict[str, bool]) -> None:
    """Persist the user-override file, atomically (write + rename)."""
    global _override_cache
    path = banks_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = {str(k).lower(): bool(v) for k, v in overrides.items()}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(clean, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    _override_cache = None


def set_bank_enabled(bank_id: str, enabled: bool) -> None:
    """Persist a bank's ON/OFF state in the user-override file.

    The write goes through the Core configuration (the single source of truth),
    so the GUI, the CLI and the pipeline observe the same state. This can also
    re-enable a default-OFF bank (e.g. RBNZ) without any code change.
    """
    overrides = load_bank_overrides()
    overrides[(bank_id or "").lower()] = bool(enabled)
    save_bank_overrides(overrides)


def clear_bank_overrides() -> None:
    """Remove the user-override file (back to the default ``BANKS_ENABLED`` map)."""
    global _override_cache
    path = banks_config_path()
    try:
        path.unlink()
    except OSError:
        pass
    _override_cache = None


def _env_disabled() -> set[str]:
    return {b.strip().lower() for b in os.environ.get(ENV_DISABLED, "").split(",") if b.strip()}


def _env_enabled() -> set[str] | None:
    raw = os.environ.get(ENV_ENABLED, "").strip()
    if not raw:
        return None
    return {b.strip().lower() for b in raw.split(",") if b.strip()}


def is_bank_enabled(bank_id: str) -> bool:
    """True when ``bank_id`` participates in operational executions.

    Precedence: ``ARGUS_BANKS_ENABLED`` allow-list > ``ARGUS_BANKS_DISABLED`` >
    persistent user overrides > ``BANKS_ENABLED`` defaults.
    """
    bank_id = (bank_id or "").lower()
    allow = _env_enabled()
    if allow is not None:
        # The environment allow-list is authoritative when set: it can re-enable
        # a bank that is OFF by default without any code change.
        return bank_id in allow
    if bank_id in _env_disabled():
        return False
    overrides = load_bank_overrides()
    if bank_id in overrides:
        return overrides[bank_id]
    return BANKS_ENABLED.get(bank_id, True)


def enabled_banks() -> tuple[str, ...]:
    """The known banks currently active (config + environment + overrides)."""
    return tuple(bank_id for bank_id in BANKS_ENABLED if is_bank_enabled(bank_id))


def filter_enabled(banks) -> tuple[str, ...]:
    """Return the subset of ``banks`` that is currently enabled.

    Used so that every integrated execution path applies the same toggle filter,
    whether the banks were selected globally or explicitly: a disabled bank is
    never scheduled for operational work unless it was first re-enabled (e.g.
    via ``ARGUS_BANKS_ENABLED`` or the persistent user overrides)."""
    return tuple(b for b in (banks or ()) if is_bank_enabled(b))
