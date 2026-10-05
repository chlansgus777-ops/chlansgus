// @vitest-environment jsdom
/** 업종 강세·약세 on the market screen (owner 2026-10-05): every sector ETF's move today, strongest first; a sector
 * without a price says "—", never a made-up number. */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { _resetQuotes, applyRows, type QuoteRow } from "../quotes";
import { SectorStrength } from "./SectorStrength";

beforeEach(() => { vi.stubGlobal("fetch", vi.fn(async () => new Response("{}"))); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); _resetQuotes(); });

const row = (ticker: string, change_pct: number | null): QuoteRow => ({ ticker, state: "LIVE", session: "REGULAR", subscribed: true, version: 1, price: 100, trade_time: new Date().toISOString(),
  received_time: null, source: "toss", feed: "poll", previous_close: 99, change_pct, error: null } as unknown as QuoteRow);

it("lists the sectors strongest first with today's move, and '—' where no price came", () => {
  render(<SectorStrength />);
  act(() => { applyRows([row("XLE", 0.0123), row("SOXX", -0.021), row("XLK", 0.004)]); });
  const items = [...screen.getByTestId("sector-strength").querySelectorAll("li")].map((li) => li.textContent);
  expect(items[0]).toContain("에너지XLE");
  expect(items[0]).toContain("+1.23%");
  expect(items[1]).toContain("기술XLK");
  expect(items[2]).toContain("반도체SOXX");
  expect(items[2]).toContain("−2.10%");
  expect(items.slice(3).every((t) => t?.endsWith("—"))).toBe(true);
  expect(screen.getByTestId("sector-strength").textContent).toContain("강세 2 · 약세 1");
});
