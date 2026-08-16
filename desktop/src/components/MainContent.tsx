import type { SectionId } from "../types";
import DataBrowser from "./DataBrowser";

interface MainContentProps {
  section: SectionId;
}

export default function MainContent({ section }: MainContentProps) {
  return <main className="main">{section === "data" ? <DataBrowser /> : null}</main>;
}
