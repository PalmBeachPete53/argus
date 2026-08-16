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

export default function Discovery({ discovery }: DiscoveryProps) {
  const { status, candidates, error, launch, openUrl } = discovery;
  const [selected, setSelected] = useState<DiscoveryCandidate | null>(null);
  const running = status.status === "running";

  return (
    <div className="discovery-view">
      <div className="discovery-head">
        <h1 className="view-title">Discovery</h1>
        {status.status !== "idle" && <StatusPill status={status.status} />}
      </div>

      {error && <div className="data-browser-error">{error}</div>}

      {status.status === "idle" && (
        <div className="discovery-empty">
          <p className="data-browser-muted">
            Discover publication candidates from configured sources.
          </p>
          <button type="button" className="primary-button" onClick={() => void launch()}>
            Run Discovery
          </button>
        </div>
      )}

      {running && (
        <div className="discovery-empty">
          <p className="discovery-running-text">Discovery running…</p>
          <button type="button" className="primary-button" disabled>
            Running…
          </button>
        </div>
      )}

      {status.status === "failed" && (
        <div className="discovery-empty">
          <p className="data-browser-muted">
            {status.error || "Discovery failed."}
          </p>
          <button type="button" className="primary-button" onClick={() => void launch()}>
            Retry Discovery
          </button>
        </div>
      )}

      {status.status === "completed" && (
        <div className="discovery-results">
          <p className="discovery-count">
            Discovery Candidates · {status.candidates.toLocaleString()} candidates discovered
          </p>
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
        </div>
      )}
    </div>
  );
}
