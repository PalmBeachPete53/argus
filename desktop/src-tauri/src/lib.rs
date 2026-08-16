//! Argus desktop — Tauri 2 native layer.
//!
//! Rust stays confined to the Tauri shell. All Argus business state (bank
//! toggle, data path, directory listing) lives in the Python Core and is
//! reached by spawning `python -m argus.gui_bridge` (single source of truth).
//! The bridge resolves and confines every path to the Argus `data/` directory.

use std::path::PathBuf;
use std::process::{Command, Stdio};

use serde::Serialize;
use tauri::Manager;
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

    /// Spawn a `python -m argus.gui_bridge` command detached, with output sent
    /// to the void. Used for long-running or fire-and-forget work: discovery
    /// campaigns and exit-time cleanup of an active campaign.
    fn spawn_detached(&self, args: &[String]) -> Result<(), String> {
        Command::new(&self.python)
            .arg("-m")
            .arg("argus.gui_bridge")
            .args(args)
            .current_dir(&self.root)
            .env("PYTHONPATH", self.root.join("src"))
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|err| format!("failed to launch bridge command {args:?}: {err}"))?;
        Ok(())
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

    /// List `<data-root>/<relative_path>` through the bridge, which resolves and
    /// confines the path to the Argus `data/` directory (rejects `..` escapes,
    /// absolute paths, missing/unreadable directories).
    fn list_dir(&self, relative_path: &str) -> Result<DirListing, String> {
        let out = self.run(&["list-dir".into(), relative_path.to_string()])?;
        let value: serde_json::Value = serde_json::from_str(&out).map_err(|err| err.to_string())?;
        if let Some(err) = value.get("error").and_then(|e| e.as_str()) {
            return Err(err.to_string());
        }
        serde_json::from_value(value).map_err(|err| err.to_string())
    }

    /// Open a supported file (`html`/`htm`/`pdf`) inside `data/` with the
    /// OS-default application. The bridge validates confinement, existence and
    /// the file type before handing the file to the system launcher.
    fn open_file(&self, relative_path: &str) -> Result<(), String> {
        let out = self.run(&["open-file".into(), relative_path.to_string()])?;
        let value: serde_json::Value = serde_json::from_str(&out).map_err(|err| err.to_string())?;
        if let Some(err) = value.get("error").and_then(|e| e.as_str()) {
            return Err(err.to_string());
        }
        Ok(())
    }

    /// Read-only view of the real ``SourceRegistry`` (never duplicated in the
    /// frontend): each bank's known sources.
    fn sources(&self) -> Result<std::collections::HashMap<String, BankSources>, String> {
        let out = self.run(&["sources".into()])?;
        let value: serde_json::Value = serde_json::from_str(&out).map_err(|err| err.to_string())?;
        value
            .get("banks")
            .cloned()
            .ok_or_else(|| "malformed bridge output: missing 'banks'".to_string())
            .and_then(|v| serde_json::from_value(v).map_err(|err| err.to_string()))
    }

    /// Launch a discovery campaign as a *detached* background subprocess.
    ///
    /// Discovery is a long-running operation; the Rust shell only starts the
    /// Core's campaign (which records its lifecycle — and its PID — in the
    /// store) and returns immediately. The frontend observes it via
    /// ``discovery_status`` / ``discovery_results`` and controls it via
    /// ``discovery_control``; the interface never blocks on the run.
    /// ``start_date`` / ``end_date`` (ISO dates, optional) bound the campaign
    /// to a publication-date window, applied by the Core itself.
    fn discovery_run(&self, start_date: Option<String>, end_date: Option<String>) -> Result<(), String> {
        let mut args = vec!["discovery-run".to_string()];
        if let Some(date) = start_date {
            args.push("--start-date".into());
            args.push(date);
        }
        if let Some(date) = end_date {
            args.push("--end-date".into());
            args.push(date);
        }
        self.spawn_detached(&args)
    }

    /// Real lifecycle control of the active campaign subprocess: the bridge
    /// signals the recorded PID (SIGSTOP / SIGCONT / SIGTERM) and returns the
    /// updated run lifecycle.
    fn discovery_control(&self, action: &str) -> Result<DiscoveryRun, String> {
        let out = self.run(&["discovery-control".into(), action.to_string()])?;
        let value: serde_json::Value = serde_json::from_str(&out).map_err(|err| err.to_string())?;
        if let Some(err) = value.get("error").and_then(|e| e.as_str()) {
            return Err(err.to_string());
        }
        serde_json::from_value(value).map_err(|err| err.to_string())
    }

    /// Drop the discovery report cache (runs + candidate snapshots only).
    fn clear_discovery_cache(&self) -> Result<ClearedCache, String> {
        let out = self.run(&["discovery-clear".into()])?;
        serde_json::from_str(&out).map_err(|err| err.to_string())
    }

    /// JSON summary of the most recent discovery campaign.
    fn discovery_status(&self) -> Result<DiscoveryRun, String> {
        let out = self.run(&["discovery-status".into()])?;
        serde_json::from_str(&out).map_err(|err| err.to_string())
    }

    /// JSON candidates of a discovery campaign (latest run by default).
    fn discovery_results(&self) -> Result<DiscoveryResults, String> {
        let out = self.run(&["discovery-results".into()])?;
        serde_json::from_str(&out).map_err(|err| err.to_string())
    }

    /// Read-only store aggregates for the Overview.
    fn stats(&self) -> Result<DataStats, String> {
        let out = self.run(&["stats".into()])?;
        serde_json::from_str(&out).map_err(|err| err.to_string())
    }

    /// Open an http(s) URL with the OS-default application (bridge validates).
    fn open_url(&self, url: &str) -> Result<(), String> {
        let out = self.run(&["open-url".into(), url.to_string()])?;
        let value: serde_json::Value = serde_json::from_str(&out).map_err(|err| err.to_string())?;
        if let Some(err) = value.get("error").and_then(|e| e.as_str()) {
            return Err(err.to_string());
        }
        Ok(())
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

// Directory types match the bridge's snake_case JSON contract (and the
// frontend's TS types): `is_dir`, `root`, `segments`, `parent`.
#[derive(Serialize, serde::Deserialize)]
struct DirEntry {
    name: String,
    path: String,
    is_dir: bool,
}

#[derive(Serialize, serde::Deserialize)]
struct DirListing {
    root: String,
    path: String,
    segments: Vec<String>,
    parent: Option<String>,
    entries: Vec<DirEntry>,
}

// Read-only view of the Core's SourceRegistry (mirrors the bridge contract).
#[derive(Serialize, serde::Deserialize)]
struct SourceInfo {
    id: String,
    name: String,
    kind: String,
    url: String,
    enabled: bool,
    publication_types: Vec<String>,
    search_fallback: bool,
}

#[derive(Serialize, serde::Deserialize)]
struct BankSources {
    bank: String,
    sources: Vec<SourceInfo>,
}

// Discovery campaign contract (mirrors the bridge's snake_case JSON).
#[derive(Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
struct DiscoveryRun {
    run_id: Option<String>,
    status: String, // idle | running | paused | completed | failed | stopped
    started_at: Option<String>,
    finished_at: Option<String>,
    error: Option<String>,
    candidates: i64,
    banks: Vec<String>,
    pid: Option<i64>,
}

#[derive(Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
struct DiscoveryCandidate {
    publication_id: Option<String>,
    bank_id: String,
    bank_name: String,
    title: String,
    url: String,
    source_id: String,
    method: String, // native | search
    is_new: bool,
    discovered_at: Option<String>,
    publication_date: Option<String>,
}

#[derive(Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
struct DiscoveryResults {
    run_id: Option<String>,
    status: String,
    started_at: Option<String>,
    finished_at: Option<String>,
    candidates: Vec<DiscoveryCandidate>,
    total: i64,
}

#[derive(Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
struct ClearedCache {
    runs_cleared: i64,
    candidates_cleared: i64,
}

#[derive(Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
struct DataStats {
    publications: i64,
    documents: i64,
    normalized_documents: i64,
    facts: i64,
    last_discovery: Option<DiscoveryRun>,
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
fn list_dir(state: State<'_, Bridge>, relative_path: String) -> Result<DirListing, String> {
    state.list_dir(&relative_path)
}

#[tauri::command]
fn open_file(state: State<'_, Bridge>, relative_path: String) -> Result<(), String> {
    state.open_file(&relative_path)
}

#[tauri::command]
fn get_sources(state: State<'_, Bridge>) -> Result<std::collections::HashMap<String, BankSources>, String> {
    state.sources()
}

#[tauri::command]
fn run_discovery(
    state: State<'_, Bridge>,
    start_date: Option<String>,
    end_date: Option<String>,
) -> Result<(), String> {
    state.discovery_run(start_date, end_date)
}

#[tauri::command]
fn discovery_control(state: State<'_, Bridge>, action: String) -> Result<DiscoveryRun, String> {
    state.discovery_control(&action)
}

#[tauri::command]
fn clear_discovery_cache(state: State<'_, Bridge>) -> Result<ClearedCache, String> {
    state.clear_discovery_cache()
}

#[tauri::command]
fn get_discovery_status(state: State<'_, Bridge>) -> Result<DiscoveryRun, String> {
    state.discovery_status()
}

#[tauri::command]
fn get_discovery_results(state: State<'_, Bridge>) -> Result<DiscoveryResults, String> {
    state.discovery_results()
}

#[tauri::command]
fn get_stats(state: State<'_, Bridge>) -> Result<DataStats, String> {
    state.stats()
}

#[tauri::command]
fn open_url(state: State<'_, Bridge>, url: String) -> Result<(), String> {
    state.open_url(&url)
}

// ---------------------------------------------------------------------------

pub fn run() {
    tauri::Builder::default()
        .manage(Bridge::new())
        .invoke_handler(tauri::generate_handler![
            get_banks,
            set_bank,
            get_data_root,
            list_dir,
            open_file,
            get_sources,
            run_discovery,
            discovery_control,
            clear_discovery_cache,
            get_discovery_status,
            get_discovery_results,
            get_stats,
            open_url
        ])
        .build(tauri::generate_context!())
        .expect("error while building Argus")
        .run(|app_handle, event| {
            // When the app closes while a discovery campaign is running, ask
            // the campaign to stop so no orphan process survives the app.
            if let tauri::RunEvent::ExitRequested { .. } = event {
                if let Some(bridge) = app_handle.try_state::<Bridge>() {
                    let _ = bridge.spawn_detached(&["discovery-control".into(), "stop".into()]);
                }
            }
        });
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
    fn bridge_lists_data_root() {
        let bridge = Bridge::new();
        if !bridge.root.join(".venv").join("bin").join("python").is_file() {
            return;
        }
        if !bridge.root.join("data").is_dir() {
            return; // no data dir in this checkout
        }
        let listing = bridge.list_dir("").expect("bridge must list data/");
        assert!(listing.root.ends_with("data"));
        assert!(listing.parent.is_none());
        assert!(listing.segments.is_empty());
    }

    #[test]
    fn bridge_rejects_escape() {
        let bridge = Bridge::new();
        if !bridge.root.join(".venv").join("bin").join("python").is_file() {
            return;
        }
        for bad in ["..", "../..", "../../etc", "/etc", "/tmp"] {
            assert!(bridge.list_dir(bad).is_err(), "expected rejection for {bad:?}");
        }
    }

    #[test]
    fn bridge_lists_existing_subdir() {
        let bridge = Bridge::new();
        if !bridge.root.join(".venv").join("bin").join("python").is_file() {
            return;
        }
        if !bridge.root.join("data").join("raw_2025").is_dir() {
            return;
        }
        let listing = bridge.list_dir("raw_2025").expect("bridge must list raw_2025/");
        assert_eq!(listing.path, "raw_2025");
        assert_eq!(listing.segments, vec!["raw_2025"]);
        assert_eq!(listing.parent, Some("".to_string()));
    }

    #[test]
    fn bridge_missing_directory_is_error() {
        let bridge = Bridge::new();
        if !bridge.root.join(".venv").join("bin").join("python").is_file() {
            return;
        }
        assert!(bridge.list_dir("definitely-not-here").is_err());
    }

    #[test]
    fn bridge_open_file_rejects_escapes_without_opening() {
        // These must fail before any system launcher is invoked.
        let bridge = Bridge::new();
        if !bridge.root.join(".venv").join("bin").join("python").is_file() {
            return;
        }
        for bad in ["..", "../secret.pdf", "../../etc/passwd", "/etc/passwd", "/tmp/x.pdf"] {
            assert!(bridge.open_file(bad).is_err(), "expected rejection for {bad:?}");
        }
        assert!(bridge.open_file("definitely-not-here.pdf").is_err());
    }

    #[test]
    fn bridge_sources_end_to_end() {
        let bridge = Bridge::new();
        if !bridge.root.join(".venv").join("bin").join("python").is_file() {
            return;
        }
        let banks = bridge.sources().expect("bridge must return the source registry");
        assert!(banks.contains_key("fed"));
        assert!(!banks["fed"].sources.is_empty(), "fed must have configured sources");
        assert!(banks.contains_key("rbnz"), "rbnz stays a known bank");
    }

    #[test]
    fn bridge_discovery_status_is_readable() {
        // Read-only: never launches a campaign, never mutates the store. The
        // real store may hold zero runs (idle) or a previous campaign.
        let bridge = Bridge::new();
        if !bridge.root.join(".venv").join("bin").join("python").is_file() {
            return;
        }
        let run = bridge.discovery_status().expect("bridge must return discovery status");
        assert!(
            matches!(run.status.as_str(), "idle" | "running" | "completed" | "failed"),
            "unexpected status: {}",
            run.status
        );
        assert!(run.candidates >= 0);
    }

    #[test]
    fn bridge_stats_are_readable() {
        // Read-only aggregates; numbers must come from the Core store.
        let bridge = Bridge::new();
        if !bridge.root.join(".venv").join("bin").join("python").is_file() {
            return;
        }
        let stats = bridge.stats().expect("bridge must return stats");
        assert!(stats.publications >= 0);
        assert!(stats.documents >= 0);
        assert!(stats.normalized_documents >= 0);
        assert!(stats.facts >= 0);
    }

    #[test]
    fn bridge_open_url_rejects_non_http() {
        let bridge = Bridge::new();
        if !bridge.root.join(".venv").join("bin").join("python").is_file() {
            return;
        }
        // Must fail validation before any system launcher is invoked.
        for bad in ["ftp://example.org", "file:///etc/passwd", "javascript:alert(1)"] {
            assert!(bridge.open_url(bad).is_err(), "expected rejection for {bad:?}");
        }
    }
}
