import type { DiscoveryRun } from "../types";

/**
 * Poll-loop state machine for the Discovery lifecycle — pure and unit-tested so
 * the frontend hook stays a thin driver over it.
 *
 * The invariant enforced here: the poll loop may only stop once the run it is
 * *following* reaches a terminal state. A `discovery-status` response for a
 * *different* run (in particular an old terminal run returned before a freshly
 * launched campaign has recorded itself) must never stop the loop.
 */

/** Campaign states that are still alive and worth polling. */
export const ACTIVE = new Set(["running", "paused"]);
/** Campaign states after which polling stops (for the followed run). */
export const TERMINAL = new Set(["completed", "cancelled", "stopped", "failed"]);

/** One decision the poll loop applies after observing a `discovery-status`. */
export interface PollStep {
  /** Keep polling (sleep and read again). */
  keepPolling: boolean;
  /** What to render now. */
  display: DiscoveryRun;
  /** Fetch the candidate results once (the followed run just ended). */
  fetchResults: boolean;
  /** Adopt this run_id as the followed target (mount-observe case). */
  adoptTarget: string | null;
  /** The waited-for target just appeared: leave the "starting" phase. */
  stopWaiting: boolean;
}

/**
 * Advance the poll loop one step.
 *
 * @param targetRunId the campaign being followed (null when observing on mount
 *   with no known target, or before a launch).
 * @param starting true while waiting for `targetRunId` to first appear after a
 *   launch — during this phase no other run may end the loop.
 * @param observed the latest `discovery-status` response.
 * @param startingDisplay the optimistic "Starting…" run shown while waiting.
 */
export function nextPollStep(
  targetRunId: string | null,
  starting: boolean,
  observed: DiscoveryRun,
  startingDisplay: DiscoveryRun,
): PollStep {
  const terminal = TERMINAL.has(observed.status);

  // Waiting for the just-launched run to record itself. The previous terminal
  // run is still "latest" until then — ignore it and keep polling.
  if (starting) {
    if (observed.run_id === targetRunId) {
      return {
        keepPolling: !terminal,
        display: observed,
        fetchResults: terminal,
        adoptTarget: null,
        stopWaiting: true,
      };
    }
    return {
      keepPolling: true,
      display: startingDisplay,
      fetchResults: false,
      adoptTarget: null,
      stopWaiting: false,
    };
  }

  // Observing on mount with no known target: adopt an active campaign, else
  // render the (terminal/idle) state once and stop.
  if (targetRunId === null) {
    if (observed.run_id && ACTIVE.has(observed.status)) {
      return {
        keepPolling: true,
        display: observed,
        fetchResults: false,
        adoptTarget: observed.run_id,
        stopWaiting: false,
      };
    }
    return {
      keepPolling: false,
      display: observed,
      fetchResults: terminal,
      adoptTarget: null,
      stopWaiting: false,
    };
  }

  // Following a known target. A response for a different run (defensive — the
  // backend is single-active, so this shouldn't happen) never ends the loop.
  if (observed.run_id !== targetRunId) {
    return {
      keepPolling: true,
      display: observed,
      fetchResults: false,
      adoptTarget: null,
      stopWaiting: false,
    };
  }

  return {
    keepPolling: !terminal,
    display: observed,
    fetchResults: terminal,
    adoptTarget: null,
    stopWaiting: false,
  };
}
