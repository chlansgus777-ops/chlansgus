// @vitest-environment jsdom
/** 과거 검증 on 성과: the measured run's numbers with their source, or "not yet" — never sample numbers on screen. */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { BacktestView, elementName, type BacktestSummary } from "./Backtest";

afterEach(cleanup);
// test data for the component only (shape of config/backtest_results.json)
const B: BacktestSummary = {
  available: true, disclaimer: "과거 시뮬레이션이며 미래 수익을 보장하지 않습니다.",
  source: { results_sha256: "abcdef1234567890", data_sha256: "a5a49cbb16c6e429", commit: "3d88b498d41", workflow_run: "99", finished_at: null },
  window: ["2017-01-05", "2026-09-25"], weeks: 507, weeks_used_h60: 495, data: { weeks: 560, verdict: "충분" },
  elements: { "score": { verdict: "약함", mean_ic: 0.021, holm_p: 0.2, weeks: 495, coverage_mean: 1, mean_ic_h20: 0.015 },
              "component.return_signals": { verdict: "유효", mean_ic: 0.031, holm_p: 0.01, weeks: 495, coverage_mean: 0.9, mean_ic_h20: 0.02 },
              "signal.ear": { verdict: "측정 불가(값 없음)", mean_ic: null, holm_p: null, weeks: 0, coverage_mean: 0, mean_ic_h20: null } },
  quintiles: { assumption: "비용 1× · 상장폐지 손실 30% · 60거래일", rows: { "1": { mean_ret: 0.01, excess_spy: -0.012, hit_rate_vs_spy: 0.4 }, "5": { mean_ret: 0.03, excess_spy: 0.008, hit_rate_vs_spy: 0.55 } },
               top_minus_bottom: { mean: 0.02, ci90_block12: [0.005, 0.034], weeks: 495 } },
  strategy: { assumption: "비용 1× · 상장폐지 손실 30%", cagr: 0.112, spy_cagr: 0.129, excess_cagr: -0.017, sharpe: 0.71, max_drawdown: -0.28, trades: 812, equity_weekly: [["2017-01-06", 100000], ["2026-09-25", 280000]] },
  application: { s12: { adopted: false, why: "검증 기간 조건 불만족 → 가중치 그대로" }, s13: { adopted: true, why: "학습 기간 판정 유효 → 후보 10점 · 채택" },
                 operating_weights: { return_signals: 0, fundamental: 25 }, final_weights: { return_signals: 10, fundamental: 22.7273 }, changed: true },
  leak_checks: { truncated_all_equal: true, shuffle_mean_ic: 0.0 },
};

it("shows the run's verdicts, strategy, quintiles and decisions with where they came from", () => {
  render(<BacktestView b={B} />);
  const t = screen.getByTestId("backtest").textContent!;
  expect(t).toContain("과거 검증 (2017~2026");
  expect(t).toContain("과거 시뮬레이션이며 미래 수익을 보장하지 않습니다.");
  expect(t).toContain("+11.2%");
  expect(t).toContain("SPY +12.9% · 초과 -1.7%");
  expect(t).toContain("수익 신호");
  expect(t).toContain("실적 발표 후 추세 (참고)");
  expect(t).toContain("수익 신호 반영(13절): 채택");
  expect(t).toContain("가중치 조정(12절): 채택 안 함");
  expect(t).toContain("수익 신호 0→10");
  expect(t).toContain("결과 abcdef123456");
  expect(t).toContain("실행 #99");
  expect(t).toContain("누수 검사 통과");
});

it("says the results are not ready instead of showing anything else", () => {
  render(<BacktestView b={{ available: false, reason: "2016년 이후 자료로 과거 검증을 계산하는 중입니다." }} />);
  expect(screen.getByTestId("backtest").textContent).toContain("계산하는 중");
  expect(screen.getByTestId("backtest").textContent).not.toContain("%");
});

it("names every element in Korean", () => {
  expect(elementName("score")).toBe("총점");
  expect(elementName("component.fundamental")).toBe("펀더멘털");
  expect(elementName("signal.rs_rank")).toBe("상대강도 순위 (참고)");
});

// independent review 2026-10-06 F05: the card says what the run left out and whether its thresholds are today's;
// §17's short-term strategies are shown with the rule's verdict, never as a recommendation
it("says what the run left out, flags thresholds that differ from today's, and shows the short-term verdicts", () => {
  const swing: BacktestSummary["swing"] = {
    prereg: "§17", assumption: "다음 날 시가 체결 · 비용 0.1%/회", spy: { train_cagr: 0.095, holdout_cagr: 0.191, holdout_sharpe: 1.2 },
    periods: { train: ["2017-01-06", "2023-10-20"], holdout: ["2024-01-19", "2026-09-25"] },
    strategies: { B1: { name: "급락 반등(추세 위)", train_cagr: -0.009, holdout_cagr: 0.093, holdout_sharpe: 0.45, holdout_max_drawdown: -0.285, holdout_trades: 1296, holdout_win_rate: 0.61, adopt: false } },
  };
  render(<BacktestView b={{ ...B, thresholds: { buy: 50, buy_small: 40 }, swing }} />);
  expect(screen.getByTestId("backtest-scope").textContent).toContain("실적·추정치와 촉매 점수는 과거 자료가 없어 빼고");
  expect(screen.getByTestId("backtest-scope").textContent).toContain("지금 기준은 아직 검증 전");
  const sw = screen.getByTestId("backtest-swing").textContent!;
  expect(sw).toContain("급락 반등(추세 위)");
  expect(sw).toContain("+9.3%");
  expect(sw).toContain("기준 미달");
  expect(sw).toContain("SPY 그냥 보유");
  expect(screen.getByTestId("backtest").textContent).not.toContain("앱 규칙 그대로");
});
