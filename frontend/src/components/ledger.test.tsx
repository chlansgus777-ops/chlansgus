// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { nyToday } from "../pages/Portfolio";
import { EMPTY_FORM, Ledger, tradeBody } from "./Ledger";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("trade record form", () => {
  it("sends only the fields of the kind, and refuses what the server would refuse", () => {
    expect(tradeBody({ ...EMPTY_FORM, ticker: " nvda ", day: "2026-09-01", quantity: "10", price: "100.5", fees: "" }, "2026-09-25").body)
      .toEqual({ ticker: "NVDA", day: "2026-09-01", kind: "BUY", quantity: 10, price: 100.5, fees: 0 });
    expect(tradeBody({ ...EMPTY_FORM, ticker: "NVDA", day: "2026-09-01", kind: "DIVIDEND", amount: "3.2" }, "2026-09-25").body)
      .toEqual({ ticker: "NVDA", day: "2026-09-01", kind: "DIVIDEND", amount: 3.2, fees: 0 });
    expect(tradeBody({ ...EMPTY_FORM, ticker: "NVDA", day: "2026-09-01", kind: "SPLIT", split_from: "1", split_to: "10" }, "2026-09-25").body)
      .toMatchObject({ split_from: 1, split_to: 10 });
    expect(tradeBody({ ...EMPTY_FORM, ticker: "NVDA", day: "2026-09-26", quantity: "1", price: "1" }, "2026-09-25").error).toContain("미래");
    expect(tradeBody({ ...EMPTY_FORM, ticker: "NVDA", day: "2026-09-01", quantity: "0", price: "1" }, "2026-09-25").error).toContain("수량");
    expect(tradeBody({ ...EMPTY_FORM, ticker: "NVDA", day: "2026-09-01", quantity: "1", price: "-1" }, "2026-09-25").error).toContain("가격");
    expect(tradeBody({ ...EMPTY_FORM, ticker: "NVDA", day: "2026-09-01", quantity: "1", price: "1", fees: "-1" }, "2026-09-25").error).toContain("수수료");
    expect(tradeBody({ ...EMPTY_FORM, ticker: "", day: "2026-09-01" }, "2026-09-25").error).toContain("종목 코드");
    expect(tradeBody({ ...EMPTY_FORM, ticker: "NVDA", day: "2026-09-01", kind: "SPLIT", split_from: "0", split_to: "2" }, "2026-09-25").error).toContain("분할");
  });
  it("dates by the New York calendar", () => {
    expect(nyToday(new Date("2026-09-26T02:00:00Z"))).toBe("2026-09-25"); // 22:00 in New York
    expect(nyToday(new Date("2026-09-26T15:00:00Z"))).toBe("2026-09-26");
  });
  it("shows the server's refusal and the computed holding", async () => {
    const calls: string[] = [];
    const view = { realized_pnl: 0, dividends: 0, note: "", securities: [{ security: "sec:NVDA", ticker: "NVDA", last_ticker: "NVDA", error: null,
      position: { quantity: 10, avg_cost: 100, cost_basis: 1000, realized_pnl: 0, dividends: 0, fees: 0, splits_applied: [] },
      trades: [{ id: 1, ticker: "NVDA", day: "2026-09-01", kind: "BUY", quantity: 10, price: 100, fees: 0, amount: 0, split_from: 0, split_to: 0, note: "" }] }] };
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? "GET"} ${url} ${String((init?.headers as Record<string, string>)?.["X-MarketLens-Client"] ?? "")}`);
      if (init?.method === "POST") return new Response(JSON.stringify({ detail: "2026-09-02: 그날 보유 10주보다 많이 매도할 수 없음 (매도 11주)" }), { status: 400 });
      return new Response(JSON.stringify(view), { status: 200 });
    }));
    render(<Ledger today="2026-09-25" onChange={() => undefined} />);
    expect(await screen.findByText(/보유 10주 · 평단/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("거래 종류"), { target: { value: "SELL" } });
    fireEvent.change(screen.getByLabelText("체결일"), { target: { value: "2026-09-02" } });
    fireEvent.change(screen.getByLabelText("거래 종목 코드"), { target: { value: "NVDA" } });
    fireEvent.change(screen.getByLabelText("거래 수량"), { target: { value: "11" } });
    fireEvent.change(screen.getByLabelText("체결 가격"), { target: { value: "120" } });
    fireEvent.click(screen.getByRole("button", { name: "기록" }));
    await waitFor(() => expect(screen.getByText(/보유 10주보다 많이 매도할 수 없음/)).toBeTruthy());
    expect(calls.some((c) => c.startsWith("POST") && c.includes("/api/transactions") && c.endsWith("marketlens-ui"))).toBe(true);
  });
});
