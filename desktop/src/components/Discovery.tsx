import { useState } from "react";
import type { DiscoveryCandidate } from "../types";
import type { DiscoveryState } from "./MainContent";
import { formatDate, formatDateTime } from "../lib/format";

interface DiscoveryProps {
  discovery: DiscoveryState;
}

function StatusPill({ status }: { status: string }) {
  return <span className={`pill pill-${status}`}>{status}</span>;
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

function RangeControl({
  discovery,
}: {
  discovery: DiscoveryState;
}) {
  const { status } = discovery;
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const active = status.status === "running" || status.status === "paused";

  const runNow = () => {
    const end = dateTo ? exclusiveEnd(dateTo) : undefined;
    void discovery.launch(dateFrom || undefined, end);
  };

  return (
    <div className="discovery-empty">
      <p className="data-browser-muted">
        Discover publication candidates from configured sources, optionally within a
        publication-date window.
      </p>
      {!active && (
        <div className="discovery-controls">
          <label className="discovery-range">
            <span>From</span>
            <input
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
            />
          </label>
          <label className="discovery-range">
            <span>To</span>
            <input
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
            />
          </label>
          <button type="button" className="primary-button" onClick={() => void runNow()}>
            Run Discovery
          </button>
        </div>
      )}
      {active && (
        <div className="discovery-controls">
          <button type="button" className="primary-button" disabled>
            {status.status === "paused" ? "Paused" : "Running…"}
          </button>
        </div>
      )}
      {!active && (
        <button
          type="button"
          className="secondary-button link"
          onClick={() => void discovery.clearCache()}
        >
          Clear discovery cache
        </button>
      )}
    </div>
  );
}

export default function Discovery({ discovery }: DiscoveryProps) {
  const { status, candidates, error, openUrl } = discovery;
  const [selected, setSelected] = useState<DiscoveryCandidate | null>(null);
  const running = status.status === "running";
  const paused = status.status === "paused";
  const active = running || paused;

  return (
    <div className="discovery-view">
      <div className="discovery-head">
        <h1 className="view-title">Discovery</h1>
        {status.status !== "idle" && <StatusPill status={status.status} />}
      </div>

      {error && <div className="data-browser-error">{error}</div>}

      {status.status === "idle" && <RangeControl discovery={discovery} />}

      {active && (
        <div className="discovery-empty">
          <p className={paused ? "discovery-paused-text" : "discovery-running-text"}>
            {paused ? "Discovery paused" : "Discovery running…"}
          </p>
          <div className="discovery-controls">
            {paused ? (
              <button type="button" className="primary-button" onClick={() => void discovery.resume()}>
                Resume
              </button>
            ) : (
              <button type="button" className="primary-button" onClick={() => void discovery.pause()}>
                Pause
              </button>
            )}
            <ConfirmButton label="Stop" confirmLabel="Confirm stop?" onClick={() => void discovery.stop()} />
          </div>
        </div>
      )}

      {status.status === "failed" && (
        <div className="discovery-empty">
          <p className="data-browser-muted">
            {status.error || "Discovery failed."}
          </p>
          <button type="button" className="primary-button" onClick={() => void discovery.launch()}>
            Retry Discovery
          </button>
        </div>
      )}

      {status.status === "stopped" && (
        <div className="discovery-empty">
          <p className="data-browser-muted">
            {status.error || "Discovery was stopped."}
          </p>
          <button type="button" className="primary-button" onClick={() => void discovery.launch()}>
            Run Discovery again
          </button>
        </div>
      )}

      {(status.status === "completed" || status.status === "stopped") && (
        <div className="discovery-results">
          {status.status === "completed" && (
            <p className="discovery-count">
              Discovery Candidates · {status.candidates.toLocaleString()} candidates discovered
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
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="data-browser-muted">No candidates were discovered.</p>
          )}
          {selected && (
            <CandidateDetail candidate={selected} onOpen={(url) => void openUrl(url)} />
          )}
          <div className="discovery-controls">
            <ConfirmButton label="Clear cache" confirmLabel="Confirm clear?" onClick={() => void discovery.clearCache()} />
          </div>
        </div>
      )}
    </div>
  );
}