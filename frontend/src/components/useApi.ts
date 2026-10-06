import { useCallback, useEffect, useReducer, useRef } from "react";
import { ApiError, api, onMutation } from "../api";
import { onAppState } from "../quotes";

export type LoadState = "idle" | "loading" | "ready" | "error";

export interface ApiState<T> {
  data: T | null;
  error: string | null;
  /** the HTTP status of that error (404: nothing stored yet), null for a network error or none */
  errorStatus: number | null;
  /** a request for this screen's data is in flight (the data shown, if any, is the previous answer) */
  loading: boolean;
  state: LoadState;
  reload: () => void;
  /** when the data on screen was received (ms since epoch) — the real time, also for an answer shown from the cache */
  fetchedAt: number | null;
}

/** The resource a path points at, ignoring query flags such as ``?refresh=true``. */
export const resourceOf = (path: string | null): string | null => (path === null ? null : (path.split("?")[0] ?? path));

/** The cache key: the full path (ticker, period and every other parameter included — never reused across them)
 * without the action flag ``refresh``, whose answer is the same resource after a re-analysis. */
export function cacheKeyOf(path: string): string {
  const [p, q] = path.split("?");
  if (!q) return p ?? path;
  const kept = q.split("&").filter((x) => x && !/^refresh=/.test(x));
  return kept.length ? `${p}?${kept.join("&")}` : (p ?? path);
}

// ------------------------------------------------------------------ the shared screen cache
// One entry per resource for the whole app: going back to a menu shows its last answer at once (with its real
// receive time) while ONE request refreshes it. Identical requests share that request; a request nobody waits for
// any more is aborted (it would hold one of the browser's few connections to the backend); an answer that
// started before an invalidation (a trade, a scan, a sync) is dropped instead of overwriting newer data.
interface Entry {
  data?: unknown;
  at?: number; // receive time of ``data`` (kept as-is when outdated — never shown as newer than it is)
  error?: string;
  errorStatus?: number;
  inflight?: { p: Promise<void>; ctrl: AbortController; gen: number };
  gen: number; // bumped by invalidation
  subs: Set<() => void>;
  used: number; // last use (LRU)
}

const MAX_ENTRIES = 48; // the stock page holds the most (chart + analysis) — older entries are dropped first
const cache = new Map<string, Entry>();

function entry(key: string): Entry {
  let e = cache.get(key);
  if (!e) {
    e = { gen: 0, subs: new Set(), used: Date.now() };
    cache.set(key, e);
    if (cache.size > MAX_ENTRIES) {
      const idle = [...cache.entries()].filter(([, v]) => v.subs.size === 0 && !v.inflight).sort((a, b) => a[1].used - b[1].used);
      for (const [k] of idle.slice(0, cache.size - MAX_ENTRIES)) cache.delete(k);
    }
  }
  e.used = Date.now();
  return e;
}

const notify = (e: Entry) => e.subs.forEach((f) => f());

function fetchInto(key: string, path: string): void {
  const e = entry(key);
  if (e.inflight && e.inflight.gen === e.gen && path === key) return; // one request per resource at a time
  e.inflight?.ctrl.abort(); // an older (pre-invalidation) or plain request is superseded by this one
  const ctrl = new AbortController();
  const gen = e.gen;
  const p = api
    .get<unknown>(path, { signal: ctrl.signal })
    .then((d) => { if (!ctrl.signal.aborted && e.gen === gen && e.inflight?.ctrl === ctrl) { e.data = d; e.at = Date.now(); e.error = undefined; e.errorStatus = undefined; } })
    .catch((err: unknown) => {
      if (ctrl.signal.aborted || (err instanceof ApiError && err.status === -1)) return;
      if (e.gen === gen) {
        e.error = err instanceof Error ? err.message : String(err);
        e.errorStatus = err instanceof ApiError && err.status > 0 ? err.status : undefined;
      }
    })
    .finally(() => { if (e.inflight?.ctrl === ctrl) e.inflight = undefined; notify(e); });
  e.inflight = { p, ctrl, gen };
  notify(e);
}

/** Mark resources outdated after a change (``prefixes`` of the cache key; none = everything). Screens showing them
 * ask again now; the others ask when they are opened next. The last answer stays visible with its real time. */
export function invalidateApi(prefixes?: string[]): void {
  for (const [k, e] of cache) {
    if (prefixes && !prefixes.some((p) => k.startsWith(p))) continue;
    e.gen += 1;
    if (e.subs.size > 0) fetchInto(k, k);
  }
}

/** What a successful change makes outdated (by the change's path). Quote subscriptions change nothing cached. */
const AFFECTS: [RegExp, string[]][] = [
  [/^\/saveticker\//, ["/saveticker/", "/news"]],
  [/^\/quotes\//, []],
  [/^\/(portfolio|transactions)/, ["/portfolio", "/transactions", "/dashboard", "/stocks/", "/opportunities"]],
  [/^\/watchlist/, ["/watchlist", "/dashboard", "/stocks/"]],
  [/^\/scan/, ["/dashboard", "/opportunities", "/stocks/", "/watchlist", "/scan", "/performance", "/readiness", "/issues"]],
  [/^\/recommendations\//, ["/stocks/", "/dashboard", "/opportunities"]],
  [/^\/(evaluation|calibration|paper)/, ["/performance", "/calibration", "/paper", "/dashboard"]],
  [/^\/sync/, ["/readiness", "/sync"]],
  [/^\/broker\//, ["/portfolio", "/broker/", "/transactions", "/dashboard", "/stocks/", "/opportunities"]],
];

onMutation((_method, path) => {
  if (/^\/stocks\/[^/]+\/analysis$/.test(path)) {
    invalidateApi([path]);
    return;
  }
  const hit = AFFECTS.find(([re]) => re.test(path));
  invalidateApi(hit ? hit[1] : undefined); // an unknown change (settings…) outdates everything
});

// A scan or data preparation that ran in the background (automatic scan, another window) is announced on the live
// stream: the screens showing its results ask again at once — nobody has to press anything or revisit a menu.
onAppState((prev, next) => {
  if (!prev) return;
  if (prev.scan_id !== next.scan_id || (prev.scan_running && !next.scan_running)) {
    invalidateApi(["/dashboard", "/opportunities", "/stocks/", "/watchlist", "/performance", "/issues", "/scan", "/readiness", "/strategies", "/routine"]);
  }
  if (prev.sync_running && !next.sync_running) invalidateAfterSync();
  // the account changed in the background (a trade at Toss, a deposit): holdings, cash and everything sized with them
  if ((prev.broker?.version ?? 0) !== (next.broker?.version ?? 0) || (prev.broker?.error ?? null) !== (next.broker?.error ?? null)) {
    invalidateApi(["/portfolio", "/broker/", "/dashboard", "/stocks/", "/opportunities", "/routine"]);
  }
});

/** Data preparation finished (seen by the status poller): prices, readiness and everything computed from them. */
export function invalidateAfterSync(): void {
  invalidateApi(["/readiness", "/dashboard", "/opportunities", "/portfolio", "/stocks/", "/watchlist", "/performance", "/strategies", "/routine"]);
}

/** Tests only: start every test from an empty cache. */
export function resetApiCache(): void {
  for (const e of cache.values()) e.inflight?.ctrl.abort();
  cache.clear();
}

/** Fetch ``path`` (null = don't fetch). A revisit shows the last answer for exactly this path at once (never another
 * ticker's or period's) and refreshes it in the background; the receive time is the real one. Exposes a retry. */
export function useApi<T>(path: string | null, deps: unknown[] = []): ApiState<T> {
  const [, force] = useReducer((x: number) => x + 1, 0);
  const key = path === null ? null : cacheKeyOf(path);
  const tick = useRef(0);
  const reload = useCallback(() => { tick.current += 1; if (path !== null) fetchInto(cacheKeyOf(path), path); }, [path]);
  useEffect(() => {
    if (path === null || key === null) return;
    const e = entry(key);
    e.subs.add(force);
    fetchInto(key, path); // every visit: the last answer at once, then one refresh (shared with any already running)
    return () => {
      e.subs.delete(force);
      if (e.subs.size === 0 && e.inflight) {
        const fl = e.inflight;
        // nobody shows this resource any more: free the connection (a quick return re-subscribes before this runs)
        queueMicrotask(() => { if (e.subs.size === 0 && e.inflight === fl) { fl.ctrl.abort(); e.inflight = undefined; } });
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, key, ...deps]);
  const e = key === null ? undefined : cache.get(key);
  const data = (e?.data ?? null) as T | null;
  const err = e?.error ?? null;
  const loading = path !== null && (!!e?.inflight || e === undefined);
  const state: LoadState = path === null ? "idle" : data === null && !err ? "loading" : err && data === null ? "error" : "ready";
  return { data, error: err, errorStatus: err ? (e?.errorStatus ?? null) : null, loading, state, reload, fetchedAt: data !== null ? (e?.at ?? null) : null };
}

/** Re-run ``tick`` every ``ms`` while ``active`` and the window is visible — for cheap status reads only
 * (never an analysis or an AI call). */
export function usePoll(tick: () => void, ms: number, active = true): void {
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      tick();
    }, ms);
    return () => window.clearInterval(id);
  }, [tick, ms, active]);
}

/** ``useApi`` for an answer the server may still be loading or refreshing in the background (``pending`` /
 * ``refreshing``): asks again every 3 s until it settles — each ask reads only what the server already has. */
export function useSettling<T extends { pending?: boolean; refreshing?: boolean }>(path: string | null): ApiState<T> {
  const r = useApi<T>(path);
  usePoll(r.reload, 3_000, !!(r.data?.pending || r.data?.refreshing));
  return r;
}
