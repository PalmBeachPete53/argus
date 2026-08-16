import { useState } from "react";
import type { DiscoveryCandidate, DiscoveryRun } from "../types";
import type { DiscoveryState } from "./MainContent";
import { formatDate, formatDateTime } from "../lib/format";
import { rangeStatus, REQUIRED_RANGE_HINT, INVALID_RANGE_HINT, discoveryProgress, discoveryView } from "../lib/discovery";
import ConfirmDialog from "./ConfirmDialog";

interface DiscoveryProps {
  discovery: DiscoveryState;
}

function StatusPill({ status }: { status: string }) {
  return <span className={`pill pill-${status}`}>{status}</span>;
}

/**
 * The Core-driven source progression bar (never candidate ratio, never time).
 * ``sources_completed / sources_total`` is read straight from the Core store:
 * - running → live, advances as the Core reports each source done;
 * - paused → frozen at the last Core-reported value;
 * - completed → full bar (the Core reports total / total on a normal end);
 * - cancelled / stopped / failed → the *last known* progression — a Stop is never
 *   transformed into 100%.
 * A zero total (no enabled sources) renders a coherent empty state instead of
 * an invalid ``0 / 0`` bar.
 */
function CampaignProgress({ status }: { status: DiscoveryRun }) {
  const p = discoveryProgress(status.sources_total, status.sources_completed);
  const active = status.status === "running" || status.status === "paused";
  if (p.total === 0) {
    return (
      <div className="discovery-progress" aria-label="Discovery source progression">
        <p className="discovery-progress-empty">{active ? "Starting…" : p.label}</p>
      </div>
    );
  }
  const remaining = p.total - p.completed;
  return (
    <div className="discovery-progress" aria-label="Discovery source progression">
      <div
        className="discovery-progress-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={p.total}
        aria-valuenow={p.completed}
      >
        <div className="discovery-progress-fill" style={{ width: `${p.percent}%` }} />
      </div>
      <div className="discovery-progress-meta">
        <span className="discovery-progress-count">{p.label}</span>
        <span className="discovery-progress-detail">
          {p.completed} completed · {remaining} remaining · {p.percent}%
        </span>
      </div>
    </div>
  );
}

function CandidateDetail({
  candidate,
  onOpen,
}: {
  candidate: DiscoveryCandidate;
  onOpen: (url: string) => void;
}) {
  return (
    <aside className="candidate-detail" aria-label="Discovery candidate detail">
      <h3 className="view-title">Discovery Candidate</h3>
      <dl className="discovery-summary">
        <div className="discovery-summary-row">
          <dt>Central bank</dt>
          <dd>{candidate.bank_name}</dd>
        </div>
        <div className="discovery-summary-row">
          <dt>Title</dt>
          <dd>{candidate.title}</dd>
        </div>
        <div className="discovery-summary-row">
          <dt>URL</dt>
          <dd className="candidate-url">{candidate.url}</dd>
        </div>
        <div className="discovery-summary-row">
          <dt>Discovery method</dt>
          <dd>
            <span className={`badge badge-method badge-${candidate.method}`}>{candidate.method}</span>
          </dd>
        </div>
        <div className="discovery-summary-row">
          <dt>Discovered at</dt>
          <dd>{formatDateTime(candidate.discovered_at)}</dd>
        </div>
        <div className="discovery-summary-row">
          <dt>Publication date</dt>
          <dd>{formatDate(candidate.publication_date)}</dd>
        </div>
        <div className="discovery-summary-row">
          <dt>Status</dt>
          <dd>
            <span className={`badge ${candidate.is_new ? "badge-new" : "badge-known"}`}>
              {candidate.is_new ? "New" : "Known"}
            </span>
          </dd>
        </div>
      </dl>
      {candidate.url && (
        <button
          type="button"
          className="primary-button"
          onClick={() => void onOpen(candidate.url)}
        >
          Open URL
        </button>
      )}
    </aside>
  );
}

/** The date AFTER `iso` (YYYY-MM-DD): the Core's window is end-exclusive, so a
 * "to" date chosen by the user is converted to its exclusive upper bound. */
function exclusiveEnd(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + 1)).toISOString().slice(0, 10);
}

/** Two-step confirmation button: first click arms it, second click fires. */
function ConfirmButton({
  label,
  confirmLabel,
  onClick,
  disabled,
  className = "secondary-button",
}: {
  label: string;
  confirmLabel: string;
  onClick: () => void;
  disabled?: boolean;
  className?: string;
}) {
  const [armed, setArmed] = useState(false);
  return (
    <button
      type="button"
      className={`${className}${armed ? " arm" : ""}`}
      disabled={disabled}
      onClick={() => {
        if (!armed) {
          setArmed(true);
          return;
        }
        setArmed(false);
        onClick();
      }}
      onBlur={() => setArmed(false)}
    >
      {armed ? confirmLabel : label}
    </button>
  );
}

/**
 * The single, operational Discovery screen. Everything shown comes from the
 * Core store through `useDiscovery` (the bridge is the source of truth — no
 * parallel React copy). Launching forwards the selected date window to the
 * Core; Run/Pause/Resume/Stop signal the campaign's recorded run_id/PID via
 * `discovery_control`; Clear Cache is only offered once a campaign has ended
 * (completed/cancelled/stopped/failed) and goes through the bridge, which refuses it
 * while a campaign is active.
 */
export default function Discovery({ discovery }: DiscoveryProps) {
  const { status, candidates, error, starting, openUrl } = discovery;
  const [selected, setSelected] = useState<DiscoveryCandidate | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const running = status.status === "running";
  const paused = status.status === "paused";
  const active = (running || paused) && !starting;
  const view = discoveryView(status);
  const hasRun = view.hasRun;

  const range =
    status.date_start || status.date_end
      ? [status.date_start && formatDate(status.date_start), status.date_end && formatDate(status.date_end)]
          .filter(Boolean)
          .join(" → ")
      : "Unbounded";

  const windowStatus = rangeStatus(dateFrom, dateTo);
  const runDisabled = active || starting || windowStatus !== "valid";

  const handleRun = () => {
    // Both dates are guaranteed non-empty here (Run is disabled otherwise).
    void discovery.launch(dateFrom, exclusiveEnd(dateTo));
  };

  return (
    <div className="discovery-view">
      <div className="discovery-head">
        <h1 className="view-title">Discovery</h1>
        {view.showStatusPill && <StatusPill status={status.status} />}
      </div>

      {error && <div className="data-browser-error">{error}</div>}

      <section className="discovery-card" aria-label="Discovery campaign controls">
        <div className="discovery-card-range">
          <label className="discovery-range">
            <span>Start date</span>
            <input
              type="date"
              value={dateFrom}
              disabled={active}
              onChange={(e) => setDateFrom(e.target.value)}
            />
          </label>
          <label className="discovery-range">
            <span>End date</span>
            <input
              type="date"
              value={dateTo}
              disabled={active}
              onChange={(e) => setDateTo(e.target.value)}
            />
          </label>
          <p
            className={`discovery-range-hint${windowStatus === "invalid" ? " discovery-range-hint-error" : ""}`}
          >
            {windowStatus === "invalid"
              ? INVALID_RANGE_HINT
              : windowStatus === "missing"
                ? REQUIRED_RANGE_HINT
                : "Publication-date window (start-inclusive, end-exclusive)."}
          </p>
        </div>

        <div className="discovery-controls discovery-controls-right">
          <button
            type="button"
            className="primary-button"
            disabled={runDisabled}
            onClick={() => void handleRun()}
          >
            Run Discovery
          </button>
          <button
            type="button"
            className="secondary-button"
            disabled={!view.canClear}
            onClick={() => setConfirmClear(true)}
          >
            Clear Discovery Cache
          </button>
          {active &&
            (paused ? (
              <button
                type="button"
                className="primary-button"
                onClick={() => void discovery.resume()}
              >
                Resume
              </button>
            ) : (
              <button
                type="button"
                className="primary-button"
                onClick={() => void discovery.pause()}
              >
                Pause
              </button>
            ))}
          {active && (
            <ConfirmButton label="Cancel Discovery" confirmLabel="Confirm cancel?" onClick={() => void discovery.stop()} />
          )}
        </div>
      </section>

      {view.showCurrentCard && (
      <section className="discovery-card" aria-label="Current Discovery">
        <dl className="discovery-summary">
          <div className="discovery-summary-row">
            <dt>Status</dt>
            <dd>{status.status}</dd>
          </div>
          <div className="discovery-summary-row">
            <dt>Run ID</dt>
            <dd>{status.run_id ?? "—"}</dd>
          </div>
          <div className="discovery-summary-row">
            <dt>Date range</dt>
            <dd>{hasRun ? range : "—"}</dd>
          </div>
        </dl>
        {!starting && view.showProgressBar && <CampaignProgress status={status} />}
        <div className="discovery-stats">
          <div className="stat-card">
            <span className="stat-value">{status.candidates.toLocaleString()}</span>
            <span className="stat-label">Candidates</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">{status.new.toLocaleString()}</span>
            <span className="stat-label">New</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">{status.known.toLocaleString()}</span>
            <span className="stat-label">Known</span>
          </div>
        </div>
      </section>
      )}

      {hasRun && (
        <section className="discovery-card" aria-label="Discovery history">
          <h2 className="discovery-section-title">Discovery History</h2>
          <dl className="discovery-summary">
            <div className="discovery-summary-row">
              <dt>Last Run</dt>
              <dd>{formatDateTime(status.started_at)}</dd>
            </div>
            <div className="discovery-summary-row">
              <dt>Finished</dt>
              <dd>{status.finished_at ? formatDateTime(status.finished_at) : "—"}</dd>
            </div>
          </dl>
        </section>
      )}

      {starting && (
        <div className="discovery-empty">
          <p className="discovery-running-text">Starting Discovery…</p>
        </div>
      )}

      {active && (
        <div className="discovery-empty">
          <p className={paused ? "discovery-paused-text" : "discovery-running-text"}>
            {paused ? "Discovery paused" : "Discovery running…"}
            {hasRun && ` · range ${range}`}
          </p>
        </div>
      )}

      {status.status === "failed" && (
        <div className="discovery-empty">
          <p className="data-browser-muted">{status.error || "Discovery failed."}</p>
        </div>
      )}
      {(status.status === "cancelled" || status.status === "stopped") && !active && (
        <div className="discovery-empty">
          <p className="data-browser-muted">
            {status.status === "cancelled" ? "Discovery cancelled" : "Discovery stopped"}
            {status.sources_total > 0
              ? ` · ${status.sources_completed} of ${status.sources_total} sources completed`
              : ""}
          </p>
        </div>
      )}

      {!active && !starting && view.emptyHeading && (
        <div className="discovery-empty">
          <p className="data-browser-muted">{view.emptyHeading}</p>
        </div>
      )}

      {view.showResults && (
        <div className="discovery-results">
          {status.status === "completed" && (
            <p className="discovery-count">
              Discovery Candidates · {status.candidates.toLocaleString()} candidates discovered
              {status.date_start || status.date_end ? ` · range ${range}` : ""}
              {" · "}
              {status.new} new / {status.known} known
            </p>
          )}
          {candidates.length > 0 ? (
            <table className="candidate-table">
              <thead>
                <tr>
                  <th>Bank</th>
                  <th>Title</th>
                  <th>Method</th>
                  <th>Status</th>
                  <th>URL</th>
                </tr>
              </thead>
              <tbody>
                {candidates.map((candidate, index) => (
                  <tr
                    key={`${candidate.publication_id ?? candidate.url}-${index}`}
                    className={selected === candidate ? "candidate-row selected" : "candidate-row"}
                    onClick={() => setSelected(candidate)}
                  >
                    <td>{candidate.bank_name}</td>
                    <td className="candidate-title">{candidate.title}</td>
                    <td>
                      <span className={`badge badge-method badge-${candidate.method}`}>
                        {candidate.method}
                      </span>
                    </td>
                    <td>
                      <span className={`badge ${candidate.is_new ? "badge-new" : "badge-known"}`}>
                        {candidate.is_new ? "New" : "Known"}
                      </span>
                    </td>
                    <td className="candidate-url">{candidate.url || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="data-browser-muted">No candidates were discovered.</p>
          )}
          {selected && <CandidateDetail candidate={selected} onOpen={(url) => void openUrl(url)} />}
        </div>
      )}

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
