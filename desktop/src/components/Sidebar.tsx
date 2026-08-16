import type { DataView } from "../types";

interface SidebarProps {
  active: DataView;
  onSelect: (view: DataView) => void;
}

const SECTIONS: { id: DataView; label: string }[] = [
  { id: "sources", label: "Sources" },
  { id: "discovery", label: "Discovery" },
  { id: "files", label: "Files" },
];

export default function Sidebar({ active, onSelect }: SidebarProps) {
  return (
    <nav className="sidebar" aria-label="Sections">
      <p className="sidebar-group">DATA</p>
      <ul className="sidebar-list">
        {SECTIONS.map((section) => (
          <li key={section.id}>
            <button
              type="button"
              className={active === section.id ? "sidebar-item active" : "sidebar-item"}
              onClick={() => onSelect(section.id)}
            >
              {section.label}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
