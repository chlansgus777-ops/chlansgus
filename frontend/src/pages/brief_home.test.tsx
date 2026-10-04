// @vitest-environment jsdom
/** 간략 모드 (owner 2026-10-04: "보고 바로 판단"): the home answers three questions from the backend's own numbers —
 * the market, what to buy (price to buy at or below, stop, target) and what to do with each holding; only current,
 * actionable buys appear; the switch to 자세히 is remembered. All data here is made up. */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { resetApiCache } from "../components/useApi";
import type { OppRow } from "../types";
import Home from "./Home";

const row = (over: Partial<OppRow>): OppRow => ({
  id: 1, rank: 1, ticker: "AAA", company: "Alpha Inc", sector: "Technology", sector_model: "software", price: 100, session: "CLOSED",
  price_timestamp: "2026-09-25T20:00:00Z", price_source: "polygon", price_quality: "FRESH", score: 84, confidence: 70, action: "BUY",
  deterministic_action: "BUY", committee_status: "COMPLETED", ideal_entry: 98, max_buy: 101, target: 115, stop: 94, downside: -0.06, rr: 2.5,
  catalyst: null, catalyst_date: null, risk: "LOW", data_quality: "FRESH", mode: "LIVE", vetoes: [], as_of: "2026-09-26T01:00:00Z",
  current_status: "CURRENT", current_status_reason: "최신", sessions_since: 0, actionable_now: true, action_ko: "매수", valuation_price_basis: "현재가",
  sector_known: true, key_reason: "매출 성장률 30% (업종 기준 우수)", key_risk: null, ...over,
});
const PLAN = { symbol: "NVDA", name: "NVIDIA", market: "US", currency: "USD", quantity: 10, avg_price: 105, price: 96, price_at: null, price_source: "실시간",
  pnl_pct: -8.57, stop: 97.65, stop_source: "평단 -7%", take1: 115.5, take1_fraction: 0.5, take1_done: false, trail: null, high_since_open: 106,
  add_mode: "winners_only", add_level: 110.25, add_qty: 5, adds_done: 0, max_adds: 1, opened: null, app_action: null, app_stop: null, app_target: null,
  rules_saved: false, notes: [], action: "STOP", action_ko: "손절", detail: "" };

beforeEach(() => {
  resetApiCache();
  try { localStorage.removeItem("ml.view"); } catch { /* */ }
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const u = String(url);
    if (u.includes("/habits/plans")) return new Response(JSON.stringify({ plans: [PLAN, { ...PLAN, symbol: "AMD", name: "AMD", action: "HOLD", action_ko: "보유 유지", pnl_pct: 3 }], rules: {}, rules_saved: false, connected: true, at: "" }));
    if (u.includes("/dashboard")) return new Response(JSON.stringify({
      scan: null, regime: { primary: "Risk Off", readings: [] }, major_risks: [], portfolio: { holdings: 2, cash: 0 }, provider_health: [], performance: null,
      recommendation_changes: [], watchlist_alerts: [], upcoming_catalysts: [{ event_id: "e1", title: "(MOCK) JPM earnings", event_date: "2026-10-06", days_until: 2, importance: 0.9 }],
      top_opportunities: [row({}), row({ id: 2, ticker: "OLD", current_status: "EXPIRED", actionable_now: false }), row({ id: 3, ticker: "WAITER", action: "WAIT" })],
    }));
    return new Response("{}");
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("answers the three questions in one look, from the backend's numbers", async () => {
  render(<MemoryRouter><Home /></MemoryRouter>);
  await screen.findByTestId("brief-home");
  expect(document.body.textContent).toContain("시장 조심");  // Risk Off
  const buy = await screen.findByTestId("brief-buy-AAA");
  expect(buy.textContent).toContain("사도 됨");
  expect(buy.textContent).toContain("$101.00 이하");
  expect(buy.textContent).toContain("지금 사도 되는 가격");  // 100 ≤ 101
  expect(buy.textContent).toContain("$94.00");
  expect(buy.textContent).toContain("$115.00");
  expect(screen.queryByTestId("brief-buy-OLD")).toBeNull();  // an expired BUY is never offered
  expect(screen.queryByTestId("brief-buy-WAITER")).toBeNull();
  const stop = await screen.findByTestId("brief-todo-NVDA");
  expect(stop.textContent).toContain("전부 팔기");
  expect(stop.textContent).toContain("$97.65");
  expect(screen.getByTestId("brief-todo-AMD").textContent).toContain("그대로 두기");
  expect(screen.getByTestId("brief-events").textContent).toContain("JPM 실적 발표");
});

it("자세히 switches to the full home and is remembered", async () => {
  render(<MemoryRouter><Home /></MemoryRouter>);
  await screen.findByTestId("brief-home");
  fireEvent.click(screen.getByRole("button", { name: "자세히 보기" }));
  expect(screen.queryByTestId("brief-home")).toBeNull();
  expect(localStorage.getItem("ml.view")).toBe("full");
});
