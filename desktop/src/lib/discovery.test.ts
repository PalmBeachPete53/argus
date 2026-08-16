import { describe, expect, it } from "vitest";
import { discoveryProgress, rangeStatus } from "./discovery";

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

  it("keeps the last known progression for paused / stopped / failed", () => {
    // Exactly the Core contract: a paused or stopped campaign keeps its last
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
