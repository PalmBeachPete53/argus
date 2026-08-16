import { describe, expect, it } from "vitest";
import { rangeStatus } from "./discovery";

describe("rangeStatus — Discovery launch precondition", () => {
  it("allows a run when both dates are present and ordered", () => {
    expect(rangeStatus("2025-01-01", "2025-12-31")).toBe("valid");
  });

  it("allows a run when start equals end (single-day window)", () => {
    expect(rangeStatus("2025-01-01", "2025-01-01")).toBe("valid");
  });

  it("refuses a run when the start date is missing", () => {
    expect(rangeStatus("", "2025-12-31")).toBe("missing");
  });

  it("refuses a run when the end date is missing", () => {
    expect(rangeStatus("2025-01-01", "")).toBe("missing");
  });

  it("refuses a run when both dates are missing", () => {
    expect(rangeStatus("", "")).toBe("missing");
  });

  it("refuses a run when start is after end", () => {
    expect(rangeStatus("2025-12-31", "2025-01-01")).toBe("invalid");
  });
});
