// @vitest-environment jsdom
/** App-wide quote store (real-time quotes 2026-09-28) — offline unit tests, not a live verification. */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { LivePrice } from "./components/LivePrice";
import { _resetQuotes, applyRows, effectiveState, handleEvent, stateLabel, type QuoteRow } from "./quotes";

const row = (o: Partial<QuoteRow>): QuoteRow => ({
  ticker: "AAPL", state: "LIVE", session: "REGULAR", subscribed: true, version: 1, price: 100, trade_time: "2026-09-30T14:00:00+00:00",
  received_time: "2026-09-30T14:00:00.100000+00:00", source: "finnhub", feed: "stream", previous_close: 99, change_pct: 100 / 99 - 1, error: null, ...o,
});

afterEach(() => { cleanup(); _resetQuotes(); });

describe("quote store", () => {
  it("never lets an older version overwrite a newer one", () => {
    render(<LivePrice ticker="AAPL" />);
    act(() => applyRows([row({ version: 5, price: 101 })]));
    act(() => applyRows([row({ version: 3, price: 90 })]));  // late event
    expect(screen.getByTestId("live-AAPL").textContent).toContain("$101.00");
  });

  it("shows the same quote in every place that renders the ticker", () => {
    render(<><LivePrice ticker="MSFT" /><LivePrice ticker="MSFT" size="sm" /></>);
    act(() => applyRows([row({ ticker: "MSFT", version: 2, price: 420.5 })]));
    const all = screen.getAllByTestId("live-MSFT");
    expect(all).toHaveLength(2);
    for (const el of all) expect(el.textContent).toContain("$420.50");
  });

  it("a same-price newer trade updates the trade time", () => {
    render(<LivePrice ticker="AAPL" showTime />);
    act(() => applyRows([row({ version: 1, trade_time: "2026-09-30T14:00:00+00:00" })]));
    const before = screen.getByTestId("live-AAPL").textContent;
    act(() => applyRows([row({ version: 2, trade_time: "2026-09-30T14:00:07+00:00" })]));
    const after = screen.getByTestId("live-AAPL").textContent;
    expect(after).not.toEqual(before);
    expect(after).toContain("$100.00");
  });

  it("keeps a stale or closed price visible with its state instead of blanking it", () => {
    render(<LivePrice ticker="NVDA" />);
    act(() => applyRows([row({ ticker: "NVDA", state: "EXTENDED_NO_TRADE", session: "PREMARKET", feed: "snapshot", price: 150 })]));
    const el = screen.getByTestId("live-NVDA");
    expect(el.textContent).toContain("$150.00");
    expect(el.textContent).toContain("장전 체결 미수신");
    expect(el.getAttribute("data-state")).toBe("EXTENDED_NO_TRADE");
  });

  it("a delayed snapshot is never labelled real-time, and a dropped link reads reconnecting", () => {
    const snap = row({ state: "DELAYED", feed: "snapshot" });
    expect(stateLabel(snap, "open")).toBe("지연 시세");
    expect(effectiveState(row({}), "retrying")).toBe("RECONNECTING");
    expect(stateLabel(row({}), "retrying")).toContain("재연결");
  });

  it("parses the SSE hello/quotes events", () => {
    render(<LivePrice ticker="TSLA" />);
    act(() => handleEvent(`event: quotes\ndata: ${JSON.stringify({ version: 9, rows: [row({ ticker: "TSLA", version: 9, price: 250.25 })] })}`));
    expect(screen.getByTestId("live-TSLA").textContent).toContain("$250.25");
  });

  it("a tick re-renders only the prices of that ticker", () => {
    let renders = 0;
    function Other() { renders += 1; return <LivePrice ticker="AMD" />; }
    render(<Other />);
    const base = renders;
    act(() => applyRows([row({ ticker: "AAPL", version: 3 })]));
    expect(renders).toBe(base);
  });
});
