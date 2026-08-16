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

## Accessing `data/` and the Python runtime

The bridge is spawned by Rust as:

```
<python> -m argus.gui_bridge <command>
```

with `cwd` = the repository root, `PYTHONPATH` = `<root>/src`, and:

- **repository root**: `ARGUS_ROOT`, else a generic upward walk from the running
  executable (the `.app` lives inside the repository build tree) looking for the
  `src/argus` marker — the working directory (which macOS sets to `/` for GUI
  apps launched from Finder) is never used.
- **Python interpreter**: `ARGUS_PYTHON`, else `<root>/.venv/bin/python`
  (or `.venv/bin/python3`), else `python3`.

`data-root` and the persistent bank-override file (`data/argus_banks.json`) are
resolved **from the package location, not from the process working directory**,
so the GUI finds the same `data/` and configuration whether it was launched from
a shell or from Finder.

## Prerequisites (Python)

The GUI talks to the Python Core, so a working Argus Python environment must
exist — the GUI does **not** bundle Python yet:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

This creates the repository `.venv` (gitignored) that the app resolves and uses
for every bridge call. Without it the bridge falls back to `python3`, which may
lack the Argus dependencies.

## Environment variables

- `ARGUS_PYTHON` — Python interpreter used to run the bridge (default: the
  repository's `.venv/bin/python`, else `python3`).
- `ARGUS_ROOT` — repository root (default: auto-detected by walking upward from
  the running executable looking for `src/argus`).
- `ARGUS_BANKS_CONFIG` — persistent bank-override file path (Core default:
  `<root>/data/argus_banks.json`).

## Install GUI dependencies

```bash
cd desktop
npm install
```

## Run in development

From the repository root (with `.venv/` present):

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

Produces the platform bundle (e.g. `src-tauri/target/release/bundle/macos/Argus.app`
and the `.dmg`). The generated `.app` finds the repository via its own location
and works when launched from Finder.

## Limitations (distribution)

- The V1 `.app` requires the Argus repository on the same machine (it resolves
  the repo root from its own build-tree location and uses the repository
  `.venv`). A fully self-contained, distributable bundle (embedded Python
  runtime + Argus package) is future work; until then, launching the `.app`
  from Finder requires the repo + `.venv` to be present, or `ARGUS_ROOT` /
  `ARGUS_PYTHON` to point to them.

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
