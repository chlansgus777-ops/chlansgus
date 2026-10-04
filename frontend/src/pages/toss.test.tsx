// @vitest-environment jsdom
/** 토스증권 계좌 연결 (owner 2026-09-29 "1번부터 꼼꼼하게"): the key form, the connected bar, the account's holdings and cash
 * in the portfolio, domestic holdings apart in won, and a background change reloading the screen by itself. */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TossCard, type TossView } from "../components/TossConnect";
import { resetApiCache } from "../components/useApi";
import { _resetQuotes, handleEvent } from "../quotes";
import Portfolio from "./Portfolio";

const OFF: TossView = { enabled: true, configured: false, active: false, version: 0, account: null, synced_at: null, age_s: null, stale: false, error: null,
  last_attempt: null, interval_s: 60, prefs: { cash: "toss_usd" }, key_store: "keychain", fills: null };
const ON: TossView = { ...OFF, configured: true, active: true, version: 3, account: { seq: 7, masked: "····8901" }, synced_at: new Date(Date.now() - 120_000).toISOString(), age_s: 120,
  cash: { USD: "1234.56", KRW: "500000" }, fx: { rate: "1385.5", mid: "1380", at: null }, fills: { count: 2, complete: true, since: "2025-09-29", synced_at: null },
  domestic: [{ symbol: "005930", name: "삼성전자", market: "KR", currency: "KRW", quantity: "30", last_price: "72000", avg_price: "65000", purchase_amount: "1950000",
    market_value: "2160000", pnl: "210000", pnl_rate: "0.1077", daily_pnl: "21600", daily_rate: "0.0100" }] };

const H = (ticker: string, source: "manual" | "ledger" | "toss", quantity: number, outside = false) => ({
  ticker, quantity, cost_basis: 100, price: 110, price_day: "2026-09-24", market_value: 110 * quantity, unrealized_pnl: 10 * quantity, unrealized_pct: 0.1,
  weight: 0.1, sector: "Technology", source, outside_broker: outside,
});
const PF = (broker: TossView | null, extra: Record<string, unknown> = {}) => ({ cash: 1234.56, cash_entered: true, cash_source: broker?.active ? "toss_usd" : "manual", nav: 5000, invested_value: 3765,
  unrealized_pnl: 100, hhi: 0.1, beta: 1, sector_weights: {}, theme_weights: {}, correlations: [], missing_prices: [], notes: [], currency: "USD", note: "",
  valuation_day: "2026-09-24", valuation_status: "COMPLETE", holdings: [H("NVDA", "toss", 10), H("MSFT", "manual", 4, true)], broker, ...extra });

let calls: { method: string; url: string; body?: string }[] = [];
let portfolio: unknown = PF(ON);
let reply: (method: string, url: string) => Response | null = () => null;
beforeEach(() => {
  resetApiCache();
  calls = [];
  portfolio = PF(ON);
  reply = () => null;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ method, url: String(url), body: init?.body as string | undefined });
    const r = reply(method, String(url));
    if (r) return r;
    if (String(url).includes("/transactions")) return new Response(JSON.stringify({ securities: [], realized_pnl: 0, dividends: 0, note: "" }));
    if (String(url).includes("/broker/toss/fills")) return new Response(JSON.stringify({ status: ON, fills: [
      { order_id: "a", symbol: "NVDA", side: "BUY", status: "FILLED", quantity: "1", avg_price: "100.00", amount: "100.00", commission: "0.10", tax: "0", currency: "USD", ordered_at: "2026-09-20T23:30:00+09:00", filled_at: "2026-09-20T23:31:00+09:00" },
      { order_id: "b", symbol: "005930", side: "SELL", status: "CANCELED", quantity: "2", avg_price: "70000", amount: "140000", commission: "20", tax: "250", currency: "KRW", ordered_at: "2026-09-19T09:30:00+09:00", filled_at: "2026-09-19T09:31:00+09:00" }] }));
    if (String(url).includes("/broker/toss")) return new Response(JSON.stringify(ON));
    return new Response(JSON.stringify(portfolio));
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); _resetQuotes(); });

describe("the connection card", () => {
  it("asks for both values, sends them trimmed once, never shows the secret, and says what failed", async () => {
    reply = (m) => (m === "PUT" ? new Response(JSON.stringify({ detail: "이 PC의 인터넷 주소(IP)가 토스증권 허용 목록에 없습니다. WTS 설정 > Open API > 허용 IP 관리에…" }), { status: 400 }) : null);
    const onChange = vi.fn();
    render(<MemoryRouter><TossCard view={OFF} onChange={onChange} /></MemoryRouter>);
    const btn = screen.getByRole("button", { name: "연결" }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("토스 client_id"), { target: { value: "  c_01ABCDEFGH  " } });
    expect((screen.getByLabelText("토스 client_secret") as HTMLInputElement).type).toBe("password");
    fireEvent.change(screen.getByLabelText("토스 client_secret"), { target: { value: "sec-ret-value " } });
    fireEvent.click(btn);
    fireEvent.click(btn);  // a double click is one request
    await waitFor(() => expect(screen.getByTestId("toss-error").textContent).toContain("허용 IP 관리"));
    expect(screen.getByTestId("toss-error").textContent).not.toContain("요청 형식");  // the backend's text alone
    const puts = calls.filter((c) => c.method === "PUT");
    expect(puts.length).toBe(1);
    expect(JSON.parse(puts[0]!.body!)).toEqual({ client_id: "c_01ABCDEFGH", client_secret: "sec-ret-value" });
    expect(onChange).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain("허용 IP 관리");  // the steps say where to register the PC
  });
  it("connected: masked account, freshness, sync now, and a failure keeps the last data with its time", async () => {
    const view = { ...ON, error: { kind: "IP_NOT_ALLOWED", text: "이 PC의 인터넷 주소(IP)가 토스증권 허용 목록에 없습니다." } };
    render(<MemoryRouter><TossCard view={view} onChange={() => {}} compact /></MemoryRouter>);
    const bar = screen.getByTestId("toss-status").textContent!;
    expect(bar).toContain("····8901");
    expect(bar).toContain("마지막 성공 동기화 2분 전");  // with an error the time is said to be the last success
    expect(bar).not.toContain("12345678901");
    expect(screen.getByTestId("toss-sync-error").textContent).toContain("2분 전에 받은 것");
    fireEvent.click(screen.getByText("지금 동기화"));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/api/broker/toss/sync"))).toBe(true));
  });
  it("says where the daily price history comes from, and which names stay on Polygon and why", () => {
    const view = { ...ON, daily_bars: { active: true, session: "2026-10-02", kept: 41, rejected: { AMD: "종가 불일치(같은 날 250거래일 중앙값 차이 2.90%)", ZZZZ: "토스에 없는 종목" }, error: null, last_run: null } };
    render(<MemoryRouter><TossCard view={view} onChange={() => {}} compact /></MemoryRouter>);
    const line = screen.getByTestId("toss-daily-bars");
    expect(line.textContent).toContain("일봉: 토스증권 41종목 (2026-10-02 장까지)");
    expect(line.textContent).toContain("시장 전체는 Polygon");
    expect(line.textContent).toContain("Polygon 유지 2종목(AMD, ZZZZ)");
    expect(line.innerHTML).toContain("종가 불일치");
    cleanup();
    render(<MemoryRouter><TossCard view={ON} onChange={() => {}} compact /></MemoryRouter>);
    expect(screen.queryByTestId("toss-daily-bars")).toBeNull();  // not connected for daily bars: nothing claimed
  });

  it("a failed 지금 동기화 reloads the view (light, ribbon, time) instead of a second red line", async () => {
    reply = (m) => (m === "POST" ? new Response(JSON.stringify({ detail: "토스증권 서버에 연결하지 못했습니다" }), { status: 503 }) : null);
    const onChange = vi.fn();
    render(<MemoryRouter><TossCard view={ON} onChange={onChange} compact /></MemoryRouter>);
    fireEvent.click(screen.getByText("지금 동기화"));
    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("toss-error")).toBeNull();
  });
  it("disconnect asks first and says the owner's own records stay", async () => {
    render(<MemoryRouter><TossCard view={ON} onChange={() => {}} /></MemoryRouter>);
    fireEvent.click(screen.getByTestId("toss-disconnect"));
    expect(document.body.textContent).toContain("직접 입력한 보유와 거래 기록은 그대로");
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    fireEvent.click(screen.getByTestId("toss-disconnect-yes"));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/api/broker/toss"))).toBe(true));
  });
  it("MOCK: no connect form on the portfolio, a reason in Settings", () => {
    const off = { ...OFF, enabled: false };
    const { container } = render(<MemoryRouter><TossCard view={off} onChange={() => {}} compact /></MemoryRouter>);
    expect(container.textContent).toBe("");
    cleanup();
    render(<MemoryRouter><TossCard view={off} onChange={() => {}} /></MemoryRouter>);
    expect(document.body.textContent).toContain("실데이터(LIVE) 모드에서");
  });
});

describe("the portfolio with the account", () => {
  it("marks where each holding comes from, keeps domestic stocks apart in won, and takes the cash from the account", async () => {
    render(<MemoryRouter><Portfolio /></MemoryRouter>);
    const table = await screen.findByTestId("holdings");
    expect(table.textContent).toContain("토스 계좌");
    expect(table.textContent).toContain("토스에 없음");
    expect(screen.queryByTestId("remove-NVDA")).toBeNull();  // an account holding is not deleted here (it would come back)
    expect(screen.getByTestId("remove-MSFT")).toBeTruthy();
    expect(screen.getByTestId("cash-toss").textContent).toContain("토스증권 달러 예수금");
    expect(screen.getByTestId("cash-from-toss")).toBeTruthy();
    expect(screen.queryByLabelText("현금(USD)")).toBeNull();
    const kr = screen.getByTestId("domestic-holdings").textContent!;
    expect(kr).toContain("삼성전자");
    expect(kr).toContain("₩2,160,000");
    expect(kr).toContain("+10.8%");
  });
  it("the fills open on request, newest first, money in each trade's currency", async () => {
    render(<MemoryRouter><Portfolio /></MemoryRouter>);
    const card = await screen.findByTestId("toss-fills");
    expect(calls.some((c) => c.url.includes("/fills"))).toBe(false);  // nothing fetched until opened
    fireEvent.click(card.querySelector("button")!);
    await waitFor(() => expect(screen.getByTestId("toss-fills").textContent).toContain("일부 체결"));
    const t = screen.getByTestId("toss-fills").textContent!;
    expect(t).toContain("$100.00");
    expect(t).toContain("₩140,000");
    expect(t).toContain("₩270");  // commission + tax
  });
  it("a sync that changed the account in the background reloads the portfolio by itself", async () => {
    render(<MemoryRouter><Portfolio /></MemoryRouter>);
    await screen.findByTestId("holdings");
    const before = calls.filter((c) => c.url.endsWith("/api/portfolio")).length;
    const app = (v: number) => `event: app\ndata: ${JSON.stringify({ scan_id: 1, scan_as_of: null, scan_running: false, sync_running: false, last_alert: 0, auto_scan: null, broker: { version: v, active: true, error: null } })}`;
    act(() => { handleEvent(app(3)); });
    act(() => { handleEvent(app(3)); });
    expect(calls.filter((c) => c.url.endsWith("/api/portfolio")).length).toBe(before);
    portfolio = PF(ON, { holdings: [H("NVDA", "toss", 4)] });
    act(() => { handleEvent(app(4)); });
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/api/portfolio")).length).toBe(before + 1));
  });
  it("not connected: one line offers the connection, the manual cash form stays", async () => {
    portfolio = PF(OFF, { cash_source: "manual", holdings: [H("MSFT", "manual", 4)] });
    render(<MemoryRouter><Portfolio /></MemoryRouter>);
    await screen.findByTestId("holdings");
    expect(screen.getByTestId("toss-card").textContent).toContain("1초 실시간 시세");  // the promise of connecting, in one line
    expect(screen.queryByLabelText("토스 client_secret")).toBeNull();
    fireEvent.click(screen.getByText("연결하기"));
    expect(screen.getByLabelText("토스 client_secret")).toBeTruthy();
    expect(screen.getByLabelText("현금(USD)")).toBeTruthy();
    expect(screen.queryByTestId("domestic-holdings")).toBeNull();
  });
});

describe("the status bar with the Toss feed", () => {
  it("says the prices come from Toss every second, and says so when they stop", async () => {
    const { QuoteFeedStatus } = await import("../components/LivePrice");
    const q = await import("../quotes");
    const base = { source: "finnhub", streaming: true, connected: false, coverage: "", max_symbols: 50, subscribed: ["NVDA", "AMD"], over_limit: [], session: "PREMARKET",
      connects: 0, disconnects: 0, messages: 0, trades: 0, out_of_order: 0, subscribe_msgs: 0, unsubscribe_msgs: 0, snapshot_calls: 0, last_error: null, connected_since: null,
      last_message_at: null, provider_latency_ms: { n: 0, p50: null, p95: null, max: null } };
    act(() => { q.handleEvent(`event: status\ndata: ${JSON.stringify({ ...base, poll: { active: true, live: true, last_ok: null, error: null, capacity: 200, every_s: 1, calls: 5, prints: 9 } })}`); });
    render(<MemoryRouter><QuoteFeedStatus /></MemoryRouter>);
    expect(screen.getByTestId("quote-feed").textContent).toContain("실시간 · 토스 1초");
    expect(screen.getByTestId("quote-feed").textContent).toContain("2/200");
    act(() => { q.handleEvent(`event: status\ndata: ${JSON.stringify({ ...base, poll: { active: true, live: false, last_ok: null, error: { kind: "IP_NOT_ALLOWED", text: "허용 IP" }, capacity: 200, every_s: 1, calls: 5, prints: 9 } })}`); });
    expect(screen.getByTestId("quote-feed").textContent).toContain("토스 시세 끊김");
  });
});
