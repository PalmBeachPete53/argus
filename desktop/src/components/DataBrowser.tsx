import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { DirListing } from "../types";

export default function DataBrowser() {
  const [listing, setListing] = useState<DirListing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (target: string) => {
    setLoading(true);
    setError(null);
    try {
      const next = await invoke<DirListing>("list_dir", { path: target });
      setListing(next);
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const root = await invoke<string>("get_data_root");
        await load(root);
      } catch (err) {
        setError(String(err));
      }
    })();
  }, [load]);

  const goParent = () => {
    if (listing?.parent) void load(listing.parent);
  };

  const refresh = () => {
    if (listing) void load(listing.path);
  };

  return (
    <div className="data-browser">
      <div className="data-browser-toolbar">
        <span className="data-browser-path" title={listing?.path ?? ""}>
          {listing?.path ?? "…"}
        </span>
        <button type="button" onClick={goParent} disabled={!listing?.parent}>
          Up
        </button>
        <button type="button" onClick={refresh} disabled={loading}>
          Refresh
        </button>
      </div>
      {error && <div className="data-browser-error">Error: {error}</div>}
      {loading && <div className="data-browser-muted">Loading…</div>}
      {listing && !loading && (
        <ul className="data-browser-list">
          {listing.entries.map((entry) => (
            <li key={entry.path}>
              <button
                type="button"
                className="data-entry"
                onClick={() => entry.is_dir && void load(entry.path)}
                disabled={!entry.is_dir}
              >
                <span className="data-entry-name">{entry.name}</span>
                <span className="data-entry-type">{entry.is_dir ? "directory" : "file"}</span>
              </button>
            </li>
          ))}
          {listing.entries.length === 0 && (
            <li className="data-browser-muted">(empty directory)</li>
          )}
        </ul>
      )}
    </div>
  );
}
