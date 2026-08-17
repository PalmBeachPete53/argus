import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { CollectionRun, CollectionRunId } from "../types";
import { nextCollectionPollStep, TERMINAL } from "../lib/collectionPoll";

const IDLE: CollectionRun = {
  run_id: null,
  status: "idle",
  started_at: null,
  finished_at: null,
  error: null,
  banks: [],
  pid: null,
  force: false,
  date_start: null,
  date_end: null,
  publications_total: 0,
  publications_completed: 0,
};

const POLL_MS = 1200;
/** How long to keep waiting for a just-launched campaign to appear. */
const LAUNCH_TIMEOUT_MS = 60_000;

/**
 * Collection campaign state machine, on the exact model of `useDiscovery`.
 *
 * Launching returns the campaign's `run_id` (the Rust shell mints it through
 * the Core *before* spawning the detached subprocess), so the poll loop can
 * follow *exactly that run* instead of racing with the previous terminal one:
 * - after launch → "starting" phase: keep polling until `run_id` appears, and
 *   never interpret the old completed run as the new campaign's end;
 * - once the run is observed → follow it (live per-publication progression)
 *   until it reaches `completed` / `cancelled` / `failed`;
 * - on mount → observe: adopt an already-active campaign, or render the
 *   terminal/idle state a single time.
 *
 * Stop is a real cancellation (`collection_control stop <run_id>` → the Core
 * terminates the campaign process → `cancelled`). There is no pause and no
 * resume: after a cancellation the Run action simply becomes available again,
 * and the hook never fabricates a `N / N` progression for interrupted work.
 * Only one campaign may run at a time; the backend enforces it, and the hook
 * mirrors it by refusing a launch while a campaign is active or starting.
 */
export function useCollection() {
  const [status, setStatus] = useState<CollectionRun>(IDLE);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  // Mirrors `status` for event handlers that must not go stale.
  const statusRef = useRef<CollectionRun>(IDLE);
  // The campaign being followed (null = observing on mount, no known target).
  const targetRunIdRef = useRef<string | null>(null);
  // True while waiting for `targetRunIdRef.current` to first appear.
  const startingRef = useRef(false);
  // Bumped to supersede/cancel a running loop (launch or unmount).
  const loopTokenRef = useRef(0);

  const readStatus = useCallback(async (): Promise<CollectionRun> => {
    // Ask for exactly the campaign being followed: the backend's
    // `collection-status --run-id <id>` reports that run alone (never silently
    // substituting the latest terminal run), so poll data can never drift to a
    // different campaign than the one the user is watching.
    const target = targetRunIdRef.current;
    const args = target ? { runId: target } : {};
    return invoke<CollectionRun>("get_collection_status", args);
  }, []);

  const applyStatus = useCallback((run: CollectionRun) => {
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
        let observed: CollectionRun;
        try {
          observed = await readStatus();
        } catch (err) {
          if (token !== loopTokenRef.current) return;
          setError(String(err));
          if (startingRef.current && Date.now() > launchDeadline) {
            setError("Collection did not start in time.");
            setStartingPhase(false);
            return;
          }
          await sleep();
          continue;
        }
        if (token !== loopTokenRef.current) return;

        const fallbackDisplay: CollectionRun = {
          ...statusRef.current,
          run_id: targetRunIdRef.current,
        };
        const step = nextCollectionPollStep(
          targetRunIdRef.current,
          startingRef.current,
          observed,
          fallbackDisplay,
        );
        if (step.adoptTarget) targetRunIdRef.current = step.adoptTarget;
        if (step.stopWaiting) setStartingPhase(false);
        applyStatus(step.display);
        if (token !== loopTokenRef.current) return;
        if (!step.keepPolling) return;
        if (startingRef.current && Date.now() > launchDeadline) {
          setError("Collection did not start in time.");
          setStartingPhase(false);
          return;
        }
        await sleep();
      }
    })();
  }, [readStatus, applyStatus, setStartingPhase, sleep]);

  // Observe an already-running campaign on mount (e.g. app reopened while a
  // background collection was in progress).
  useEffect(() => {
    startLoop();
    return () => {
      loopTokenRef.current += 1;
    };
  }, [startLoop]);

  const launch = useCallback(
    async (startDate?: string, endDate?: string) => {
      setError(null);
      // Mirror the backend's single-active invariant so the UI never issues a
      // second launch while one is running or just starting.
      const current = statusRef.current;
      if (current.status === "running" || startingRef.current) {
        setError("a collection campaign is already active");
        return false;
      }
      let runId: string;
      try {
        const args: Record<string, string> = {};
        if (startDate) args.startDate = startDate;
        if (endDate) args.endDate = endDate;
        const launched = await invoke<CollectionRunId>("run_collection", args);
        runId = launched.run_id;
      } catch (err) {
        setError(String(err));
        return false;
      }
      // The campaign is not in the store yet — follow its known identity and
      // show an explicit "Starting…" state (the stale previous run's numbers
      // are cleared, never mistaken for the new campaign).
      targetRunIdRef.current = runId;
      setStartingPhase(true);
      const optimistic: CollectionRun = {
        ...statusRef.current,
        status: "running",
        run_id: runId,
        publications_completed: 0,
        publications_total: 0,
        date_start: startDate ?? statusRef.current.date_start,
        date_end: endDate ?? statusRef.current.date_end,
      };
      applyStatus(optimistic);
      startLoop();
      return true;
    },
    [applyStatus, setStartingPhase, startLoop],
  );

  const stop = useCallback(async () => {
    setError(null);
    // Re-read the authoritative lifecycle first so the command targets the
    // *current* campaign's run_id (never an implicit "latest" or a stale
    // optimistic copy held by React).
    let run: CollectionRun;
    try {
      run = await readStatus();
    } catch (err) {
      setError(String(err));
      return false;
    }
    // Prefer the run this hook is following (the campaign just launched, or the
    // one adopted on mount) — never an older terminal run that is still
    // "latest" until the target records itself.
    const followed = targetRunIdRef.current;
    const runId = followed ?? (run.status === "running" ? run.run_id : null) ?? null;
    if (!runId) {
      setError("cannot stop: no active collection campaign");
      return false;
    }
    // The followed run is already terminal in the store (e.g. its bootstrap
    // failed): adopt the authoritative state instead of asking the Core to stop
    // something that already ended.
    if (run.run_id === runId && TERMINAL.has(run.status)) {
      applyStatus(run);
      setStartingPhase(false);
      loopTokenRef.current += 1;
      return true;
    }
    try {
      const updated = await invoke<CollectionRun>("collection_control", {
        action: "stop",
        runId,
      });
      // Terminal, authoritative: apply it and supersede the polling loop (which
      // has not observed the cancellation yet) so no later poll can resurrect an
      // older state. There is nothing left to follow.
      applyStatus(updated);
      setStartingPhase(false);
      loopTokenRef.current += 1;
    } catch (err) {
      setError(String(err));
      return false;
    }
    return true;
  }, [readStatus, applyStatus, setStartingPhase]);

  return {
    status,
    error,
    starting,
    launch,
    stop,
  };
}
