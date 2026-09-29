// @vitest-environment jsdom
/** 원화 기준 수익 (owner 2026-09-29: "주가로 +8%, 환율로 +3%"): the split, the per-holding rows, and an unknown row with its reason. */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { KrwReturn, type KrwView } from "./KrwReturn";

afterEach(cleanup);
const K: KrwView = { fx_now: 1339, fx_source: "토스증권 매매기준율", fx_at: "2026-09-29T09:30:00+09:00", loading: false, note: "참고",
  rows: [{ ticker: "NVDA", known: true, reason: null, buy_fx: 1300, cost_krw: 1300000, value_krw: 1446120, stock_krw: 104000, fx_krw: 42120, total_krw: 146120, stock_pct: 0.08, fx_pct: 0.03, total_pct: 0.1124 },
         { ticker: "MSFT", known: false, reason: "매수일 기록이 없어 매수 당시 환율을 알 수 없음(직접 입력한 줄)", buy_fx: null, cost_krw: null, value_krw: null, stock_krw: null, fx_krw: null, total_krw: null, stock_pct: null, fx_pct: null, total_pct: null }],
  totals: { count: 1, cost_krw: 1300000, stock_krw: 104000, fx_krw: 42120, total_krw: 146120, stock_pct: 0.08, fx_pct: 0.0324, total_pct: 0.1124 } };

it("shows the won P&L split into the stock and the dollar", () => {
  render(<KrwReturn k={K} />);
  const t = screen.getByTestId("krw-return").textContent!;
  expect(t).toContain("주가로 +₩104,000");
  expect(t).toContain("환율로 +₩42,120");
  expect(t).toContain("+₩146,120");
  expect(t).toContain("1,300.0원");
  expect(t).toContain("나눌 수 없음 — 매수일 기록이 없어");
  expect(t).toContain("토스증권 매매기준율");
});

it("says why nothing can be split, and a loss is signed", () => {
  render(<KrwReturn k={{ ...K, rows: [K.rows[1]!], totals: { count: 0, cost_krw: null, stock_krw: null, fx_krw: null, total_krw: null, stock_pct: null, fx_pct: null, total_pct: null } }} />);
  expect(screen.getByTestId("krw-return").textContent).toContain("매수일이 기록된 보유가 없어");
  cleanup();
  render(<KrwReturn k={{ ...K, totals: { ...K.totals, fx_krw: -42120 } }} />);
  expect(screen.getByTestId("krw-return").textContent).toContain("환율로 −₩42,120");
});
