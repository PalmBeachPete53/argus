import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { DataStats } from "../types";
import type { DiscoveryState } from "./MainContent";
import { formatDate, formatDateTime } from "../lib/format";
import ConfirmDialog from "./ConfirmDialog";

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

function formatRange(dateStart: string | null, dateEnd: string | null): string {
  if (!dateStart && !dateEnd) return "Unbounded";
  return [dateStart && formatDate(dateStart), dateEnd && formatDate(dateEnd)]
    .filter(Boolean)
    .join(" → ");
}

/**
 * Voluntarily light entry point of the DATA section. Every number comes from
 * the Core/Store (`stats` bridge command) — nothing is hardcoded or invented.
 * The Discovery card shares the same `useDiscovery` state machine as the
 * Discovery view, so lifecycle controls and range are always in sync.
 */
export default function Overview({ discovery, onGoToDiscovery }: OverviewProps) {
  const [stats, setStats] = useState<DataStats | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);

  useEffect(() => {
    void invoke<DataStats>("get_stats")
      .then(setStats)
      .catch(() => setStats(null));
  }, []);

  const last = stats?.last_discovery ?? null;
  const run = discovery.status;
  const active = run.status === "running" || run.status === "paused";

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

      <section className="overview-card" aria-label="Discovery">
        <div className="overview-card-head">
          <h2 className="overview-heading">Discovery</h2>
          {run.status !== "idle" && (
            <span className={`pill pill-${run.status}`}>{run.status}</span>
          )}
        </div>
        <dl className="discovery-summary">
          <div className="discovery-summary-row">
            <dt>Last run</dt>
            <dd>{formatDateTime(run.started_at ?? last?.started_at)}</dd>
          </div>
          <div className="discovery-summary-row">
            <dt>Status</dt>
            <dd>{run.status}</dd>
          </div>
          <div className="discovery-summary-row">
            <dt>Range</dt>
            <dd>{formatRange(run.date_start ?? last?.date_start ?? null, run.date_end ?? last?.date_end ?? null)}</dd>
          </div>
          <div className="discovery-summary-row">
            <dt>Candidates</dt>
            <dd>{run.candidates.toLocaleString()}</dd>
          </div>
          {(run.new > 0 || run.known > 0) && (
            <div className="discovery-summary-row">
              <dt>New / Known</dt>
              <dd>
                {run.new} new · {run.known} known
              </dd>
            </div>
          )}
        </dl>
        <div className="discovery-controls">
          {active ? (
            <>
              {run.status === "paused" ? (
                <button type="button" className="primary-button" onClick={() => void discovery.resume()}>
                  Resume
                </button>
              ) : (
                <button type="button" className="primary-button" onClick={() => void discovery.pause()}>
                  Pause
                </button>
              )}
              <button type="button" className="secondary-button" onClick={() => void discovery.stop()}>
                Stop
              </button>
            </>
          ) : (
            <button type="button" className="primary-button" onClick={() => void handleRun()} disabled={active}>
              Run Discovery
            </button>
          )}
          {run.status !== "idle" && !active && (
            <button type="button" className="secondary-button" onClick={() => setConfirmClear(true)}>
              Clear cache
            </button>
          )}
        </div>
      </section>

      <ConfirmDialog
        open={confirmClear}
        title="Clear discovery cache"
        message="This removes the discovery candidate snapshots. Campaign history is kept. Continue?"
        confirmLabel="Clear cache"
        onCancel={() => setConfirmClear(false)}
        onConfirm={() => {
          setConfirmClear(false);
          void discovery.clearCache();
        }}
      />
    </div>
  );
}