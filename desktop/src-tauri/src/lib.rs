//! Argus desktop — Tauri 2 native layer.
//!
//! Rust stays confined to the Tauri shell. All Argus business state (bank
//! toggle, data path) lives in the Python Core and is reached by spawning
//! `python -m argus.gui_bridge` (single source of truth). Directory browsing
//! of the exposed `data/` area is generic filesystem access.

use std::path::PathBuf;
use std::process::Command;

use serde::Serialize;
use tauri::State;

// ---------------------------------------------------------------------------
// Bridge to the Python Core
// ---------------------------------------------------------------------------

pub struct Bridge {
    python: String,
    root: PathBuf,
}

fn resolve_root() -> PathBuf {
    if let Ok(root) = std::env::var("ARGUS_ROOT") {
        if !root.is_empty() {
            return PathBuf::from(root);
        }
    }
    // Generic project detection (not a hardcoded path): walk upward from the
    // running executable (works both under `cargo tauri dev` and for the
    // packaged .app, which lives inside the repository build tree) until a
    // directory containing the Python Core marker `src/argus` is found — that
    // is the repository root that owns `data/`. The current working directory
    // (which macOS sets to `/` for GUI apps launched from Finder) is only a
    // fallback.
    let mut start = std::env::current_exe().ok().and_then(|p| p.parent().map(PathBuf::from));
    if start.is_none() {
        start = std::env::current_dir().ok();
    }
    let start = start.unwrap_or_else(|| PathBuf::from("."));
    let mut dir: Option<&std::path::Path> = Some(start.as_path());
    while let Some(candidate) = dir {
        if candidate.join("src").join("argus").is_dir() {
            return candidate.to_path_buf();
        }
        dir = candidate.parent();
    }
    start
}

fn resolve_python(root: &PathBuf) -> String {
    if let Ok(python) = std::env::var("ARGUS_PYTHON") {
        if !python.is_empty() {
            return python;
        }
    }
    // The repository's virtualenv is the portable home of the Argus Core Python
    // environment (gitignored, created by setup). Resolved relative to the
    // repository root — never a machine-specific path.
    for name in ["python", "python3"] {
        let candidate = root.join(".venv").join("bin").join(name);
        if candidate.is_file() {
            return candidate.to_string_lossy().into_owned();
        }
    }
    "python3".to_string()
}

impl Bridge {
    fn new() -> Self {
        let root = resolve_root();
        let python = resolve_python(&root);
        Bridge { python, root }
    }

    /// Run one `argus.gui_bridge` command and return stdout as a string.
    fn run(&self, args: &[String]) -> Result<String, String> {
        let output = Command::new(&self.python)
            .arg("-m")
            .arg("argus.gui_bridge")
            .args(args)
            .current_dir(&self.root)
            .env("PYTHONPATH", self.root.join("src"))
            .output()
            .map_err(|err| format!("failed to launch the Argus Core bridge: {err}"))?;
        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            let stdout = String::from_utf8_lossy(&output.stdout);
            let detail = if stderr.trim().is_empty() { stdout.trim() } else { stderr.trim() };
            return Err(format!("Argus Core bridge failed: {detail}"));
        }
        Ok(String::from_utf8_lossy(&output.stdout).into_owned())
    }

    fn banks(&self) -> Result<Vec<BankInfo>, String> {
        let out = self.run(&["banks".into()])?;
        let value: serde_json::Value = serde_json::from_str(&out).map_err(|err| err.to_string())?;
        let banks = value
            .get("banks")
            .and_then(|b| b.as_array())
            .ok_or_else(|| "malformed bridge output: missing 'banks'".to_string())?;
        banks
            .iter()
            .map(|b| serde_json::from_value::<BankInfo>(b.clone()).map_err(|err| err.to_string()))
            .collect()
    }

    fn data_root(&self) -> Result<String, String> {
        let out = self.run(&["data-root".into()])?;
        let value: serde_json::Value = serde_json::from_str(&out).map_err(|err| err.to_string())?;
        value
            .get("root")
            .and_then(|r| r.as_str())
            .map(String::from)
            .ok_or_else(|| "malformed bridge output: missing 'root'".to_string())
    }
}

// ---------------------------------------------------------------------------
// Shared types
// ---------------------------------------------------------------------------

#[derive(Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
struct BankInfo {
    id: String,
    name: String,
    currency: String,
    enabled: bool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct DirEntry {
    name: String,
    path: String,
    is_dir: bool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct DirListing {
    path: String,
    parent: Option<String>,
    entries: Vec<DirEntry>,
}

// ---------------------------------------------------------------------------
// Tauri commands
// ---------------------------------------------------------------------------

#[tauri::command]
fn get_banks(state: State<'_, Bridge>) -> Result<Vec<BankInfo>, String> {
    state.banks()
}

#[tauri::command]
fn set_bank(state: State<'_, Bridge>, bank_id: String, enabled: bool) -> Result<Vec<BankInfo>, String> {
    let flag = if enabled { "on".to_string() } else { "off".to_string() };
    state.run(&["banks-set".into(), bank_id, flag])?;
    state.banks()
}

#[tauri::command]
fn get_data_root(state: State<'_, Bridge>) -> Result<String, String> {
    state.data_root()
}

#[tauri::command]
fn list_dir(path: String) -> Result<DirListing, String> {
    let dir = PathBuf::from(&path);
    let meta = std::fs::metadata(&dir).map_err(|err| format!("cannot access {path}: {err}"))?;
    if !meta.is_dir() {
        return Err(format!("not a directory: {path}"));
    }
    let mut entries: Vec<DirEntry> = Vec::new();
    for item in std::fs::read_dir(&dir).map_err(|err| format!("cannot read {path}: {err}"))? {
        let item = item.map_err(|err| err.to_string())?;
        let file_type = item.file_type().map_err(|err| err.to_string())?;
        entries.push(DirEntry {
            name: item.file_name().to_string_lossy().into_owned(),
            path: item.path().to_string_lossy().into_owned(),
            is_dir: file_type.is_dir(),
        });
    }
    entries.sort_by(|a, b| b.is_dir.cmp(&a.is_dir).then_with(|| a.name.cmp(&b.name)));
    let parent = dir
        .parent()
        .map(|p| p.to_string_lossy().into_owned())
        .filter(|p| !p.is_empty());
    Ok(DirListing { path, parent, entries })
}

// ---------------------------------------------------------------------------

pub fn run() {
    tauri::Builder::default()
        .manage(Bridge::new())
        .invoke_handler(tauri::generate_handler![get_banks, set_bank, get_data_root, list_dir])
        .run(tauri::generate_context!())
        .expect("error while running Argus");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolve_root_finds_repo_from_executable() {
        // current_exe() walks up from the test binary (target/debug/deps) to
        // the repository root that owns `src/argus` — the same mechanism the
        // packaged .app relies on when launched from Finder.
        let root = resolve_root();
        assert!(root.join("src").join("argus").is_dir(), "root={root:?}");
        assert!(root.join("data").exists() || root.join("pyproject.toml").exists());
    }

    #[test]
    fn resolve_python_prefers_repo_venv() {
        let root = resolve_root();
        let python = resolve_python(&root);
        if root.join(".venv").join("bin").join("python").is_file() {
            assert!(python.contains(".venv"), "expected venv python, got {python}");
        } else {
            // without a venv, fall back to a bare interpreter
            assert!(!python.is_empty());
        }
    }

    #[test]
    fn bridge_runs_banks_end_to_end() {
        // Real spawn: the exact chain the GUI uses (python -m argus.gui_bridge).
        // Skips gracefully when the repository venv is not provisioned.
        let bridge = Bridge::new();
        if !bridge.root.join(".venv").join("bin").join("python").is_file() {
            return;
        }
        let out = bridge.run(&["banks".into()]).expect("bridge must run");
        let value: serde_json::Value = serde_json::from_str(&out).expect("bridge must emit JSON");
        let banks = value.get("banks").and_then(|b| b.as_array()).expect("must contain banks");
        assert!(banks.len() >= 10);
        assert!(banks.iter().any(|b| b.get("id").and_then(|i| i.as_str()) == Some("rbnz")));
    }

    #[test]
    fn list_dir_lists_and_sorts() {
        let dir = std::env::temp_dir().join(format!("argus-test-{}", std::process::id()));
        std::fs::create_dir_all(dir.join("sub")).unwrap();
        std::fs::write(dir.join("a.txt"), b"x").unwrap();
        let listing = list_dir(dir.to_string_lossy().into_owned()).unwrap();
        assert_eq!(listing.parent, dir.parent().map(|p| p.to_string_lossy().into_owned()));
        // directories sort before files; names then alphabetically
        let kinds: Vec<&str> = listing.entries.iter().map(|e| if e.is_dir { "dir" } else { "file" }).collect();
        assert_eq!(kinds, vec!["dir", "file"]);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn list_dir_errors_on_missing_path() {
        let missing = std::env::temp_dir().join(format!("argus-does-not-exist-{}", std::process::id()));
        assert!(list_dir(missing.to_string_lossy().into_owned()).is_err());
    }
}
