import { describe, expect, it } from "vitest";
import {
  dateInputStatus,
  discoveryWindowStatus,
  exclusiveEnd,
  formatDateInput,
  maskDateInput,
  monthGrid,
  parseDateInput,
  toIsoDate,
} from "./dateInput";

describe("formatDateInput — internal ISO to UI display", () => {
  it("formats 2026-08-16 as 16/08/2026", () => {
    expect(formatDateInput("2026-08-16")).toBe("16/08/2026");
  });

  it("returns an empty string for empty / unknown values", () => {
    expect(formatDateInput("")).toBe("");
    expect(formatDateInput(null)).toBe("");
    expect(formatDateInput(undefined)).toBe("");
  });
});

describe("parseDateInput — UI display to internal ISO", () => {
  it("parses 16/08/2026 into 2026-08-16", () => {
    expect(parseDateInput("16/08/2026")).toBe("2026-08-16");
  });

  it("parses a digit-only entry (no slashes) into ISO", () => {
    expect(parseDateInput("16082026")).toBe("2026-08-16");
  });

  it("accepts a leap-year February 29th", () => {
    expect(parseDateInput("29/02/2024")).toBe("2024-02-29");
  });

  it("rejects an impossible day (32/08/2026)", () => {
    expect(parseDateInput("32/08/2026")).toBeNull();
  });

  it("rejects an impossible month (16/13/2026)", () => {
    expect(parseDateInput("16/13/2026")).toBeNull();
  });

  it("rejects February 29th in a non-leap year", () => {
    expect(parseDateInput("29/02/2025")).toBeNull();
  });

  it("rejects February 30th", () => {
    expect(parseDateInput("30/02/2026")).toBeNull();
  });

  it("returns null for incomplete input", () => {
    expect(parseDateInput("16/08")).toBeNull();
    expect(parseDateInput("16/0")).toBeNull();
    expect(parseDateInput("")).toBeNull();
  });
});

describe("maskDateInput — natural typing", () => {
  it("keeps a fully-typed masked value intact", () => {
    expect(maskDateInput("16/08/2026")).toBe("16/08/2026");
  });

  it("inserts slashes while typing digits", () => {
    expect(maskDateInput("16")).toBe("16");
    expect(maskDateInput("1608")).toBe("16/08");
    expect(maskDateInput("16082026")).toBe("16/08/2026");
  });

  it("never grows past DD/MM/YYYY", () => {
    expect(maskDateInput("1608202611")).toBe("16/08/2026");
  });

  it("round-trips through parseDateInput", () => {
    expect(parseDateInput(maskDateInput("16/08/2026"))).toBe("2026-08-16");
    expect(parseDateInput(maskDateInput("16082026"))).toBe("2026-08-16");
  });
});

describe("dateInputStatus — per-field classification", () => {
  it("flags empty, incomplete, invalid and valid states", () => {
    expect(dateInputStatus("")).toBe("empty");
    expect(dateInputStatus("16/0")).toBe("incomplete");
    expect(dateInputStatus("32/08/2026")).toBe("invalid");
    expect(dateInputStatus("16/08/2026")).toBe("valid");
  });
});

describe("discoveryWindowStatus — the launch window", () => {
  it("allows an ordered window with the DD/MM/YYYY fields", () => {
    expect(discoveryWindowStatus("16/08/2026", "17/08/2026")).toBe("valid");
  });

  it("keeps identical dates a valid single-day window", () => {
    expect(discoveryWindowStatus("16/08/2026", "16/08/2026")).toBe("valid");
  });

  it("refuses an inverted window (17/08/2026 → 16/08/2026)", () => {
    expect(discoveryWindowStatus("17/08/2026", "16/08/2026")).toBe("invalid");
  });

  it("refuses a window with an impossible date", () => {
    expect(discoveryWindowStatus("32/08/2026", "17/08/2026")).toBe("invalid-date");
    expect(discoveryWindowStatus("16/08/2026", "32/08/2026")).toBe("invalid-date");
  });

  it("refuses a window with a missing or incomplete date", () => {
    expect(discoveryWindowStatus("", "17/08/2026")).toBe("missing");
    expect(discoveryWindowStatus("16/08/2026", "")).toBe("missing");
    expect(discoveryWindowStatus("16/0", "17/08/2026")).toBe("missing");
    expect(discoveryWindowStatus("", "")).toBe("missing");
  });
});

describe("calendar — keyboard and picker agree on the internal value", () => {
  it("hitting a calendar day yields the same ISO as typing it", () => {
    const iso = toIsoDate(2026, 8, 16); // calendar -> internal
    expect(iso).toBe("2026-08-16");
    expect(formatDateInput(iso)).toBe("16/08/2026"); // calendar -> display
    expect(parseDateInput(formatDateInput(iso))).toBe(iso); // identical to keyboard
  });

  it("builds a Monday-first month grid of full rows", () => {
    const august2026 = monthGrid(2026, 8);
    expect(august2026.length % 7).toBe(0);
    expect(august2026.filter((d) => d !== null)).toHaveLength(31);
    expect(monthGrid(2026, 2).filter((d) => d !== null)).toHaveLength(28);
  });
});

describe("exclusiveEnd — backend contract stays YYYY-MM-DD", () => {
  it("transmits ISO dates to the Core (start inclusive, end exclusive)", () => {
    const startIso = parseDateInput("16/08/2026");
    const endIso = parseDateInput("17/08/2026");
    expect(startIso).toBe("2026-08-16");
    expect(endIso).toBe("2026-08-17");
    expect(exclusiveEnd(endIso ?? "")).toBe("2026-08-18");
    expect(startIso).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});