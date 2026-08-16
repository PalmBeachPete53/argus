import type { SectionId } from "../types";

interface SidebarProps {
  active: SectionId;
  onSelect: (section: SectionId) => void;
}

// V1 exposes a single section ("Data"); the list is structured so further
// sections can be added without a layout refactor.
const SECTIONS: { id: SectionId; label: string }[] = [{ id: "data", label: "Data" }];

export default function Sidebar({ active, onSelect }: SidebarProps) {
  return (
    <nav className="sidebar" aria-label="Sections">
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
