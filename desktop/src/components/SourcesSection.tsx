import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { BankSources } from "../types";

// Read-only view of the Core's SourceRegistry — never duplicated in the
// frontend: the data comes from the bridge (`get_sources`).
export default function SourcesSection() {
  const [ordered, setOrdered] = useState<[string, BankSources][] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const map = await invoke<Record<string, BankSources>>("get_sources");
        const entries = Object.entries(map).sort(([a], [b]) => a.localeCompare(b));
        setOrdered(entries);
      } catch (err) {
        setError(String(err));
      }
    })();
  }, []);

  return (
    <div>
      {error && <div className="modal-error">Error: {error}</div>}
      {!ordered && !error && <div className="data-browser-muted">Loading…</div>}
      {ordered &&
        ordered.map(([bankId, bank]) => (
          <div key={bankId} className="sources-bank">
            <h4 className="sources-bank-name">{bank.bank}</h4>
            {bank.sources.length === 0 && (
              <div className="data-browser-muted">(no configured sources)</div>
            )}
            {bank.sources.map((source) => (
              <div key={source.id} className="source-card">
                <div className="source-card-head">
                  <span className="source-name">{source.name}</span>
                  <span className="source-kind">{source.kind}</span>
                </div>
                <div className="source-url" title={source.url}>
                  {source.url}
                </div>
                <div className="source-meta">
                  <span className={source.enabled ? "badge on" : "badge"}>
                    {source.enabled ? "enabled" : "disabled"}
                  </span>
                  {source.search_fallback && <span className="badge">search fallback</span>}
                  {source.publication_types.length > 0 && (
                    <span className="badge">{source.publication_types.join(", ")}</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        ))}
    </div>
  );
}
