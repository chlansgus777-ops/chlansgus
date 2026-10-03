// @vitest-environment jsdom
/** An empty portfolio says so: never "분산 정도 좋음" or a green ▲ $0.00 (quality pass 2026-09-28). */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import Portfolio from "./Portfolio";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const EMPTY = { cash: 100000, nav: 100000, invested_value: 0, unrealized_pnl: 0, hhi: 0, beta: null, holdings: [], sector_weights: {}, theme_weights: {},
  correlations: [], missing_prices: [], notes: [], currency: "USD", note: "", valuation_day: null, valuation_status: "EMPTY" };

it("shows no P&L arrow and no diversification verdict without holdings", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const path = String(url).split("?")[0];
    const body = path === "/api/portfolio" ? EMPTY
      : path === "/api/transactions" ? { securities: [], realized_pnl: 0, dividends: 0, note: "" }
      : path === "/api/broker/toss" ? { enabled: false, connected: false, holdings: [], fills: [] }
      : [];
    return new Response(JSON.stringify(body), { status: 200 });
  }));
  render(<MemoryRouter><Portfolio /></MemoryRouter>);
  await screen.findByText("해당 없음");
  await waitFor(() => expect(screen.getByText("전부 현금")).toBeTruthy());
  expect(document.body.textContent).not.toContain("▲");
  expect(screen.queryByText("좋음")).toBeNull();
  expect(screen.getByText("전부 현금")).toBeTruthy();
  expect(screen.queryByTestId("holdings")).toBeNull();
});
