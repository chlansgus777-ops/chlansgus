// @vitest-environment jsdom
/** 내 매매 진단 (owner 2026-10-04: why the account loses and what to fix, mechanical stop/take-profit/add rules per holding,
 * inside MarketLens): the diagnosis, the rules form, the trade log with its detail, the virtual sample kept apart, and
 * the holdings' "지금 할 일" on the portfolio, the stock page and home. All data here is made up. */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { HoldingPlans, PlanCard, type Plan } from "../components/HoldingPlans";
import { resetApiCache } from "../components/useApi";
import MyTrading, { type Report } from "./MyTrading";

const RULES = { stop_pct: -7, use_app_stop: false, take1_pct: 10, take1_fraction: 0.5, trail_pct: -8, add_mode: "winners_only", add_trigger_pct: 5, add_fraction: 0.5, max_adds: 1, saved_at: null };
const TRADE = {
  id: "ord0002~0", symbol: "NVDA", currency: "USD", sell_order_id: "ord0002", buy_order_id: "ord0001", row_of_order: 0, bought_at: "2026-09-01T23:31:00+09:00",
  sold_at: "2026-09-08T23:31:00+09:00", quantity: 10, entry: 100, exit: 101, holding_days: 7, gross_pnl: 10, net_pnl: 9.8, gross_ret: 1, net_ret: 0.98, buy_fees: 0.1,
  sell_fees: 0.1, mae: -4.2, mfe: 1, pattern: "CANDIDATE", pattern_reason: "매도 전 확인된 최저 -4.20% 후 순수익률 +0.98%로 청산",
  path: { status: "OK", reason: "", first_bar: "2026-09-01", last_bar: "2026-09-08", missing_days: [], interval: "1일(일봉)", notes: ["일봉 기준 관측치"], uncertain_low: null },
  stop: { status: "NO_STOP", reason: "사전 손절가 미설정", stop: null, target: null, pre_recorded: null, source: null },
  early: { status: "INSUFFICIENT", reason: "관찰 기간 부족", max_rise: null, max_fall: null, days_observed: 2 }, has_note: false,
};
const PLAN: Plan = {
  symbol: "NVDA", name: "NVIDIA", market: "US", currency: "USD", quantity: 10, avg_price: 105, price: 96, price_at: null, price_source: "토스 동기화 시점 가격", pnl_pct: -8.57,
  stop: 97.65, stop_source: "평단 -7%", take1: 115.5, take1_fraction: 0.5, take1_done: false, trail: null, high_since_open: 106, add_mode: "winners_only", add_level: 110.25,
  add_qty: 5, adds_done: 0, max_adds: 1, opened: "2026-09-15", app_action: "HOLD", app_stop: null, app_target: null, rules_saved: false, notes: [],
  action: "STOP", action_ko: "손절", detail: "현재가 96 ≤ 손절가 97.65 (평단 -7%) — 규칙상 전량 매도",
};
const REPORT = (sample = false): Report => ({
  source: sample ? "sample" : "toss", is_sample: sample, generated_at: "2026-09-25T15:00:00Z", period: { first: "2026-09-01T23:31:00+09:00", last: "2026-09-15T23:31:00+09:00" },
  meta: sample ? { label: "가상 샘플" } : { connected: true, fills: { count: 5, complete: true, since: "2025-09-25", synced_at: "2026-09-25T15:00:00Z" }, skipped: [] },
  rules: { dip_pct: -3, exit_min_pct: -0.5, exit_max_pct: 3, repeat_min: 2, early_days: 5, early_rise_pct: 5 }, trade_rules: RULES as Report["trade_rules"], rules_saved: false,
  summary: { sell_orders: 2, matched_rows: 2, realized: { USD: { net: -40.4, gross: -40, fees: 0.4 } }, profit_orders: 1, known_orders: 2, profit_ratio: 0.5,
    breakeven: { candidates: 1, partial: 0, unconfirmed: 0, evaluable: 2, ratio: 0.5, not_evaluable: 0, repeated: false },
    early_exit: { candidates: 0, evaluable: 0 }, stop_review: { candidates: 0, no_stop: 2 }, data_short: 0, count_unit: "매도 주문 1건 = 1회" },
  diagnosis: { closed: 2, headline: "매도 2건 · 승률 50% · 평균 이익 +1.0% / 평균 손실 -20.1% · 거래당 -9.56%", cause: "가장 큰 원인: 이익은 작게, 손실은 크게 (거래가 적어 참고만)",
    findings: [{ id: "payoff", title: "이익은 작게, 손실은 크게", evidence: "이긴 매도 1건 평균 +1.0%, 진 매도 1건 평균 -20.1%", fix: "손절 -7%를 기계적으로 지키고…", confidence: "표본 적음" }],
    stats: null, suggested: { rules: { ...RULES, stop_pct: -5 } as Report["trade_rules"], why: ["이긴 거래가 1건뿐이라 기본값을 제안합니다"] } },
  by_symbol: [{ symbol: "NVDA", currency: "USD", sell_orders: 1, evaluable: 1, candidates: 1, partial: 0, insufficient: 0, early: 0, stop_review: 0, net_pnl: 9.8, ratio: 1, repeated: false }],
  orders: [], trades: [TRADE as unknown as Report["trades"][number]], open_lots: [], warnings: [],
});

let calls: { method: string; url: string; body?: string }[] = [];
let connected = true;
beforeEach(() => {
  resetApiCache();
  calls = [];
  connected = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const u = String(url);
    calls.push({ method, url: u, body: init?.body as string | undefined });
    if (u.includes("/habits/trades/")) return new Response(JSON.stringify({ trade: TRADE, order: null, is_sample: false, calc: ["진입가 100 = 매수 주문 ord0001의 평균 체결가"],
      bars: [{ day: "2026-09-01", open: 100, high: 100.5, low: 99.5, close: 100 }, { day: "2026-09-03", open: 98, high: 100, low: 95.8, close: 97 }],
      raw: { buy: { order_id: "ord0001" }, sell: { order_id: "ord0002" } }, note: {}, user_stop: null, basis_note: null }));
    if (u.includes("/habits/plans")) return new Response(JSON.stringify({ plans: connected && !u.includes("MSFT") ? [PLAN, { ...PLAN, symbol: "AMD", action: "HOLD", action_ko: "보유 유지" }] : [], rules: RULES, rules_saved: false, connected, at: "" }));
    if (u.includes("/habits/rules")) return new Response(JSON.stringify({ trade: RULES, pattern: {} }));
    if (u.includes("/habits")) {
      if (u.includes("sample=1")) return new Response(JSON.stringify(REPORT(true)));
      return new Response(JSON.stringify(connected ? REPORT() : { ...REPORT(), meta: { connected: false }, trades: [], by_symbol: [] }));
    }
    return new Response("{}");
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const page = () => render(<MemoryRouter><MyTrading /></MemoryRouter>);

it("says why the account loses, with its numbers and the fix, from the real account", async () => {
  page();
  await screen.findByTestId("why-losing");
  expect(screen.getByTestId("real-banner").textContent).toContain("읽기 전용");
  expect(screen.getByTestId("why-losing").textContent).toContain("이익은 작게, 손실은 크게");
  expect(screen.getByTestId("finding-payoff").textContent).toContain("고칠 점");
  expect(screen.getByTestId("tile-breakeven").textContent).toContain("1회 / 2");
  expect(screen.queryByTestId("sample-banner")).toBeNull();
});

it("keeps the virtual sample apart with its own banner and no saving", async () => {
  page();
  await screen.findByTestId("why-losing");
  fireEvent.click(screen.getByRole("tab", { name: "가상 샘플" }));
  await screen.findByTestId("sample-banner");
  expect(calls.some((c) => c.url.includes("/habits?sample=1"))).toBe(true);
  expect((screen.getByRole("button", { name: "규칙 저장" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByTestId("plans")).toBeNull();  // the account's holdings are never shown on the sample
});

it("saves the rules the owner sets, starting from the suggestion", async () => {
  page();
  await screen.findByTestId("rules-suggested");
  fireEvent.click(screen.getByRole("button", { name: "제안값을 입력칸에 넣기" }));
  expect((screen.getByLabelText("손절 (평단 대비 %)") as HTMLInputElement).value).toBe("-5");
  fireEvent.click(screen.getByRole("button", { name: "규칙 저장" }));
  await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url.endsWith("/api/habits/rules"))).toBe(true));
  const body = JSON.parse(calls.find((c) => c.method === "PUT")!.body!);
  expect(body.trade.stop_pct).toBe(-5);
  expect(body.pattern).toBeNull();
});

it("opens a trade: price path, calculation, raw records and the memo", async () => {
  page();
  await screen.findByTestId("trade-log");
  fireEvent.click(screen.getByTestId("trade-ord0002~0"));
  const d = await screen.findByTestId("trade-detail");
  expect(within(d).getByTestId("path-chart")).toBeTruthy();
  expect(d.textContent).toContain("진입가 100");
  fireEvent.change(within(d).getByLabelText("매도 이유"), { target: { value: "본전이라서" } });
  fireEvent.click(within(d).getByRole("button", { name: "메모 저장" }));
  await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url.includes("/api/habits/notes/ord0002~0"))).toBe(true));
});

it("without a connection: asks to connect and offers the sample, never invents trades", async () => {
  connected = false;
  page();
  await screen.findByText("토스증권을 연결하면 내 매매를 진단합니다");
  expect(screen.queryByTestId("trade-log")).toBeNull();
});

it("holdings: the rule's action now, on the portfolio and only the actions at home", async () => {
  render(<MemoryRouter><HoldingPlans /></MemoryRouter>);
  await screen.findByTestId("plans");
  expect(screen.getByTestId("plan-action-NVDA").textContent).toBe("손절");
  expect(screen.getByTestId("plan-NVDA").textContent).toContain("$97.65");
  cleanup(); resetApiCache();
  render(<MemoryRouter><HoldingPlans actionsOnly /></MemoryRouter>);
  await screen.findByTestId("plan-actions");
  expect(screen.queryByTestId("plan-AMD")).toBeNull();  // 보유 유지 is not an action
  cleanup(); resetApiCache();
  connected = false;
  const { container } = render(<MemoryRouter><HoldingPlans /></MemoryRouter>);
  await waitFor(() => expect(calls.filter((c) => c.url.includes("/habits/plans")).length).toBeGreaterThan(2));
  expect(container.textContent).toBe("");
});

it("a name not held: the plan for buying it now under my rules, compared with the app's stop", async () => {
  render(<MemoryRouter><HoldingPlans ticker="MSFT" preview={{ price: 400, appStop: 380, appTarget: 460 }} /></MemoryRouter>);
  const card = await screen.findByTestId("preview-MSFT");
  expect(card.textContent).toContain("$372.00");  // 400 − 7 %
  expect(card.textContent).toContain("$440.00");  // + 10 %
  expect(card.textContent).toContain("$420.00");  // add at + 5 %
  expect(card.textContent).toContain("1.43");     // (440 − 400) ÷ (400 − 372)
  expect(card.textContent).toContain("앱 분석 손절가 $380.00가 내 규칙 손절보다 위");
  // the other half after the first take-profit has its own exit (owner 2026-10-05: "나머지 50%는 언제 팔아야")
  expect(screen.getByTestId("preview-rest-MSFT").textContent).toContain("나머지 50%");
  expect(screen.getByTestId("preview-rest-MSFT").textContent).toContain("$404.80부터");  // 440 − 8 %
});

it("a holding says when the rest after the first take-profit is sold: the starting line, then the live trailing line", () => {
  const base = { ...PLAN, action: "HOLD" as const, trail_pct: -8, rest_fraction: 0.5, rest_start: 106.26 };
  render(<MemoryRouter><PlanCard p={base} /></MemoryRouter>);
  const rest = screen.getByTestId("rest-NVDA");
  expect(rest.textContent).toContain("나머지 50%");
  expect(rest.textContent).toContain("$106.26");
  expect(rest.textContent).toContain("1차 익절가에 닿으면 시작");
  cleanup();
  render(<MemoryRouter><PlanCard p={{ ...base, take1_done: true, high_since_open: 130, trail: 119.6 }} /></MemoryRouter>);
  expect(screen.getByTestId("rest-NVDA").textContent).toContain("$119.60");
  expect(screen.getByTestId("rest-NVDA").textContent).toContain("이 가격 아래로 내려오면 나머지 전부 매도");
});

it("an add level at the first take-profit is a contradiction: said, and the rules cannot be saved", async () => {
  page();
  await screen.findByTestId("rules-card");
  fireEvent.change(screen.getByLabelText("1차 익절 (평단 대비 %)"), { target: { value: "5" } });
  expect(screen.getByTestId("rules-clash").textContent).toContain("+2.5%");
  expect((screen.getByRole("button", { name: "규칙 저장" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("평단보다 이만큼 올랐을 때"), { target: { value: "2.5" } });
  expect(screen.queryByTestId("rules-clash")).toBeNull();
  expect((screen.getByRole("button", { name: "규칙 저장" }) as HTMLButtonElement).disabled).toBe(false);
});
