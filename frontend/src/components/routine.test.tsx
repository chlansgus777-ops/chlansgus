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
