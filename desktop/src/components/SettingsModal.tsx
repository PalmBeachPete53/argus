import { useState } from "react";
import type { SettingsSection } from "../types";
import BanksSection from "./BanksSection";
import SourcesSection from "./SourcesSection";
import GeneralSection from "./GeneralSection";

interface SettingsModalProps {
  onClose: () => void;
}

const SECTIONS: { id: SettingsSection; label: string }[] = [
  { id: "general", label: "General" },
  { id: "banks", label: "Banks" },
  { id: "sources", label: "Sources" },
];

export default function SettingsModal({ onClose }: SettingsModalProps) {
  const [section, setSection] = useState<SettingsSection>("general");

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="Settings" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2 className="modal-title">Settings</h2>
          <button type="button" className="modal-close" onClick={onClose} title="Close">
            &times;
          </button>
        </div>
        <div className="modal-split">
          <nav className="settings-nav" aria-label="Settings sections">
            <ul className="settings-nav-list">
              {SECTIONS.map((s) => (
                <li key={s.id}>
                  <button
                    type="button"
                    className={section === s.id ? "settings-nav-item active" : "settings-nav-item"}
                    onClick={() => setSection(s.id)}
                  >
                    {s.label}
                  </button>
                </li>
              ))}
            </ul>
          </nav>
          <div className="settings-content">
            {section === "general" && <GeneralSection />}
            {section === "banks" && <BanksSection />}
            {section === "sources" && <SourcesSection />}
          </div>
        </div>
      </div>
    </div>
  );
}
