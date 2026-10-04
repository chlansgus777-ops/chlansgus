// @vitest-environment jsdom
/** GLANCE MODE (owner 2026-10-04, redesign v2): the focus symbol's price and call as the centre, the market as a quiet
 * band, the plan's three prices — all read from the existing endpoints; optional rows without data fold away, never
 * N/A; a partial failure is a small amber "갱신 지연", not an error box. All data here is made up. */
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { resetApiCache } from "../components/useApi";
import Glance from "./Glance";

let fail = new Set<string>();
let liveBody: unknown;
let detailBody: unknown;
let candidates: unknown[];
let acctBody: unknown;
const calls: string[] = [];

beforeEach(() => {
  resetApiCache();
  fail = new Set();
  calls.length = 0;
  candidates = [];
  acctBody = {};
  localStorage.setItem("ml.focus", "ANET");
  liveBody = { live: { action: "WAIT", price: 201.82, max_buy: 196, stop: 188, target1: 230, rr: 2.1, current_status: "CURRENT", actionable_now: null, session: "REGULAR" } };
  detailBody = {
    recommendation: { ticker: "ANET", company: "Arista Networks", action: "WAIT", price: 201.82, max_buy: 196, stop: 188, target: 230, current_status: "CURRENT", actionable_now: null },
    position_plan: { available: true, amount: 1500, shares: 7 },
    brief: { changed: [{ kind: "CALC", text: "점수 70→78", label: "지난 분석 대비" }, { kind: "CALC", text: "손절 상향", label: "지난 분석 대비" }, { kind: "FACT", text: "최근 실적", label: "실적" }] },
    price_history: Array.from({ length: 30 }, (_, i) => ({ day: `2026-09-${String(i + 1).padStart(2, "0")}`, close: 190 + i * 0.4 })),
  };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const u = String(url).split("/api")[1] ?? "";
    calls.push(u.split("?")[0]!);
    for (const f of fail) if (u.startsWith(f)) return new Response(JSON.stringify({ detail: "provider down" }), { status: 500 });  // not retried
    if (u.startsWith("/system")) return new Response(JSON.stringify({ mode: "LIVE", market: { session: "REGULAR" } }));
    if (u.startsWith("/dashboard")) return new Response(JSON.stringify({ regime: { primary: "Neutral", readings: [{ regime: "Neutral", score: 1, confidence: 0.72, evidence: [] }] }, top_opportunities: candidates, upcoming_catalysts: [] }));
    if (u.startsWith("/macro")) return new Response(JSON.stringify({ available: true, series: { VIX: { latest: { value: 17.4, quality: "FRESH" }, pct_change_20d: null }, US10Y: { latest: { value: 4.12, quality: "FRESH" }, pct_change_20d: null } } }));
    if (u.startsWith("/portfolio/live")) return new Response(JSON.stringify(acctBody));
    if (u.startsWith("/stocks/ANET/live")) return new Response(JSON.stringify(liveBody));
    if (u.startsWith("/stocks/ANET")) return new Response(JSON.stringify(detailBody));
    return new Response("{}");
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); });

const show = () => render(<MemoryRouter><Glance /></MemoryRouter>);

it("shows the focus symbol, its call and the plan's prices with the engine's own values, and nothing else", async () => {
  show();
  await screen.findByTestId("glance-action");
  expect(screen.getByTestId("glance-market").textContent).toContain("중립");
  expect(screen.getByTestId("glance-market").textContent).toContain("데이터 72%");  // the regime's data coverage, not a probability
  expect(screen.getByTestId("glance-metrics").textContent).toContain("17.4");
  expect(screen.getByTestId("glance-metrics").textContent).toContain("4.12%");
  expect(screen.getByTestId("glance-action").textContent).toBe("대기WAIT");  // the existing action, one-to-one in Korean
  expect(screen.getByTestId("glance-focus").textContent).toContain("Arista Networks");
  const rows = screen.getByTestId("glance-rows").textContent ?? "";
  expect(rows).toContain("매수 상한$196.00");
  expect(rows).toContain("목표$230.00");
  expect(rows).toContain("무효화$188.00");
  const meta = screen.getByTestId("glance-meta").textContent ?? "";
  expect(meta).toContain("권장 매수 $1,500 · 7주");
  expect(meta).toContain("손익비 2.1");  // the engine's rr, not recomputed here
  await waitFor(() => expect(screen.getByTestId("glance-changes").textContent).toContain("지난 분석 대비 2건 변화"));  // the material ones only
  expect(document.body.textContent).toContain("장중");
  expect(document.body.textContent).not.toMatch(/N\/A|Unknown/);
});

it("a missing value folds its column away; no analysis is one short line", async () => {
  liveBody = { live: { action: "HOLD", price: 50, max_buy: null, stop: 45, current_status: "CURRENT" } };
  detailBody = { recommendation: { ticker: "ANET", action: "HOLD", price: 50, max_buy: null, stop: 45, target: null }, position_plan: { available: false } };
  show();
  await screen.findByTestId("glance-action");
  const rows = screen.getByTestId("glance-rows").textContent ?? "";
  expect(rows).not.toContain("매수 상한");
  expect(rows).not.toContain("목표");
  expect(rows).toContain("무효화");
  expect(screen.queryByTestId("glance-meta")).toBeNull();  // no sizing, no rr: the line is gone, not "N/A"
  cleanup(); resetApiCache();
  liveBody = { live: null };
  detailBody = {};
  show();
  await waitFor(() => expect(screen.getByTestId("glance-focus").textContent).toContain("분석 데이터 없음"));
});

it("a partial failure keeps what it has and shows only a small delayed mark", async () => {
  fail = new Set(["/macro", "/system"]);
  show();
  await screen.findByTestId("glance-action");
  expect(screen.queryByTestId("glance-metrics")).toBeNull();
  await waitFor(() => expect(document.querySelector(".gl-dot.warn")).not.toBeNull());
  expect(document.body.textContent).toContain("갱신 지연");
  expect(screen.getByTestId("glance-market").textContent).toContain("중립");
});

it("loads quietly, then follows the focus symbol the main window opens", async () => {
  show();
  expect(document.body.textContent).toContain("시장 상태를 불러오는 중");
  await screen.findByTestId("glance-action");
  localStorage.setItem("ml.focus", "NVDA");
  act(() => { window.dispatchEvent(new StorageEvent("storage", { key: "ml.focus" })); });
  await waitFor(() => expect(screen.getByTestId("glance-focus").textContent).toContain("NVDA"));
  expect(calls.filter((c) => c === "/dashboard").length).toBe(1);  // no duplicate polling on a focus change
});

it("a few other candidates sit in one line, and picking one makes it the focus", async () => {
  candidates = ["ANET", "AMD", "AVGO", "MU", "TSM"].map((t, i) => ({ id: i, ticker: t, action: "BUY", current_status: "CURRENT", actionable_now: true, price: 10, data_quality: "FRESH", vetoes: [] }));
  show();
  await screen.findByTestId("glance-action");
  const others = await screen.findByTestId("glance-others");
  const names = [...others.querySelectorAll("button")].map((b) => b.textContent);
  expect(names.length).toBeLessThanOrEqual(3);
  expect(names).not.toContain("ANET");  // the focus itself is not repeated
  act(() => { (others.querySelector("button") as HTMLButtonElement).click(); });
  expect(localStorage.getItem("ml.focus")).toBe(names[0]);
});

it("shows the account's own total and today's return as percentages only, and the menu can hide it", async () => {
  acctBody = { at: "x", live: true, rows: [], notes: [], totals: { count: 2, valued: 2, daily_count: 2, value: 12345.67, purchase: 1, pnl: 456.78, pnl_rate: 0.0324, daily: -50.5, daily_rate: -0.0041 } };
  show();
  const row = await screen.findByTestId("glance-account");
  expect(row.textContent).toContain("총 +3.24%");
  expect(row.textContent).toContain("오늘 −0.41%");
  expect(row.textContent).not.toMatch(/12,345|456|\$/);  // no amounts on an always-visible widget
  act(() => { screen.getByRole("button", { name: "Glance 설정" }).click(); });
  act(() => { screen.getByRole("switch", { name: "내 계좌 수익률 표시" }).click(); });
  await waitFor(() => expect(screen.queryByTestId("glance-account")).toBeNull());
  expect(localStorage.getItem("ml.glance.account")).toBe("0");
});

it("an empty account (nothing held or no data) shows no account line", async () => {
  acctBody = { at: "x", live: true, rows: [], notes: [], totals: { count: 0, valued: 0, daily_count: 0, value: null, purchase: null, pnl: null, pnl_rate: null, daily: null, daily_rate: null } };
  show();
  await screen.findByTestId("glance-action");
  expect(screen.queryByTestId("glance-account")).toBeNull();
});
