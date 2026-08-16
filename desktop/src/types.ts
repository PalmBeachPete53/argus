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
  path: string;
  parent: string | null;
  entries: DirEntry[];
}

export type SectionId = "data";
