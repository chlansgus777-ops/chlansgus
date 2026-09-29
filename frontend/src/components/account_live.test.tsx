// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { AccountLiveCard, type AccountLive } from "./AccountLive";

afterEach(cleanup);

const A: AccountLive = {
  at: "2026-09-29T14:30:00Z", live: true, live_at: "2026-09-29T14:29:59Z", synced_at: "2026-09-29T14:29:10Z",
  fx: { rate: 1380, source: "토스증권 매매기준율", at: null }, notes: [],
  rows: [{ ticker: "NVDA", quantity: 10, avg_price: 100, source: "toss", basis: "TOSS", price: 125.5, price_at: "2026-09-29T14:29:59Z", live: true,
    purchase: 1000, value: 1255, pnl: 255, pnl_rate: 0.255, daily: 62.05, daily_rate: 0.0521, value_krw: 1731900, pnl_krw: 351900, daily_krw: 85629 }],
  totals: { count: 1, valued: 1, daily_count: 1, value: 1255, purchase: 1000, pnl: 255, pnl_rate: 0.255, daily: 62.05, daily_rate: 0.0521, value_krw: 1731900, pnl_krw: 351900, daily_krw: 85629 },
};

describe("the account now (owner 2026-09-29: '토스랑 수익이 안 맞아')", () => {
  it("shows Toss's figures in dollars and in won at the current rate, with the live time in KST", () => {
    render(<AccountLiveCard a={A} />);
    expect(screen.getByTestId("acct-pnl").textContent).toContain("$255.00");
    expect(screen.getByTestId("acct-pnl").textContent).toContain("+₩351,900");
    expect(screen.getByTestId("acct-daily").textContent).toContain("+5.21%");
    expect(screen.getByText(/토스증권 계좌 · 미국 주식/)).toBeTruthy();
    expect(screen.getByText(/실시간 23:29:59/)).toBeTruthy();  // 14:29:59 UTC = 23:29:59 KST
    expect(screen.getByText(/토스 앱의 원화 손익과 같은 방식/)).toBeTruthy();
  });
  it("an unknown today's P&L is a dash with the reason, never a zero", () => {
    render(<AccountLiveCard a={{ ...A, totals: { ...A.totals, daily: null, daily_rate: null, daily_krw: null, daily_count: 0 }, notes: ["1종목은 지금 가격이 없어 오늘 손익에서 뺐습니다"] }} />);
    expect(screen.getByTestId("acct-daily").textContent).toContain("—");
    expect(screen.getByText(/오늘 손익에서 뺐습니다/)).toBeTruthy();
  });
});
