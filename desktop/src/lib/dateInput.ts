/**
 * Discovery date fields: French UX (DD/MM/YYYY) over an ISO (YYYY-MM-DD)
 * internal format.
 *
 * The UI shows and accepts dates as `DD/MM/YYYY`; the Core, the Rust shell and
 * the bridge all speak `YYYY-MM-DD`. All conversions live here, pure and
 * unit-testable, so the two never mix.
 *
 * The old `input[type="date"]` controls were dropped on purpose: they are
 * rendered by the OS/browser and keep a locale-dependent display/input format
 * that a stylesheet or placeholder cannot override. The replacement is a text
 * field with a light mask (typing slashes is optional) plus a custom calendar
 * popup; both funnel through the same parsing/formatting so keyboard and
 * calendar always produce the exact same internal value.
 */

export type DateInputStatus = "empty" | "incomplete" | "invalid" | "valid";
export type DiscoveryWindowStatus = "missing" | "invalid-date" | "invalid" | "valid";

export const INVALID_DATE_HINT = "Enter a valid date in DD/MM/YYYY format.";

const pad2 = (n: number): string => String(n).padStart(2, "0");

/** Extract up to the first 8 digits of a raw field value (junk-proof). */
function digits(raw: string): string {
  return (raw || "").replace(/[^\d]/g, "").slice(0, 8);
}

/** Progressive `DD/MM/YYYY` mask: typing slashes is optional. */
export function maskDateInput(text: string): string {
  const d = digits(text);
  if (!d) return "";
  if (d.length <= 2) return d;
  if (d.length <= 4) return `${d.slice(0, 2)}/${d.slice(2)}`;
  return `${d.slice(0, 2)}/${d.slice(2, 4)}/${d.slice(4)}`;
}

/**
 * Parse a displayed `DD/MM/YYYY` value into the internal ISO `YYYY-MM-DD`.
 * Accepts the masked form with or without separators still being present, but
 * requires a real calendar date (day-in-month, leap years honoured). Returns
 * `null` for incomplete or impossible values (e.g. `32/08/2026`).
 */
export function parseDateInput(text: string): string | null {
  const d = digits(text);
  if (d.length < 8) return null;
  const day = Number(d.slice(0, 2));
  const month = Number(d.slice(2, 4));
  const year = Number(d.slice(4, 8));
  if (month < 1 || month > 12) return null;
  const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate();
  if (day < 1 || day > daysInMonth) return null;
  return `${pad2(year)}-${pad2(month)}-${pad2(day)}`;
}

/** Format an internal ISO `YYYY-MM-DD` for display as `DD/MM/YYYY`. */
export function formatDateInput(iso: string | null | undefined): string {
  if (!iso) return "";
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return match ? `${match[3]}/${match[2]}/${match[1]}` : "";
}

/** Build an ISO `YYYY-MM-DD` from a calendar pick (month is 1-12). */
export function toIsoDate(year: number, month: number, day: number): string {
  return `${pad2(year)}-${pad2(month)}-${pad2(day)}`;
}

/**
 * The date AFTER `iso` (YYYY-MM-DD): the Core's window is end-exclusive, so a
 * "to" date chosen by the user is converted to its exclusive upper bound
 * before it reaches the backend. Unchanged contract.
 */
export function exclusiveEnd(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + 1)).toISOString().slice(0, 10);
}

/**
 * Per-field classification, used to decide whether the launch window is
 * blocked by a field that cannot ever become a date.
 *
 * - `empty` — nothing typed;
 * - `incomplete` — digits are present but the value is not a full `DD/MM/YYYY`
 *   yet (treated as "not entered" while typing; never yells at the user);
 * - `invalid` — a structurally complete value that is not a real calendar date
 *   (e.g. `32/08/2026`);
 * - `valid` — a real calendar date.
 */
export function dateInputStatus(text: string): DateInputStatus {
  if (!digits(text)) return "empty";
  if (parseDateInput(text)) return "valid";
  return digits(text).length >= 8 ? "invalid" : "incomplete";
}

/**
 * Classify the window formed by the two displayed fields. Mirrors the existing
 * backend/`rangeStatus` contract (start inclusive, end exclusive) on top of
 * real parsing:
 *
 * - either field holds an impossible date → `"invalid-date"`;
 * - either field is empty/incomplete → `"missing"`;
 * - `start > end` → `"invalid"`;
 * - otherwise → `"valid"` (identical dates stay a valid single-day window).
 */
export function discoveryWindowStatus(fromText: string, toText: string): DiscoveryWindowStatus {
  const fromStatus = dateInputStatus(fromText);
  const toStatus = dateInputStatus(toText);
  if (fromStatus === "invalid" || toStatus === "invalid") return "invalid-date";
  const fromIso = parseDateInput(fromText);
  const toIso = parseDateInput(toText);
  if (!fromIso || !toIso) return "missing";
  return fromIso <= toIso ? "valid" : "invalid";
}

export const FR_MONTHS = [
  "janvier",
  "février",
  "mars",
  "avril",
  "mai",
  "juin",
  "juillet",
  "août",
  "septembre",
  "octobre",
  "novembre",
  "décembre",
];

export const FR_WEEKDAYS = ["Lu", "Ma", "Me", "Je", "Ve", "Sa", "Di"];

/**
 * Cells for a month-grid calendar (Monday first). `null` cells pad out the
 * weeks so the grid always aligns to full rows; `month` is 1-12.
 */
export function monthGrid(year: number, month: number): (number | null)[] {
  const firstWeekday = new Date(year, month - 1, 1).getDay(); // 0 = Sunday
  const offset = (firstWeekday + 6) % 7; // shift to a Monday-first week
  const daysInMonth = new Date(year, month, 0).getDate();
  const cells: (number | null)[] = [];
  for (let i = 0; i < offset; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(d);
  while (cells.length % 7 !== 0) cells.push(null);
  return cells;
}