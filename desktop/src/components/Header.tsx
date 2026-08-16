interface HeaderProps {
  onOpenSettings: () => void;
}

export default function Header({ onOpenSettings }: HeaderProps) {
  return (
    <header className="header" data-tauri-drag-region>
      <span className="header-logo">
        <img src="/argus-logo.png" alt="Argus logo" className="header-logo-img" />
        <span className="header-logo-text">Argus</span>
      </span>
      <button className="header-settings" onClick={onOpenSettings} title="Open settings">
        &#9881; Settings
      </button>
    </header>
  );
}
