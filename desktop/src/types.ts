export interface BankInfo {
  id: string;
  name: string;
  currency: string;
  enabled: boolean;
}

export interface DirEntry {
  name: string;
  path: string;
  is_dir: boolean;
}

export interface DirListing {
  root: string;
  /** Relative path from the data root ("" = data root itself). */
  path: string;
  /** Path segments for the breadcrumb (["cache", "sub"] for "cache/sub"). */
  segments: string[];
  /** Parent relative path, or null when already at the data root. */
  parent: string | null;
  entries: DirEntry[];
}

export type SectionId = "data";

/** Sub-views of the DATA section. */
export type DataView = "sources" | "discovery" | "files";

export interface SourceInfo {
  id: string;
  name: string;
  kind: string;
  url: string;
  enabled: boolean;
  publication_types: string[];
  search_fallback: boolean;
}

export interface BankSources {
  bank: string;
  sources: SourceInfo[];
}

export type SettingsSection = "general" | "banks";

/** A discovery campaign lifecycle (from the Core store, read-only). */
export interface DiscoveryRun {
  run_id: string | null;
  status: "idle" | "running" | "paused" | "completed" | "failed" | "cancelled" | "stopped";
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  candidates: number;
  banks: string[];
  /** OS PID of the campaign subprocess (null when none is recorded). */
  pid: number | null;
  /** Publication-date window bounding the campaign (ISO, null = unbounded). */
  date_start: string | null;
  date_end: string | null;
  /**
   * Core-driven source progression (never candidates): total sources fixed at
   * launch, and how many have actually finished so far.
   */
  sources_total: number;
  sources_completed: number;
  /** Candidate snapshot split: not-yet-known vs already-known publications. */
  new: number;
  known: number;
}

/** One publication candidate produced by a discovery campaign. */
export interface DiscoveryCandidate {
  publication_id: string | null;
  bank_id: string;
  bank_name: string;
  title: string;
  url: string;
  source_id: string;
  /** Provenance: "native" or "search" (from the Core, never invented). */
  method: string;
  /** Whether the candidate was already known in the store before the run. */
  is_new: boolean;
  discovered_at: string | null;
  publication_date: string | null;
}

export interface DiscoveryResults {
  run_id: string | null;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  candidates: DiscoveryCandidate[];
  total: number;
}

/** Result of clearing the discovery report cache: the candidate snapshots are
 * gone, the campaign history (runs) is preserved. */
export interface ClearedCache {
  runs_preserved: number;
  candidates_cleared: number;
}

/** Identity returned by `run_discovery` so the frontend can follow exactly the
 * campaign it just launched (never "the latest run"). */
export interface DiscoveryRunId {
  run_id: string;
}

/**
 * A collection campaign lifecycle (from the Core store, read-only).
 *
 * Collection is the second half of the discovery→collect workflow: after
 * Discovery persists publications into `publications`, the Core builds its own
 * collection plan (never the frontend) and fetches documents for every
 * publication that needs work. Statuses are `idle | running | completed |
 * failed | cancelled` — there is no pause, and Stop means a real cancellation.
 */
export interface CollectionRun {
  run_id: string | null;
  status: "idle" | "running" | "completed" | "failed" | "cancelled";
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  banks: string[];
  /** OS PID of the campaign subprocess (null when none is recorded). */
  pid: number | null;
  /** Whether the campaign re-collected already-fetched documents. */
  force: boolean;
  /** Optional publication-date window bounding the campaign (ISO, null = all). */
  date_start: string | null;
  date_end: string | null;
  /**
   * Core-driven per-publication progression: `publications_total` is fixed at
   * launch and `publications_completed` advances as workers really finish
   * (failing publications still count). Never derived, never fabricated to
   * total/total for interrupted work.
   */
  publications_total: number;
  publications_completed: number;
}

/** Identity returned by `run_collection` so the frontend can follow exactly the
 * campaign it just launched (never "the latest run"). */
export interface CollectionRunId {
  run_id: string;
}
