// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { OppRow } from "../types";
import { OppTable, StalePriceNote, stalePriceCause } from "./OppTable";

afterEach(cleanup);

// the scan ran before the regular session: every price older than the 20-minute rule (owner screenshot 2026-09-29)
function row(i: number): OppRow {
  return {
    id: i, rank: i, ticker: `T${i}`, company: `Co ${i}`, sector: "Technology", sector_model: "x", price: null, session: "PREMARKET",
    price_timestamp: null, price_source: null, price_quality: "STALE", score: 60, confidence: 50, action: "DATA INSUFFICIENT",
    deterministic_action: "DATA INSUFFICIENT", committee_status: "NOT_RUN", ideal_entry: null, max_buy: null, target: null, stop: null, downside: null, rr: null,
    catalyst: null, catalyst_date: null, risk: "LOW", data_quality: "STALE", mode: "LIVE", vetoes: ["STALE_PRICE"], as_of: "2026-09-28T11:00:00Z",
    current_status: "AGING", current_status_reason: null, sessions_since: 0, actionable_now: null,
    action_ko: "데이터 부족", valuation_price_basis: null, sector_known: true,
  } as OppRow;
}

describe("a scan-wide stale price", () => {
  const rows = [1, 2, 3, 4].map(row);
  it("is explained once, and the rows do not repeat the same three badges or N/A", () => {
    const c = stalePriceCause(rows)!;
    render(<MemoryRouter><StalePriceNote c={c} /><OppTable rows={rows} commonStale /></MemoryRouter>);
    expect(screen.getByTestId("stale-price-note").textContent).toContain("프리마켓");
    expect(screen.queryAllByText(/현재가 오래됨/).length).toBe(0);  // the veto chip is not repeated per row
    expect(screen.queryAllByText("N/A").length).toBe(0);  // no plan → a quiet dash with the reason in its tooltip
  });
  it("offers a rescan only when it can give a verdict: regular session, or market fully closed (the close is current)", () => {
    const c = stalePriceCause(rows)!;
    const onRescan = vi.fn();
    const { rerender } = render(<StalePriceNote c={c} session="PREMARKET" onRescan={onRescan} />);
    expect(screen.queryByTestId("rescan-now")).toBeNull();
    rerender(<StalePriceNote c={c} session="AFTER_HOURS" onRescan={onRescan} />);
    expect(screen.queryByTestId("rescan-now")).toBeNull();
    rerender(<StalePriceNote c={c} session="REGULAR" onRescan={onRescan} />);
    fireEvent.click(screen.getByTestId("rescan-now"));
    expect(onRescan).toHaveBeenCalledTimes(1);
    rerender(<StalePriceNote c={c} session="CLOSED" onRescan={onRescan} />);
    expect(screen.getByTestId("rescan-now").textContent).toContain("종가 기준");
  });
});
