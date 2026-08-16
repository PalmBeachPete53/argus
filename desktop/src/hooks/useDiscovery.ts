import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { DiscoveryCandidate, DiscoveryRun } from "../types";

const IDLE: DiscoveryRun = {
  run_id: null,
  status: "idle",
  started_at: null,
  finished_at: null,
  error: null,
  candidates: 0,
  banks: [],
};

const POLL_MS = 1200;

/**
 * Discovery campaign state machine shared by the Overview, the Discovery view
 * and the footer.
 *
 * Launching runs the campaign in a detached backend subprocess (Rust spawns
 * `python -m argus.gui_bridge discovery-run`); the interface never blocks. The
 * Core records the lifecycle in the store, so this hook only observes it:
 * - mount / after launch → poll `discovery_status` until it leaves `running`;
 * - on `completed` → fetch `discovery_results` once;
 * - `failed` → surface the Core-provided error.
 * No progress percentage is invented — only the Core's own state is shown.
 */
export function useDiscovery() {
  const [status, setStatus] = useState<DiscoveryRun>(IDLE);
  const [candidates, setCandidates] = useState<DiscoveryCandidate[]>([]);
  const [error, setError] = useState<string | null>(null);
  const loopActiveRef = useRef(false);

  const fetchResults = useCallback(async (runId: string | null) => {
    if (!runId) return;
    try {
      const results = await invoke<import("../types").DiscoveryResults>("get_discovery_results");
      setCandidates(results.candidates ?? []);
    } catch (err) {
      setError(String(err));
    }
  }, []);

  const check = useCallback(async (): Promise<DiscoveryRun["status"]> => {
    try {
      const run = await invoke<DiscoveryRun>("get_discovery_status");
      setStatus(run);
      if (run.status === "completed") {
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
          if (state !== "running") break;
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

  const launch = useCallback(async () => {
    setError(null);
    try {
      await invoke("run_discovery");
    } catch (err) {
      setError(String(err));
      return false;
    }
    setCandidates([]);
    setStatus((prev) => ({ ...prev, status: "running" }));
    startLoop();
    return true;
  }, [startLoop]);

  const openUrl = useCallback(async (url: string) => {
    try {
      await invoke("open_url", { url });
    } catch (err) {
      setError(String(err));
    }
  }, []);

  return { status, candidates, error, launch, openUrl };
}
