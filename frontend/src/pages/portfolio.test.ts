import { describe, expect, it } from "vitest";
import { interpret } from "./Portfolio";

const base = { valuation_day: "2026-09-24", cash: 20000, invested_value: 80000, nav: 100000, unrealized_pnl: 5000, sector_weights: {}, theme_weights: {}, hhi: 0.2, beta: 1.2, correlations: [] as [string, string, number][], missing_prices: [], notes: [], currency: "USD", note: "" };
const h = (ticker: string, weight: number, sector = "Technology") => ({ ticker, quantity: 1, cost_basis: 1, price: 1, price_day: "2026-09-24", market_value: weight * 100000, unrealized_pnl: 0, unrealized_pct: 0, weight, sector });

describe("portfolio interpretation", () => {
  it("warns about AI/semiconductor concentration in plain Korean", () => {
    const out = interpret({ ...base, holdings: [h("NVDA", 0.4), h("AMD", 0.4)], sector_weights: { Technology: 0.8 }, theme_weights: { AI: 0.8 }, correlations: [["NVDA", "AMD", 0.86]] });
    const text = out.map((o) => o.text).join(" ");
    expect(text).toContain("AI/반도체 비중");
    expect(text).toContain("Technology 업종");
    expect(text).toContain("거의 같이 움직입니다");
    expect(out.some((o) => o.tone === "warn")).toBe(true);
  });
  it("says a diversified portfolio is fine", () => {
    const out = interpret({ ...base, holdings: [h("A", 0.08, "Energy"), h("B", 0.08, "Healthcare")], sector_weights: { Energy: 0.08, Healthcare: 0.08 } });
    expect(out[0]!.text).toContain("고르게 분산");
  });
  it("says nothing when there are no holdings", () => {
    expect(interpret({ ...base, holdings: [] })).toEqual([]);
  });
});
