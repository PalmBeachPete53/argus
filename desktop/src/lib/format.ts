/** Format an ISO timestamp as a compact `YYYY-MM-DD HH:MM` (UTC) label. */
export function formatDateTime(isoValue: string | null | undefined): string {
  if (!isoValue) return "—";
  const match = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(isoValue);
  if (match) return `${match[1]} ${match[2]}`;
  return isoValue.slice(0, 16).replace("T", " ");
}

/** Format an ISO date (or timestamp) as `YYYY-MM-DD`. */
export function formatDate(isoValue: string | null | undefined): string {
  if (!isoValue) return "—";
  const match = /^(\d{4}-\d{2}-\d{2})/.exec(isoValue);
  return match ? match[1] : isoValue.slice(0, 10);
}
