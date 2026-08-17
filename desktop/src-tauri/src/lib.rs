//! Argus desktop — Tauri 2 native layer.
//!
//! Rust stays confined to the Tauri shell. All Argus business state (bank
//! toggle, data path, directory listing) lives in the Python Core and is
//! reached by spawning `python -m argus.gui_bridge` (single source of truth).
//! The bridge resolves and confines every path to the Argus `data/` directory.

use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};

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

    /// Build a bridge whose store root is pinned via ``ARGUS_ROOT`` to ``root``
    /// while the Python interpreter and sources still resolve from the
    /// repository. Automated tests use this to keep the real `data/` pristine.
    fn with_root(root: PathBuf) -> Self {
        let repo = resolve_root();
        let python = resolve_python(&repo);
        Bridge { python, root }
    }

    /// Run one `argus.gui_bridge` command and return stdout as a string.
    fn run(&self, args: &[String]) -> Result<String, String> {
        // The interpreter, import path and working directory always come from
        // the repository; ARGUS_ROOT alone pins where the Core stores state.
        let repo = resolve_root();
        let output = Command::new(&self.python)
            .arg("-m")
            .arg("argus.gui_bridge")
            .args(args)
            .current_dir(&repo)
            .env("PYTHONPATH", repo.join("src"))
            .env("ARGUS_ROOT", &self.root)
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
    /// to the void. Used for the discovery / collection campaign subprocesses.
    /// Returns the spawned ``Child`` so the caller can read its PID (to
    /// pre-register the run with the Core) and can terminate it if the
    /// pre-registration is refused — the running child is never abandoned.
    ///
    /// The campaign is launched as the leader of its own process group
    /// (``process_group(0)``, pgid == pid) so that on shutdown the bridge can
    /// terminate the *whole* tree (the campaign plus any descendants it spawned)
    /// via a negative-pid kill — never an unrelated process. ``detached_env``
    /// is the marker env var that tells the campaign it is launcher-owned and
    /// to arm its parent watchdog (a campaign must never outlive Argus):
    /// ``ARGUS_DISCOVERY_DETACHED`` for discovery, ``ARGUS_COLLECTION_DETACHED``
    /// for collection — the Core distinguishes them, never invents a value here.
    fn spawn_detached(&self, args: &[String], detached_env: &str) -> Result<Child, String> {
        let repo = resolve_root();
        let mut cmd = Command::new(&self.python);
        cmd.arg("-m")
            .arg("argus.gui_bridge")
            .args(args)
            .current_dir(&repo)
            .env("PYTHONPATH", repo.join("src"))
            .env("ARGUS_ROOT", &self.root)
            .env(detached_env, "1")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            cmd.process_group(0);
        }
        cmd.spawn()
            .map_err(|err| format!("failed to launch bridge command {args:?}: {err}"))
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
    /// ``start_date`` / ``end_date`` (ISO dates) are **required** and bound
    /// the campaign to a publication-date window, applied by the Core itself
    /// (start-inclusive, end-exclusive). Both must be present and ordered
    /// (``start_date <= end_date``); a detached spawn would swallow a bridge
    /// refusal, so the precondition is enforced here synchronously.
    ///
    /// Only one campaign may run at a time: the Core refuses a second launch,
    /// but the detached spawn would swallow that refusal — so the active state
    /// is re-read here *before* spawning, and a busy campaign surfaces a
    /// synchronous error instead of a silent no-op. The Core guard remains the
    /// authority for the racing case.
    fn discovery_run(&self, start_date: Option<String>, end_date: Option<String>) -> Result<DiscoveryRunId, String> {
        let start = start_date.filter(|s| !s.trim().is_empty());
        let end = end_date.filter(|s| !s.trim().is_empty());
        if start.is_none() || end.is_none() {
            return Err("Discovery requires both start_date and end_date.".to_string());
        }
        if start.as_deref() > end.as_deref() {
            return Err("start_date must be <= end_date".to_string());
        }
        let active = self.discovery_status()?;
        if matches!(active.status.as_str(), "running" | "paused") {
            let run_id = active.run_id.as_deref().unwrap_or("<unknown>");
            return Err(format!("a discovery campaign is already active: {run_id}"));
        }
        // Mint the campaign's identity *before* spawning, so the caller can
        // return it synchronously and follow exactly this run — never "latest".
        let run_id = self.discovery_run_id()?.run_id;
        let mut args = vec!["discovery-run".to_string()];
        args.push("--run-id".into());
        args.push(run_id.clone());
        args.push("--start-date".into());
        args.push(start.unwrap_or_default());
        args.push("--end-date".into());
        args.push(end.unwrap_or_default());
        self.spawn_detached(&args, "ARGUS_DISCOVERY_DETACHED")?;
        Ok(DiscoveryRunId { run_id })
    }

    /// Mint a fresh discovery-run identifier from the Core (no side effect).
    /// The Core owns the id format; the launcher only relays it.
    fn discovery_run_id(&self) -> Result<DiscoveryRunId, String> {
        let out = self.run(&["discovery-run-id".into()])?;
        serde_json::from_str(&out).map_err(|err| err.to_string())
    }

    /// Real lifecycle control of a campaign subprocess, targeted by its
    /// ``run_id``: the bridge signals the recorded PID (SIGSTOP / SIGCONT /
    /// SIGTERM) and returns the updated run lifecycle. The id makes the
    /// command address an explicit campaign — never an implicit "latest".
    ///
    /// Command-level failures surface through ``run`` (the bridge exits
    /// non-zero); the parsed run may legitimately carry an ``error`` field
    /// (e.g. ``"cancelled by user"``) that is *not* a command failure.
    fn discovery_control(&self, action: &str, run_id: &str) -> Result<DiscoveryRun, String> {
        let out = self.run(&["discovery-control".into(), action.to_string(), run_id.to_string()])?;
        serde_json::from_str(&out).map_err(|err| err.to_string())
    }

    /// Synchronously stop any active discovery campaign, mirroring a user Stop.
    ///
    /// Called at application exit. Reads the real campaign state; only an
    /// active (``running`` / ``paused``) campaign is stopped, targeted by its
    /// explicit ``run_id``. The bridge performs the SIGCONT→SIGTERM→(SIGKILL)
    /// escalation and only records ``cancelled`` once the process tree is
    /// verified gone — so a successful return means no discovery process from
    /// this instance remains. Terminal campaigns are left untouched.
    fn stop_active_discovery(&self) -> Result<DiscoveryRun, String> {
        let run = self.discovery_status()?;
        if !matches!(run.status.as_str(), "running" | "paused") {
            return Ok(run);
        }
        let run_id = run.run_id.as_deref().unwrap_or("");
        if run_id.is_empty() {
            return Err("active discovery run has no run_id".to_string());
        }
        self.discovery_control("stop", run_id)
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

    /// JSON candidates of a discovery campaign (a specific run_id, or the
    /// latest run when none is given).
    fn discovery_results(&self, run_id: &str) -> Result<DiscoveryResults, String> {
        let mut args = vec!["discovery-results".to_string()];
        if !run_id.is_empty() {
            args.push(run_id.to_string());
        }
        let out = self.run(&args)?;
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

    /// Launch a collection campaign as a *detached* background subprocess.
    ///
    /// Exactly the discovery pattern: the Core's ``collect_campaign`` is a long
    /// operation, so the Rust shell only starts it (the Core records the run's
    /// lifecycle — and its PID — in the store) and returns immediately. The
    /// frontend observes it via ``collection_status`` and stops it via
    /// ``collection_control``; the interface never blocks on the run.
    ///
    /// ``start_date`` / ``end_date`` (ISO dates) are **optional**: when given,
    /// both must be present and ordered (``start_date <= end_date``) — they are
    /// forwarded so the Core can bound its collection plan to the same
    /// publication-date window the user just discovered. A detached spawn would
    /// swallow a bridge refusal, so the precondition is enforced here
    /// synchronously.
    ///
    /// Only one campaign may run at a time: the Core refuses a second launch,
    /// but the detached spawn would swallow that refusal — so the active state
    /// is re-read here *before* spawning, and a busy campaign surfaces a
    /// synchronous error instead of a silent no-op. The Core guard remains the
    /// authority for the racing case.
    fn collection_run(
        &self,
        start_date: Option<String>,
        end_date: Option<String>,
    ) -> Result<CollectionRunId, String> {
        let start = start_date.filter(|s| !s.trim().is_empty());
        let end = end_date.filter(|s| !s.trim().is_empty());
        if start.is_some() != end.is_some() {
            return Err("Collection's date window requires both start_date and end_date.".to_string());
        }
        if start.as_deref() > end.as_deref() {
            return Err("start_date must be <= end_date".to_string());
        }
        let active = self.collection_status("")?;
        if active.status == "running" {
            let run_id = active.run_id.as_deref().unwrap_or("<unknown>");
            return Err(format!("a collection campaign is already active: {run_id}"));
        }
        // Mint the campaign's identity *before* spawning, so the caller can
        // return it synchronously and follow exactly this run — never "latest".
        let run_id = self.collection_run_id()?.run_id;
        let mut args = vec!["collection-run".to_string()];
        args.push("--run-id".into());
        args.push(run_id.clone());
        if let Some(s) = start {
            args.push("--start-date".into());
            args.push(s);
        }
        if let Some(e) = end {
            args.push("--end-date".into());
            args.push(e);
        }
        let mut child = self.spawn_detached(&args, "ARGUS_COLLECTION_DETACHED")?;
        let pid = child.id();
        // Pre-register the run *in the launcher* so it is observable the instant
        // the frontend receives its id — the detached subprocess is still
        // booting and will adopt the same row itself. If the Core refuses the
        // claim (a competing campaign already owns the slot — the detached spawn
        // would have swallowed that), the just-spawned child must not be left
        // running: terminate it and surface the refusal synchronously.
        if let Err(err) = self.collection_run_begin(&run_id, pid) {
            let _ = child.kill();
            return Err(err);
        }
        drop(child); // the campaign continues as a detached background process
        Ok(CollectionRunId { run_id })
    }

    /// Mint a fresh collection-run identifier from the Core (no side effect).
    /// The Core owns the id format; the launcher only relays it.
    fn collection_run_id(&self) -> Result<CollectionRunId, String> {
        let out = self.run(&["collection-run-id".into()])?;
        serde_json::from_str(&out).map_err(|err| err.to_string())
    }

    /// Real lifecycle control of a collection campaign, targeted by its
    /// ``run_id``: the bridge signals the recorded PID and returns the updated
    /// run lifecycle. Collection has exactly one control — ``stop``, which is a
    /// real cancellation (SIGTERM→SIGKILL escalation, ``cancelled`` recorded
    /// only once the process is verified gone). The id addresses an explicit
    /// campaign, never an implicit "latest".
    fn collection_control(&self, action: &str, run_id: &str) -> Result<CollectionRun, String> {
        let out = self.run(&["collection-control".into(), action.to_string(), run_id.to_string()])?;
        serde_json::from_str(&out).map_err(|err| err.to_string())
    }

    /// Pre-register a collection campaign row *in the launcher* (the bridge's
    /// ``collection-run-begin``), with the just-spawned subprocess's PID, so the
    /// run is observable the instant the frontend receives its id — even while
    /// the detached subprocess is still booting. The campaign later adopts the
    /// same row itself (self-adoption in the Core).
    fn collection_run_begin(&self, run_id: &str, pid: u32) -> Result<CollectionRun, String> {
        let out = self.run(&[
            "collection-run-begin".into(),
            "--run-id".into(),
            run_id.to_string(),
            "--pid".into(),
            pid.to_string(),
        ])?;
        serde_json::from_str(&out).map_err(|err| err.to_string())
    }

    /// Synchronously stop any active collection campaign, mirroring a user Stop.
    ///
    /// Called at application exit. Reads the real campaign state; only an
    /// active (``running``) campaign is stopped, targeted by its explicit
    /// ``run_id``. Terminal campaigns are left untouched.
    fn stop_active_collection(&self) -> Result<CollectionRun, String> {
        let run = self.collection_status("")?;
        if run.status != "running" {
            return Ok(run);
        }
        let run_id = run.run_id.as_deref().unwrap_or("");
        if run_id.is_empty() {
            return Err("active collection run has no run_id".to_string());
        }
        self.collection_control("stop", run_id)
    }

    /// JSON summary of a collection campaign (``run_id`` explicit, or the most
    /// recent when empty). The frontend passes the id it is following so the
    /// report never drifts to a different campaign than the one being polled.
    fn collection_status(&self, run_id: &str) -> Result<CollectionRun, String> {
        let args = if run_id.is_empty() {
            vec!["collection-status".to_string()]
        } else {
            vec!["collection-status".to_string(), "--run-id".into(), run_id.to_string()]
        };
        let out = self.run(&args)?;
        serde_json::from_str(&out).map_err(|err| err.to_string())
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
    status: String, // idle | running | paused | completed | failed | cancelled | stopped (legacy)
    started_at: Option<String>,
    finished_at: Option<String>,
    error: Option<String>,
    candidates: i64,
    banks: Vec<String>,
    pid: Option<i64>,
    date_start: Option<String>,
    date_end: Option<String>,
    sources_total: i64,
    sources_completed: i64,
    new: i64,
    known: i64,
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
    runs_preserved: i64,
    candidates_cleared: i64,
}

// The identity of a newly-launched discovery campaign (returned by
// `run_discovery` so the frontend can follow exactly this run).
#[derive(Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
struct DiscoveryRunId {
    run_id: String,
}

// Collection campaign contract (mirrors the bridge's snake_case JSON). The
// Core is the single source of truth for every counter: `publications_total`
// is fixed at launch and `publications_completed` advances as workers really
// finish, so the GUI only reflects what the Core recorded. Statuses:
// idle | running | completed | failed | cancelled.
#[derive(Debug, Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
struct CollectionRun {
    run_id: Option<String>,
    status: String,
    started_at: Option<String>,
    finished_at: Option<String>,
    error: Option<String>,
    banks: Vec<String>,
    pid: Option<i64>,
    force: bool,
    date_start: Option<String>,
    date_end: Option<String>,
    publications_total: i64,
    publications_completed: i64,
}

// The identity of a newly-launched collection campaign (returned by
// `run_collection` so the frontend can follow exactly this run).
#[derive(Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
struct CollectionRunId {
    run_id: String,
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
) -> Result<DiscoveryRunId, String> {
    state.discovery_run(start_date, end_date)
}

#[tauri::command]
fn discovery_control(state: State<'_, Bridge>, action: String, run_id: String) -> Result<DiscoveryRun, String> {
    state.discovery_control(&action, &run_id)
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
fn get_discovery_results(
    state: State<'_, Bridge>,
    run_id: Option<String>,
) -> Result<DiscoveryResults, String> {
    state.discovery_results(run_id.as_deref().unwrap_or(""))
}

#[tauri::command]
fn run_collection(
    state: State<'_, Bridge>,
    start_date: Option<String>,
    end_date: Option<String>,
) -> Result<CollectionRunId, String> {
    state.collection_run(start_date, end_date)
}

#[tauri::command]
fn collection_control(state: State<'_, Bridge>, action: String, run_id: String) -> Result<CollectionRun, String> {
    state.collection_control(&action, &run_id)
}

#[tauri::command]
fn get_collection_status(
    state: State<'_, Bridge>,
    run_id: Option<String>,
) -> Result<CollectionRun, String> {
    state.collection_status(run_id.as_deref().unwrap_or(""))
}

#[tauri::command]
fn open_url(state: State<'_, Bridge>, url: String) -> Result<(), String> {
    state.open_url(&url)
}

/// Stop any active discovery or collection campaign when the application is
/// closing.
///
/// Called from the run loop on both ``ExitRequested`` (the documented
/// interception point) and ``Exit`` (which is what macOS actually emits on a
/// terminate/AppleScript quit — see the shutdown smoke tests). Runs entirely in
/// Rust, independent of the React frontend, and blocks until each active
/// campaign is verified gone (or reported as error), so no orphan process
/// survives Argus.
fn stop_active_campaigns_on_exit<R: tauri::Runtime>(app_handle: &tauri::AppHandle<R>) {
    let Some(bridge) = app_handle.try_state::<Bridge>() else {
        return;
    };
    match bridge.stop_active_discovery() {
        Ok(run) => eprintln!(
            "argus: shutdown: discovery run {} finalized as {}",
            run.run_id.as_deref().unwrap_or("(none)"),
            run.status
        ),
        Err(err) => eprintln!("argus: shutdown: {err}"),
    }
    match bridge.stop_active_collection() {
        Ok(run) => eprintln!(
            "argus: shutdown: collection run {} finalized as {}",
            run.run_id.as_deref().unwrap_or("(none)"),
            run.status
        ),
        Err(err) => eprintln!("argus: shutdown: {err}"),
    }
}

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
            run_collection,
            collection_control,
            get_collection_status,
            open_url
        ])
        .build(tauri::generate_context!())
        .expect("error while building Argus")
        .run(|app_handle, event| {
            if let tauri::RunEvent::ExitRequested { .. } = event {
                stop_active_campaigns_on_exit(&app_handle);
            }
            if let tauri::RunEvent::Exit = event {
                stop_active_campaigns_on_exit(&app_handle);
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    static TEST_ROOT_SEQ: AtomicUsize = AtomicUsize::new(0);

    /// A bridge pinned to a unique per-test temp root, so store-opening tests
    /// never create or touch the repository's real `data/` directory. The
    /// interpreter and `PYTHONPATH` still come from the repository venv.
    fn temp_bridge() -> Bridge {
        let seq = TEST_ROOT_SEQ.fetch_add(1, Ordering::Relaxed);
        let dir = std::env::temp_dir().join(format!(
            "argus-test-{}-{seq}",
            std::process::id()
        ));
        std::fs::create_dir_all(&dir).expect("create temp test store root");
        Bridge::with_root(dir)
    }

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
        // temp store may hold zero runs (idle) or a previous campaign.
        let bridge = temp_bridge();
        if !bridge.root.join(".venv").join("bin").join("python").is_file() {
            return;
        }
        let run = bridge.discovery_status().expect("bridge must return discovery status");
        assert!(
            matches!(run.status.as_str(), "idle" | "running" | "completed" | "failed" | "cancelled" | "stopped"),
            "unexpected status: {}",
            run.status
        );
        assert!(run.candidates >= 0);
        // The Core-driven source progression is part of the discovery-status
        // contract (an idle run guarantees 0/0).
        assert!(run.sources_total >= 0);
        assert!(run.sources_completed >= 0);
        if run.status == "idle" {
            assert_eq!(run.sources_total, 0);
            assert_eq!(run.sources_completed, 0);
        }
    }

    #[test]
    fn discovery_run_id_is_minted_by_core() {
        // The launcher asks the Core to mint a run identity (no side effect).
        // It must be non-empty and unique across mints — this is the id the
        // frontend follows, so it can never be empty or reused.
        let bridge = temp_bridge();
        if !bridge.root.join(".venv").join("bin").join("python").is_file() {
            return;
        }
        let a = bridge.discovery_run_id().expect("mint a run id");
        let b = bridge.discovery_run_id().expect("mint another run id");
        assert!(!a.run_id.is_empty());
        assert!(!b.run_id.is_empty());
        assert!(a.run_id != b.run_id, "run ids must be unique: {} vs {}", a.run_id, b.run_id);
    }

    #[test]
    fn discovery_run_requires_complete_ordered_window() {
        // The launch precondition must be enforced synchronously *before* any
        // store or subprocess access, so a valid-launch case is never tested
        // here (it would spawn a real detached campaign against the real
        // store). These refusals must not touch the store at all.
        let bridge = Bridge::new();
        if !bridge.root.join(".venv").join("bin").join("python").is_file() {
            return;
        }
        assert!(bridge.discovery_run(None, None).is_err());
        assert!(bridge.discovery_run(None, Some("2026-01-01".into())).is_err());
        assert!(bridge.discovery_run(Some("2026-01-01".into()), None).is_err());
        assert!(bridge.discovery_run(Some("2026-01-01".into()), Some("".into())).is_err());
        assert!(bridge.discovery_run(Some("".into()), Some("2026-01-01".into())).is_err());
        assert!(bridge.discovery_run(Some("2026-12-31".into()), Some("2026-01-01".into())).is_err());
    }

    #[test]
    #[cfg(unix)]
    fn detached_spawn_isolates_process_group() {
        // The campaign spawn uses process_group(0) so the child is its own
        // process-group leader (pgid == pid) — this is how the bridge kills the
        // whole tree on shutdown via a negative-pid signal, never unrelated
        // processes.
        use std::io::Read;
        use std::os::unix::process::CommandExt;

        let bridge = Bridge::new();
        let mut child = Command::new(&bridge.python)
            .arg("-c")
            .arg("import os; print(os.getpid(), os.getpgrp(), flush=True)")
            .process_group(0)
            .stdout(Stdio::piped())
            .spawn()
            .expect("spawn python");
        let mut out = String::new();
        child
            .stdout
            .take()
            .expect("stdout")
            .read_to_string(&mut out)
            .expect("read stdout");
        assert!(child.wait().expect("wait").success(), "python must run");
        let parts: Vec<i64> = out
            .split_whitespace()
            .map(|p| p.parse().expect("int"))
            .collect();
        assert_eq!(parts.len(), 2, "output: {out}");
        assert_eq!(parts[0], parts[1], "pgid must equal the child pid (own group): {out}");
    }

    #[test]
    fn shutdown_noop_when_no_active_campaign() {
        // The exit handler must never transform a terminal campaign. Read the
        // temp store; only when nothing is active is stop_active_discovery
        // exercised (a live active campaign is never stopped from a unit test).
        let bridge = temp_bridge();
        if !bridge.root.join(".venv").join("bin").join("python").is_file() {
            return;
        }
        let run = bridge.discovery_status().expect("bridge must return discovery status");
        if matches!(run.status.as_str(), "running" | "paused") {
            return;
        }
        let after = bridge.stop_active_discovery().expect("shutdown no-op must succeed");
        assert_eq!(after.status, run.status, "terminal state must be unchanged");
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

    #[test]
    fn collection_status_is_readable() {
        // Read-only: never launches a campaign, never mutates the store. The
        // temp store may hold zero runs (idle) or a previous campaign.
        let bridge = temp_bridge();
        if !bridge.root.join(".venv").join("bin").join("python").is_file() {
            return;
        }
        let run = bridge.collection_status("").expect("bridge must return collection status");
        assert!(
            matches!(run.status.as_str(), "idle" | "running" | "completed" | "failed" | "cancelled"),
            "unexpected status: {}",
            run.status
        );
        // The Core-driven publication progression is part of the
        // collection-status contract (an idle run guarantees 0/0).
        assert!(run.publications_total >= 0);
        assert!(run.publications_completed >= 0);
        assert!(run.publications_completed <= run.publications_total);
        if run.status == "idle" {
            assert_eq!(run.publications_total, 0);
            assert_eq!(run.publications_completed, 0);
            assert!(run.run_id.is_none());
        }
    }

    #[test]
    fn collection_run_id_is_minted_by_core() {
        // The launcher asks the Core to mint a run identity (no side effect).
        // It must be non-empty and unique across mints — this is the id the
        // frontend follows, so it can never be empty or reused.
        let bridge = temp_bridge();
        if !bridge.root.join(".venv").join("bin").join("python").is_file() {
            return;
        }
        let a = bridge.collection_run_id().expect("mint a run id");
        let b = bridge.collection_run_id().expect("mint another run id");
        assert!(!a.run_id.is_empty());
        assert!(!b.run_id.is_empty());
        assert!(a.run_id != b.run_id, "run ids must be unique: {} vs {}", a.run_id, b.run_id);
    }

    #[test]
    fn collection_run_begin_pre_registers_observable_run() {
        // `collection-run-begin` claims the run *in the launcher* with the
        // just-spawned subprocess's PID, so the row exists (status running) the
        // instant the frontend receives the id — the detailed bootstrap comes
        // later, when the detached subprocess self-adopts the same row.
        let bridge = temp_bridge();
        if !bridge.root.join(".venv").join("bin").join("python").is_file() {
            return;
        }
        let id = bridge.collection_run_id().expect("mint a run id");
        let me = std::process::id();
        let run = bridge
            .collection_run_begin(&id.run_id, me)
            .expect("pre-registration must succeed");
        assert_eq!(run.run_id.as_deref(), Some(id.run_id.as_str()));
        assert_eq!(run.status, "running");
        assert_eq!(run.pid, Some(me as i64));
        assert_eq!(run.publications_total, 0);
    }

    #[test]
    fn collection_run_begin_refused_when_different_campaign_active() {
        // A pre-registration is a claim on the single-active slot: a *different*
        // active campaign is refused (the launcher then kills its child), so a
        // racing begin cannot silently double-book.
        let bridge = temp_bridge();
        if !bridge.root.join(".venv").join("bin").join("python").is_file() {
            return;
        }
        let me = std::process::id();
        bridge
            .collection_run_begin("occupier", me)
            .expect("first pre-registration claims the slot");
        let id = bridge.collection_run_id().expect("mint a run id");
        let err = match bridge.collection_run_begin(&id.run_id, me) {
            Ok(run) => panic!("a competing campaign must refuse the claim: {run:?}"),
            Err(err) => err,
        };
        assert!(err.contains("already active"), "unexpected error: {err}");
    }

    #[test]
    fn collection_status_never_substitutes_latest_for_unknown_run() {
        // An explicit `--run-id` addresses exactly that campaign: an unknown id
        // is reported as idle — never silently replaced by a more recent run
        // that happens to exist (the poll-loop bug that resurrected a stale
        // terminal campaign during cold boot).
        let bridge = temp_bridge();
        if !bridge.root.join(".venv").join("bin").join("python").is_file() {
            return;
        }
        let known = bridge.collection_run_id().expect("mint a run id");
        let me = std::process::id();
        bridge
            .collection_run_begin(&known.run_id, me)
            .expect("pre-register a run");
        let explicit = bridge
            .collection_status(&known.run_id)
            .expect("explicit status must return the requested run");
        assert_eq!(explicit.run_id.as_deref(), Some(known.run_id.as_str()));
        assert_eq!(explicit.status, "running");

        let unknown = bridge
            .collection_status("never-minted")
            .expect("unknown run must be reported as idle — never substituted");
        assert_eq!(unknown.run_id, None);
        assert_eq!(unknown.status, "idle");
    }

    #[test]
    fn collection_run_rejects_imbalanced_or_reversed_window() {
        // Collection's date window is optional, but when given it must be
        // complete and ordered. These refusals are enforced synchronously
        // *before* any store access or detached spawn, so a valid-launch case
        // (both empty, or a complete window) is never tested here — those would
        // spawn a real detached campaign against the real store.
        let bridge = Bridge::new();
        if !bridge.root.join(".venv").join("bin").join("python").is_file() {
            return;
        }
        // One bound without the other.
        assert!(bridge.collection_run(None, Some("2026-01-01".into())).is_err());
        assert!(bridge.collection_run(Some("2026-01-01".into()), None).is_err());
        // Reversed window.
        assert!(bridge
            .collection_run(Some("2026-12-31".into()), Some("2026-01-01".into()))
            .is_err());
    }

    #[test]
    fn shutdown_noop_when_no_active_collection() {
        // The exit handler must never transform a terminal campaign. Read the
        // temp store; only when nothing is active is stop_active_collection
        // exercised (a live active campaign is never stopped from a unit test).
        let bridge = temp_bridge();
        if !bridge.root.join(".venv").join("bin").join("python").is_file() {
            return;
        }
        let run = bridge.collection_status("").expect("bridge must return collection status");
        if run.status == "running" {
            return;
        }
        let after = bridge.stop_active_collection().expect("shutdown no-op must succeed");
        assert_eq!(after.status, run.status, "terminal state must be unchanged");
    }

    #[test]
    fn collection_control_rejects_unknown_action() {
        // The bridge refuses actions other than `stop` (collection has no
        // pause/resume), surfaced as a command failure — never a silent no-op.
        let bridge = temp_bridge();
        if !bridge.root.join(".venv").join("bin").join("python").is_file() {
            return;
        }
        assert!(bridge.collection_control("pause", "whatever").is_err());
        assert!(bridge.collection_control("resume", "whatever").is_err());
    }
}
