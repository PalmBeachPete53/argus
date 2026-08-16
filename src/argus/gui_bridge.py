"""Command-line bridge for the Argus desktop GUI (Tauri 2).

The GUI never re-implements Argus logic: the Rust layer shells out to this
module and reads JSON on stdout. All bank-state and data-path knowledge stays in
the Python Core, which remains the single source of truth.

Commands (``python -m argus.gui_bridge <command>``):

- ``banks``          → JSON ``{"banks": [{id, name, currency, enabled}, …]}``
- ``banks-set <id> on|off`` → persists the toggle, returns the updated list
- ``data-root``      → JSON ``{"root": "<absolute path of data/>"}``
- ``help``           → usage

Every command exits 0 and prints one JSON object on stdout; errors exit
non-zero with a message on stderr.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

USAGE = "usage: python -m argus.gui_bridge banks|banks-set <id> on|off|data-root"

# Repository root, resolved from this module's own location
# (`<root>/src/argus/gui_bridge.py`), never from the process working directory —
# so the bridge behaves identically whether spawned from a shell, `tauri dev` or
# a Finder-launched `.app`.
ROOT = Path(__file__).resolve().parents[2]


def _bank_list() -> list[dict]:
    from .config import is_bank_enabled
    from .registry import SourceRegistry

    registry = SourceRegistry()
    return [
        {
            "id": bank.id,
            "name": bank.name,
            "currency": bank.currency,
            "enabled": is_bank_enabled(bank.id),
        }
        for bank in registry.banks
    ]


def _cmd_banks() -> int:
    print(json.dumps({"banks": _bank_list()}, indent=2))
    return 0


def _cmd_banks_set(argv: list[str]) -> int:
    if len(argv) < 2:
        print(USAGE, file=sys.stderr)
        return 2
    bank_id = argv[0]
    state = argv[1].strip().lower()
    if state not in ("on", "off"):
        print(f"invalid state: {state!r} (expected on|off)", file=sys.stderr)
        return 2
    from .config import set_bank_enabled

    set_bank_enabled(bank_id, state == "on")
    print(json.dumps({"banks": _bank_list()}, indent=2))
    return 0


def _cmd_data_root() -> int:
    # Explicit, working-directory-independent resolution of the Argus data dir.
    print(json.dumps({"root": str(ROOT / "data")}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(USAGE, file=sys.stderr)
        return 2
    command = argv[0]
    if command == "banks":
        return _cmd_banks()
    if command == "banks-set":
        return _cmd_banks_set(argv[1:])
    if command == "data-root":
        return _cmd_data_root()
    if command in ("help", "--help", "-h"):
        print(USAGE)
        return 0
    print(f"unknown command: {command}", file=sys.stderr)
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
