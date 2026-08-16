interface HeaderProps {
  onOpenSettings: () => void;
}

export default function Header({ onOpenSettings }: HeaderProps) {
  return (
    <header className="header">
      <span className="header-logo">Argus</span>
      <button className="header-settings" onClick={onOpenSettings} title="Open settings">
        &#9881; Settings
      </button>
    </header>
  );
}
