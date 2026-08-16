import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { DataStats } from "../types";
import type { DiscoveryState } from "./MainContent";
import { formatDateTime } from "../lib/format";

interface OverviewProps {
  discovery: DiscoveryState;
  onGoToDiscovery: () => void;
}

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="stat-card">
      <span className="stat-value">{value.toLocaleString()}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}

/**
 * Voluntarily light entry point of the DATA section. Every number comes from
 * the Core/Store (`stats` bridge command) — nothing is hardcoded or invented.
 */
export default function Overview({ discovery, onGoToDiscovery }: OverviewProps) {
  const [stats, setStats] = useState<DataStats | null>(null);

  useEffect(() => {
    void invoke<DataStats>("get_stats")
      .then(setStats)
      .catch(() => setStats(null));
  }, []);

  const last = stats?.last_discovery ?? null;
  const active = discovery.status.status === "running" || discovery.status.status === "paused";

  const handleRun = async () => {
    const started = await discovery.launch();
    if (started) onGoToDiscovery();
  };

  return (
    <div className="overview">
      <h1 className="view-title">Overview</h1>

      <section className="overview-section" aria-label="Data overview">
        <h2 className="overview-heading">Publications</h2>
        <StatCard label="Publications" value={stats?.publications ?? 0} />
        <h2 className="overview-heading">Documents</h2>
        <StatCard label="Raw documents" value={stats?.documents ?? 0} />
        <StatCard label="Normalized documents" value={stats?.normalized_documents ?? 0} />
        <h2 className="overview-heading">Facts</h2>
        <StatCard label="Facts" value={stats?.facts ?? 0} />
      </section>

      <section className="overview-card" aria-label="Last discovery">
        <div className="overview-card-head">
          <h2 className="overview-heading">Discovery</h2>
          {last && <span className={`pill pill-${last.status}`}>{last.status}</span>}
        </div>
        <dl className="discovery-summary">
          <div className="discovery-summary-row">
            <dt>Last run</dt>
            <dd>{formatDateTime(last?.started_at)}</dd>
          </div>
          <div className="discovery-summary-row">
            <dt>Status</dt>
            <dd>{last ? last.status : "Idle"}</dd>
          </div>
          <div className="discovery-summary-row">
            <dt>Candidates</dt>
            <dd>{last ? last.candidates.toLocaleString() : "—"}</dd>
          </div>
        </dl>
        <button
          type="button"
          className="primary-button"
          onClick={() => void handleRun()}
          disabled={active}
        >
          {active ? (discovery.status.status === "paused" ? "Paused" : "Running…") : "Run Discovery"}
        </button>
      </section>
    </div>
  );
}
