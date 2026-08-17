// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { describe, expect, it, vi, beforeEach } from "vitest";

// React 18's `act` needs this flag outside a configured testing environment.
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

import { invoke } from "@tauri-apps/api/core";
import { useCollection } from "./useCollection";
import type { CollectionRun } from "../types";

const invokeMock = vi.mocked(invoke);

const idleRun: CollectionRun = {
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

function run(over: Partial<CollectionRun>): CollectionRun {
  return { ...idleRun, ...over };
}

let latestStatus = idleRun;
let renderer: Root;
let container: HTMLDivElement;
let hook: ReturnType<typeof useCollection> | undefined;

function Harness() {
  hook = useCollection();
  return null;
}

function mount() {
  container = document.createElement("div");
  renderer = createRoot(container);
  act(() => renderer.render(<Harness />));
}

function unmount() {
  act(() => renderer.unmount());
}

/** Flush pending microtasks (an immediate poll read has no timer). */
async function microtasks() {
  await act(async () => {});
}

/** Advance the fake clock by `ms`, letting pending polls fire. */
async function pump(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

let controlCalls: { action: string; runId: string }[];

beforeEach(() => {
  vi.useFakeTimers();
  latestStatus = idleRun;
  hook = undefined;
  controlCalls = [];
  invokeMock.mockReset();
  invokeMock.mockImplementation((cmd: string, args?: unknown) => {
    if (cmd === "get_collection_status") return Promise.resolve({ ...latestStatus });
    if (cmd === "run_collection") return Promise.resolve({ run_id: "col-1" });
    if (cmd === "collection_control") {
      const { action, runId } = args as { action: string; runId: string };
      controlCalls.push({ action, runId });
      // The Core preserves the campaign's counters on a stop (finish_collection_run
      // only touches status/error/finished_at) and keeps the targeted run_id.
      latestStatus = { ...latestStatus, status: "cancelled", error: "cancelled by user", run_id: runId };
      return Promise.resolve({ ...latestStatus });
    }
    return Promise.resolve({});
  });
});

describe("useCollection", () => {
  it("launches, follows exactly its run_id, reaches completed with 0/N → N/N", async () => {
    mount();
    await microtasks();
    expect(hook!.status.status).toBe("idle");

    let launched: boolean | undefined;
    await act(async () => {
      launched = await hook!.launch("2026-01-01", "2026-02-01");
    });
    expect(launched).toBe(true);
    // The launcher returned the run identity; the optimistic state shows it.
    expect(hook!.status).toMatchObject({ status: "running", run_id: "col-1", publications_total: 0 });

    expect(invokeMock).toHaveBeenCalledWith("run_collection", {
      startDate: "2026-01-01",
      endDate: "2026-02-01",
    });

    // The old terminal run is still "latest": it must not stop the loop nor
    // surface its own numbers as the new campaign's progression.
    latestStatus = run({ run_id: "old", status: "completed", publications_total: 283, publications_completed: 283 });
    await microtasks();
    expect(hook!.status).toMatchObject({ status: "running", run_id: "col-1", publications_completed: 0, publications_total: 0 });

    // The target appears and progresses.
    latestStatus = run({ run_id: "col-1", status: "running", publications_total: 10, publications_completed: 0 });
    await pump(1200);
    expect(hook!.status).toMatchObject({ status: "running", publications_total: 10, publications_completed: 0 });

    latestStatus = run({ run_id: "col-1", status: "running", publications_total: 10, publications_completed: 3 });
    await pump(1200);
    expect(hook!.status).toMatchObject({ status: "running", publications_completed: 3 });

    latestStatus = run({ run_id: "col-1", status: "completed", publications_total: 10, publications_completed: 10 });
    await pump(1200);
    expect(hook!.status.status).toBe("completed");
    expect(hook!.status.publications_completed).toBe(10);
    expect(hook!.status.publications_total).toBe(10);

    // Terminal → polling stops.
    const readsAtEnd = invokeMock.mock.calls.filter(([c]) => c === "get_collection_status").length;
    await pump(5000);
    const readsAfter = invokeMock.mock.calls.filter(([c]) => c === "get_collection_status").length;
    expect(readsAfter).toBe(readsAtEnd);

    unmount();
  });

  it("keeps partial progression visible on cancel (never total/total)", async () => {
    mount();
    await microtasks();
    await act(async () => {
      await hook!.launch();
    });
    latestStatus = run({ run_id: "col-1", status: "running", publications_total: 283, publications_completed: 12 });
    await pump(1200);
    expect(hook!.status.publications_completed).toBe(12);

    let stopped: boolean | undefined;
    await act(async () => {
      stopped = await hook!.stop();
    });
    expect(stopped).toBe(true);
    expect(controlCalls).toEqual([{ action: "stop", runId: "col-1" }]);
    expect(hook!.status.status).toBe("cancelled");
    expect(hook!.status.publications_completed).toBe(12);
    expect(hook!.status.publications_total).toBe(283);

    // After a cancellation the poll loop stops (no resurrection):
    latestStatus = { ...hook!.status, status: "completed", publications_completed: 283 };
    await pump(5000);
    expect(hook!.status.status).toBe("cancelled");
    unmount();
  });

  it("exposes no pause/resume after cancellation (Stop is terminal)", async () => {
    mount();
    await microtasks();
    await act(async () => {
      await hook!.launch();
    });
    await act(async () => {
      await hook!.stop();
    });
    expect(hook!.status.status).toBe("cancelled");
    // The collection hook intentionally has no resume: Stop is a real
    // cancellation, the Run button simply becomes available again.
    expect("resume" in hook!).toBe(false);
    expect("pause" in hook!).toBe(false);
    unmount();
  });

  it("prevents a double launch while a campaign is active or starting", async () => {
    mount();
    await microtasks();
    await act(async () => {
      await hook!.launch();
    });
    const run_calls = () => invokeMock.mock.calls.filter(([c]) => c === "run_collection").length;
    expect(run_calls()).toBe(1);

    let second: boolean | undefined;
    await act(async () => {
      second = await hook!.launch("2026-03-01", "2026-04-01");
    });
    expect(second).toBe(false);
    expect(run_calls()).toBe(1); // the backend was never asked twice
    unmount();
  });

  it("cleans up its poll loop on unmount", async () => {
    mount();
    await microtasks();
    await act(async () => {
      await hook!.launch();
    });
    latestStatus = run({ run_id: "col-1", status: "running", publications_total: 50, publications_completed: 0 });
    await pump(1200);
    const readsBefore = invokeMock.mock.calls.filter(([c]) => c === "get_collection_status").length;
    expect(readsBefore).toBeGreaterThanOrEqual(2);

    unmount();
    const readsAtUnmount = invokeMock.mock.calls.filter(([c]) => c === "get_collection_status").length;
    await pump(5000);
    const readsAfter = invokeMock.mock.calls.filter(([c]) => c === "get_collection_status").length;
    // No poll reads after unmount — the loop token was invalidated.
    expect(readsAfter).toBe(readsAtUnmount);
    expect(readsAtUnmount).toBe(readsBefore);
  });

  it("keeps waiting when the run appears late (never confuses the old run)", async () => {
    mount();
    await microtasks();
    await act(async () => {
      await hook!.launch();
    });
    // Several polls while the new run has not recorded itself yet.
    for (let i = 0; i < 4; i++) {
      latestStatus = run({ run_id: "old", status: "cancelled", publications_total: 5, publications_completed: 2 });
      await pump(1200);
      expect(hook!.status).toMatchObject({ status: "running", run_id: "col-1" });
      expect(hook!.status.publications_total).toBe(0);
    }
    latestStatus = run({ run_id: "col-1", status: "running", publications_total: 8, publications_completed: 1 });
    await pump(1200);
    expect(hook!.status).toMatchObject({ run_id: "col-1", status: "running", publications_total: 8 });
    unmount();
  });

  it("cancels during starting, targeting the followed run — never an older terminal one", async () => {
    mount();
    await microtasks();
    // An older completed run is still "latest" while the new campaign bootstraps.
    latestStatus = run({ run_id: "old", status: "completed", publications_total: 5, publications_completed: 5 });
    await act(async () => {
      await hook!.launch();
    });
    expect(hook!.starting).toBe(true);

    let stopped: boolean | undefined;
    await act(async () => {
      stopped = await hook!.stop();
    });
    expect(stopped).toBe(true);
    expect(controlCalls).toEqual([{ action: "stop", runId: "col-1" }]);
    expect(hook!.status).toMatchObject({ status: "cancelled", run_id: "col-1" });
    expect(hook!.starting).toBe(false);
    unmount();
  });

  it("cancels during starting before the run has recorded itself", async () => {
    mount();
    await microtasks();
    await act(async () => {
      await hook!.launch();
    });
    // The backend still reports idle (the detached subprocess is booting).
    latestStatus = idleRun;
    let stopped: boolean | undefined;
    await act(async () => {
      stopped = await hook!.stop();
    });
    expect(stopped).toBe(true);
    expect(controlCalls).toEqual([{ action: "stop", runId: "col-1" }]);
    expect(hook!.status.status).toBe("cancelled");
    unmount();
  });

  it("adopts a followed run that already failed instead of invoking stop", async () => {
    mount();
    await microtasks();
    await act(async () => {
      await hook!.launch();
    });
    latestStatus = run({ run_id: "col-1", status: "failed", error: "RuntimeError: plan exploded" });

    let stopped: boolean | undefined;
    await act(async () => {
      stopped = await hook!.stop();
    });
    expect(stopped).toBe(true);
    expect(controlCalls).toEqual([]); // never asked the Core to stop a dead run
    expect(hook!.status).toMatchObject({ status: "failed", run_id: "col-1" });
    expect(hook!.status.error).toContain("plan exploded");
    unmount();
  });

  it("never displays a foreign run reported by the backend while following the target", async () => {
    mount();
    await microtasks();
    await act(async () => {
      await hook!.launch();
    });
    latestStatus = run({ run_id: "col-1", status: "running", publications_total: 10, publications_completed: 2 });
    await pump(1200);
    expect(hook!.status).toMatchObject({ run_id: "col-1", status: "running", publications_completed: 2 });

    // The backend briefly reports a different run (defensive) — the hook must
    // keep showing the followed run's state, never the foreign run's.
    latestStatus = run({ run_id: "other", status: "completed", publications_total: 9, publications_completed: 9 });
    await pump(1200);
    expect(hook!.status).toMatchObject({ run_id: "col-1", status: "running", publications_completed: 2 });
    expect(hook!.status.run_id).not.toBe("other");
    unmount();
  });
});
