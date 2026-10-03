import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { stamp, stampEt } from "../format";
import { READINESS_KO } from "../i18n";
import { Card, Notice, Ribbon, StatePanel } from "./ui";
import { useApi } from "./useApi";

export interface ReadinessInfo {
  mode: string; recommendation_readiness: string; readiness_reasons: string[];
  scanner_status: string; scanner_reasons: string[]; progress: Record<string, number | null>;
  sync: { status?: string; at?: string; bar_days_remaining?: number };
  categories: { category: string; status: string; provider: string; note: string }[];
  /** the counts are from ``stats_as_of`` and a recount of newer data is running (null readiness: never counted yet) */
  stats_pending?: boolean; stats_as_of?: string | null;
}

const PROGRESS_KO: Record<string, string> = {
  price_history: "가격 이력(60일+)", long_history: "가격 이력(1년)", market_cap: "시가총액", sector: "업종 정보(대형주)",
  fundamentals: "재무(대형주)", market_days: "저장된 거래일", estimate_history: "추정치 누적(90일 기준)",
};
const STATUS_KO: Record<string, string> = {
  READY: "준비됨", PARTIAL: "부분", ACCUMULATING: "누적 중", UNAVAILABLE: "제공 안 함", BLOCKED_BY_CREDENTIAL: "API 키 필요", NOT_LIVE_VERIFIED: "실제 검증 전",
};

export function statusKo(s: string): string {
  const base = s.split(" ")[0] ?? s;
  const tail = s.includes("NOT_LIVE_VERIFIED") ? " · 실제 검증 전" : "";
  return (STATUS_KO[base] ?? base) + tail;
}

/** The first reason that says something specific: a missing key or a data shortfall, not the summary line. */
export function firstReason(r: ReadinessInfo): string | undefined {
  const specific = r.readiness_reasons.filter((x) => !x.startsWith("스캐너 데이터 준비가 끝나지 않아"));
  return specific[0] ?? r.scanner_reasons[0];
}

/** One-line banner: can today's recommendations be relied on — and if not, the first actual reason. */
export function ReadinessBanner({ r, always = false }: { r: ReadinessInfo | undefined | null; always?: boolean }) {
  if (!r) return null;
  // on the working screens only when recommendations cannot be made at all (owner 2026-09-29: "제한적 참고 — 거슬려,
  // 없애"); the full state stays on the data page (``always``)
  if (!always && r.recommendation_readiness !== "NOT READY") return null;
  const info = READINESS_KO[r.recommendation_readiness];
  if (!info) return null;
  const tone: "info" | "warn" | "danger" = info.tone === "neg" ? "danger" : info.tone === "warn" ? "warn" : "info";
  const why = r.recommendation_readiness === "NOT READY" ? firstReason(r) : undefined;
  return (
    <Ribbon tone={tone} cap="추천 준비도" testId="readiness-ribbon">
      <b>추천 준비도: {info.label}</b> — {info.help} <Link to="/health">자세히 보기</Link>
      {why ? <div>이유: {why}</div> : null}
      {r.stats_pending && r.stats_as_of ? <div className="caption" data-testid="readiness-recount">저장된 데이터가 바뀌어 다시 세는 중 — 위 판정은 {stampEt(r.stats_as_of)} 기준입니다.</div> : null}
    </Ribbon>
  );
}

export function ProgressBars({ p }: { p: Record<string, number | null> }) {
  const keys = Object.keys(p).filter((k) => p[k] !== null && PROGRESS_KO[k]);
  return (
    <div>
      {keys.map((k) => {
        const v = p[k] ?? 0;
        return (
          <div className="progress" key={k}>
            <span>{PROGRESS_KO[k]}</span>
            <div className="track" role="progressbar" aria-valuenow={Math.round(v * 100)} aria-valuemin={0} aria-valuemax={100} aria-label={PROGRESS_KO[k]}>
              <div className={v >= 0.9 ? "ok" : v < 0.5 ? "low" : ""} style={{ width: `${Math.round(v * 100)}%` }} />
            </div>
            <b>{Math.round(v * 100)}%</b>
          </div>
        );
      })}
    </div>
  );
}

export interface SyncJob {
  status: "RUNNING" | "DONE" | "FAILED" | "PAUSED" | "INTERRUPTED" | "NEEDS_SETUP" | "INCOMPLETE"; round: number;
  bar_days_remaining?: number; fundamentals_pending?: number; errors?: string[]; missing?: string[]; started_at?: string; finished_at?: string;
  reasons?: string[]; failures?: string[]; retry_at?: string; progress?: SyncProgress | null; up_to_date?: boolean; target_session?: string;
}
export interface SyncProgress {
  percent: number; eta_seconds?: number | null; current?: string | null; current_for_seconds?: number | null;
  steps: Record<string, { done: number; total: number; percent: number; detail?: string }>;
}
const STEP_KO: Record<string, [string, string]> = {
  bars: ["가격 이력", "거래일"], universe: ["종목 목록(SEC)", ""], splits: ["주식분할 기록", ""], estimates: ["실적 추정치 일정", "구간"],
  shares: ["발행주식수·시가총액", ""], share_lookups: ["발행주식수 개별 확인", "종목"], profiles: ["업종 정보", "종목"], fundamentals: ["재무", "종목"],
};
const STEP_ORDER = ["bars", "universe", "splits", "estimates", "shares", "share_lookups", "profiles", "fundamentals"];

function eta(seconds: number | null | undefined): string {
  if (seconds === undefined) return "";
  if (seconds === null) return "남은 시간 계산 중(이 단계가 예상보다 오래 걸림)";
  if (seconds < 60) return "남은 시간 1분 미만";
  const m = Math.ceil(seconds / 60);
  return m >= 90 ? `남은 시간 약 ${Math.floor(m / 60)}시간 ${m % 60}분` : `남은 시간 약 ${m}분`;
}

/** The preparation's real progress: the overall share of the work, each step's counts, the item being fetched. */
export function SyncProgressView({ p, running }: { p: SyncProgress; running: boolean }) {
  const steps = STEP_ORDER.filter((k) => p.steps[k]).concat(Object.keys(p.steps).filter((k) => !STEP_ORDER.includes(k)));
  return (
    <div className="sync-progress">
      <div className="progress">
        <span>데이터 준비 {p.percent}%</span>
        <div className="track" role="progressbar" aria-label="데이터 준비 전체" aria-valuenow={Math.round(p.percent)} aria-valuemin={0} aria-valuemax={100}>
          <div className={p.percent >= 100 ? "ok" : ""} style={{ width: `${Math.min(100, p.percent)}%` }} />
        </div>
        <b>{running ? eta(p.eta_seconds) : ""}</b>
      </div>
      <ul className="list">{steps.map((k) => {
        const st = p.steps[k]!;
        const [label, unit] = STEP_KO[k] ?? [k, ""];
        const active = running && p.current === k && st.done < st.total;
        const took = active && p.current_for_seconds != null && p.current_for_seconds >= 10 ? ` · ${p.current_for_seconds >= 120 ? `${Math.floor(p.current_for_seconds / 60)}분` : `${p.current_for_seconds}초`}째` : "";
        const now = active ? ` — ${st.detail ? `${st.detail} ` : ""}받는 중${took}` : "";
        // a one-shot step (a single download) has no count worth showing: done or in progress
        const count = st.total === 1 && !unit ? (st.done >= 1 ? "완료" : "진행 중") : `${st.done}/${st.total}${unit ? ` ${unit}` : ""} (${st.percent}%)`;
        return <li key={k}><span className={`dot ${st.done >= st.total ? "info" : "warn"}`}>{st.done >= st.total ? "✓" : "…"}</span>
          <span>{label}: {count}{now}</span></li>;
      })}</ul>
      {running && p.percent < 100 && !p.steps.fundamentals && <div className="caption">재무·업종 단계의 양은 가격 이력을 받은 뒤에 정해지므로 전체 %가 그때 한 번 달라질 수 있습니다.</div>}
    </div>
  );
}
const JOB_KO: Record<SyncJob["status"], string> = {
  RUNNING: "받는 중", DONE: "끝남", FAILED: "실패", PAUSED: "일시 정지(다시 누르면 이어서 받음)", INTERRUPTED: "중단됨(앱이 꺼짐) — 다시 누르면 이어서 받음",
  NEEDS_SETUP: "설정 필요 — 키가 없어 받을 수 없는 데이터가 있음",
  INCOMPLETE: "지금 받을 수 있는 것은 다 받았지만 아직 준비 안 됨 — 아래 이유",
};

/** LIVE: the button that fills the local data store (SEC list and filings, Polygon daily prices), with its progress.
 * Without it an installed app had no way to leave "NOT READY". */
export function SyncControl({ onChange, compact = false }: { onChange?: () => void; compact?: boolean }) {
  const st = useApi<{ job: SyncJob | null; target_session?: string }>("/sync/status");
  const [err, setErr] = useState<string | null>(null);
  const [requesting, setRequesting] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const sending = useRef(false);
  const [answer, setAnswer] = useState<SyncJob | null>(null);
  useEffect(() => { setAnswer(null); }, [st.data]);
  const job = answer ?? st.data?.job ?? null;
  const targetSession = st.data?.target_session ?? job?.target_session;
  const running = job?.status === "RUNNING";
  useEffect(() => {
    if (!running) return;
    let n = 0;
    // the progress changes item by item: read it every 3 s; the readiness (heavier) every 15 s
    const id = setInterval(() => { st.reload(); if (++n % 5 === 0) onChange?.(); }, 3000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);
  const start = async () => {
    if (sending.current || running) return;
    sending.current = true;
    setRequesting(true);
    setErr(null);
    setMessage(null);
    try {
      if (uncertain) {
        const recovered = await api.get<{ job: SyncJob | null }>("/sync/status");
        setAnswer(recovered.job);
        setUncertain(false);
        setMessage(recovered.job?.status === "RUNNING" ? "이미 시작된 데이터 준비를 확인했습니다." : "작업 상태를 확인했습니다. 필요하면 다시 시작하세요.");
      } else {
        const result = await api.post<{ started: boolean; reason?: string; job?: SyncJob | null }>("/sync/start");
        setAnswer(result.job ?? null);
        setMessage(result.started ? "다운로드 작업을 시작했습니다." : result.reason ?? "시작되지 않았습니다. 작업 상태를 확인하세요.");
      }
      st.reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setUncertain(true);
      try {
        const recovered = await api.get<{ job: SyncJob | null }>("/sync/status");
        setAnswer(recovered.job);
        if (recovered.job?.status === "RUNNING") {
          setUncertain(false);
          setMessage("응답은 끊겼지만 서버에서 시작된 작업을 찾았습니다. 진행 상황을 이어서 표시합니다.");
        }
      } catch { /* keep the last result; never automatically repeat a POST */ }
    } finally { sending.current = false; setRequesting(false); }
  };
  const feedback = <div aria-live="polite" role="status">
    {requesting && <span>시작 요청 중…</span>}
    {message && <Notice tone="warn">{message}</Notice>}
    {(err || st.error) && <Notice tone="neg">{err || st.error}</Notice>}
    {uncertain && <span>서버의 작업 시작 여부를 확인해야 합니다. 다시 누르면 상태만 조회합니다.</span>}
  </div>;
  const [open, setOpen] = useState(false);
  if (compact && job?.status === "DONE" && !job.missing?.length && !open) {
    // finished: one line (when, what next) instead of a finished checklist taking half the home screen
    return (
      <div className="row" data-testid="sync-done">
        <span className="pos" aria-hidden>✓</span>
        <span>준비 완료{job.finished_at ? ` · ${stamp(job.finished_at)}` : ""}</span>
        <span className="t-sub">일봉·재무 데이터{job.target_session ? ` · 받은 기준 ${job.target_session}` : ""}{targetSession ? ` · 최신 기준 ${targetSession}` : ""}</span>
        <span style={{ flex: 1 }} />
        <button className="ghost sm" onClick={() => setOpen(true)}>자세히</button>
        <button className="sm" disabled={requesting} onClick={start}>{uncertain ? "작업 상태 확인" : requesting ? "시작 요청 중…" : "새 거래일 받기"}</button>
        {feedback}
      </div>
    );
  }
  return (
    <div className="sync-control">
      <div className="row">
        <button className="primary" disabled={running || requesting} onClick={start}>{requesting ? "시작 요청 중…" : running ? "데이터 받는 중…" : uncertain ? "작업 상태 확인" : "데이터 준비 시작"}</button>
        {job && <span className="t-sub">상태: {JOB_KO[job.status] ?? job.status} · {job.round}회차
          {job.bar_days_remaining !== undefined && ` · 남은 가격 거래일 ${job.bar_days_remaining}`}
          {job.fundamentals_pending !== undefined && ` · 재무 대기 ${job.fundamentals_pending}종목`}</span>}
      </div>
      <p className="caption">일봉 가격 이력·종목 목록·재무 자료를 받습니다. 실시간 시세 연결은 별도입니다.{targetSession && ` 최신 기준 거래일: ${targetSession}`}{job?.status === "DONE" && job.up_to_date && job.target_session === targetSession && " · 새로 받을 데이터 없이 이미 최신입니다."}</p>
      {feedback}
      {job?.progress ? <SyncProgressView p={job.progress} running={running} /> : null}
      <div className="explain">무료 API 요청 한도를 지키며 받기 때문에 처음 한 번은 1시간 안팎 걸릴 수 있습니다. 앱을 켜 둔 채 기다리세요.
        중간에 꺼도 받은 데이터는 남고, 다시 누르면 이어서 받습니다. 그 뒤로는 하루 한 번 누르면 새 거래일만 받습니다.</div>
      {job?.missing?.length ? (
        <Notice tone="neg">
          <b>이 버튼만으로는 준비를 끝낼 수 없습니다.</b> 아래 항목을 <Link to="/settings">설정 화면</Link>에서 입력하고 앱을 다시 시작한 뒤 다시 누르세요.
          <ul className="list">{job.missing.map((x, i) => <li key={i}><span className="dot warn">!</span><span>{x}</span></li>)}</ul>
        </Notice>
      ) : null}
      {job && job.status !== "RUNNING" && job.reasons?.length ? (
        <Notice tone="warn">
          <b>아직 준비되지 않은 이유</b>
          <ul className="list">{job.reasons.map((x, i) => <li key={i}><span className="dot warn">!</span><span>{x}</span></li>)}</ul>
          {job.failures?.length ? <div className="caption">실패 예: {job.failures.join(" · ")}</div> : null}
          {job.retry_at ? <div className="caption">실패한 항목을 다시 시도할 수 있는 시각: {stamp(job.retry_at)} — 그 뒤에 다시 누르세요</div> : null}
        </Notice>
      ) : null}
      {job?.errors?.length ? <ul className="list">{job.errors.map((x, i) => <li key={i}><span className="dot warn">!</span><span>{x}</span></li>)}</ul> : null}
    </div>
  );
}

/** Shown instead of "no opportunities" when the scanner's data is not ready. */
export function NotReady({ r, onChange }: { r: ReadinessInfo; onChange?: () => void }) {
  // says what is really happening: collecting only while a job runs — a fresh install has started nothing yet
  const st = useApi<{ job: SyncJob | null }>(r.mode === "LIVE" ? "/sync/status" : null);
  const job = st.data?.job ?? null;
  const running = job?.status === "RUNNING";
  const idle = r.mode === "LIVE" && st.data !== null && !running;
  return (
    <Card title={idle ? (job ? "데이터 준비가 끝나지 않았습니다" : "먼저 데이터를 준비하세요") : "데이터를 준비하는 중입니다"} icon="⏳" tone="warn" explain="데이터가 준비되지 않아 추천 종목이 없는 것처럼 보일 수 있습니다. 이 목록이 비어 있어도 ‘살 종목이 없다’는 뜻이 아닙니다.">
      <div style={{ marginBottom: 12 }}>{idle
        ? <StatePanel kind="collecting" testId="state-not-started" title={job ? "받다가 멈춘 데이터가 있습니다" : "아직 받은 데이터가 없습니다"}
            what={job ? "지난 데이터 준비가 끝까지 가지 못했습니다. 받은 데이터는 남아 있고, 아래 버튼을 다시 누르면 이어서 받습니다." : "가격 이력·시가총액·재무 자료가 아직 이 PC에 없습니다. 아래 ‘데이터 준비 시작’을 누르면 무료 공급원에서 받기 시작합니다."}
            todo="‘데이터 준비 시작’을 누르세요. 처음 한 번은 1시간 안팎 걸리고, 진행률이 여기에 표시됩니다." />
        : <StatePanel kind="collecting" />}</div>
      {r.mode === "LIVE" && <SyncControl onChange={onChange} />}
      <ProgressBars p={r.progress} />
      <ul className="list">{r.scanner_reasons.map((x, i) => <li key={i}><span className="dot warn">!</span><span>{x}</span></li>)}</ul>
    </Card>
  );
}
