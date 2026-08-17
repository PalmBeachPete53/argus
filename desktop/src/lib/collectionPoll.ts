import type { CollectionRun } from "../types";

/**
 * Poll-loop state machine for the Collection lifecycle — pure and unit-tested
 * so the frontend hook stays a thin driver over it.
 *
 * The invariant enforced here is the same as Discovery's: the poll loop may
 * only stop once the run it is *following* reaches a terminal state. A
 * `collection-status` response for a *different* run (in particular an old
 * terminal run returned before a freshly launched campaign has recorded itself)
 * must never stop the loop, and never display that old run's numbers as the new
 * campaign's progression.
 *
 * Collection has no pause/resume and no cached results to fetch, so this is a
 * slimmer machine than Discovery's: it only decides keepPolling, what to
 * display, whether to adopt an observed active run (mount case) and whether the
 * just-launched target finally appeared (leaving the "starting" phase).
 */

/** Campaign states that are still alive and worth polling. */
export const ACTIVE = new Set(["running"]);
/** Campaign states after which polling stops (for the followed run). */
export const TERMINAL = new Set(["completed", "cancelled", "failed"]);

/** One decision the poll loop applies after observing a `collection-status`. */
export interface CollectionPollStep {
  /** Keep polling (sleep and read again). */
  keepPolling: boolean;
  /** What to render now. */
  display: CollectionRun;
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
 * @param observed the latest `collection-status` response.
 * @param startingDisplay the optimistic "Starting…" run shown while waiting.
 */
export function nextCollectionPollStep(
  targetRunId: string | null,
  starting: boolean,
  observed: CollectionRun,
  startingDisplay: CollectionRun,
): CollectionPollStep {
  const terminal = TERMINAL.has(observed.status);

  // Waiting for the just-launched run to record itself. The previous terminal
  // run is still "latest" until then — ignore it and keep polling.
  if (starting) {
    if (observed.run_id === targetRunId) {
      return {
        keepPolling: !terminal,
        display: observed,
        adoptTarget: null,
        stopWaiting: true,
      };
    }
    return {
      keepPolling: true,
      display: startingDisplay,
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
        adoptTarget: observed.run_id,
        stopWaiting: false,
      };
    }
    return {
      keepPolling: false,
      display: observed,
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
      adoptTarget: null,
      stopWaiting: false,
    };
  }

  return {
    keepPolling: !terminal,
    display: observed,
    adoptTarget: null,
    stopWaiting: false,
  };
}
