import { describe, expect, it } from "vitest";
import { actionClass, big, day, koUnits, krwAux, num, pct, price, stamp, usdWithKo } from "./format";

describe("format", () => {
  it("never invents values for missing data", () => {
    expect(num(null)).toBe("N/A");
    expect(pct(undefined)).toBe("N/A");
    expect(price(null)).toBe("N/A");
    expect(stamp(null)).toBe("N/A");
    expect(big(Number.NaN)).toBe("N/A");
    expect(krwAux(100, null)).toBe(""); // no FX rate → no KRW figure at all
  });
  it("formats numbers with thousand separators and USD", () => {
    expect(num(1234567.891, 2)).toBe("1,234,567.89");
    expect(price(1234.5)).toBe("$1,234.50");
    expect(price(-5)).toBe("-$5.00");
    expect(pct(0.1234)).toBe("+12.3%");
    expect(pct(-0.05)).toBe("-5.0%");
    expect(big(2.5e12)).toBe("$2.50T");
  });
  it("reads large amounts in Korean units", () => {
    expect(koUnits(2.55e12)).toBe("2조 5,500억");
    expect(koUnits(123_400_000)).toBe("1억 2,340만");
    expect(koUnits(9_999)).toBe("9,999");
    expect(usdWithKo(2.55e12)).toBe("$2.55T (약 2조 5,500억 달러)");
    expect(krwAux(1000, 1385)).toBe("≈ ₩138만");
  });
  it("shows US Eastern time with Korea time alongside", () => {
    expect(stamp("2026-09-25T13:30:00Z")).toBe("2026-09-25 09:30 ET (2026-09-25 22:30 KST)");
    expect(stamp("2026-01-15T21:05:00Z")).toBe("2026-01-15 16:05 ET (2026-01-16 06:05 KST)"); // EST, date rolls over in Korea
    expect(day("2026-09-25")).toBe("2026-09-25");
  });
  it("maps actions to classes", () => {
    expect(actionClass("BUY SMALL")).toBe("badge a-BUY-SMALL");
    expect(actionClass("DATA INSUFFICIENT")).toBe("badge a-DATA-INSUFFICIENT");
  });
});
