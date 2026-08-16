/**
 * Discovery launch preconditions. A campaign may only be launched with a
 * complete, ordered publication-date window:
 *
 *   start_date present AND end_date present AND start_date <= end_date
 *
 * The same invariant is enforced by the backend (Rust shell and the Python
 * bridge) — this module is the single shared frontend definition, kept pure so
 * it is trivially unit-testable.
 */

export type DiscoveryRangeStatus = "missing" | "invalid" | "valid";

/**
 * Classify the user-selected window. Dates are `YYYY-MM-DD` strings (or empty
 * when unset), so plain string ordering is the correct date comparison.
 */
export function rangeStatus(start: string, end: string): DiscoveryRangeStatus {
  if (!start || !end) return "missing";
  return start <= end ? "valid" : "invalid";
}

export const REQUIRED_RANGE_HINT = "A start and end date are required to run Discovery.";
export const INVALID_RANGE_HINT = "End date must be on or after start date.";
