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
export type DataView = "overview" | "discovery" | "files";

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

export type SettingsSection = "general" | "banks" | "sources";

/** A discovery campaign lifecycle (from the Core store, read-only). */
export interface DiscoveryRun {
  run_id: string | null;
  status: "idle" | "running" | "completed" | "failed";
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  candidates: number;
  banks: string[];
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

/** Read-only Core/Store aggregates shown on the Overview. */
export interface DataStats {
  publications: number;
  documents: number;
  normalized_documents: number;
  facts: number;
  last_discovery: DiscoveryRun | null;
}
