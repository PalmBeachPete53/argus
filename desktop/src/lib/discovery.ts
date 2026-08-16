/**
 * Discovery launch preconditions. A campaign may only be launched with a
 * complete, ordered publication-date window:
 *
 *   start_date present AND end_date present AND start_date <= end_date
 *
 * The same invariant is enforced by the backend (Rust shell and the Python
 * bridge) — this module is the single shared frontend definition, kept pure so
 * it is trivially unit-testable.
 */

import type { DiscoveryRun } from "../types";

export type DiscoveryRangeStatus = "missing" | "invalid" | "valid";

/**
 * Classify the user-selected window. Dates are `YYYY-MM-DD` strings (or empty
 * when unset), so plain string ordering is the correct date comparison.
 */
export function rangeStatus(start: string, end: string): DiscoveryRangeStatus {
  if (!start || !end) return "missing";
  return start <= end ? "valid" : "invalid";
}

export const REQUIRED_RANGE_HINT = "A start and end date are required to run Discovery.";
export const INVALID_RANGE_HINT = "End date must be on or after start date.";

/**
 * Core-driven discovery progression. The Core is the single source of truth:
 * `total` sources were fixed at launch and `completed` of them have actually
 * finished (a failing or empty source still counts). This is *never* derived
 * from candidate counts or elapsed time.
 *
 * The result is sanitized so the UI can render it without extra guards:
 * counts are clamped to the valid range and the percentage never exceeds
 * 100 (a stopped or cancelled campaign keeps its last known progression — the Core never
 * fabricates `total / total` for interrupted work).
 */
export interface DiscoveryProgress {
  completed: number;
  total: number;
  /** Fraction of the track to fill, in [0, 1] (0 when total is 0). */
  fraction: number;
  /** Whole percentage, clamped to [0, 100]. */
  percent: number;
  /** "21 / 34 sources" when a campaign has sources, else "No sources to discover". */
  label: string;
}

export function discoveryProgress(total: number, completed: number): DiscoveryProgress {
  const t = Math.max(0, Number(total) || 0);
  const c = Math.min(Math.max(0, Number(completed) || 0), t);
  const fraction = t > 0 ? c / t : 0;
  const percent = Math.round(fraction * 100);
  return {
    completed: c,
    total: t,
    fraction,
    percent,
    label: t > 0 ? `${c} / ${t} sources` : "No sources to discover",
  };
}

/**
 * The Discovery view model: what to render for a given Core run state.
 *
 * "Current Discovery" is the *live campaign or its cached candidate report* —
 * the Core's ``discovery_candidates`` snapshot. A terminal run whose cached
 * snapshots are gone (e.g. right after ``clear_discovery_cache``) has no
 * current report: the status card, the source-progression bar and the results
 * all disappear and the view degrades to a neutral "No active Discovery"
 * state, while the run record (campaign history) is left untouched. This is
 * derived purely from the Store's own numbers, so it can never drift from
 * backend truth.
 */
export interface DiscoveryViewState {
  /** A campaign record exists — history (Last Run / Finished) should be kept. */
  hasRun: boolean;
  /** Render the current run card (Status / Run ID / Date range / stats). */
  showCurrentCard: boolean;
  /** Render the source-progression bar. */
  showProgressBar: boolean;
  /** Render the cached candidates results. */
  showResults: boolean;
  /** "Clear" is enabled only when no campaign is active (backend refuses otherwise). */
  canClear: boolean;
  /** Neutral empty-state heading, or null when a status-specific banner shows. */
  emptyHeading: string | null;
  /** The header status pill. */
  showStatusPill: boolean;
}

export function discoveryView(status: DiscoveryRun): DiscoveryViewState {
  const active = status.status === "running" || status.status === "paused";
  const hasRun = status.run_id !== null;
  // The cache is the Current Discovery content: an active campaign holds no
  // snapshots yet, and a terminal run without snapshots (cleared, or nothing
  // discovered) has nothing current to display.
  const hasCachedReport = hasRun && !active && (status.candidates ?? 0) > 0;
  const showCurrentCard = active || hasCachedReport;
  return {
    hasRun,
    showCurrentCard,
    showProgressBar: active || (status.status === "completed" && hasCachedReport),
    showResults: hasCachedReport,
    canClear: !active && hasRun,
    emptyHeading:
      showCurrentCard || status.status === "failed" || status.status === "cancelled" || status.status === "stopped"
        ? null
        : "No active Discovery",
    showStatusPill: showCurrentCard && status.status !== "idle",
  };
}
