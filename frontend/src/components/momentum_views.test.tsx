// @vitest-environment jsdom
/** 대형주 모멘텀 (owner's choice 2026-10-06): the monthly list always carries its failed-backtest status and its risks,
 * says the app does not order, and never reads as a probability; the forward record is labelled as paper. */
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { MomentumBoard, MomentumToday, type MomBook } from "./MomentumViews";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const BOOK: MomBook = {
  strategy: "MN", name: "대형주 모멘텀", version: "M-NF-1.0", entry: ["…"], exit: ["…"], state: "READY", session: "2026-10-05",
  next_rebalance: "2026-10-30", next_execute: "2026-11-02", rebalance_day: "2026-09-30", execute_day: "2026-10-01",
  holdings: [{ ticker: "APP", sector: "Technology", rank: 1, momentum: 2.1, since: "2026-09-30", in_top20: true },
             { ticker: "HOOD", sector: "Finance", rank: null, momentum: null, since: "2026-08-29", in_top20: false }],
  bought: ["APP"], sold: ["XYZ"], note: "앱은 주문하지 않습니다. 이 목록은 상승 확률이 아닙니다.",
  validation: { status: "OWNER", status_ko: "직접 선택 · 백테스트 기준 미달 · 전진 모의운영 중", checks: { sharpe_beats_spy_total_return: false }, spy_cagr: 0.152,
    risks: ["백테스트(2017~2026)에서 최대 낙폭 −61%, 변동성 SPY의 2.4배였습니다."],
    backtest: { cagr: 0.195, mdd: -0.61, vol: 0.43, sharpe: 0.63, matched: 0.147, stress_cagr: 0.187, halves: [0.084, 0.576], trades: 470 } },
  forward: { started: null, note: "전진 모의운영 — 실제 주문·실거래 성과가 아닙니다.", first_rebalance: "2026-10-30" },
};

function serve(book: MomBook) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ready: true, refreshing: false, error: null, book }))));
}

it("the list comes with its status, its risk, its backtest as a simulation and the no-order note", async () => {
  serve(BOOK);
  render(<MemoryRouter><MomentumBoard /></MemoryRouter>);
  await screen.findByTestId("mom-table");
  expect(screen.getByTestId("mom-status").textContent).toContain("백테스트 기준 미달");
  expect(screen.getByTestId("mom-risk").textContent).toContain("최대 낙폭 −61%");
  expect(screen.getByTestId("mom-backtest").textContent).toContain("실거래 아님");
  expect(screen.getByTestId("mom-changes").textContent).toContain("새로 담음 APP");
  expect(screen.getByTestId("mom-table").textContent).toContain("21~40");  // a holding kept between rank 21 and 40
  const t = document.body.textContent!;
  expect(t).toContain("주문하지 않습니다");
  expect(t).toContain("2026-10-30 월말 순위부터 기록");
  expect(t).not.toMatch(/확률\s*\d|매수 신호입니다|수익 보장/);
});

it("home shows the tickers and the next change; a name without history is held, not guessed", async () => {
  serve(BOOK);
  render(<MemoryRouter><MomentumToday /></MemoryRouter>);
  await screen.findByText("APP");
  expect(document.body.textContent).toContain("다음 교체 2026-10-30");
  cleanup();
  serve({ ...BOOK, state: "HELD", holdings: [], reasons: ["일봉 253거래일 이상이 필요합니다"] });
  render(<MemoryRouter><MomentumToday /></MemoryRouter>);
  await screen.findByText("아직 순위를 낼 수 없습니다.");
});
