import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { BankInfo } from "../types";

// Bank Toggle — reads/writes the real Argus configuration through the bridge
// (single source of truth; no parallel state in the GUI).
export default function BanksSection() {
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
    <div>
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
  );
}
