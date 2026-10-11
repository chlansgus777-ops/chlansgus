// @vitest-environment jsdom
/** The top of home (owner spec 2026-10-11): my status with its basis and goal, this month's to-do as one summary card
 * that unfolds the list, and the past record next to its yardsticks with the worst case on the same card. */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { EvidenceCard, MyStatus, RoutineSummary, routineAction, type Evidence } from "./HomeTop";
import type { Routine } from "./RoutineCard";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const serve = (routes: Record<string, unknown>) => vi.stubGlobal("fetch", vi.fn(async (u: string) => {
  const hit = Object.entries(routes).find(([k]) => String(u).includes(k));
  return new Response(JSON.stringify(hit ? hit[1] : {}));
}));

const curve = (cagr: number, mdd: number, extra: object = {}) => ({ cagr, total_return: cagr * 10, volatility: 0.2, max_drawdown: mdd, by_year: { "2020": cagr },
  worst_12m: { return: mdd, start: "2021-02-09", end: "2022-02-08" }, drawdown: { max_drawdown: mdd, peak: "2021-02-09", bottom: "2022-07-14", recovered: "2024-11-08" }, ...extra });
const EV: Evidence = { available: true, period: { start: "2017-01-06", end: "2026-09-25" },
  routine: { ...curve(0.196, -0.606), volatility: 0.43, trades: 471 }, spy: { ...curve(0.152, -0.338), volatility: 0.18 },
  deposit: { ...curve(0.025, 0), series: "FRED DTB3" },
  random: { seeds: 50, cagr: { p10: 0.07, median: 0.115, p90: 0.16, min: 0.045, max: 0.194 }, max_drawdown_median: -0.38, routine_beats_share: 1, median_run: curve(0.117, -0.41) } };

const ACT: Routine = { state: "ACT", headline: "이번 달 할 일: 2종목 사기", days_to_next: 19, next_rebalance: "2026-10-30",
  buy: [{ ticker: "AAA", rank: 1, price: 100, shares: 50, amount: 5000, target_amount: 5000, status: "OK" }, { ticker: "BBB", rank: 2, price: 50, shares: 100, amount: 5000, target_amount: 5000, status: "OK" }],
  account: { total: 100000, cash: 100000, known: true, slot: 5000, unpriced: [] }, funding: { cash: 100000, sell_proceeds: 0, available: 100000, planned: 10015, needed_full: null, shortfall: null, cost_rate: 0.0015, outside_value: 0, outside_share: null },
  risk: "고위험" };

it("the to-do says what, how much and by when in two lines, and the full list unfolds on a tap", async () => {
  expect(routineAction(ACT)).toEqual({ what: "2종목 사기", how: "종목마다 약 $5,000.00씩 · 합계 약 $10,015.00" });
  serve({ "/routine": ACT });
  render(<MemoryRouter><RoutineSummary /></MemoryRouter>);
  expect((await screen.findByTestId("todo-what")).textContent).toBe("2종목 사기");
  expect(screen.getByTestId("routine-summary").textContent).toContain("다음 교체까지 19일");
  expect(screen.queryByTestId("routine-unfold")).toBeNull();
  fireEvent.click(screen.getByTestId("routine-toggle"));
  expect(await screen.findByTestId("routine-unfold")).toBeTruthy();
});

it("my status shows the total with what it is made of, the gain against cost with its sign, and the goal progress", async () => {
  serve({ "/portfolio/live": { at: "", live: false, live_at: null, synced_at: null, fx: null, rows: [], notes: [],
            totals: { count: 2, valued: 2, daily_count: 2, value: 60000, purchase: 50000, pnl: 10000, pnl_rate: 0.2, daily: -500, daily_rate: -0.008 } },
          "/goal": { amount: 200000 } });
  render(<MemoryRouter><MyStatus cash={40000} cashEntered /></MemoryRouter>);
  await screen.findByText("$100,000.00");
  const t = screen.getByTestId("my-status").textContent!;
  expect(t).toContain("주식 $60,000.00 + 현금 $40,000.00");
  expect(screen.getByTestId("status-pnl").className).toContain("pos");
  expect(t).toContain("+$10,000.00 (+20.0%)");
  expect(t).toContain("산 가격 대비");
  expect(t).toContain("어제 종가 대비");
  expect((await screen.findByTestId("goal-pct")).textContent).toBe("50%");
});

it("with no account entered it asks for one instead of showing an assumed amount", async () => {
  serve({ "/portfolio/live": { at: "", live: false, rows: [], notes: [], totals: { count: 0, value: null, pnl: null } }, "/goal": { amount: null } });
  render(<MemoryRouter><MyStatus cash={100000} cashEntered={false} /></MemoryRouter>);
  expect(await screen.findByTestId("status-setup")).toBeTruthy();
  expect(screen.getByTestId("status-total").textContent).toBe("—");
});

it("the evidence card compares four ways, shows the worst case next to them and promises nothing", async () => {
  serve({ "/routine/evidence": EV });
  render(<MemoryRouter><EvidenceCard /></MemoryRouter>);
  await screen.findByTestId("ev-routine");
  for (const id of ["ev-routine", "ev-spy", "ev-random", "ev-deposit"]) expect(screen.getByTestId(id)).toBeTruthy();
  expect(screen.getByTestId("ev-routine").textContent).toContain("+19.6%");
  expect(screen.getByTestId("ev-routine").textContent).toContain("-61%");
  expect(screen.getByTestId("ev-random-note").textContent).toContain("50번 모두");
  const risk = screen.getByTestId("ev-risk").textContent!;
  expect(risk).toContain("2021년 2월부터 2022년 7월까지");
  expect(risk).toContain("3년 9개월");
  const t = screen.getByTestId("evidence").textContent!;
  expect(t).toContain("보장은 없습니다");
  expect(t).not.toMatch(/수익이 (높아|납니다)|보장합니다|확실/);
});

it("a clash with my own stop or take-profit rule is said on the folded card", async () => {
  serve({ "/routine": { ...ACT, conflicts: [{ ticker: "AAA", strategy: "유지", rule: "손절", basis: "" }] } });
  render(<MemoryRouter><RoutineSummary /></MemoryRouter>);
  expect((await screen.findByTestId("todo-clash")).textContent).toContain("1종목은 내 규칙과 엇갈림");
});

it("the to-do folds to one line for this month, keeps a rule clash visible, and opens again", async () => {
  localStorage.clear();
  serve({ "/routine": { ...ACT, rebalance_day: "2026-09-30", conflicts: [{ ticker: "AAA", strategy: "유지", rule: "손절", basis: "" }] } });
  render(<MemoryRouter><RoutineSummary /></MemoryRouter>);
  fireEvent.click(await screen.findByTestId("routine-fold"));
  const bar = await screen.findByTestId("routine-folded");
  expect(bar.textContent).toContain("다음 목록 10월 30일 (19일 남음)");
  expect(bar.textContent).toContain("1종목은 내 규칙과 엇갈림");
  expect(screen.queryByTestId("routine-summary")).toBeNull();
  expect(localStorage.getItem("ml.todoFolded")).toBe("2026-09-30");
  fireEvent.click(screen.getByTestId("routine-unfold-card"));
  expect(await screen.findByTestId("routine-summary")).toBeTruthy();
});

it("a new month's list opens by itself even when the last one was folded", async () => {
  localStorage.setItem("ml.todoFolded", "2026-08-29");
  serve({ "/routine": { ...ACT, rebalance_day: "2026-09-30" } });
  render(<MemoryRouter><RoutineSummary /></MemoryRouter>);
  expect(await screen.findByTestId("routine-summary")).toBeTruthy();
});
