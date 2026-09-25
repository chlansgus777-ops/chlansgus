import { describe, expect, it } from "vitest";
import { actionClass, big, num, pct, price, stamp } from "./format";

describe("format", () => {
  it("never invents values for missing data", () => {
    expect(num(null)).toBe("N/A");
    expect(pct(undefined)).toBe("N/A");
    expect(price(null)).toBe("N/A");
    expect(stamp(null)).toBe("N/A");
  });
  it("formats numbers", () => {
    expect(pct(0.1234)).toBe("+12.3%");
    expect(pct(-0.05)).toBe("-5.0%");
    expect(big(2.5e12)).toBe("2.50T");
  });
  it("shows ET market time", () => {
    expect(stamp("2026-09-25T13:30:00Z")).toContain("09:30");
  });
  it("maps actions to classes", () => {
    expect(actionClass("BUY SMALL")).toBe("badge a-BUY-SMALL");
    expect(actionClass("DATA INSUFFICIENT")).toBe("badge a-DATA-INSUFFICIENT");
  });
});
