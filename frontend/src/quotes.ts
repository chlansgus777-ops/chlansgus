/** One app-wide latest-quote store (real-time quotes, 2026-09-28).
 *
 * - ONE event stream per window (`/api/quotes/stream`, fetched so the per-launch token header can be sent), opened on
 *   the first subscriber and kept while any screen shows a price. No screen opens its own connection or polls.
 * - Rows are applied only when their server `version` is newer than the one held: a late, older event never
 *   overwrites a newer one.
 * - Components subscribe per ticker (useSyncExternalStore): a tick re-renders only the prices of that ticker,
 *   never the whole screen.
 * - Screens declare the tickers they show (`useViewQuotes`); the union is sent as one lease to the backend, which
 *   owns the subscription set (watchlist + holdings + viewed, capped at the feed's symbol limit).
 * - While the stream is down every row reads "연결 끊김·재연결 중" with its last price and real time kept. */
import { useEffect, useSyncExternalStore } from "react";
import { api, baseUrl, clientHeaders } from "./api";

export type QuoteState = "LIVE" | "QUIET" | "DELAYED" | "CLOSED_LAST" | "EXTENDED_NO_TRADE" | "RECONNECTING" | "NO_DATA" | "OVER_LIMIT" | "UNAVAILABLE";

export interface QuoteRow {
  ticker: string;
  state: QuoteState;
  session: string;
  subscribed: boolean;
  version: number;
  price: number | null;
  trade_time: string | null;
  received_time: string | null;
  source: string | null;
  feed: "stream" | "snapshot" | null;
  previous_close: number | null;
  change_pct: number | null;
  error: string | null;
  /** the stored plan re-judged at this price (backend application/live_judge) — absent for names without a plan */
  judge?: Judge | null;
}

export type Zone = "BUY_ZONE" | "ABOVE_MAX" | "RR_LOW" | "STOP_HIT" | "TARGET_HIT" | "HOLD_RANGE" | "NO_PLAN";
export interface Judge {
  rec_id: number; as_of: string; action: string; action_ko: string; bullish: boolean; zone: Zone;
  rr_now: number | null; min_rr: number; max_buy: number | null; stop: number | null; target: number | null; ideal_entry: number | null;
  to_max_pct: number | null; to_stop_pct: number | null; to_target_pct: number | null; move_pct: number | null;
  quote_current: boolean; quote_age_s: number | null; valid_now: boolean; problems: string[];
  held: boolean; watched: boolean; pnl_pct: number | null; pnl_abs: number | null; needs_reanalysis: boolean;
}
export interface LiveAlert { id: number; at: string; ticker: string; kind: string; level: "positive" | "warning" | "danger" | "info"; text: string; price: number | null; rec_id: number | null }
export interface AppState {
  scan_id: number | null; scan_as_of: string | null; scan_running: boolean; sync_running: boolean; last_alert: number;
  auto_scan: { enabled: boolean; interval_minutes: number; last_auto_scan: string | null; next_due: string | null; why: string } | null;
  /** the 토스증권 account (LIVE only): a new version = its holdings or cash changed, or it was (dis)connected */
  broker?: { version: number; active: boolean; error: string | null } | null;
}

export interface QuoteStatus {
  source: string;
  streaming: boolean;
  connected: boolean;
  coverage: string;
  max_symbols: number;
  subscribed: string[];
  over_limit: string[];
  session: string;
  connects: number;
  disconnects: number;
  messages: number;
  trades: number;
  out_of_order: number;
  subscribe_msgs: number;
  unsubscribe_msgs: number;
  snapshot_calls: number;
  last_error: string | null;
  connected_since: string | null;
  last_message_at: string | null;
  provider_latency_ms: { n: number; p50: number | null; p95: number | null; p99?: number | null; max: number | null; mean?: number | null };
  out_of_order_late_by_ms?: { n: number; p50: number | null; p95: number | null; max: number | null };
  duplicates?: number;
  /** the broker's 1-second price feed (토스증권): null in MOCK */
  poll?: { active: boolean; live: boolean; last_ok: string | null; error: { kind: string; text: string } | null; capacity: number; every_s: number; calls: number; prints: number } | null;
}

export type Link = "idle" | "connecting" | "open" | "retrying";

const rows = new Map<string, QuoteRow>();
const tickerListeners = new Map<string, Set<() => void>>();
const globalListeners = new Set<() => void>();
const linkListeners = new Set<() => void>();
let status: QuoteStatus | null = null;
let link: Link = "idle";
let linkSnap: { link: Link; status: QuoteStatus | null } = { link, status };
let refs = 0;
let abort: AbortController | null = null;
let retryTimer: ReturnType<typeof setTimeout> | null = null;
let backoff = 1000;
/** backend receipt → applied in this window (ms), last 2000 — the display-side latency (same machine clock) */
export const clientLatency: number[] = [];

function emit(ticker: string) {
  tickerListeners.get(ticker)?.forEach((f) => f());
}

function setLink(l: Link) {
  if (l === link) return;
  link = l;
  linkSnap = { link, status };
  // every row's state word depends on the link (RECONNECTING while down)
  linkListeners.forEach((f) => f());
  globalListeners.forEach((f) => f());
}

const batchListeners = new Set<() => void>();
let judgedSnap: QuoteRow[] = [];
let allSnap = new Map<string, QuoteRow>();
/** Every held row by ticker (for sums over several names, such as the live portfolio value). */
export function useAllQuotes(): Map<string, QuoteRow> {
  useEffect(() => { retain(); return release; }, []);
  return useSyncExternalStore((f) => { batchListeners.add(f); return () => { batchListeners.delete(f); }; }, () => allSnap);
}
/** Every row that carries a live verdict (for lists such as "지금 매수 구간") — refreshed once per batch of rows. */
export function useJudgedRows(): QuoteRow[] {
  useEffect(() => { retain(); return release; }, []);
  return useSyncExternalStore((f) => { batchListeners.add(f); return () => { batchListeners.delete(f); }; }, () => judgedSnap);
}

/** Apply rows from the server; older versions are ignored (ordering guard). Exported for tests. */
export function applyRows(list: QuoteRow[], now = Date.now()) {
  applyRowsInner(list, now);
  allSnap = new Map(rows);
  judgedSnap = [...rows.values()].filter((r) => r.judge);
  batchListeners.forEach((f) => f());
}

function applyRowsInner(list: QuoteRow[], now: number) {
  for (const r of list) {
    const cur = rows.get(r.ticker);
    if (cur && cur.version >= r.version) continue;
    rows.set(r.ticker, r);
    if (r.received_time && (!cur || cur.received_time !== r.received_time)) {
      const d = now - Date.parse(r.received_time);
      if (Number.isFinite(d) && d >= 0) {
        clientLatency.push(d);
        if (clientLatency.length > 2000) clientLatency.splice(0, 1000);
      }
    }
    emit(r.ticker);
  }
}

function setStatus(s: QuoteStatus) {
  status = s;
  linkSnap = { link, status };
  globalListeners.forEach((f) => f());
}

// ------------------------------------------------------------------ alerts and background work (same stream)
const ALERTS_KEPT = 50;
let alertList: LiveAlert[] = [];
let lastSeenAlert = 0;  // the newest alert the owner has looked at (the bell's unread count)
const alertListeners = new Set<() => void>();
let appState: AppState | null = null;
const appListeners = new Set<() => void>();

function readSeen(): number {
  try { return Number(localStorage.getItem("ml.alerts.seen") ?? 0) || 0; } catch { return 0; }
}

/** Add alerts (dedup by id, newest last). ``fresh`` marks the ones that arrived live (shown as toasts). */
export function applyAlerts(list: LiveAlert[], fresh = true) {
  const have = new Set(alertList.map((a) => a.id));
  const add = list.filter((a) => !have.has(a.id));
  if (!add.length) return;
  alertList = [...alertList, ...add].sort((a, b) => a.id - b.id).slice(-ALERTS_KEPT);
  if (!lastSeenAlert) lastSeenAlert = readSeen();
  if (fresh) for (const a of add) toastQueue.push(a);
  alertListeners.forEach((f) => f());
}

export const toastQueue: LiveAlert[] = [];

export function markAlertsSeen() {
  lastSeenAlert = alertList.at(-1)?.id ?? lastSeenAlert;
  try { localStorage.setItem("ml.alerts.seen", String(lastSeenAlert)); } catch { /* private window: the count resets */ }
  alertListeners.forEach((f) => f());
}

let alertSnap = { alerts: alertList, unread: 0 };
function alertSnapshot() {
  const unread = alertList.filter((a) => a.id > (lastSeenAlert || readSeen())).length;
  if (alertSnap.alerts !== alertList || alertSnap.unread !== unread) alertSnap = { alerts: alertList, unread };
  return alertSnap;
}

export function useAlerts(): { alerts: LiveAlert[]; unread: number } {
  useEffect(() => { retain(); return release; }, []);
  return useSyncExternalStore((f) => { alertListeners.add(f); return () => { alertListeners.delete(f); }; }, alertSnapshot);
}

type AppHook = (prev: AppState | null, next: AppState) => void;
const appHooks = new Set<AppHook>();
/** Screens' cache invalidation hooks: called when a background scan finished or data preparation ended. */
export function onAppState(f: AppHook): () => void { appHooks.add(f); return () => { appHooks.delete(f); }; }

function setApp(next: AppState) {
  const prev = appState;
  appState = next;
  appHooks.forEach((f) => f(prev, next));
  appListeners.forEach((f) => f());
}

export function useAppState(): AppState | null {
  useEffect(() => { retain(); return release; }, []);
  return useSyncExternalStore((f) => { appListeners.add(f); return () => { appListeners.delete(f); }; }, () => appState);
}

/** Parse one SSE block ("event: x\ndata: {...}"). Exported for tests. */
export function handleEvent(block: string) {
  let ev = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) ev = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trim());
  }
  if (!data.length) return;
  let j: { rows?: QuoteRow[]; status?: QuoteStatus; alerts?: LiveAlert[] } & Partial<QuoteStatus>;
  try { j = JSON.parse(data.join("\n")); } catch { return; }
  if (ev === "hello") {
    if (j.rows) applyRows(j.rows);
    if (j.status) setStatus(j.status);
    if (j.alerts) applyAlerts(j.alerts, false);  // the log so far: listed, not popped up again
  } else if (ev === "alerts") {
    if (j.alerts) applyAlerts(j.alerts);
  } else if (ev === "app") {
    setApp(j as unknown as AppState);
  } else if (ev === "quotes") {
    if (j.rows) applyRows(j.rows);
  } else if (ev === "status") {
    setStatus(j as QuoteStatus);
  }
}

/** Unit tests render screens with a mocked fetch: the stream and lease calls stay off there unless a test opts in. */
function disabled(): boolean {
  return import.meta.env.MODE === "test" && !(globalThis as { __QUOTES_TEST__?: boolean }).__QUOTES_TEST__;
}

async function connect() {
  if (abort || refs === 0 || disabled()) return;
  const ctl = new AbortController();
  abort = ctl;
  setLink(link === "idle" ? "connecting" : "retrying");
  try {
    const r = await fetch(`${baseUrl()}/api/quotes/stream`, { headers: { ...clientHeaders(), Accept: "text/event-stream" }, signal: ctl.signal, cache: "no-store" });
    if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);
    setLink("open");
    backoff = 1000;
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i: number;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, i);
        buf = buf.slice(i + 2);
        if (block && !block.startsWith(":")) handleEvent(block);
      }
    }
  } catch {
    /* network loss, sleep, backend restart → retry below */
  } finally {
    if (abort === ctl) abort = null;
  }
  if (ctl.signal.aborted || refs === 0) return;
  setLink("retrying");
  scheduleRetry();
}

function scheduleRetry() {
  if (retryTimer || refs === 0) return;
  const wait = backoff * (0.5 + Math.random() / 2);
  backoff = Math.min(30000, backoff * 2);
  retryTimer = setTimeout(() => { retryTimer = null; void connect(); }, wait);
}

function reconnectNow() {
  if (refs === 0 || abort) return;
  if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
  backoff = 1000;
  void connect();
}

function retain() {
  refs += 1;
  if (refs === 1) {
    if (typeof window !== "undefined") {
      window.addEventListener("online", reconnectNow);
      document.addEventListener("visibilitychange", onVisible);
    }
    void connect();
  }
}

function release() {
  refs -= 1;
  if (refs > 0) return;
  refs = 0;
  if (typeof window !== "undefined") {
    window.removeEventListener("online", reconnectNow);
    document.removeEventListener("visibilitychange", onVisible);
  }
  if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
  abort?.abort();
  abort = null;
  setLink("idle");
}

function onVisible() {
  if (document.visibilityState === "visible") reconnectNow();  // after sleep/resume the socket may be half-dead
}

// ------------------------------------------------------------------ view leases (one POST for the whole app)
const viewed = new Map<string, number>();
let viewTimer: ReturnType<typeof setTimeout> | null = null;
let leaseTimer: ReturnType<typeof setInterval> | null = null;

function sendViews() {
  if (viewTimer || disabled()) return;
  viewTimer = setTimeout(() => {
    viewTimer = null;
    const t = [...viewed.keys()].slice(0, 60);
    if (t.length) api.post("/quotes/view", { tickers: t }).catch(() => undefined);
  }, 150);
}

function addViews(tickers: string[]) {
  let added = false;
  for (const t of tickers) { const n = viewed.get(t) ?? 0; viewed.set(t, n + 1); added ||= n === 0; }
  if (added) sendViews();
  if (!leaseTimer && viewed.size) leaseTimer = setInterval(sendViews, 60000);  // renew before the 90 s lease ends
}

function dropViews(tickers: string[]) {
  for (const t of tickers) { const n = (viewed.get(t) ?? 1) - 1; if (n <= 0) viewed.delete(t); else viewed.set(t, n); }
  if (!viewed.size && leaseTimer) { clearInterval(leaseTimer); leaseTimer = null; }
}

/** Tell the backend (once, for the whole app) that the watchlist or holdings changed. */
export function refreshQuoteSubscriptions() {
  if (disabled()) return;
  api.post("/quotes/refresh-subscriptions").catch(() => undefined);
}

// ------------------------------------------------------------------ hooks
const EMPTY: QuoteRow | undefined = undefined;

export function useQuote(ticker: string | null | undefined): { row: QuoteRow | undefined; link: Link } {
  const t = (ticker ?? "").toUpperCase();
  useEffect(() => { retain(); return release; }, []);
  const row = useSyncExternalStore(
    (f) => {
      if (!t) return () => undefined;
      let s = tickerListeners.get(t);
      if (!s) { s = new Set(); tickerListeners.set(t, s); }
      s.add(f);
      return () => { s!.delete(f); if (!s!.size) tickerListeners.delete(t); };
    },
    () => (t ? rows.get(t) : EMPTY),
  );
  const l = useSyncExternalStore((f) => { linkListeners.add(f); return () => { linkListeners.delete(f); }; }, () => link);
  return { row, link: l };
}

export function useQuoteStatus(): { link: Link; status: QuoteStatus | null } {
  useEffect(() => { retain(); return release; }, []);
  return useSyncExternalStore((f) => { globalListeners.add(f); return () => globalListeners.delete(f); }, () => linkSnap);
}

/** The tickers a screen shows: subscribed while it is mounted (deduplicated across screens). */
export function useViewQuotes(tickers: (string | null | undefined)[]) {
  const key = [...new Set(tickers.filter((x): x is string => !!x).map((x) => x.toUpperCase()))].sort().join(",");
  useEffect(() => {
    const list = key ? key.split(",") : [];
    addViews(list);
    return () => dropViews(list);
  }, [key]);
}

/** The state shown for a row, given the client link: a down stream overrides "live". */
export function effectiveState(row: QuoteRow | undefined, l: Link): QuoteState {
  if (!row) return l === "retrying" ? "RECONNECTING" : "NO_DATA";
  if (l === "retrying" && row.subscribed) return "RECONNECTING";
  return row.state;
}

export const STATE_KO: Record<QuoteState, string> = {
  LIVE: "실시간 수신 중", QUIET: "연결됨 · 최근 체결 없음", DELAYED: "지연 시세", CLOSED_LAST: "장 마감 · 마지막 체결가",
  EXTENDED_NO_TRADE: "장전·시간외 체결 미수신", RECONNECTING: "연결 끊김 · 재연결 중", NO_DATA: "아직 미수신",
  OVER_LIMIT: "구독 한도 초과", UNAVAILABLE: "실시간 스트림 없음",
};

export function stateLabel(row: QuoteRow | undefined, l: Link): string {
  const s = effectiveState(row, l);
  if (s === "EXTENDED_NO_TRADE" && row) {
    return row.session === "PREMARKET" ? "장전 체결 미수신 · 직전 체결가" : row.session === "AFTER_HOURS" ? "시간외 체결 미수신 · 마지막 체결가" : "개장 후 체결 미수신 · 직전 체결가";
  }
  if (s === "NO_DATA" && row?.error) return "수신 실패";
  return STATE_KO[s];
}

/** Test hook: clear the store. */
export function _resetQuotes() {
  rows.clear();
  alertList = [];
  toastQueue.length = 0;
  appState = null;
  status = null;
  link = "idle";
  linkSnap = { link, status };
  clientLatency.length = 0;
}
