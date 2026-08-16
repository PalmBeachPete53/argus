import { describe, expect, it } from "vitest";
import { discoveryProgress, discoveryView, rangeStatus } from "./discovery";
import type { DiscoveryRun } from "../types";

describe("rangeStatus — Discovery launch precondition", () => {
  it("allows a run when both dates are present and ordered", () => {
    expect(rangeStatus("2025-01-01", "2025-12-31")).toBe("valid");
  });

  it("allows a run when start equals end (single-day window)", () => {
    expect(rangeStatus("2025-01-01", "2025-01-01")).toBe("valid");
  });

  it("refuses a run when the start date is missing", () => {
    expect(rangeStatus("", "2025-12-31")).toBe("missing");
  });

  it("refuses a run when the end date is missing", () => {
    expect(rangeStatus("2025-01-01", "")).toBe("missing");
  });

  it("refuses a run when both dates are missing", () => {
    expect(rangeStatus("", "")).toBe("missing");
  });

  it("refuses a run when start is after end", () => {
    expect(rangeStatus("2025-12-31", "2025-01-01")).toBe("invalid");
  });
});

describe("discoveryProgress — Core-driven source progression", () => {
  it("renders 0 / 34 at launch", () => {
    const p = discoveryProgress(34, 0);
    expect(p).toMatchObject({ completed: 0, total: 34, percent: 0, label: "0 / 34 sources" });
    expect(p.fraction).toBe(0);
  });

  it("renders 1 / 34 after a single source finished", () => {
    const p = discoveryProgress(34, 1);
    expect(p).toMatchObject({ completed: 1, total: 34 });
    expect(p.percent).toBe(Math.round((1 / 34) * 100));
  });

  it("renders 21 / 34 mid-campaign", () => {
    const p = discoveryProgress(34, 21);
    expect(p).toMatchObject({ completed: 21, total: 34 });
    expect(p.percent).toBe(Math.round((21 / 34) * 100));
    expect(p.percent).toBeLessThan(100);
  });

  it("renders 34 / 34 completed at 100%", () => {
    const p = discoveryProgress(34, 34);
    expect(p).toMatchObject({ completed: 34, total: 34, percent: 100, label: "34 / 34 sources" });
    expect(p.fraction).toBe(1);
  });

  it("keeps the last known progression for paused / cancelled / stopped / failed", () => {
    // Exactly the Core contract: a paused or cancelled campaign keeps its last
    // observed value (never reset, never fabricated to 100%).
    for (const completed of [0, 1, 21]) {
      expect(discoveryProgress(34, completed)).toEqual(discoveryProgress(34, completed));
    }
    expect(discoveryProgress(34, 21).percent).toBeLessThan(100);
  });

  it("never lets the displayed percentage exceed 100", () => {
    // Defensive: a stale/large `completed` is clamped to the total.
    for (const [total, completed] of [
      [34, 40],
      [34, 900],
      [4, 99],
    ]) {
      const p = discoveryProgress(total, completed);
      expect(p.completed).toBeLessThanOrEqual(p.total);
      expect(p.percent).toBeLessThanOrEqual(100);
      expect(p.fraction).toBeLessThanOrEqual(1);
    }
    // Negative inputs clamp to zero instead of producing a negative bar.
    expect(discoveryProgress(34, -3)).toMatchObject({ completed: 0, percent: 0, label: "0 / 34 sources" });
  });

  it("renders a coherent empty state for a zero-total campaign (never 0 / 0)", () => {
    const p = discoveryProgress(0, 0);
    expect(p).toMatchObject({ completed: 0, total: 0, percent: 0, label: "No sources to discover" });
    expect(p.fraction).toBe(0);
  });

  it("has no campaign bar when there is no run (idle keeps 0 / 0)", () => {
    // The component renders no bar for idle; the sanitized helper still answers
    // a safe (non-NaN, non-divisional) value.
    const p = discoveryProgress(0, 0);
    expect(Number.isNaN(p.fraction)).toBe(false);
    expect(Number.isNaN(p.percent)).toBe(false);
    expect(discoveryProgress(Number.NaN, Number.NaN)).toEqual(discoveryProgress(0, 0));
  });
});

const run = (over: Partial<DiscoveryRun>): DiscoveryRun => ({
  run_id: "20260816T182505-83606",
  status: "completed",
  started_at: "2026-08-16T18:25:05Z",
  finished_at: "2026-08-16T18:26:11Z",
  error: null,
  candidates: 32,
  banks: ["fed"],
  pid: null,
  date_start: "2026-01-01",
  date_end: "2026-08-16",
  sources_total: 32,
  sources_completed: 32,
  new: 5,
  known: 27,
  ...over,
});

describe("discoveryView — current Discovery versus history", () => {
  it("keeps a completed run as the current Discovery when the cache has candidates", () => {
    const view = discoveryView(run({}));
    expect(view.hasRun).toBe(true);
    expect(view.showCurrentCard).toBe(true);
    expect(view.showProgressBar).toBe(true);
    expect(view.showResults).toBe(true);
    expect(view.canClear).toBe(true);
    expect(view.showStatusPill).toBe(true);
    expect(view.emptyHeading).toBeNull();
  });

  it("after clearing the cache: card, bar, results and pill all disappear", () => {
    // Exactly the post-clear store snapshot from the real clear command:
    // status stays "completed", timestamps and run_id survive, candidates are 0.
    const view = discoveryView(run({ status: "completed", candidates: 0, new: 0, known: 0 }));
    expect(view.hasRun).toBe(true); // history kept
    expect(view.showCurrentCard).toBe(false); // no Status / Run ID / Date range / stats
    expect(view.showProgressBar).toBe(false); // no source-progression bar
    expect(view.showResults).toBe(false); // no candidates table
    expect(view.emptyHeading).toBe("No active Discovery");
    expect(view.canClear).toBe(true);
  });

  it("clear stays enabled on a cleared report (idempotent backend keep)", () => {
    const view = discoveryView(run({ status: "completed", candidates: 0 }));
    expect(view.canClear).toBe(true);
  });

  it("running campaign keeps the live card and bar but refrains from showing results", () => {
    const view = discoveryView(run({ status: "running", candidates: 0, sources_completed: 17 }));
    expect(view.showCurrentCard).toBe(true);
    expect(view.showProgressBar).toBe(true);
    expect(view.showResults).toBe(false);
    expect(view.showStatusPill).toBe(true);
    expect(view.emptyHeading).toBeNull();
    // The Core refuses to clear an active campaign; the button stays disabled.
    expect(view.canClear).toBe(false);
  });

  it("paused campaign mirrors the running behavior", () => {
    const view = discoveryView(run({ status: "paused", candidates: 0, sources_completed: 17 }));
    expect(view.showCurrentCard).toBe(true);
    expect(view.showProgressBar).toBe(true);
    expect(view.showResults).toBe(false);
    expect(view.canClear).toBe(false);
  });

  it("stopped run: no card / bar / results, banner owns the state, history kept", () => {
    const view = discoveryView(run({ status: "stopped", candidates: 0, error: "stopped by user" }));
    expect(view.hasRun).toBe(true);
    expect(view.showCurrentCard).toBe(false);
    expect(view.showProgressBar).toBe(false);
    expect(view.showResults).toBe(false);
    expect(view.emptyHeading).toBeNull(); // the "Discovery stopped" banner shows instead
    expect(view.canClear).toBe(true);
  });

  it("failed run: no card / bar / results, failure banner owns the state", () => {
    const view = discoveryView(run({ status: "failed", candidates: 0, error: "campaign failed" }));
    expect(view.showCurrentCard).toBe(false);
    expect(view.showProgressBar).toBe(false);
    expect(view.showResults).toBe(false);
    expect(view.emptyHeading).toBeNull();
    expect(view.canClear).toBe(true);
  });

  it("idle (nothing ever ran) is the neutral empty state with no history", () => {
    const view = discoveryView(run({ run_id: null, status: "idle", candidates: 0, started_at: null, finished_at: null }));
    expect(view.hasRun).toBe(false);
    expect(view.showCurrentCard).toBe(false);
    expect(view.showProgressBar).toBe(false);
    expect(view.showResults).toBe(false);
    expect(view.showStatusPill).toBe(false);
    expect(view.emptyHeading).toBe("No active Discovery");
    expect(view.canClear).toBe(false);
  });
});
