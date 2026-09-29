// @vitest-environment jsdom
/** 오늘 아침 브리핑 (owner 2026-09-29: "아침 브리핑 (한국시간 오전 7시)", in the app only). */
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it } from "vitest";
import { BriefingView, signedUsd, type Briefing } from "./Briefing";

afterEach(cleanup);
const B: Briefing = {
  date_kst: "2026-09-26", ready: true, built_at: "2026-09-25T23:00:00+00:00", session: "2026-09-25", session_expected: "2026-09-25", notes: [],
  market: [{ name: "S&P 500", ticker: "SPY", close: 671.2, change: 0.0081 }, { name: "나스닥 100", ticker: "QQQ", close: null, change: null }],
  account: { holdings: 3, priced: 2, pnl: -1234.4, change: -0.0123, movers: [{ ticker: "NVDA", change: -0.031, pnl: -980, close: 180.1 }] },
  watch: [{ ticker: "AMD", kind: "STOP", price: 101, level: 100, distance: 0.01, text: "AMD 손절 기준 근처 (현재 101.00 · 손절 100.00)" }],
  alerts: [{ id: 3, at: "2026-09-25T20:00:00+00:00", ticker: "TSLA", kind: "BIG_MOVE", level: "warning", text: "TSLA 급등 +7%" }],
  events: [{ date: "2026-09-28", title: "NVDA 실적 발표", tickers: ["NVDA"], type: "EARNINGS" }],
  candidates: [{ ticker: "AVGO", action: "BUY", action_ko: "매수", score: 83.4, max_buy: 350, as_of: "2026-09-25T20:05:00+00:00" }],
  scan_as_of: "2026-09-25T20:05:00+00:00", headline: "S&P 500 +0.8% · 내 계좌 −$1,234 (−1.2%)",
};

it("shows the session, the market, my account and what to check — each with its data or why not", () => {
  render(<MemoryRouter><BriefingView x={B} /></MemoryRouter>);
  const t = screen.getByTestId("morning-briefing").textContent!;
  expect(t).toContain("9월 26일 (토) · 미국 9/25 장 기준");
  expect(t).toContain("+0.81%");
  expect(t).toContain("QQQ 종가 없음");  // a part without data says so
  expect(t).toContain("−$1,234");
  expect(t).toContain("2/3종목 가격 확인");
  expect(t).toContain("AMD 손절 기준 근처");
  expect(t).toContain("NVDA 실적 발표 · NVDA");
  expect(t).toContain("AVGO · 점수 83");
  expect(t).toContain("TSLA 급등");
  expect(screen.getByRole("link", { name: /NVDA/ }).getAttribute("href")).toBe("/stocks/NVDA");
});

it("is not shown before 07:00 KST, and a late night's bars are named", () => {
  const { container } = render(<MemoryRouter><BriefingView x={{ ...B, ready: false }} /></MemoryRouter>);
  expect(container.textContent).toBe("");
  cleanup();
  render(<MemoryRouter><BriefingView x={{ ...B, session: "2026-09-24", notes: ["9/25 종가가 아직 저장되지 않아 9/24 종가 기준입니다"] }} /></MemoryRouter>);
  const t = screen.getByTestId("morning-briefing").textContent!;
  expect(t).toContain("미국 9/24 장 기준");
  expect(t).toContain("9/25 종가가 아직 저장되지 않아");
});

it("signs dollars the way the rest of the app does", () => {
  expect(signedUsd(1234.4)).toBe("+$1,234");
  expect(signedUsd(-0.4)).toBe("$0");
  expect(signedUsd(null)).toBe("N/A");
});
