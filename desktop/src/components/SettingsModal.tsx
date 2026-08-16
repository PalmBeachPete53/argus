import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { BankInfo } from "../types";

interface SettingsModalProps {
  onClose: () => void;
}

export default function SettingsModal({ onClose }: SettingsModalProps) {
  const [banks, setBanks] = useState<BankInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        setBanks(await invoke<BankInfo[]>("get_banks"));
      } catch (err) {
        setError(String(err));
      }
    })();
  }, []);

  const toggle = async (bank: BankInfo) => {
    setBusy(bank.id);
    setError(null);
    try {
      setBanks(await invoke<BankInfo[]>("set_bank", { bankId: bank.id, enabled: !bank.enabled }));
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="Settings" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2 className="modal-title">Settings</h2>
          <button type="button" className="modal-close" onClick={onClose} title="Close">
            &times;
          </button>
        </div>
        <div className="modal-body">
          <h3 className="modal-section">Banks</h3>
          {error && <div className="modal-error">Error: {error}</div>}
          {!banks && !error && <div className="data-browser-muted">Loading…</div>}
          {banks && (
            <ul className="bank-list">
              {banks.map((bank) => (
                <li key={bank.id} className="bank-row">
                  <div className="bank-text">
                    <span className="bank-name">{bank.name}</span>
                    <span className="bank-meta">
                      {bank.id} &middot; {bank.currency}
                    </span>
                  </div>
                  <button
                    type="button"
                    className={bank.enabled ? "toggle on" : "toggle"}
                    onClick={() => toggle(bank)}
                    disabled={busy === bank.id}
                    aria-pressed={bank.enabled}
                  >
                    {bank.enabled ? "ON" : "OFF"}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
