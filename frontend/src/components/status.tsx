import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ago, clockPair, stampEt } from "../format";
import { HEALTH_KO, QUALITY_INFO, READINESS_KO, SESSION_KO } from "../i18n";
import type { SystemInfo } from "../types";
import { QuoteFeedStatus } from "./LivePrice";
import type { ReadinessInfo, SyncJob } from "./Readiness";
import { type ApiState, useApi, usePoll } from "./useApi";

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

type SbTone = "ok" | "warn" | "danger" | "info" | undefined;
function Item({ tone, icon, children, to, title, testId }: { tone?: SbTone; icon?: string; children: ReactNode; to?: string; title?: string; testId?: string }) {
  const cls = `sb-item${tone ? ` tone-${tone}` : ""}`;
  const body = <>{icon && <span className="ico" aria-hidden>{icon}</span>}{children}</>;
  return to ? <Link to={to} className={cls} title={title} data-testid={testId}>{body}</Link> : <span className={cls} title={title} data-testid={testId}>{body}</span>;
}

const SESSION_TONE: Record<string, SbTone> = { REGULAR: "ok", PREMARKET: "info", AFTER_HOURS: "info", CLOSED: undefined };
const JOB_SHORT: Record<string, string> = { RUNNING: "받는 중", DONE: "끝남", FAILED: "실패", PAUSED: "일시 정지", INTERRUPTED: "중단됨", NEEDS_SETUP: "설정 필요", INCOMPLETE: "아직 준비 안 됨" };

/** Always-visible status: data source, US session with New York and Korea time, the page's data time and its age,
 * collection and analysis progress, provider failures and AI availability. */
export function StatusBar() {
  const st = useStatus();
  if (!st) return null;
  const sys = st.system.data;
  const now = new Date(st.nowMs).toISOString();
  const clock = clockPair(now);
  const session = sys?.market?.session ?? null;
  const rd = st.readiness.data;
  const rr = rd ? READINESS_KO[rd.recommendation_readiness] : undefined;
  const job = st.sync.data?.job ?? null;
  const scanState = st.scan.data?.state ?? null;
  const providers = st.health.data?.providers ?? [];
  const down = providers.filter((p) => p.status === "DOWN");
  const shaky = providers.filter((p) => p.status === "DEGRADED");
  const p = st.page;
  const disconnected = !!st.system.error;
  return (
    <div className="statusbar" role="region" aria-label="상태 표시줄" data-testid="statusbar">
      {sys ? (
        <Item tone={sys.mode === "MOCK" ? "danger" : "ok"} testId="sb-mode" title={sys.mode === "MOCK" ? "가상의 데이터입니다. 실제 투자 판단에 쓰지 마세요." : "실제 시장 데이터입니다."}>
          <b className="sb-mode">{sys.mode === "MOCK" ? "⚠ MOCK 모의" : "● LIVE 실데이터"}</b>
        </Item>
      ) : !disconnected ? <Item icon="…">연결 확인 중</Item> : null}
      {disconnected && (
        <Item tone="danger" icon="⚡" testId="sb-disconnected" title={st.system.error ?? ""}>
          <b>분석 서버 연결 끊김</b>{st.system.fetchedAt ? <span className="k">마지막 확인 {stampEt(new Date(st.system.fetchedAt).toISOString())}</span> : null}
        </Item>
      )}
      {clock && (
        <Item tone={session ? SESSION_TONE[session] : undefined} icon="◷" testId="sb-session" title="미국 주식시장 세션은 서버의 거래소 달력(휴장일·조기 마감 포함) 기준입니다.">
          <span className="k">미국장</span><b>{session ? (SESSION_KO[session] ?? session) : "확인 전"}</b>
          <span className="k">· 뉴욕</span>{clock.et}<span className="k">· 한국</span>{clock.kst}
        </Item>
      )}
      {p && p.priceTs && (
        <Item tone={p.quality && !["FRESH", "DELAYED"].includes(p.quality) ? "warn" : undefined} icon="$" testId="sb-price-time"
              title="화면에 보이는 가격이 언제 가격인지입니다(화면을 불러온 시각이 아닙니다).">
          <span className="k">{p.label} 가격</span><b>{stampEt(p.priceTs)}</b>
          <span className="k">· {ago(p.priceTs, st.nowMs)}{p.priceSession ? ` · ${SESSION_KO[p.priceSession] ?? p.priceSession}` : ""}{p.quality ? ` · ${QUALITY_INFO[p.quality]?.label ?? p.quality}` : ""}</span>
        </Item>
      )}
      {p && p.analysedAt && (
        <Item icon="⌖" testId="sb-analysed"><span className="k">분석</span><b>{stampEt(p.analysedAt)}</b><span className="k">· {ago(p.analysedAt, st.nowMs)}</span></Item>
      )}
      {sys?.mode === "LIVE" && (job?.status === "RUNNING" ? (
        <Item tone="info" icon="⏳" to="/settings?tab=data" testId="sb-sync"><span className="k">데이터 수집</span><b>{job.progress ? `${job.progress.percent}%` : "받는 중"}</b></Item>
      ) : rd && rd.recommendation_readiness === "NOT READY" ? (
        <Item tone="danger" icon="!" to="/settings?tab=data" testId="sb-sync"><span className="k">데이터 준비</span><b>안 됨</b>{job ? <span className="k">· 마지막 수집 {JOB_SHORT[job.status] ?? job.status}</span> : null}</Item>
      ) : rd ? (
        <Item tone={rd.recommendation_readiness === "FULL" ? "ok" : "warn"} icon={rd.recommendation_readiness === "FULL" ? "✓" : "!"} to="/settings?tab=data" testId="sb-sync"><span className="k">데이터 준비</span><b>{rd.recommendation_readiness === "FULL" ? "완료" : "일부"}</b></Item>
      ) : null)}
      {st.scanning || scanState?.status === "RUNNING" ? (
        <Item tone="info" icon="⟳" testId="sb-scan"><span className="k">분석 중</span><b>{scanState?.status === "RUNNING" ? `${scanState.saved}/${scanState.total}` : "시작"}</b></Item>
      ) : scanState?.status === "INTERRUPTED" ? (
        <Item tone="warn" icon="!" testId="sb-scan"><span className="k">지난 분석 중단</span><b>{scanState.saved}/{scanState.total} 저장</b></Item>
      ) : p?.analysedAt ? null : scanState ? (
        <Item icon="✓" testId="sb-scan"><span className="k">마지막 스캔</span><b>{ago(scanState.started_at, st.nowMs)}</b></Item>
      ) : st.scan.data ? <Item icon="○" testId="sb-scan"><span className="k">스캔 기록 없음</span></Item> : null}
      {sys && (sys.mode === "MOCK" ? (
        <Item icon="●" testId="sb-providers"><span className="k">공급자</span><b>모의</b></Item>
      ) : down.length ? (
        <Item tone="danger" icon="⛔" to="/settings?tab=status" testId="sb-providers" title={down.map((d) => `${d.kind}: ${d.last_error ?? ""}`).join("\n")}><span className="k">공급자</span><b>{down.length}곳 중단</b><span className="k">({down.map((d) => d.kind).join(", ")})</span></Item>
      ) : shaky.length ? (
        <Item tone="warn" icon="!" to="/settings?tab=status" testId="sb-providers"><span className="k">공급자</span><b>{shaky.length}곳 불안정</b></Item>
      ) : providers.some((x) => x.status === "HEALTHY") ? (
        <Item tone="ok" icon="✓" to="/settings?tab=status" testId="sb-providers"><span className="k">공급자</span><b>{HEALTH_KO.HEALTHY}</b></Item>
      ) : <Item icon="○" to="/settings?tab=status" testId="sb-providers"><span className="k">공급자</span><b>호출 전</b></Item>)}
      <span className="grow" />
      <span className="tools">
        <QuoteFeedStatus />
        {rr &&<Link to="/settings?tab=data" className={`readiness-link ${rr.tone}`} title={rr.help} data-testid="readiness-badge">추천 준비도 · {rr.label}</Link>}
      </span>
    </div>
  );
}
