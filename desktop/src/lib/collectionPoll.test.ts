import { describe, expect, it } from "vitest";
import type { CollectionRun } from "../types";
import { nextCollectionPollStep } from "./collectionPoll";

/** A complete CollectionRun with the given overrides. */
function run(partial: Partial<CollectionRun> = {}): CollectionRun {
  return {
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
    ...partial,
  };
}

/** The optimistic run shown while waiting for the target to appear. */
function startingDisplay(target: string): CollectionRun {
  return run({ run_id: target, status: "running", publications_total: 0, publications_completed: 0 });
}

describe("nextCollectionPollStep", () => {
  it("does not stop polling on the old terminal run after a launch", () => {
    // Reproduces the race: the previous campaign is still "latest" right after
    // launching, so the first collection-status returns it (completed). The loop
    // must keep going and keep showing "Starting…", not treat it as the end.
    const target = "new";
    const oldCompleted = run({
      run_id: "old",
      status: "completed",
      publications_total: 283,
      publications_completed: 283,
    });

    const first = nextCollectionPollStep(target, true, oldCompleted, startingDisplay(target));
    expect(first.keepPolling).toBe(true);
    expect(first.stopWaiting).toBe(false);
    expect(first.display.run_id).toBe(target); // optimistic, not the old run
    expect(first.display.status).toBe("running");
    // the old run's numbers must never surface as the new campaign's progress
    expect(first.display.publications_completed).toBe(0);
    expect(first.display.publications_total).toBe(0);

    const newRunning = run({
      run_id: target,
      status: "running",
      publications_total: 12,
      publications_completed: 0,
    });
    const second = nextCollectionPollStep(target, true, newRunning, startingDisplay(target));
    expect(second.keepPolling).toBe(true);
    expect(second.stopWaiting).toBe(true);
    expect(second.display).toBe(newRunning);
  });

  it("keeps polling while the target runs and reports each progression 0/N → N/N", () => {
    const target = "new";
    const steps = [0, 1, 5, 12].map((c) =>
      nextCollectionPollStep(
        target,
        false,
        run({ run_id: target, status: "running", publications_total: 12, publications_completed: c }),
        startingDisplay(target),
      ),
    );

    steps.forEach((step) => expect(step.keepPolling).toBe(true));
    expect(steps.map((s) => s.display.publications_completed)).toEqual([0, 1, 5, 12]);

    const completed = nextCollectionPollStep(
      target,
      false,
      run({ run_id: target, status: "completed", publications_total: 12, publications_completed: 12 }),
      startingDisplay(target),
    );
    expect(completed.keepPolling).toBe(false);
    expect(completed.display.publications_completed).toBe(12);
    expect(completed.display.publications_total).toBe(12);
  });

  it("keeps the real partial progression on cancel (never fabricates total/total)", () => {
    const target = "new";
    const running = nextCollectionPollStep(
      target,
      false,
      run({ run_id: target, status: "running", publications_total: 283, publications_completed: 12 }),
      startingDisplay(target),
    );
    expect(running.keepPolling).toBe(true);

    const cancelled = nextCollectionPollStep(
      target,
      false,
      run({ run_id: target, status: "cancelled", publications_total: 283, publications_completed: 12 }),
      startingDisplay(target),
    );
    expect(cancelled.keepPolling).toBe(false);
    expect(cancelled.display.status).toBe("cancelled");
    expect(cancelled.display.publications_completed).toBe(12);
    expect(cancelled.display.publications_total).toBe(283);
    expect(cancelled.display.publications_completed).not.toBe(cancelled.display.publications_total);
  });

  it("stops on failed and keeps the partial progression with the error", () => {
    const target = "new";
    const failed = nextCollectionPollStep(
      target,
      false,
      run({
        run_id: target,
        status: "failed",
        error: "boom",
        publications_total: 283,
        publications_completed: 5,
      }),
      startingDisplay(target),
    );
    expect(failed.keepPolling).toBe(false);
    expect(failed.display.status).toBe("failed");
    expect(failed.display.error).toBe("boom");
    expect(failed.display.publications_completed).toBe(5);
  });

  it("a run that appears late (after starting) is adopted only when it matches the target", () => {
    // Several polls while "starting": the old terminal run stays visible, then
    // the awaited run finally records itself — polling follows it live.
    const target = "run-late";
    for (let i = 0; i < 3; i++) {
      const step = nextCollectionPollStep(
        target,
        true,
        run({ run_id: "old", status: "cancelled", publications_total: 5, publications_completed: 2 }),
        startingDisplay(target),
      );
      expect(step.keepPolling).toBe(true);
      expect(step.stopWaiting).toBe(false);
      expect(step.display.run_id).toBe(target);
    }
    const appears = nextCollectionPollStep(
      target,
      true,
      run({ run_id: target, status: "running", publications_total: 10, publications_completed: 0 }),
      startingDisplay(target),
    );
    expect(appears.stopWaiting).toBe(true);
    expect(appears.keepPolling).toBe(true);
    expect(appears.display).toMatchObject({ run_id: target, status: "running" });
  });

  it("adopts an active run when observing on mount with no target", () => {
    const active = run({
      run_id: "bg",
      status: "running",
      publications_total: 30,
      publications_completed: 3,
    });
    const step = nextCollectionPollStep(null, false, active, startingDisplay(""));
    expect(step.keepPolling).toBe(true);
    expect(step.adoptTarget).toBe("bg");
    expect(step.display).toBe(active);
  });

  it("stops after a single read when idle and observing", () => {
    const step = nextCollectionPollStep(null, false, run({ status: "idle" }), startingDisplay(""));
    expect(step.keepPolling).toBe(false);
    expect(step.display.status).toBe("idle");
  });

  it("renders a fresh terminal run once when observing and stops", () => {
    const completed = nextCollectionPollStep(
      null,
      false,
      run({ run_id: "old", status: "completed", publications_total: 3, publications_completed: 3 }),
      startingDisplay(""),
    );
    expect(completed.keepPolling).toBe(false);
    expect(completed.display.run_id).toBe("old");
  });

  it("never ends the loop when following a target but observing a different run", () => {
    // Defensive: single-active backend makes this impossible, but if the status
    // reports a different (e.g. older) run while we follow another, the loop
    // must keep polling and must not render the foreign run's terminal state.
    const target = "mine";
    const step = nextCollectionPollStep(
      target,
      false,
      run({ run_id: "other", status: "completed", publications_total: 9, publications_completed: 9 }),
      startingDisplay(target),
    );
    expect(step.keepPolling).toBe(true);
    expect(step.display.run_id).toBe("other");
  });
});