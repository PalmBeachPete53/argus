import { useState } from "react";
import Header from "./components/Header";
import Sidebar from "./components/Sidebar";
import MainContent from "./components/MainContent";
import Footer from "./components/Footer";
import SettingsModal from "./components/SettingsModal";
import { useDiscovery } from "./hooks/useDiscovery";
import type { DataView } from "./types";

export default function App() {
  const [view, setView] = useState<DataView>("discovery");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const discovery = useDiscovery();

  return (
    <div className="app">
      <Header onOpenSettings={() => setSettingsOpen(true)} />
      <div className="app-body">
        <Sidebar active={view} onSelect={setView} />
        <MainContent view={view} discovery={discovery} />
      </div>
      <Footer status={discovery.status} starting={discovery.starting} />
      {settingsOpen && <SettingsModal onClose={() => setSettingsOpen(false)} />}
    </div>
  );
}
