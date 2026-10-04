// @vitest-environment jsdom
/** GLANCE MODE (owner 2026-10-04): four answers only — the market state, the focus symbol's action, the price to buy
 * at or below, and whether anything material changed — read from the existing endpoints; rows without data hidden,
 * never N/A; a partial failure is a small dot, not an error box. All data here is made up. */
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { resetApiCache } from "../components/useApi";
import Glance from "./Glance";

let fail = new Set<string>();
let liveBody: unknown;
let detailBody: unknown;
const calls: string[] = [];

beforeEach(() => {
  resetApiCache();
  fail = new Set();
  calls.length = 0;
  localStorage.setItem("ml.focus", "ANET");
  liveBody = { live: { action: "WAIT", price: 201.82, max_buy: 196, stop: 188, target1: 230, current_status: "CURRENT", actionable_now: null, session: "REGULAR" } };
  detailBody = {
    recommendation: { ticker: "ANET", action: "WAIT", price: 201.82, max_buy: 196, stop: 188, target: 230, current_status: "CURRENT", actionable_now: null },
    position_plan: { available: true, amount: 1500 },
    brief: { changed: [{ kind: "CALC", text: "점수 70→78", label: "지난 분석 대비" }, { kind: "CALC", text: "손절 상향", label: "지난 분석 대비" }, { kind: "FACT", text: "최근 실적", label: "실적" }] },
    price_history: Array.from({ length: 30 }, (_, i) => ({ day: `2026-09-${String(i + 1).padStart(2, "0")}`, close: 190 + i * 0.4 })),
  };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const u = String(url).split("/api")[1] ?? "";
    calls.push(u.split("?")[0]!);
    for (const f of fail) if (u.startsWith(f)) return new Response(JSON.stringify({ detail: "provider down" }), { status: 500 });  // not retried
    if (u.startsWith("/system")) return new Response(JSON.stringify({ mode: "LIVE", market: { session: "REGULAR" } }));
    if (u.startsWith("/dashboard")) return new Response(JSON.stringify({ regime: { primary: "Neutral", readings: [{ regime: "Neutral", score: 1, confidence: 0.72, evidence: [] }] }, top_opportunities: [], upcoming_catalysts: [] }));
    if (u.startsWith("/macro")) return new Response(JSON.stringify({ available: true, series: { VIX: { latest: { value: 17.4, quality: "FRESH" }, pct_change_20d: null }, US10Y: { latest: { value: 4.12, quality: "FRESH" }, pct_change_20d: null } } }));
    if (u.startsWith("/stocks/ANET/live")) return new Response(JSON.stringify(liveBody));
    if (u.startsWith("/stocks/ANET")) return new Response(JSON.stringify(detailBody));
    return new Response("{}");
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); });

const show = () => render(<MemoryRouter><Glance /></MemoryRouter>);

it("answers the four questions with the engine's own values, and nothing else", async () => {
  show();
  await screen.findByTestId("glance-action");
  expect(screen.getByTestId("glance-market").textContent).toContain("NEUTRAL");
  expect(screen.getByTestId("glance-market").textContent).toContain("Confidence 72");
  expect(screen.getByTestId("glance-metrics").textContent).toContain("17.4");
  expect(screen.getByTestId("glance-metrics").textContent).toContain("4.12%");
  expect(screen.getByTestId("glance-action").textContent).toBe("WAIT");
  const rows = screen.getByTestId("glance-rows").textContent ?? "";
  expect(rows).toContain("BUY BELOW$196.00");
  expect(rows).toContain("MAX SIZE$1,500");
  expect(rows).toContain("INVALID$188.00");
  await waitFor(() => expect(screen.getByTestId("glance-changes").textContent).toContain("2 THINGS CHANGED"));  // the material ones only
  expect(document.body.textContent).toContain("OPEN");
  expect(document.body.textContent).not.toMatch(/N\/A|Unknown/);
});

it("a missing value hides its row; no analysis is a small DATA UNAVAILABLE", async () => {
  liveBody = { live: { action: "HOLD", price: 50, max_buy: null, stop: 45, current_status: "CURRENT" } };
  detailBody = { recommendation: { ticker: "ANET", action: "HOLD", price: 50, max_buy: null, stop: 45, target: null }, position_plan: { available: false } };
  show();
  await screen.findByTestId("glance-action");
  const rows = screen.getByTestId("glance-rows").textContent ?? "";
  expect(rows).not.toContain("BUY BELOW");
  expect(rows).not.toContain("MAX SIZE");
  expect(rows).toContain("INVALID");
  cleanup(); resetApiCache();
  liveBody = { live: null };
  detailBody = {};
  show();
  await waitFor(() => expect(screen.getByTestId("glance-focus").textContent).toContain("DATA UNAVAILABLE"));
});

it("a partial failure keeps what it has and shows only a small delayed dot", async () => {
  fail = new Set(["/macro", "/system"]);
  show();
  await screen.findByTestId("glance-action");
  expect(screen.queryByTestId("glance-metrics")).toBeNull();
  await waitFor(() => expect(document.querySelector(".gl-dot.warn")).not.toBeNull());
  expect(screen.getByTestId("glance-market").textContent).toContain("NEUTRAL");
});

it("loads quietly, then follows the focus symbol the main window opens", async () => {
  show();
  expect(document.body.textContent).toContain("Loading market state");
  await screen.findByTestId("glance-action");
  localStorage.setItem("ml.focus", "NVDA");
  act(() => { window.dispatchEvent(new StorageEvent("storage", { key: "ml.focus" })); });
  await waitFor(() => expect(screen.getByTestId("glance-focus").textContent).toContain("NVDA"));
  expect(calls.filter((c) => c === "/dashboard").length).toBe(1);  // no duplicate polling on a focus change
});
