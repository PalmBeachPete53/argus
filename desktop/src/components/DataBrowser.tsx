import { Fragment, useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { DirEntry, DirListing } from "../types";

// File types openable with the OS-default application (case-insensitive).
const OPENABLE_EXTENSIONS = [".html", ".htm", ".pdf"];

function isOpenable(name: string): boolean {
  const dot = name.lastIndexOf(".");
  if (dot < 0) return false;
  return OPENABLE_EXTENSIONS.includes(name.slice(dot).toLowerCase());
}

export default function DataBrowser() {
  const [listing, setListing] = useState<DirListing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Navigation is relative to the Argus data root; the bridge resolves and
  // confines every path to data/ (the frontend never sends absolute paths).
  const load = useCallback(async (relativePath: string) => {
    setLoading(true);
    setError(null);
    try {
      setListing(await invoke<DirListing>("list_dir", { relativePath }));
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load("");
  }, [load]);

  const openFile = useCallback(async (entry: DirEntry) => {
    setError(null);
    try {
      await invoke("open_file", { relativePath: entry.path });
    } catch (err) {
      setError(String(err));
    }
  }, []);

  const onEntryClick = (entry: DirEntry) => {
    if (entry.is_dir) {
      void load(entry.path);
    } else if (isOpenable(entry.name)) {
      void openFile(entry);
    }
  };

  const goParent = () => {
    if (listing && listing.parent !== null) void load(listing.parent);
  };

  const refresh = () => {
    if (listing) void load(listing.path);
  };

  const openSegment = (index: number) => {
    // index 0 = root ("Data"); index n = up to the nth segment
    if (index === 0) {
      void load("");
    } else if (listing) {
      void load(listing.segments.slice(0, index).join("/"));
    }
  };

  const atRoot = listing === null || listing.parent === null;

  return (
    <div className="data-browser">
      <div className="data-browser-toolbar">
        <nav className="data-breadcrumb" aria-label="Data path">
          <button type="button" className="crumb root" onClick={() => void load("")}>
            Data
          </button>
          {listing?.segments.map((segment, index) => (
            <Fragment key={`${index}-${segment}`}>
              <span className="crumb-sep">/</span>
              <button
                type="button"
                className="crumb"
                onClick={() => openSegment(index + 1)}
              >
                {segment}
              </button>
            </Fragment>
          ))}
        </nav>
        <button type="button" onClick={goParent} disabled={atRoot}>
          Up
        </button>
        <button type="button" onClick={refresh} disabled={loading}>
          Refresh
        </button>
      </div>
      {error && <div className="data-browser-error">{error}</div>}
      {loading && <div className="data-browser-muted">Loading…</div>}
      {listing && !loading && (
        <ul className="data-browser-list">
          {listing.entries.map((entry) => {
            const openable = !entry.is_dir && isOpenable(entry.name);
            const interactive = entry.is_dir || openable;
            return (
              <li key={entry.path}>
                <button
                  type="button"
                  className={
                    openable ? "data-entry openable" : entry.is_dir ? "data-entry" : "data-entry disabled"
                  }
                  onClick={() => onEntryClick(entry)}
                  disabled={!interactive}
                  title={entry.is_dir ? "Open directory" : openable ? "Open with system default app" : undefined}
                >
                  <span className={entry.is_dir ? "data-entry-name dir" : "data-entry-name"}>
                    {entry.name}
                  </span>
                  <span className="data-entry-type">
                    {entry.is_dir ? "directory" : openable ? "open" : "file"}
                  </span>
                </button>
              </li>
            );
          })}
          {listing.entries.length === 0 && (
            <li className="data-browser-muted">(empty directory)</li>
          )}
        </ul>
      )}
    </div>
  );
}
