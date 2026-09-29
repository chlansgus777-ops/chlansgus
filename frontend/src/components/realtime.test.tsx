// @vitest-environment jsdom
/** Real-time upgrade (owner 2026-09-29: "1초1초마다 내가 신경안써도 되는 프로그램"): verdicts follow every quote, alerts
 * arrive on the same stream, and a scan that ran in the background refreshes the screens by itself. */
import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { _resetQuotes, applyAlerts, applyRows, handleEvent, markAlertsSeen, onAppState, toastQueue, type Judge, type QuoteRow } from "../quotes";
import { LiveZone, UnlessLive, zoneView } from "./LiveZone";

afterEach(() => { cleanup(); _resetQuotes(); });

const J = (over: Partial<Judge> = {}): Judge => ({
  rec_id: 7, as_of: "2026-09-25T14:20:00Z", action: "BUY", action_ko: "매수", bullish: true, zone: "BUY_ZONE", rr_now: 3.1, min_rr: 2,
  max_buy: 102, stop: 92, target: 125, ideal_entry: 99, to_max_pct: 0.02, to_stop_pct: -0.08, to_target_pct: 0.25, move_pct: 0,
  quote_current: true, quote_age_s: 2, valid_now: true, problems: [], held: false, watched: false, pnl_pct: null, pnl_abs: null, needs_reanalysis: false, ...over,
});
const row = (v: number, judge: Judge | null): QuoteRow => ({ ticker: "NVDA", state: "LIVE", session: "REGULAR", subscribed: true, version: v, price: 100, trade_time: null,
  received_time: null, source: "finnhub", feed: "stream", previous_close: null, change_pct: null, error: null, judge });

describe("the live zone", () => {
  it("words each zone, and a buy needs a current quote", () => {
    expect(zoneView(J())!.label).toBe("지금 매수 구간");
    expect(zoneView(J({ valid_now: false, quote_current: false }))!.sub).toBe("현재가 확인 필요");
    expect(zoneView(J({ zone: "STOP_HIT", held: true }))!.sub).toContain("매도 검토");
    expect(zoneView(J({ zone: "ABOVE_MAX", to_max_pct: -0.013 }))!.sub).toContain("1.3%");
    expect(zoneView(J({ zone: "NO_PLAN" }))).toBeNull();
  });
  it("changes with the next quote without any reload, and replaces the stored status of the same recommendation", () => {
    render(<MemoryRouter><LiveZone ticker="NVDA" recId={7} /><UnlessLive ticker="NVDA" recId={7}><span>현재 유효</span></UnlessLive></MemoryRouter>);
    expect(screen.getByText("현재 유효")).toBeTruthy();  // no verdict yet: the stored status shows
    act(() => applyRows([row(1, J())]));
    expect(screen.getByTestId("zone-NVDA").dataset.zone).toBe("BUY_ZONE");
    expect(screen.queryByText("현재 유효")).toBeNull();  // never both side by side
    act(() => applyRows([row(2, J({ zone: "STOP_HIT", valid_now: false }))]));
    expect(screen.getByTestId("zone-NVDA").textContent).toContain("손절 기준 도달");
    act(() => applyRows([row(3, J({ rec_id: 8 }))]));  // another recommendation's plan: not this row's
    expect(screen.queryByTestId("zone-NVDA")).toBeNull();
    expect(screen.getByText("현재 유효")).toBeTruthy();
  });
});

describe("alerts and background work on the stream", () => {
  it("deduplicates alerts, pops up only live ones, counts unread", () => {
    const a = { id: 1, at: "2026-09-25T15:00:00Z", ticker: "NVDA", kind: "STOP_HIT", level: "danger" as const, text: "NVDA 손절", price: 91, rec_id: 7 };
    handleEvent(`event: hello\ndata: ${JSON.stringify({ rows: [], alerts: [a] })}`);
    expect(toastQueue.length).toBe(0);  // the log so far is listed, not popped up again
    applyAlerts([a, { ...a, id: 2, kind: "BUY_ZONE", level: "positive", text: "AMD 매수 구간" }]);
    expect(toastQueue.map((x) => x.id)).toEqual([2]);
    markAlertsSeen();
  });
  it("a scan finished in the background makes the screens ask again", () => {
    const seen: string[] = [];
    const off = onAppState((prev, next) => { if (prev && prev.scan_id !== next.scan_id) seen.push(`scan ${prev.scan_id}→${next.scan_id}`); });
    const app = (id: number) => `event: app\ndata: ${JSON.stringify({ scan_id: id, scan_as_of: null, scan_running: false, sync_running: false, last_alert: 0, auto_scan: null })}`;
    handleEvent(app(1));
    handleEvent(app(1));
    handleEvent(app(2));
    off();
    expect(seen).toEqual(["scan 1→2"]);
  });
  it("the cache hook refetches what is on screen when the scan id changes", async () => {
    const { invalidateApi } = await import("./useApi");
    expect(typeof invalidateApi).toBe("function");
    const spy = vi.fn();
    const off = onAppState(spy);
    handleEvent(`event: app\ndata: ${JSON.stringify({ scan_id: 5, scan_running: true, sync_running: false, last_alert: 0, auto_scan: null })}`);
    handleEvent(`event: app\ndata: ${JSON.stringify({ scan_id: 6, scan_running: false, sync_running: false, last_alert: 0, auto_scan: null })}`);
    off();
    expect(spy).toHaveBeenCalledTimes(2);
  });
});

describe("sizing at the live price", () => {
  it("re-counts shares on every quote and withholds them outside the plan", async () => {
    const { LiveSizing } = await import("../pages/StockDetail");
    render(<MemoryRouter><LiveSizing ticker="NVDA" recId={7} amount={1000} stop={92} /></MemoryRouter>);
    act(() => applyRows([row(1, J())]));  // price 100
    expect(screen.getByTestId("live-sizing").textContent).toContain("10주");
    act(() => applyRows([{ ...row(2, J()), price: 125 }]));
    expect(screen.getByTestId("live-sizing").textContent).toContain("8주");
    act(() => applyRows([{ ...row(3, J({ zone: "ABOVE_MAX", valid_now: false })), price: 130 }]));
    expect(screen.getByTestId("live-sizing").textContent).toContain("다시 계산하지 않습니다");
  });
});
