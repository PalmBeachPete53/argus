import { describe, expect, it } from "vitest";
import { collectionProgress, collectionView } from "./collection";
import type { CollectionRun } from "../types";

describe("collectionProgress — Core-driven per-publication progression", () => {
  it("renders 0 / 283 at launch", () => {
    const p = collectionProgress(283, 0);
    expect(p).toMatchObject({ completed: 0, total: 283, percent: 0, label: "0 / 283 publications" });
    expect(p.fraction).toBe(0);
  });

  it("renders 12 / 283 mid-campaign", () => {
    const p = collectionProgress(283, 12);
    expect(p).toMatchObject({ completed: 12, total: 283 });
    expect(p.percent).toBe(Math.round((12 / 283) * 100));
    expect(p.percent).toBeLessThan(100);
  });

  it("renders 283 / 283 at 100%", () => {
    const p = collectionProgress(283, 283);
    expect(p).toMatchObject({ completed: 283, total: 283, percent: 100, label: "283 / 283 publications" });
    expect(p.fraction).toBe(1);
  });

  it("keeps the last known progression for a cancelled / partial run", () => {
    // Exactly the Core contract: a cancelled campaign keeps its last observed
    // value (never reset, never fabricated to 100%).
    expect(collectionProgress(283, 12).percent).toBeLessThan(100);
    expect(collectionProgress(283, 12)).toEqual(collectionProgress(283, 12));
  });

  it("never lets the displayed percentage exceed 100", () => {
    for (const [total, completed] of [
      [283, 300],
      [12, 99],
    ]) {
      const p = collectionProgress(total, completed);
      expect(p.completed).toBeLessThanOrEqual(p.total);
      expect(p.percent).toBeLessThanOrEqual(100);
      expect(p.fraction).toBeLessThanOrEqual(1);
    }
    expect(collectionProgress(283, -3)).toMatchObject({ completed: 0, percent: 0, label: "0 / 283 publications" });
  });

  it("renders a coherent empty state for a zero-total campaign (never 0 / 0)", () => {
    const p = collectionProgress(0, 0);
    expect(p).toMatchObject({ completed: 0, total: 0, percent: 0, label: "No publications to collect" });
    expect(Number.isNaN(p.fraction)).toBe(false);
  });
});

const run = (over: Partial<CollectionRun>): CollectionRun => ({
  run_id: "collection-20260816T182505",
  status: "completed",
  started_at: "2026-08-16T18:25:05Z",
  finished_at: "2026-08-16T18:26:11Z",
  error: null,
  banks: ["fed"],
  pid: null,
  force: false,
  date_start: "2026-01-01",
  date_end: "2026-08-16",
  publications_total: 32,
  publications_completed: 32,
  ...over,
});

describe("collectionView", () => {
  it("completed run: current card + full progression, no empty heading", () => {
    const view = collectionView(run({}));
    expect(view.hasRun).toBe(true);
    expect(view.showCurrentCard).toBe(true);
    expect(view.showProgressBar).toBe(true);
    expect(view.showStatusPill).toBe(true);
    expect(view.emptyHeading).toBeNull();
  });

  it("running campaign keeps the live card, bar and pill", () => {
    const view = collectionView(run({ status: "running", publications_completed: 17 }));
    expect(view.showCurrentCard).toBe(true);
    expect(view.showProgressBar).toBe(true);
    expect(view.showStatusPill).toBe(true);
    expect(view.emptyHeading).toBeNull();
  });

  it("cancelled run: static summary card, no active progression bar", () => {
    const view = collectionView(
      run({ status: "cancelled", publications_completed: 12, error: "cancelled by user" }),
    );
    expect(view.hasRun).toBe(true);
    expect(view.showCurrentCard).toBe(true); // Status / Run ID still shown
    expect(view.showProgressBar).toBe(false); // never an active bar on cancel
    expect(view.emptyHeading).toBeNull(); // the "Collection cancelled" banner shows instead
  });

  it("failed run: no bar, failure banner owns the state", () => {
    const view = collectionView(run({ status: "failed", publications_completed: 3, error: "boom" }));
    expect(view.showCurrentCard).toBe(true);
    expect(view.showProgressBar).toBe(false);
    expect(view.emptyHeading).toBeNull();
  });

  it("idle (nothing ever ran) is the neutral state with no card and no history", () => {
    const view = collectionView(
      run({ run_id: null, status: "idle", started_at: null, finished_at: null, publications_total: 0, publications_completed: 0 }),
    );
    expect(view.hasRun).toBe(false);
    expect(view.showCurrentCard).toBe(false);
    expect(view.showProgressBar).toBe(false);
    expect(view.showStatusPill).toBe(false);
    expect(view.emptyHeading).toBe("No active Collection");
  });
});