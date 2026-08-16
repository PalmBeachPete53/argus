import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { ClearedCache, DiscoveryCandidate, DiscoveryRun, DiscoveryRunId } from "../types";
import { nextPollStep } from "../lib/discoveryPoll";

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
  sources_total: 0,
  sources_completed: 0,
  new: 0,
  known: 0,
};

const POLL_MS = 1200;
/** How long to keep waiting for a just-launched campaign to appear. */
const LAUNCH_TIMEOUT_MS = 60_000;

/**
 * Discovery campaign state machine shared by the Discovery view and the
 * footer.
 *
 * Launching returns the campaign's `run_id` (the Rust shell mints it through
 * the Core *before* spawning the detached subprocess), so the poll loop can
 * follow *exactly that run* instead of racing with the previous terminal one:
 * - after launch → "starting" phase: keep polling until `run_id` appears, and
 *   never interpret the old completed run as the new campaign's end;
 * - once the run is observed → follow it (live source progression) until it
 *   reaches `completed` / `stopped` / `failed`, then fetch results once;
 * - on mount → observe: adopt an already-active campaign, or render the
 *   terminal/idle state a single time.
 * The real lifecycle controls (pause / resume / stop) re-read the
 * authoritative status and target the recorded run's PID via `discovery_control`.
 * No progress percentage is invented — only the Core's own state is shown.
 */
export function useDiscovery() {
  const [status, setStatus] = useState<DiscoveryRun>(IDLE);
  const [candidates, setCandidates] = useState<DiscoveryCandidate[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  // Mirrors `status` for event handlers that must not go stale.
  const statusRef = useRef<DiscoveryRun>(IDLE);
  // The campaign being followed (null = observing on mount, no known target).
  const targetRunIdRef = useRef<string | null>(null);
  // True while waiting for `targetRunIdRef.current` to first appear.
  const startingRef = useRef(false);
  // Bumped to supersede/cancel a running loop (launch or unmount).
  const loopTokenRef = useRef(0);

  const readStatus = useCallback(async (): Promise<DiscoveryRun> => {
    return invoke<DiscoveryRun>("get_discovery_status");
  }, []);

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

  const applyStatus = useCallback((run: DiscoveryRun) => {
    setStatus(run);
    statusRef.current = run;
  }, []);

  const setStartingPhase = useCallback((value: boolean) => {
    startingRef.current = value;
    setStarting(value);
  }, []);

  const sleep = useCallback(() => new Promise((resolve) => setTimeout(resolve, POLL_MS)), []);

  const startLoop = useCallback(() => {
    const token = ++loopTokenRef.current;
    const launchDeadline = Date.now() + LAUNCH_TIMEOUT_MS;
    void (async () => {
      for (;;) {
        if (token !== loopTokenRef.current) return;
        let observed: DiscoveryRun;
        try {
          observed = await readStatus();
        } catch (err) {
          if (token !== loopTokenRef.current) return;
          setError(String(err));
          if (startingRef.current && Date.now() > launchDeadline) {
            setError("Discovery did not start in time.");
            setStartingPhase(false);
            return;
          }
          await sleep();
          continue;
        }
        if (token !== loopTokenRef.current) return;

        const startingDisplay: DiscoveryRun = {
          ...statusRef.current,
          status: "running",
          run_id: targetRunIdRef.current,
          sources_completed: 0,
          sources_total: 0,
        };
        const step = nextPollStep(
          targetRunIdRef.current,
          startingRef.current,
          observed,
          startingDisplay,
        );
        if (step.adoptTarget) targetRunIdRef.current = step.adoptTarget;
        if (step.stopWaiting) setStartingPhase(false);
        applyStatus(step.display);
        if (step.fetchResults) await fetchResults(step.display.run_id);
        if (token !== loopTokenRef.current) return;
        if (!step.keepPolling) return;
        if (startingRef.current && Date.now() > launchDeadline) {
          setError("Discovery did not start in time.");
          setStartingPhase(false);
          return;
        }
        await sleep();
      }
    })();
  }, [readStatus, fetchResults, applyStatus, setStartingPhase, sleep]);

  // Observe an already-running campaign on mount (e.g. app reopened while a
  // background discovery was in progress).
  useEffect(() => {
    startLoop();
    return () => {
      loopTokenRef.current += 1;
    };
  }, [startLoop]);

  const launch = useCallback(
    async (startDate?: string, endDate?: string) => {
      setError(null);
      let runId: string;
      try {
        const args: Record<string, string> = {};
        if (startDate) args.startDate = startDate;
        if (endDate) args.endDate = endDate;
        const launched = await invoke<DiscoveryRunId>("run_discovery", args);
        runId = launched.run_id;
      } catch (err) {
        setError(String(err));
        return false;
      }
      setCandidates([]);
      // The campaign is not in the store yet — follow its known identity and
      // show an explicit "Starting…" state (the stale previous run's numbers
      // are cleared, never mistaken for the new campaign).
      targetRunIdRef.current = runId;
      setStartingPhase(true);
      const optimistic: DiscoveryRun = {
        ...statusRef.current,
        status: "running",
        run_id: runId,
        sources_completed: 0,
        sources_total: 0,
        date_start: startDate ?? statusRef.current.date_start,
        date_end: endDate ?? statusRef.current.date_end,
      };
      applyStatus(optimistic);
      startLoop();
      return true;
    },
    [applyStatus, setStartingPhase, startLoop],
  );

  const control = useCallback(
    async (action: "pause" | "resume" | "stop") => {
      setError(null);
      // Re-read the authoritative lifecycle first so the command targets the
      // *current* campaign's run_id (never an implicit "latest" or a stale
      // optimistic copy held by React).
      let run: DiscoveryRun;
      try {
        run = await readStatus();
      } catch (err) {
        setError(String(err));
        return false;
      }
      const runId = run.run_id ?? targetRunIdRef.current ?? null;
      if (!runId) {
        setError(`cannot ${action}: no active campaign`);
        return false;
      }
      try {
        const updated = await invoke<DiscoveryRun>("discovery_control", {
          action,
          runId,
        });
        applyStatus(updated);
      } catch (err) {
        setError(String(err));
        return false;
      }
      return true;
    },
    [readStatus, applyStatus],
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
      const run = await readStatus();
      applyStatus(run);
      return cleared;
    } catch (err) {
      setError(String(err));
      return null;
    }
  }, [readStatus, applyStatus]);

  const openUrl = useCallback(async (url: string) => {
    try {
      await invoke("open_url", { url });
    } catch (err) {
      setError(String(err));
    }
  }, []);

  return {
    status,
    candidates,
    error,
    starting,
    launch,
    pause,
    resume,
    stop,
    clearCache,
    openUrl,
  };
}
