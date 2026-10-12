import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import type { SystemInfo } from "../types";
import type { ReadinessInfo, SyncJob } from "./Readiness";
import { type ApiState, invalidateAfterSync, invalidateApi, useApi, usePoll } from "./useApi";

/** /scan/status — the last scan's progress and how much of the list it judged. */
export interface ScanStatus {
  state: { scan_id: number; status: "COMPLETE" | "RUNNING" | "INTERRUPTED"; started_at: string; saved: number; total: number } | null;
  coverage: { universe: number; excluded: number; deep_analysed: number; analysed: number; data_insufficient: number; data_insufficient_rate: number | null;
    missing_by_field: Record<string, { count: number; rate: number }>; excluded_by_reason: Record<string, number>;
    llm: { calls: number; estimated_cost_usd: number; cost_complete: boolean } } | null;
}
export interface ProviderHealth { name: string; kind: string; mode: string; status: string; last_error: string | null; last_failure: string | null }
interface HealthResp { providers: ProviderHealth[]; llm: { provider: string; available: boolean; status: string } }

/** The data time of what the current page shows: its price time (never the time the screen fetched it) and when
 * it was analysed. Pages report it; the status bar shows it. */
export interface PageTime { label: string; priceTs: string | null; priceSession?: string | null; quality?: string | null; analysedAt?: string | null }

interface Status {
  system: ApiState<SystemInfo>;
  readiness: ApiState<ReadinessInfo>;
  scan: ApiState<ScanStatus>;
  sync: ApiState<{ job: SyncJob | null }>;
  health: ApiState<HealthResp>;
  nowMs: number;
  page: PageTime | null;
  setPage: (p: PageTime | null) => void;
  scanning: boolean;
  setScanning: (b: boolean) => void;
  refresh: () => void;
}

const Ctx = createContext<Status | null>(null);

/** Cheap status reads, kept apart from any analysis: /system every minute (session and clock), /scan/status and
 * /sync/status every few seconds only while something runs, /health every two minutes. Navigation never re-runs
 * an analysis or an AI call. */
export function StatusProvider({ children }: { children: ReactNode }) {
  const system = useApi<SystemInfo>("/system");
  const readiness = useApi<ReadinessInfo>("/readiness");
  const scan = useApi<ScanStatus>("/scan/status");
  const live = system.data?.mode === "LIVE";
  const sync = useApi<{ job: SyncJob | null }>(live ? "/sync/status" : null);
  const health = useApi<HealthResp>("/health");
  const [page, setPage] = useState<PageTime | null>(null);
  const [scanning, setScanning] = useState(false);
  const [tick, setTick] = useState(() => Date.now());
  const scanRunning = scanning || scan.data?.state?.status === "RUNNING";
  const syncRunning = sync.data?.job?.status === "RUNNING";
  usePoll(system.reload, 60_000);
  usePoll(health.reload, 120_000);
  usePoll(scan.reload, 3_000, scanRunning);
  usePoll(sync.reload, 5_000, syncRunning);
  usePoll(readiness.reload, 30_000, syncRunning);
  usePoll(readiness.reload, 4_000, !syncRunning && !!readiness.data?.stats_pending); // a background recount: take it when done
  // a preparation or scan that just ended changed prices / recommendations: screens cached before it ask again
  const was = useRef({ sync: false, scan: false });
  const scanServerRunning = scan.data?.state?.status === "RUNNING";
  useEffect(() => {
    if (was.current.sync && !syncRunning) invalidateAfterSync();
    if (was.current.scan && !scanServerRunning) invalidateApi(["/dashboard", "/opportunities", "/stocks/", "/watchlist", "/performance", "/issues"]);
    was.current = { sync: syncRunning, scan: scanServerRunning };
  }, [syncRunning, scanServerRunning]);
  const clock = useCallback(() => setTick(Date.now()), []);
  usePoll(clock, 20_000);
  // the server's clock, not the PC's: offset measured when /system arrived
  const offset = useMemo(() => (system.data && system.fetchedAt ? Date.parse(system.data.now) - system.fetchedAt : 0), [system.data, system.fetchedAt]);
  const refresh = useCallback(() => { system.reload(); readiness.reload(); scan.reload(); health.reload(); if (live) sync.reload(); }, [system, readiness, scan, health, sync, live]);
  const value: Status = { system, readiness, scan, sync, health, nowMs: tick + (Number.isFinite(offset) ? offset : 0), page, setPage, scanning, setScanning, refresh };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStatus(): Status | null {
  return useContext(Ctx);
}

/** A page reports the data time of what it shows (cleared when it leaves). */
export function usePageTime(p: PageTime | null): void {
  const st = useStatus();
  const set = st?.setPage;
  const key = p ? JSON.stringify(p) : "";
  useEffect(() => {
    if (!set) return;
    set(key ? (JSON.parse(key) as PageTime) : null);
    return () => set(null);
  }, [key, set]);
}

/** The one line above every screen, shown only when something is wrong (owner spec 2026-10-12: no clock, session or
 * feed indicators on a healthy app). One message, the most important first; it links to where it is fixed. */
export function problemOf(st: Status): { text: string; to?: string } | null {
  const sys = st.system.data;
  if (st.system.error) return null;  // the page shows its own "연결 실패" panel with a retry button
  if (!sys) return null;
  if (sys.mode === "MOCK") return { text: "모의 데이터입니다 · 실제 투자 판단에 쓰지 마세요" };
  const job = st.sync.data?.job ?? null;
  if (job?.status === "RUNNING") return { text: `데이터를 받는 중입니다${job.progress ? ` (${job.progress.percent}%)` : ""}`, to: "/settings?tab=status" };
  const down = (st.health.data?.providers ?? []).filter((p) => p.status === "DOWN");
  if (down.length) return { text: `자료 ${down.length}곳을 받지 못하고 있습니다`, to: "/settings?tab=status" };
  if (st.readiness.data?.recommendation_readiness === "NOT READY") return { text: "데이터 준비가 끝나지 않았습니다", to: "/settings?tab=status" };
  return null;
}

export function ProblemLine() {
  const st = useStatus();
  const p = st ? problemOf(st) : null;
  if (!p) return null;
  return (
    <div className="m-problem" role="status" data-testid="problem-line">
      ⚠ {p.text}{p.to && <> · <Link to={p.to}>확인</Link></>}
    </div>
  );
}
