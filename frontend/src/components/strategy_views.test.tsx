// @vitest-environment jsdom
/** 전략 신호 screens (owner 2026-10-06): every signal says strategy + version, preliminary or confirmed, why, when it
 * would execute, how it exits, its price time and its verification; an unadopted strategy's signals are a record only;
 * a backtest is a simulation, never performance; nothing reads as a probability of success. */
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { StockStrategies, StrategyBoard, type StrategiesResp, type Validation } from "./StrategyViews";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const notAdopted: Validation = { status: "NOT_ADOPTED", status_ko: "미채택", checks: { at_least_100_trades: true, both_halves_positive: false },
  backtest: { cagr: -0.03, max_drawdown: -0.4, sharpe: -0.2, exposure: 0.8, trades: 900, win_rate: 0.6, expectancy: 0.0004, avg_sessions: 3.4, matched_spy_cagr: 0.1, stress_expectancy: -0.002 } };
const verifying: Validation = { status: "VERIFYING", status_ko: "검증 중", backtest: null };
const sig = (strategy: string, v: Validation, kind: "confirmed" | "preliminary" = "confirmed") => ({
  strategy, name: strategy === "A" ? "상승 추세 눌림목" : "돌파 추세 추종", version: `${strategy}-1.0`, ticker: `T${strategy}${kind[0]}`, kind,
  kind_ko: kind === "confirmed" ? "확정 신호" : "예비 신호", signal_day: "2026-09-24", close: 101.5,
  conditions: [{ rule: "RSI(2, Wilder) ≤ 10", ok: true, value: 4.2, ref: 10 }], execute_at: "2099-01-02 시가 (미국 동부)",
  exit: ["종가 > 5일 단순이동평균이면 다음 거래일 시가에 매도"], max_hold: 10, price_ts: "2026-09-24T20:00:00Z", bars_through: "2026-09-24", validation: v });
const RESP: StrategiesResp = { ready: true, refreshing: false, error: null, computed_at: "2026-09-25T00:00:00Z", strategies: null, scan: {
  session: "2026-09-24", computed_at: "2026-09-25T00:00:00Z", names: 3000, names_without_last_bar: 12, spy_up: true,
  confirmed: [sig("A", notAdopted), sig("C", verifying)], preliminary: [sig("A", notAdopted, "preliminary")], held: { A: {}, C: { "오늘 거래량 없음": 3 } },
  strategies: [{ id: "A", name: "상승 추세 눌림목", version: "A-1.0", entry: ["RSI(2, Wilder) ≤ 10"], exit: ["…"], max_hold: 10, ...notAdopted },
               { id: "C", name: "돌파 추세 추종", version: "C-1.0", entry: ["…"], exit: ["…"], max_hold: null, ...verifying }],
  preliminary_note: "예비 신호는 장중 가격으로 계산한 것이라 종가에 사라질 수 있습니다." } };

function serve(body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(String(url).includes("/forward") ? { started: null, session: "2026-09-24", note: "전진 모의운영 — 실거래 아님", by_strategy: {}, trades: [] } : body))));
}

it("shows each signal with its strategy, version, kind, reasons, execution, exit and verification — an unadopted one as a record only", async () => {
  serve(RESP);
  render(<MemoryRouter><StrategyBoard /></MemoryRouter>);
  await screen.findAllByTestId("strategy-signal");
  const t = document.body.textContent!;
  expect(t).toContain("C-1.0");
  expect(t).toContain("확정 신호");
  expect(t).toContain("예비 신호");
  expect(t).toContain("2099-01-02 시가");
  expect(t).toContain("종가 > 5일 단순이동평균이면 다음 거래일 시가에 매도");
  expect(t).toContain("미채택 전략의 신호 1개");
  expect(t).toContain("매수 신호로 보지 마세요");
  expect(t).toContain("과거 시뮬레이션(비용 0.15%/회 차감, 실거래 아님)");
  expect(t).toContain("검증 중");
  expect(t).toContain("보류: 오늘 거래량 없음 3종목");
  expect(t).not.toMatch(/확률|성공률/);
});

it("the stock card says why a strategy is held and ignores another name's answer", async () => {
  serve({ ticker: "AAA", session: "2026-09-24", note: "가격은 장 마감 종가 기준으로 판정합니다.", strategies: [
    { strategy: "A", name: "상승 추세 눌림목", version: "A-1.0", exit: [], max_hold: 10, validation: verifying, state: "HELD", reasons: ["일봉 120거래일 — 252거래일 필요(200일선과 1년 거래 이력)"] }] });
  render(<MemoryRouter><StockStrategies ticker="AAA" /></MemoryRouter>);
  expect((await screen.findByTestId("stock-strategies")).textContent).toContain("보류");
  expect(screen.getByTestId("stock-strategies").textContent).toContain("252거래일 필요");
  cleanup();
  serve({ ticker: "BBB", session: "2026-09-24", note: "", strategies: [] });
  render(<MemoryRouter><StockStrategies ticker="AAA" /></MemoryRouter>);
  await new Promise((r) => setTimeout(r, 30));
  expect(screen.queryByTestId("stock-strategies")).toBeNull();
});
