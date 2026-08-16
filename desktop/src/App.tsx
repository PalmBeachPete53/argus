import { useState } from "react";
import Header from "./components/Header";
import Sidebar from "./components/Sidebar";
import MainContent from "./components/MainContent";
import Footer from "./components/Footer";
import SettingsModal from "./components/SettingsModal";
import type { SectionId } from "./types";

export default function App() {
  const [section, setSection] = useState<SectionId>("data");
  const [settingsOpen, setSettingsOpen] = useState(false);

  return (
    <div className="app">
      <Header onOpenSettings={() => setSettingsOpen(true)} />
      <div className="app-body">
        <Sidebar active={section} onSelect={setSection} />
        <MainContent section={section} />
      </div>
      <Footer />
      {settingsOpen && <SettingsModal onClose={() => setSettingsOpen(false)} />}
    </div>
  );
}
