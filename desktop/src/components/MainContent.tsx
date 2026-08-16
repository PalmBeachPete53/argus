import type { DataView, DiscoveryCandidate, DiscoveryRun } from "../types";
import DataBrowser from "./DataBrowser";
import Discovery from "./Discovery";
import Overview from "./Overview";
import Sources from "./Sources";

export interface DiscoveryState {
  status: DiscoveryRun;
  candidates: DiscoveryCandidate[];
  error: string | null;
  launch: () => Promise<boolean>;
  openUrl: (url: string) => Promise<void>;
}

interface MainContentProps {
  view: DataView;
  discovery: DiscoveryState;
  onGoToDiscovery: () => void;
}

export default function MainContent({ view, discovery, onGoToDiscovery }: MainContentProps) {
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
          <Discovery discovery={discovery} />
        </main>
      );
    case "files":
      return (
        <main className="main">
          <DataBrowser />
        </main>
      );
    case "overview":
    default:
      return (
        <main className="main">
          <Overview discovery={discovery} onGoToDiscovery={onGoToDiscovery} />
        </main>
      );
  }
}
