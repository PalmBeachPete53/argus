import { describe, expect, it } from "vitest";
import type { DiscoveryRun } from "../types";
import { nextPollStep } from "./discoveryPoll";

/** A complete DiscoveryRun with the given overrides. */
function run(partial: Partial<DiscoveryRun> = {}): DiscoveryRun {
  return {
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
    ...partial,
  };
}

/** The optimistic run shown while waiting for the target to appear. */
function startingDisplay(target: string): DiscoveryRun {
  return run({ run_id: target, status: "running", sources_total: 0, sources_completed: 0 });
}

describe("nextPollStep", () => {
  it("does not stop polling on the old terminal run after a launch", () => {
    // Reproduces the race: the previous campaign is still "latest" right after
    // launching, so the first discovery-status returns it (completed). The loop
    // must keep going and keep showing "Starting…", not treat it as the end.
    const target = "new";
    const oldCompleted = run({ run_id: "old", status: "completed", sources_total: 34, sources_completed: 34 });

    const first = nextPollStep(target, true, oldCompleted, startingDisplay(target));
    expect(first.keepPolling).toBe(true);
    expect(first.stopWaiting).toBe(false);
    expect(first.display.run_id).toBe(target); // optimistic, not the old run
    expect(first.display.status).toBe("running");

    const newRunning = run({ run_id: target, status: "running", sources_total: 4, sources_completed: 0 });
    const second = nextPollStep(target, true, newRunning, startingDisplay(target));
    expect(second.keepPolling).toBe(true);
    expect(second.stopWaiting).toBe(true);
    expect(second.display).toBe(newRunning);
  });

  it("keeps polling while the target is running and reports each progression", () => {
    const target = "new";
    const steps = [0, 1, 2, 3].map((c) => nextPollStep(target, false, run({
      run_id: target,
      status: "running",
      sources_total: 4,
      sources_completed: c,
    }), startingDisplay(target)));

    steps.forEach((step) => {
      expect(step.keepPolling).toBe(true);
      expect(step.fetchResults).toBe(false);
    });
    expect(steps.map((s) => s.display.sources_completed)).toEqual([0, 1, 2, 3]);

    const completed = nextPollStep(target, false, run({
      run_id: target,
      status: "completed",
      sources_total: 4,
      sources_completed: 4,
    }), startingDisplay(target));
    expect(completed.keepPolling).toBe(false);
    expect(completed.fetchResults).toBe(true);
    expect(completed.display.sources_completed).toBe(4);
    expect(completed.display.sources_total).toBe(4);
  });

  it("stays active through paused and resumes", () => {
    const target = "new";
    const seq = [
      { status: "running", completed: 2 },
      { status: "paused", completed: 2 },
      { status: "paused", completed: 2 },
      { status: "running", completed: 3 },
      { status: "completed", completed: 4 },
    ] as const;

    let step = nextPollStep(target, false, run({ run_id: target, status: "running", sources_total: 4, sources_completed: 0 }), startingDisplay(target));
    expect(step.keepPolling).toBe(true);

    for (let i = 0; i < seq.length; i++) {
      const { status: s, completed } = seq[i];
      step = nextPollStep(target, false, run({
        run_id: target,
        status: s,
        sources_total: 4,
        sources_completed: completed,
      }), startingDisplay(target));
      if (i < seq.length - 1) {
        expect(step.keepPolling).toBe(true);
        expect(step.fetchResults).toBe(false);
      } else {
        expect(step.keepPolling).toBe(false);
        expect(step.fetchResults).toBe(true);
      }
    }
  });

  it("keeps the real partial progression on cancel (never fabricates total/total)", () => {
    const target = "new";
    const running = nextPollStep(target, false, run({
      run_id: target,
      status: "running",
      sources_total: 4,
      sources_completed: 2,
    }), startingDisplay(target));
    expect(running.keepPolling).toBe(true);

    const cancelled = nextPollStep(target, false, run({
      run_id: target,
      status: "cancelled",
      sources_total: 4,
      sources_completed: 2,
    }), startingDisplay(target));
    expect(cancelled.keepPolling).toBe(false);
    expect(cancelled.fetchResults).toBe(true);
    expect(cancelled.display.status).toBe("cancelled");
    expect(cancelled.display.sources_completed).toBe(2);
    expect(cancelled.display.sources_total).toBe(4);
    expect(cancelled.display.sources_completed).not.toBe(cancelled.display.sources_total);
  });

  it("treats a legacy stopped run as terminal", () => {
    const target = "new";
    const legacy = nextPollStep(target, false, run({
      run_id: target,
      status: "stopped",
      sources_total: 4,
      sources_completed: 2,
    }), startingDisplay(target));
    expect(legacy.keepPolling).toBe(false);
    expect(legacy.fetchResults).toBe(true);
    expect(legacy.display.status).toBe("stopped");
  });

  it("adopts an active run when observing on mount with no target", () => {
    const active = run({ run_id: "bg", status: "running", sources_total: 10, sources_completed: 3 });
    const step = nextPollStep(null, false, active, startingDisplay(""));
    expect(step.keepPolling).toBe(true);
    expect(step.adoptTarget).toBe("bg");
    expect(step.display).toBe(active);
  });

  it("stops after a single read when idle and observing", () => {
    const step = nextPollStep(null, false, run({ status: "idle" }), startingDisplay(""));
    expect(step.keepPolling).toBe(false);
    expect(step.fetchResults).toBe(false);
  });
});
