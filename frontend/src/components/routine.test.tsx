// @vitest-environment jsdom
/** 이번 달 할 일: one headline, numbered sell/buy steps with shares and amounts, the next change as D-n, the risk line
 * always shown, and a held book shown as preparing, never as a list. */
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { RoutineCard, type Routine } from "./RoutineCard";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const serve = (b: Routine) => vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(b))));

const ACT: Routine = {
  state: "ACT", headline: "이번 달 할 일: 1종목 팔기, 2종목 사기", when: "2026-10-01 미국장 시가에 맞춰 하세요", strategy: "대형주 모멘텀",
  next_rebalance: "2026-10-30", next_execute: "2026-11-02", days_to_next: 24, execute_day: "2026-10-01", rebalance_day: "2026-09-30",
  sell: [{ ticker: "OLD", shares: 50, price: 20 }],
  buy: [{ ticker: "AAA", rank: 1, price: 100, shares: 53, amount: 5300, target_amount: 5300 }, { ticker: "BIG", rank: 2, price: 9000, shares: 0, amount: 0, target_amount: 5300 }],
  keep: ["CCC"], outside: ["KO"], account: { total: 106000, cash: 98500, known: true, slot: 5300, unpriced: [] },
  note: "종목당 계좌의 5% 기준입니다. 주문은 증권사 앱에서 직접 하세요.", risk: "고위험 · 백테스트 기준 미달(최대 낙폭 −61%) · 앱은 주문하지 않습니다",
};

it("says what to do this month in steps, with shares and about how much, and keeps the risk line", async () => {
  serve(ACT);
  render(<MemoryRouter><RoutineCard /></MemoryRouter>);
  await screen.findByRole("heading", { name: "이번 달 할 일: 1종목 팔기, 2종목 사기" });
  const t = screen.getByTestId("routine").textContent!;
  expect(t).toContain("팔기 — 이번 달 목록에서 빠진 종목");
  expect(t).toContain("50주 전부");
  expect(t).toContain("53주");
  expect(t).toContain("약 $5,300.00");
  expect(t).toContain("1주 가격이 5%보다 큼");
  expect(t).toContain("D-24");
  expect(t).toContain("그대로 두기 CCC");
  expect(t).toContain("앱은 주문하지 않습니다");
});

it("a book still preparing shows the reason and no list", async () => {
  serve({ state: "PREPARING", headline: "이번 달 목록을 준비하는 중입니다", reasons: ["2026-09-30 순위를 낼 자료가 아직 부족합니다"], days_to_next: 24, next_rebalance: "2026-10-30", risk: "고위험" });
  render(<MemoryRouter><RoutineCard /></MemoryRouter>);
  await screen.findByText("2026-09-30 순위를 낼 자료가 아직 부족합니다");
  expect(screen.getByTestId("routine").querySelector(".routine-steps")).toBeNull();
});

it("an unpriced holding shows the warning and no amounts instead of 5 % of a partial total", async () => {
  serve({ ...ACT, account: { total: null, cash: 1000, known: true, slot: null, unpriced: ["BIGHOLD"] },
          buy: [{ ticker: "AAA", rank: 1, price: 100, shares: null, amount: null, target_amount: 0 }],
          warning: "보유 종목 BIGHOLD의 가격을 받지 못해 계좌 총액을 계산할 수 없습니다 — 살 수량은 가격을 받은 뒤 표시합니다." });
  render(<MemoryRouter><RoutineCard /></MemoryRouter>);
  expect((await screen.findByTestId("routine-warning")).textContent).toContain("BIGHOLD");
  const t = screen.getByTestId("routine").textContent!;
  expect(t).toContain("수량 계산 대기");
  expect(t).not.toContain("약 $");
});

// owner-supplied check 2026-10-06: $95,000 held outside the strategy, $5,000 cash, 20 names to buy — the card must not
// offer $100,000 of buys; it shows what the cash buys, what is cut, how much is missing, and where my rules disagree
const SHORT: Routine = {
  ...ACT, state: "ACT", headline: "이번 달 할 일: 1종목 사기", sell: [],
  buy: [{ ticker: "AAA", rank: 1, price: 100, shares: 49, amount: 4900, target_amount: 5000, status: "OK", reason: null },
        { ticker: "BBB", rank: 2, price: 100, shares: 0, amount: 0, target_amount: 5000, status: "CASH", reason: "현금 부족 — 약 $4,907 모자람" },
        { ticker: "CCC", rank: 3, price: 100, shares: 0, amount: 0, target_amount: 5000, status: "SECTOR", reason: "Technology 업종이 계좌의 30%를 넘게 됨(지금 28%)" }],
  account: { total: 100000, cash: 5000, known: true, slot: 5000, unpriced: [] },
  funding: { cash: 5000, sell_proceeds: 0, available: 5000, planned: 4907.35, needed_full: 98147, shortfall: 93147, cost_rate: 0.0015, outside_value: 95000, outside_share: 0.95 },
  deviations: [{ ticker: "BBB", kind: "CASH", detail: "BBB: 현금 부족" }, { ticker: "CCC", kind: "SECTOR", detail: "CCC: 업종 한도" }],
  conflicts: [{ ticker: "KEEP", strategy: "유지 — 이번 달 순위 7위", rule: "손절 — 손절가 $90 아래", basis: "전략에는 손절·익절이 없습니다. 어느 쪽을 따를지 직접 정하세요." }],
  today: [{ ticker: "KEEP", kind: "CONFLICT", text: "KEEP 판단이 엇갈림 — 전략: 유지 / 내 규칙: 손절", source: "전략·내 규칙" },
          { ticker: null, kind: "BUY", text: "1종목 사기 — 약 $4,907 (자금 부족으로 2건 줄임)", source: "전략" }],
};

it("buys are sized inside the cash: the cut lines say why, the shortfall and the outside share are shown", async () => {
  serve(SHORT);
  render(<MemoryRouter><RoutineCard /></MemoryRouter>);
  const f = await screen.findByTestId("routine-funding");
  expect(f.textContent).toContain("쓸 수 있는 돈 $5,000.00");
  expect(f.textContent).toContain("계획한 매수 $4,907.35");
  expect(screen.getByTestId("routine-shortfall").textContent).toContain("$93,147.00 모자람");
  expect(screen.getByTestId("routine-shortfall").textContent).toContain("전략 밖 보유가 계좌의 95%");
  expect(screen.getByTestId("routine-buy-AAA").textContent).toContain("49주");
  expect(screen.getByTestId("routine-buy-BBB").textContent).toContain("현금 부족");
  expect(screen.getByTestId("routine-buy-CCC").textContent).toContain("업종 한도로 사지 않음");
  expect(screen.getByTestId("routine-buy-CCC").textContent).toContain("30%를 넘게 됨");
  expect(screen.getByTestId("routine-deviations").textContent).toContain("2건");
});

it("the strategy and my own rule are shown side by side when they disagree, first in today's list", async () => {
  serve(SHORT);
  render(<MemoryRouter><RoutineCard /></MemoryRouter>);
  const c = await screen.findByTestId("routine-conflicts");
  expect(c.textContent).toContain("전략 유지 — 이번 달 순위 7위");
  expect(c.textContent).toContain("내 규칙 손절 — 손절가 $90 아래");
  expect(c.textContent).toContain("직접 정하세요");
  const items = screen.getByTestId("routine-today").querySelectorAll("li");
  expect(items[0]!.textContent).toContain("KEEP 판단이 엇갈림");
});

it("a blocked month says so with the reasons, never as nothing to do", async () => {
  serve({ ...SHORT, state: "BLOCKED", headline: "이번 달 3종목을 사야 하지만 지금 계좌로는 살 수 없습니다",
          buy: SHORT.buy!.map((b) => ({ ...b, shares: 0, amount: 0, status: "CASH" as const, reason: "현금 부족 — 약 $5,007 모자람" })), today: [], conflicts: [] });
  render(<MemoryRouter><RoutineCard /></MemoryRouter>);
  await screen.findByRole("heading", { name: "이번 달 3종목을 사야 하지만 지금 계좌로는 살 수 없습니다" });
  expect(screen.getByTestId("routine-shortfall")).toBeTruthy();
  expect(screen.getByTestId("routine").textContent).not.toContain("할 일 없음");
});

it("a failed load shows the error and a retry, not '불러오는 중' forever", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "boom" }), { status: 500 })));
  render(<MemoryRouter><RoutineCard /></MemoryRouter>);
  await screen.findByRole("heading", { name: "이번 달 목록을 불러오지 못했습니다" }, { timeout: 5000 });
  expect(screen.getByText("다시 시도")).toBeTruthy();
}, 10000);
