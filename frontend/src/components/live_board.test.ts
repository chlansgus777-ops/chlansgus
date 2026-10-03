/** The 1-second list (owner 2026-09-29: "시장 스캔 없이 1초마다"): a live re-judgement replaces the stored decision, score,
 * price and plan of its row; the order is the live score with data-insufficient rows last. */
import { expect, it } from "vitest";
import { applyLive, liveOrder, type LiveJudgement } from "./liveBoard";
import type { OppRow } from "../types";

const row = (id: number, ticker: string, action: string, score: number, rank: number): OppRow => ({
  id, rank, ticker, company: ticker, sector: "Tech", sector_model: "x", price: null, session: null, price_timestamp: null, price_source: null, price_quality: "MISSING",
  score, confidence: 0.5, action, deterministic_action: action, committee_status: "NOT_RUN", ideal_entry: null, max_buy: null, target: null, stop: null, downside: null, rr: null,
  catalyst: null, catalyst_date: null, risk: null, data_quality: "MISSING", mode: "LIVE", vetoes: ["DATA_INSUFFICIENT"], as_of: "2026-09-29T12:00:00Z",
  current_status: "EXPIRED", current_status_reason: "데이터 부족", sessions_since: 0, actionable_now: null, action_ko: "데이터 부족", valuation_price_basis: null, sector_known: true,
} as OppRow);
const j = (action: string, score: number): LiveJudgement => ({ at: "2026-09-29T13:00:01Z", quote_ts: "2026-09-29T13:00:00Z", price: 101.5, source: "toss", session: "PREMARKET",
  action, score, current_status: "CURRENT", actionable_now: true, data_quality: "FRESH", vetoes: [], max_buy: 103, ideal_entry: 99, stop: 94, target1: 115, target2: 122, rr: 2.3, downside: -0.07, buy_zone_low: 97, buy_zone_high: 103 });

it("the same computed price can expire on the next status read", () => {
  const live = j("BUY", 82);
  const first = applyLive(row(7, "NVDA", "BUY", 82, 1), live);
  const expired = applyLive(first, { ...live, current_status: "EXPIRED", actionable_now: false });
  expect(expired.current_status).toBe("EXPIRED");
  expect(expired.actionable_now).toBe(false);
  expect(expired.price).toBe(first.price);
});

it("a price-less row becomes the live decision with its price and plan", () => {
  const r = applyLive(row(7, "NVDA", "DATA INSUFFICIENT", 65, 11), j("BUY", 82));
  expect(r.action).toBe("BUY");
  expect(r.action_ko).toBe("매수");
  expect(r.price).toBe(101.5);
  expect(r.max_buy).toBe(103);
  expect(r.actionable_now).toBe(true);
  expect(r.vetoes).toEqual([]);
  expect(r.live_at).toBeTruthy();
  expect(applyLive(r, undefined)).toBe(r);  // no judgement: the row as it is
});

it("the list follows the live score, data-insufficient last, ranks renumbered", () => {
  const rows = liveOrder([row(1, "A", "WATCH", 60, 1), row(2, "B", "DATA INSUFFICIENT", 90, 2), row(3, "C", "BUY", 85, 3)]);
  expect(rows.map((r) => r.ticker)).toEqual(["C", "A", "B"]);
  expect(rows.map((r) => r.rank)).toEqual([1, 2, 3]);
});
