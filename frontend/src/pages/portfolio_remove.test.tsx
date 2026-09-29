// @vitest-environment jsdom
/** Owner 2026-09-29 "포트폴리오에 왜 넣는 기능만 있고 빼는 기능은 없어?": every holding has a visible 삭제 (asked twice in
 * place), entered lines have 수정, and amounts are read the way people type them. */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetApiCache } from "../components/useApi";
import { parseAmount } from "../format";
import Portfolio from "./Portfolio";

const H = (ticker: string, source: "manual" | "ledger", quantity: number) => ({
  ticker, quantity, cost_basis: 100, price: 110, price_day: "2026-09-24", market_value: 110 * quantity, unrealized_pnl: 10 * quantity, unrealized_pct: 0.1,
  weight: 0.1, sector: "Technology", source,
});
const PF = { cash: 20000, cash_entered: true, nav: 30000, invested_value: 10000, unrealized_pnl: 100, hhi: 0.1, beta: 1, sector_weights: {}, theme_weights: {},
  correlations: [], missing_prices: [], notes: [], currency: "USD", note: "", valuation_day: "2026-09-24", valuation_status: "COMPLETE",
  holdings: [H("MSFT", "manual", 2.5), H("NVDA", "ledger", 10)] };

let calls: { method: string; url: string }[] = [];
beforeEach(() => {
  resetApiCache();
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ method: init?.method ?? "GET", url: String(url) });
    if (String(url).includes("/transactions")) return new Response(JSON.stringify({ securities: [], realized_pnl: 0, dividends: 0, note: "" }));
    if ((init?.method ?? "GET") === "DELETE") return new Response(JSON.stringify({ ticker: "MSFT", manual_removed: true, trades_removed: 0 }));
    return new Response(JSON.stringify(PF));
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("removing and editing holdings", () => {
  it("deletes only after the in-place confirmation, through the one-step endpoint", async () => {
    render(<MemoryRouter><Portfolio /></MemoryRouter>);
    fireEvent.click(await screen.findByTestId("remove-MSFT"));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);  // first click only asks
    expect(document.body.textContent).toContain("MSFT 2.5주를 뺄까요?");  // fractional shares are not rounded
    fireEvent.click(screen.getByTestId("remove-MSFT-yes"));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/api/portfolio/holdings/MSFT"))).toBe(true));
  });
  it("cancel leaves everything as it was", async () => {
    render(<MemoryRouter><Portfolio /></MemoryRouter>);
    fireEvent.click(await screen.findByTestId("remove-NVDA"));
    expect(document.body.textContent).toContain("매도 기록");  // the confirmation for traded stocks points to the sale
    fireEvent.click(screen.getByText("취소"));
    expect(screen.getByTestId("remove-NVDA")).toBeTruthy();
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  });
  it("수정 puts the entered line back into the form and says it replaces it", async () => {
    render(<MemoryRouter><Portfolio /></MemoryRouter>);
    fireEvent.click(await screen.findByText("수정"));
    expect((screen.getByLabelText("보유 종목 코드") as HTMLInputElement).value).toBe("MSFT");
    expect((screen.getByLabelText("보유 수량") as HTMLInputElement).value).toBe("2.5");
    expect(screen.getByTestId("holding-edit-hint").textContent).toContain("바꿉니다");
    expect(screen.getByText("수정 저장")).toBeTruthy();
  });
  it("a traded stock typed into the entry form is told to use the trade records", async () => {
    render(<MemoryRouter><Portfolio /></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText("보유 종목 코드"), { target: { value: "nvda" } });
    expect(document.body.textContent).toContain("거래 기록으로 계산하는 종목이라");
  });
});

describe("amounts as people type them", () => {
  it("reads commas and a dollar sign, rejects anything ambiguous", () => {
    expect(parseAmount("25,000")).toBe(25000);
    expect(parseAmount("$1,250.50")).toBe(1250.5);
    expect(parseAmount(" 0.5 ")).toBe(0.5);
    for (const bad of ["1,2", "abc", "-3", "1e5", ""]) expect(Number.isNaN(parseAmount(bad))).toBe(true);
  });
});
