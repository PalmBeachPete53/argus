/**
 * Collection view model — the second, natural half of the discovery→collect
 * workflow. Everything shown comes from the Core store: the plan is built by
 * the Core (never the frontend), and the only counters used are
 * `publications_completed / publications_total`.
 */

import type { CollectionRun } from "../types";

/**
 * Core-driven per-publication collection progression. `total` publications were
 * fixed at launch and `completed` of them have actually finished (a failing
 * publication still counts as one completed step). This is *never* derived from
 * document counts, bytes or elapsed time.
 *
 * The result is sanitized so the UI can render it without extra guards: counts
 * are clamped to the valid range and the percentage never exceeds 100 (a
 * cancelled or failed campaign keeps its last known progression — the Core
 * never fabricates `total / total` for interrupted work).
 */
export interface CollectionProgress {
  completed: number;
  total: number;
  /** Fraction of the track to fill, in [0, 1] (0 when total is 0). */
  fraction: number;
  /** Whole percentage, clamped to [0, 100]. */
  percent: number;
  /** "12 / 283 publications" when a campaign has publications, else a coherent
   * empty label (never an invalid `0 / 0`). */
  label: string;
}

export function collectionProgress(total: number, completed: number): CollectionProgress {
  const t = Math.max(0, Number(total) || 0);
  const c = Math.min(Math.max(0, Number(completed) || 0), t);
  const fraction = t > 0 ? c / t : 0;
  const percent = Math.round(fraction * 100);
  return {
    completed: c,
    total: t,
    fraction,
    percent,
    label: t > 0 ? `${c} / ${t} publications` : "No publications to collect",
  };
}

/**
 * The Collection view model: what to render for a given Core run state.
 *
 * Collection has no cached candidate snapshots and no clear action, so the
 * model is deliberately slimmer than Discovery's: it decides whether a run
 * exists, whether the header pill / current-card render, and when a neutral
 * empty state is appropriate. All terminal states keep their own banner
 * (completed / cancelled / failed), so nothing degrades to a bare "No active
 * Collection" unless there was truly no run.
 */
export interface CollectionViewState {
  /** A campaign record exists (run card / pill should render). */
  hasRun: boolean;
  /** Render the current run card (Status / Run ID / dates). */
  showCurrentCard: boolean;
  /** Render the per-publication progression bar. */
  showProgressBar: boolean;
  /** Neutral empty-state heading, or null when a status-specific banner shows. */
  emptyHeading: string | null;
  /** The header status pill. */
  showStatusPill: boolean;
}

export function collectionView(status: CollectionRun): CollectionViewState {
  const hasRun = status.run_id !== null;
  const active = status.status === "running";
  const showCurrentCard = active || (hasRun && status.status !== "idle");
  return {
    hasRun,
    showCurrentCard,
    // Live bar while running; after a normal completion the Core reports
    // total / total; a cancelled or failed run keeps its last partial value
    // (rendered statically by the panel, never fabricated to 100%).
    showProgressBar: status.status === "running" || status.status === "completed",
    emptyHeading: showCurrentCard ? null : "No active Collection",
    showStatusPill: showCurrentCard && status.status !== "idle",
  };
}
