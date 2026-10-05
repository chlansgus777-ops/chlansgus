// @vitest-environment jsdom
/** Glance in the desktop app never turns into the full app (owner 2026-10-05: with MarketLens closed, a click in Glance
 * opened the whole app inside the small Glance window — "모바일 모드로 버그처럼"). */
import { afterEach, expect, it } from "vitest";
import { splitAlert } from "../components/LiveZone";
import { openAnalyze } from "./desktop";

afterEach(() => { delete (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__; window.location.hash = ""; });

it("a failed 'open in MarketLens' leaves the Glance window as it is in the desktop app", async () => {
  (window as unknown as { __TAURI_INTERNALS__: unknown }).__TAURI_INTERNALS__ = { invoke: async () => { throw new Error("main window missing"); } };
  window.location.hash = "#/glance";
  await openAnalyze("/stocks/NVDA");
  expect(window.location.hash).toBe("#/glance");
});

it("in a browser the same call opens the page", async () => {
  await openAnalyze("/stocks/NVDA");
  expect(window.location.hash).toBe("#/stocks/NVDA");
});

it("an alert's text splits into a headline and its detail line", () => {
  expect(splitAlert("AMZN 매수 구간 진입 — $251.60 (최대 매수가 $252.12 이하, 손익비 2.56)")).toEqual(["AMZN 매수 구간 진입", "$251.60 (최대 매수가 $252.12 이하, 손익비 2.56)"]);
  expect(splitAlert("오늘의 브리핑")).toEqual(["오늘의 브리핑", ""]);
});
