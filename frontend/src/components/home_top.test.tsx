// @vitest-environment jsdom
/** 내 현황 at the top of 내 계좌 (moved off home by the owner spec 2026-10-12): the total with its basis, the gain, today
 * and the goal. Home itself is covered by pages/home.test.tsx. */
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { MyStatus } from "./HomeTop";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const serve = (routes: Record<string, unknown>) => vi.stubGlobal("fetch", vi.fn(async (u: string) => {
  const hit = Object.entries(routes).find(([k]) => String(u).includes(k));
  return new Response(JSON.stringify(hit ? hit[1] : {}));
}));

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
  expect((await screen.findByTestId("status-total")).textContent).toBe("—");
  expect(screen.getByTestId("my-status").textContent).toContain("가진 주식과 현금을 넣으면");
});
