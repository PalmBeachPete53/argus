# Argus Desktop GUI (Tauri 2)

A minimal desktop shell around the Argus Python Core. The GUI is a thin
interface layer — **no Argus business logic lives outside the Core**.

## Architecture

```
Argus Desktop
│
├── desktop/src-tauri/          Tauri 2 (Rust) — native shell only
│   └── src/lib.rs              tauri commands → spawn the Python Core bridge
│
├── desktop/src/                React + TypeScript frontend
│   ├── components/             Header · Sidebar · MainContent · Footer · SettingsModal
│   └── ...                     Data view (browser of the Argus data/ folder)
│
└── src/argus/gui_bridge.py     Python bridge — the ONLY Core↔GUI boundary
```

Rust is confined to the Tauri shell (commands, controlled filesystem access for
browsing). Every piece of Argus state comes from the Python Core via
`python -m argus.gui_bridge`:

| Command | Purpose |
|---|---|
| `banks` | JSON list of banks `{id, name, currency, enabled}` |
| `banks-set <id> on|off` | persist a bank toggle, returns the updated list |
| `data-root` | absolute path of the Argus `data/` directory |

No REST API is used: Tauri IPC (`invoke`) + the subprocess bridge is the whole
communication layer for V1.

## Single source of truth (Bank Toggle)

The GUI never keeps a parallel bank state. It reads/writes the **existing Argus
configuration** (`src/argus/config.py`):

- `BANKS_ENABLED` — default map (unchanged).
- `ARGUS_BANKS_ENABLED` / `ARGUS_BANKS_DISABLED` — environment overrides
  (unchanged, still authoritative over the rest).
- Persistent user overrides file — default `data/argus_banks.json`
  (overridable via `ARGUS_BANKS_CONFIG`), written by `config.set_bank_enabled`.

Precedence in `is_bank_enabled`:

```
ARGUS_BANKS_ENABLED (allow-list, authoritative)
  > ARGUS_BANKS_DISABLED (env)
  > persistent user overrides (GUI / operator)
  > BANKS_ENABLED (default map)
```

The CLI (`argus --list-banks`), the pipeline and the GUI all read exactly the
same `is_bank_enabled` / `enabled_banks`, so a toggle made in the GUI is
immediately visible to the CLI and vice-versa. RBNZ can be re-enabled from the
GUI without any code change.

## Accessing `data/`

`data-root` resolves the Core's `DEFAULT_STORE_PATH` (`data/argus.db`) parent.
The Rust shell locates the repository root (the directory containing
`src/argus`) by walking upward from its working directory, or via `ARGUS_ROOT`.
No machine-specific absolute path is hardcoded.

The Data view lists the `data/` contents (files and directories), supports
navigation into directories and back to the parent, and surfaces access errors.
It does **not** interpret the SQLite business content at this stage.

## Environment variables

- `ARGUS_PYTHON` — Python interpreter used to run the bridge (default: the
  repository's `.venv/bin/python`, else `python3`).
- `ARGUS_ROOT` — repository root (default: auto-detected by walking upward from
  the working directory).
- `ARGUS_BANKS_CONFIG` — persistent bank-override file path (Core default:
  `data/argus_banks.json`).

## Install GUI dependencies

```bash
cd desktop
npm install
```

## Run in development

From the repository root (with the Python venv active or `.venv/` present):

```bash
cd desktop
npm run tauri dev
```

This starts Vite (port 1420) and launches the Tauri window pointing at the
React dev server.

## Build the application

```bash
cd desktop
npm run tauri build
```

Produces the platform bundle (e.g. `src-tauri/target/release/bundle/`).

## Verify

```bash
# Core untouched by the GUI layer
python -m pytest
python -m compileall -q src tests scripts

# Frontend type-check + production build
cd desktop && npm run build

# Rust build + unit tests
cd desktop/src-tauri && cargo build && cargo test
```
