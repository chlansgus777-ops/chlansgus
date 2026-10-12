// @vitest-environment jsdom
/** Home after the owner spec 2026-10-12 "미니멀 개편": three things only — my total and return, this month's to-do in one
 * sentence with the list a tap away, the method's record in one line with the worst case — and at most two buttons. */
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import type { Evidence } from "../components/HomeTop";
import type { Routine } from "../components/RoutineCard";
import Home, { todoSentence } from "./Home";
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
  expect(todoSentence({ ...ACT, state: "WAIT", headline: "이번 달 할 일: 다음 교체를 기다리세요" })).toBe("다음 교체를 기다리세요");
});

it("home shows my two numbers, the to-do with the days left, and the record in one line — nothing more", async () => {
  serve({ "/routine/evidence": EV, "/routine": ACT, "/portfolio/live": LIVE });
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
          "/portfolio/live": { ...LIVE, totals: { count: 0, value: null, pnl: null } } });
  render(<MemoryRouter><Home /></MemoryRouter>);
  expect((await screen.findByTestId("status-setup")).getAttribute("href")).toBe("/portfolio");
  expect(screen.queryByTestId("status-total")).toBeNull();
});

it("the list screen shows what to sell and buy with the shares — no money, limits, clashes or kept names", async () => {
  serve({ "/routine": ACT });
  render(<MemoryRouter><RoutineList /></MemoryRouter>);
  expect((await screen.findByTestId("routine-sells")).textContent).toContain("OLD7주 전부");
  const buys = screen.getByTestId("routine-buys").textContent!;
  expect(buys).toContain("살 종목 2");
  expect(screen.getByTestId("routine-buy-AAA").textContent).toBe("AAA50주");
  expect(buys).not.toContain("CCC");
  expect(document.body.textContent).not.toMatch(/\$|쓸 수 있는 돈|엇갈|그대로 두기|ZZZ/);
});
