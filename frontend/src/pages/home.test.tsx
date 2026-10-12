// @vitest-environment jsdom
/** Home after the owner spec 2026-10-12 "미니멀 개편": three things only — my total and return, this month's to-do in one
 * sentence with the list a tap away, the method's record in one line with the worst case — and at most two buttons. */
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import type { Evidence } from "../components/HomeTop";
import type { Routine } from "../components/RoutineCard";
import Home, { todoSentence, todoWhy } from "./Home";
import RoutineList from "./RoutineList";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const serve = (routes: Record<string, unknown>) => vi.stubGlobal("fetch", vi.fn(async (u: string) => {
  const hit = Object.entries(routes).sort((a, b) => b[0].length - a[0].length).find(([k]) => String(u).includes(k));
  return new Response(JSON.stringify(hit ? hit[1] : {}));
}));

const ACT: Routine = { state: "ACT", headline: "이번 달 할 일: 3종목 사기", days_to_next: 19, next_rebalance: "2026-10-30",
  sell: [{ ticker: "OLD", shares: 7, price: 10 }],
  buy: [{ ticker: "AAA", rank: 1, price: 100, shares: 50, amount: 5000, target_amount: 5000, status: "OK" },
    { ticker: "BBB", rank: 2, price: 50, shares: 100, amount: 5000, target_amount: 5000, status: "OK" },
    { ticker: "CCC", rank: 3, price: 50, shares: 0, amount: 0, target_amount: 5000, status: "SECTOR", reason: "업종 한도" }],
  account: { total: 100000, cash: 40000, known: true, slot: 5000, unpriced: [] },
  funding: { cash: 40000, sell_proceeds: 70, available: 40070, planned: 10015, needed_full: null, shortfall: null, cost_rate: 0.0015, outside_value: 0, outside_share: null },
  conflicts: [{ ticker: "AAA", strategy: "유지", rule: "손절", basis: "" }], keep: ["ZZZ"], risk: "고위험" };
const curve = (cagr: number, mdd: number) => ({ cagr, total_return: cagr * 10, volatility: 0.2, max_drawdown: mdd, by_year: {}, worst_12m: null, drawdown: null });
const EV = { available: true, routine: { ...curve(0.196, -0.606), trades: 1 }, spy: curve(0.152, -0.338) } as Evidence;
const LIVE = { at: "", live: false, rows: [], notes: [], totals: { count: 2, valued: 2, value: 60000, purchase: 50000, pnl: 10000, pnl_rate: 0.2, daily: -500, daily_rate: -0.008 } };

it("the to-do is one sentence that counts only the buys this account will make", () => {
  expect(todoSentence(ACT)).toBe("1종목 팔고 2종목 사세요");
  expect(todoSentence({ ...ACT, sell: [] })).toBe("2종목 사세요");
  expect(todoSentence({ ...ACT, state: "BLOCKED", headline: "이번 달 20종목을 사야 하지만 지금 계좌로는 살 수 없습니다" })).toBe("지금 계좌로는 살 수 없습니다");
  expect(todoSentence({ ...ACT, state: "PREPARING", headline: "이번 달 목록을 준비하는 중입니다" })).toBe("목록을 준비하는 중입니다");
});

it("outside a plain to-do home says why in one line, never a dead end", () => {
  expect(todoWhy(ACT)).toBeNull();
  expect(todoWhy({ ...ACT, state: "PREPARING" })).toContain("저절로");
  expect(todoWhy({ ...ACT, state: "BLOCKED", funding: { ...ACT.funding!, shortfall: 1234 } })).toContain("$1,234.00이 모자랍니다");
  expect(todoWhy({ ...ACT, state: "DONE", when: "다음 교체: 2026-10-30" })).toBe("다음 교체: 2026-10-30");
});

it("my own rule alerts and the data being prepared are shown on home", async () => {
  serve({ "/routine/evidence": EV, "/routine": { ...ACT, today: [{ ticker: "AAA", kind: "CONFLICT", text: "AAA 판단이 엇갈림", source: "" }] },
          "/portfolio/live": LIVE, "/portfolio": { cash: 40000, cash_entered: true } });
  render(<MemoryRouter><Home /></MemoryRouter>);
  expect((await screen.findByTestId("todo-alerts")).textContent).toContain("내 규칙 알림 1건");
  cleanup();
  serve({ "/routine/evidence": EV, "/routine": { state: "PREPARING", headline: "이번 달 목록을 준비하는 중입니다", reasons: ["자료 부족"] },
          "/portfolio/live": LIVE, "/portfolio": { cash: 40000, cash_entered: true } });
  render(<MemoryRouter><Home /></MemoryRouter>);
  expect((await screen.findByTestId("todo-why")).textContent).toContain("저절로");
  expect(screen.getByTestId("routine-list-open").textContent).toBe("준비 상황 보기");
});

it("holdings without a price are said to be left out of the total", async () => {
  serve({ "/routine/evidence": EV, "/routine": { state: "PREPARING", headline: "" },
          "/portfolio/live": { ...LIVE, totals: { ...LIVE.totals, valued: 0, value: 0, pnl: null, pnl_rate: null } }, "/portfolio": { cash: 3000, cash_entered: true } });
  render(<MemoryRouter><Home /></MemoryRouter>);
  expect((await screen.findByTestId("status-unpriced")).textContent).toContain("보유 2종목");
  expect(screen.getByTestId("status-total").textContent).toBe("$3,000.00");
});

it("home shows my two numbers, the to-do with the days left, and the record in one line — nothing more", async () => {
  serve({ "/routine/evidence": EV, "/routine": ACT, "/portfolio/live": LIVE, "/portfolio": { cash: 40000, cash_entered: true } });
  render(<MemoryRouter><Home /></MemoryRouter>);
  expect((await screen.findByTestId("status-total")).textContent).toBe("$100,000.00");
  expect(screen.getByTestId("status-pnl").textContent).toBe("+20.0%");
  expect(screen.getByTestId("status-pnl").className).toContain("pos");
  expect((await screen.findByTestId("todo-what")).textContent).toBe("1종목 팔고 2종목 사세요");
  expect(screen.getByTestId("todo-next").textContent).toBe("다음 교체까지 19일");
  expect(screen.getByTestId("routine-summary").textContent).toContain("왜 이 종목들?");
  const line = (await screen.findByText(/과거 연평균/)).closest("[data-testid=ev-line]")!.textContent!;
  expect(line).toBe("과거 연평균 +19.6% · S&P 500 +15.2% · 최악 -61%");
  expect(screen.getByTestId("ev-more").getAttribute("href")).toBe("/evidence");
  expect(document.querySelectorAll("section").length).toBe(3);
  expect(document.querySelectorAll("button, a.btn").length).toBeLessThanOrEqual(2);
  // moved away: today's P/L, the goal, rule clashes, candidates, the market
  expect(document.body.textContent).not.toMatch(/오늘|목표|엇갈|후보|시장 분위기/);
});

it("with no account home says so and links to 내 계좌 (no assumed amount)", async () => {
  serve({ "/routine/evidence": EV, "/routine": { ...ACT, account: { total: null, cash: 100000, known: false, slot: null, unpriced: [] } },
          "/portfolio/live": { ...LIVE, totals: { count: 0, value: null, pnl: null } }, "/portfolio": { cash: 100000, cash_entered: false } });
  render(<MemoryRouter><Home /></MemoryRouter>);
  expect((await screen.findByTestId("status-setup")).getAttribute("href")).toBe("/portfolio");
  expect(screen.queryByTestId("status-total")).toBeNull();
});

it("the list screen shows the buys with shares, the ones it cannot make with why, what to keep and my rule alerts", async () => {
  serve({ "/routine": { ...ACT, today: [{ ticker: "AAA", kind: "CONFLICT", text: "AAA 판단이 엇갈림", source: "" }] } });
  render(<MemoryRouter><RoutineList /></MemoryRouter>);
  expect((await screen.findByTestId("routine-sells")).textContent).toContain("OLD7주 전부");
  expect(screen.getByTestId("routine-buys").textContent).toContain("살 종목 2 · 못 사는 종목 1");
  expect(screen.getByTestId("routine-buy-AAA").textContent).toBe("AAA50주");
  expect(screen.getByTestId("routine-cut-CCC").textContent).toBe("CCC업종 한도로 사지 않음");
  expect(screen.getByTestId("routine-keep").textContent).toContain("ZZZ");
  expect(screen.getByTestId("routine-alerts").textContent).toContain("AAA 판단이 엇갈림");
});

it("a list being prepared says why instead of one bare sentence", async () => {
  serve({ "/routine": { state: "PREPARING", headline: "이번 달 목록을 준비하는 중입니다", reasons: ["일봉 자료가 부족합니다"] } });
  render(<MemoryRouter><RoutineList /></MemoryRouter>);
  expect((await screen.findByTestId("routine-preparing")).textContent).toContain("일봉 자료가 부족합니다");
});
