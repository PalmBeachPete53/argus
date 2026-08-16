import type { DiscoveryRun } from "../types";

interface FooterProps {
  status: DiscoveryRun;
}

// Minimal use of the footer: it becomes the global status bar, but stays sober
// for V1. Discovery is the only activity surfaced so far; the text comes from
// the Core's run state — never a simulated progress percentage.
export default function Footer({ status }: FooterProps) {
  let text = "";
  if (status.status === "running") text = "Discovery running…";
  else if (status.status === "completed")
    text = `Discovery completed · ${status.candidates} candidates`;
  else if (status.status === "failed") text = "Discovery failed";

  if (!text) return <footer className="footer" aria-hidden="true" />;

  return (
    <footer className={`footer footer-status footer-${status.status}`}>
      <span className="footer-dot" aria-hidden="true" />
      <span>{text}</span>
    </footer>
  );
}
