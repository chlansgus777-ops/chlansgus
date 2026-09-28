// @vitest-environment jsdom
/** One price basis and one "may I buy now" rule on the stock page (independent review 2026-09-28, F03/F04). */
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import fixture from "../__fixtures__/stock_mock.json";
import { planNow, quantityShown } from "../advice";
import StockDetailPage from "./StockDetail";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

// eslint-disable-next-line @typescript-eslint/no-explicit-any
async function show(d: any) {
  vi.stubGlobal("fetch", async () => new Response(JSON.stringify(d), { status: 200 }));
  render(<MemoryRouter initialEntries={[`/stocks/${d.analysis.ticker}`]}><Routes><Route path="/stocks/:ticker" element={<StockDetailPage />} /></Routes></MemoryRouter>);
  return screen.findByTestId("tile-maxbuy");
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function withSplit(f: number): any {
  const d = structuredClone(fixture) as any; // eslint-disable-line @typescript-eslint/no-explicit-any
  const en = d.analysis.entry;
  // the snapshot keeps the analysis-day prices; the backend sends the plan on today's share basis
  Object.assign(d.recommendation, {
    split_factor_since: f, price: en.current_price / f, ideal_entry: en.ideal_entry / f, max_buy: en.max_buy / f, stop: en.stop / f, target: en.target1 / f,
    target2: en.target2 / f, buy_zone_low: en.acceptable_low / f, buy_zone_high: en.acceptable_high / f, add_zone_low: en.add_zone_low / f, add_zone_high: en.add_zone_high / f,
  });
  return d;
}

const usd = (v: number) => `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

describe("the stock page uses today's share basis everywhere", () => {
  it.each([[10, "forward 10:1 split"], [0.1, "reverse 1:10 merge"]])("factor %s (%s): tiles, plan and ladder agree", async (f) => {
    const d = withSplit(f);
    const en = d.analysis.entry;
    const tile = await show(d);
    expect(tile.textContent).toContain(usd(en.max_buy / f));
    expect(screen.getByTestId("tile-stop").textContent).toContain(usd(en.stop / f));
    const plan = screen.getByTestId("price-plan").textContent ?? "";
    for (const v of [en.ideal_entry, en.acceptable_low, en.acceptable_high, en.max_buy, en.add_zone_low, en.add_zone_high, en.stop, en.target1, en.target2]) {
      expect(plan).toContain(usd(v / f));
      expect(plan).not.toContain(usd(v)); // never a pre-split level beside post-split ones
    }
    // the analysis-day prices are still there — labelled as the record
    expect(screen.getByTestId("split-adjusted").textContent).toContain(usd(en.max_buy));
  });

  it("derives a level the server did not send from the snapshot with the same factor", () => {
    const en = (fixture as any).analysis.entry; // eslint-disable-line @typescript-eslint/no-explicit-any
    const p = planNow(en, { price: 22.77, ideal_entry: null, max_buy: 22.94, stop: 22.4, target: 24.04, split_factor_since: 10 }, en.current_price);
    expect(p?.max_buy).toBe(22.94);
    expect(p?.acceptable_low).toBeCloseTo(en.acceptable_low / 10, 9);
    expect(p?.target2).toBeCloseTo(en.target2 / 10, 9);
    expect(p?.ideal_entry).toBeCloseTo(en.ideal_entry / 10, 9);
    expect(p?.rr_at_current).toBe(en.rr_at_current); // ratios do not change with the share count
    expect(planNow(null, { price: 1, ideal_entry: null, max_buy: null, stop: null, target: null }, 1)).toBeNull();
  });
});

describe("a buy quantity only while the recommendation holds now", () => {
  it("is withheld for every status but CURRENT, and for a server-side 'not actionable'", () => {
    expect(quantityShown("CURRENT", true)).toBe(true);
    for (const s of ["AGING", "EXPIRED", "PLAN_INVALIDATED", "NEEDS_REVALIDATION", "SUPERSEDED", "UNVERIFIED", null]) expect(quantityShown(s, true)).toBe(false);
    expect(quantityShown("CURRENT", false)).toBe(false);
  });

  it.each(["PLAN_INVALIDATED", "AGING", "NEEDS_REVALIDATION"])("%s: the page shows why instead of '권장 매수'", async (status) => {
    const d = structuredClone(fixture) as any; // eslint-disable-line @typescript-eslint/no-explicit-any
    Object.assign(d.recommendation, { current_status: status, current_status_reason: "현재가 확인 필요", actionable_now: false });
    d.position_plan = { available: true, nav: 100000, size_class: "FULL", weight: 0.05, amount: 5000, shares: 21, price: 227.67, risk_amount: 80, risk_pct: 0.0008, notes: [] };
    await show(d);
    expect(screen.queryByText("권장 매수")).toBeNull();
    expect(screen.getByTestId("position-plan-withheld").textContent).toContain("현재가 확인 필요");
  });

  it("CURRENT and actionable: the quantity is shown", async () => {
    const d = structuredClone(fixture) as any; // eslint-disable-line @typescript-eslint/no-explicit-any
    d.position_plan = { available: true, nav: 100000, size_class: "HALF", weight: 0.025, amount: 2504.37, shares: 11, price: 227.67, risk_amount: 40, risk_pct: 0.0004, notes: [] };
    await show(d);
    expect(screen.getByText("권장 매수")).toBeTruthy();
  });
});
