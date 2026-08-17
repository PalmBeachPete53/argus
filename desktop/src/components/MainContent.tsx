import type { CollectionRun, DataView, DiscoveryCandidate, DiscoveryRun } from "../types";
import DataBrowser from "./DataBrowser";
import Discovery from "./Discovery";
import Sources from "./Sources";

export interface DiscoveryState {
  status: DiscoveryRun;
  candidates: DiscoveryCandidate[];
  error: string | null;
  /** True while a just-launched campaign is awaited to appear in the store. */
  starting: boolean;
  launch: (startDate?: string, endDate?: string) => Promise<boolean>;
  pause: () => Promise<boolean>;
  resume: () => Promise<boolean>;
  stop: () => Promise<boolean>;
  clearCache: () => Promise<import("../types").ClearedCache | null>;
  openUrl: (url: string) => Promise<void>;
}

export interface CollectionState {
  status: CollectionRun;
  error: string | null;
  /** True while a just-launched campaign is awaited to appear in the store. */
  starting: boolean;
  launch: (startDate?: string, endDate?: string) => Promise<boolean>;
  stop: () => Promise<boolean>;
}

interface MainContentProps {
  view: DataView;
  discovery: DiscoveryState;
  collection: CollectionState;
}

export default function MainContent({ view, discovery, collection }: MainContentProps) {
  switch (view) {
    case "sources":
      return (
        <main className="main">
          <Sources />
        </main>
      );
    case "discovery":
      return (
        <main className="main">
          <Discovery discovery={discovery} collection={collection} />
        </main>
      );
    case "files":
    default:
      return (
        <main className="main">
          <DataBrowser />
        </main>
      );
  }
}
