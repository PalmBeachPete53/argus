import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { ClearedCache, DiscoveryCandidate, DiscoveryRun } from "../types";

const IDLE: DiscoveryRun = {
  run_id: null,
  status: "idle",
  started_at: null,
  finished_at: null,
  error: null,
  candidates: 0,
  banks: [],
  pid: null,
  date_start: null,
  date_end: null,
  new: 0,
  known: 0,
};

const POLL_MS = 1200;

// States in which the campaign subprocess is still alive and worth polling.
const ACTIVE: ReadonlySet<string> = new Set(["running", "paused"]);
const TERMINAL: ReadonlySet<string> = new Set(["completed", "stopped", "failed"]);

/**
 * Discovery campaign state machine shared by the Discovery view and the
 * footer.
 *
 * Launching runs the campaign in a detached backend subprocess (Rust spawns
 * `python -m argus.gui_bridge discovery-run`); the interface never blocks. The
 * Core records the lifecycle in the store, so this hook only observes it:
 * - mount / after launch → poll `discovery_status` until it leaves an active
 *   state (a *paused* campaign is still alive, so polling keeps going);
 * - on a terminal state → fetch `discovery_results` once, so the candidate
 *   list always mirrors what the backend actually holds (no stale React copy);
 * - `failed` / `stopped` → surface the Core-provided state.
 * The real lifecycle controls (pause / resume / stop) first re-read the
 * authoritative status, then signal the campaign's recorded PID through
 * `discovery_control` targeted at that run_id; clear-cache drops only the
 * Core's discovery report tables and re-reads the state afterwards.
 * No progress percentage is invented — only the Core's own state is shown.
 */
export function useDiscovery() {
  const [status, setStatus] = useState<DiscoveryRun>(IDLE);
  const [candidates, setCandidates] = useState<DiscoveryCandidate[]>([]);
  const [error, setError] = useState<string | null>(null);
  const loopActiveRef = useRef(false);
  // Mirrors `status` for event handlers that must not go stale (e.g. control
  // resolving the *current* campaign's run_id without re-subscribing).
  const statusRef = useRef<DiscoveryRun>(IDLE);

  const fetchResults = useCallback(async (runId: string | null) => {
    if (!runId) return;
    try {
      const results = await invoke<import("../types").DiscoveryResults>("get_discovery_results", {
        runId,
      });
      setCandidates(results.candidates ?? []);
    } catch (err) {
      setError(String(err));
    }
  }, []);

  const check = useCallback(async (): Promise<DiscoveryRun["status"]> => {
    try {
      const run = await invoke<DiscoveryRun>("get_discovery_status");
      setStatus(run);
      statusRef.current = run;
      if (TERMINAL.has(run.status)) {
        await fetchResults(run.run_id);
      }
      return run.status;
    } catch (err) {
      setError(String(err));
      return "failed";
    }
  }, [fetchResults]);

  const startLoop = useCallback(() => {
    if (loopActiveRef.current) return;
    loopActiveRef.current = true;
    void (async () => {
      try {
        for (;;) {
          const state = await check();
          if (!ACTIVE.has(state)) break;
          await new Promise((resolve) => setTimeout(resolve, POLL_MS));
        }
      } finally {
        loopActiveRef.current = false;
      }
    })();
  }, [check]);

  // Observe an already-running campaign on mount (e.g. app reopened while a
  // background discovery was in progress).
  useEffect(() => {
    startLoop();
    return () => {
      loopActiveRef.current = false;
    };
  }, [startLoop]);

  const launch = useCallback(
    async (startDate?: string, endDate?: string) => {
      setError(null);
      try {
        const args: Record<string, string> = {};
        if (startDate) args.startDate = startDate;
        if (endDate) args.endDate = endDate;
        await invoke("run_discovery", args);
      } catch (err) {
        setError(String(err));
        return false;
      }
      setCandidates([]);
      const optimistic: DiscoveryRun = { ...statusRef.current, status: "running" };
      setStatus(optimistic);
      statusRef.current = optimistic;
      startLoop();
      return true;
    },
    [startLoop],
  );

  const control = useCallback(
    async (action: "pause" | "resume" | "stop") => {
      setError(null);
      // Re-read the authoritative lifecycle first so the command targets the
      // *current* campaign's run_id (never an implicit "latest" or a stale
      // optimistic copy held by React).
      try {
        await check();
      } catch {
        // check() never throws (it surfaces errors through `error`), but keep
        // control resilient if the backend is temporarily unreachable.
      }
      const runId = statusRef.current?.run_id ?? null;
      try {
        const run = await invoke<DiscoveryRun>("discovery_control", {
          action,
          runId: runId ?? "",
        });
        setStatus(run);
        statusRef.current = run;
      } catch (err) {
        setError(String(err));
        return false;
      }
      return true;
    },
    [check],
  );

  const pause = useCallback(() => control("pause"), [control]);
  const resume = useCallback(() => control("resume"), [control]);
  const stop = useCallback(() => control("stop"), [control]);

  const clearCache = useCallback(async () => {
    setError(null);
    try {
      const cleared = await invoke<ClearedCache>("clear_discovery_cache");
      setCandidates([]);
      // Clearing drops the candidate snapshots *and* their report — the last
      // campaign record survives (history preserved), so the authoritative
      // state is re-read rather than assumed.
      await check();
      return cleared;
    } catch (err) {
      setError(String(err));
      return null;
    }
  }, [check]);

  const openUrl = useCallback(async (url: string) => {
    try {
      await invoke("open_url", { url });
    } catch (err) {
      setError(String(err));
    }
  }, []);

  return { status, candidates, error, launch, pause, resume, stop, clearCache, openUrl };
}
