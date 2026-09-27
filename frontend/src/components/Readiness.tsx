import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { READINESS_KO } from "../i18n";
import { Card, Notice } from "./ui";
import { useApi } from "./useApi";

export interface ReadinessInfo {
  mode: string; recommendation_readiness: string; readiness_reasons: string[];
  scanner_status: string; scanner_reasons: string[]; progress: Record<string, number | null>;
  sync: { status?: string; at?: string; bar_days_remaining?: number };
  categories: { category: string; status: string; provider: string; note: string }[];
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

/** One-line banner: can today's recommendations be relied on? */
export function ReadinessBanner({ r }: { r: ReadinessInfo | undefined | null }) {
  if (!r) return null;
  const info = READINESS_KO[r.recommendation_readiness];
  if (!info) return null;
  const tone: "info" | "warn" | "neg" = info.tone === "neg" ? "neg" : info.tone === "warn" ? "warn" : "info";
  return (
    <Notice tone={tone}>
      <b>추천 준비도: {info.label}</b> — {info.help} <Link to="/health">자세히 보기</Link>
    </Notice>
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
  status: "RUNNING" | "DONE" | "FAILED" | "PAUSED" | "INTERRUPTED" | "NEEDS_SETUP"; round: number;
  bar_days_remaining?: number; fundamentals_pending?: number; errors?: string[]; missing?: string[]; started_at?: string; finished_at?: string;
}
const JOB_KO: Record<SyncJob["status"], string> = {
  RUNNING: "받는 중", DONE: "끝남", FAILED: "실패", PAUSED: "일시 정지(다시 누르면 이어서 받음)", INTERRUPTED: "중단됨(앱이 꺼짐) — 다시 누르면 이어서 받음",
  NEEDS_SETUP: "설정 필요 — 키가 없어 받을 수 없는 데이터가 있음",
};

/** LIVE: the button that fills the local data store (SEC list and filings, Polygon daily prices), with its progress.
 * Without it an installed app had no way to leave "NOT READY". */
export function SyncControl({ onChange }: { onChange?: () => void }) {
  const st = useApi<{ job: SyncJob | null }>("/sync/status");
  const [err, setErr] = useState<string | null>(null);
  const job = st.data?.job ?? null;
  const running = job?.status === "RUNNING";
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => { st.reload(); onChange?.(); }, 10000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);
  const start = async () => {
    setErr(null);
    try { await api.post("/sync/start"); st.reload(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <div className="sync-control">
      <div className="row">
        <button className="primary" disabled={running} onClick={start}>{running ? "데이터 받는 중…" : "데이터 준비 시작"}</button>
        {job && <span className="t-sub">상태: {JOB_KO[job.status] ?? job.status} · {job.round}회차
          {job.bar_days_remaining !== undefined && ` · 남은 가격 거래일 ${job.bar_days_remaining}`}
          {job.fundamentals_pending !== undefined && ` · 재무 대기 ${job.fundamentals_pending}종목`}</span>}
      </div>
      <div className="explain">무료 API 요청 한도를 지키며 받기 때문에 처음 한 번은 1시간 안팎 걸릴 수 있습니다. 앱을 켜 둔 채 기다리세요.
        중간에 꺼도 받은 데이터는 남고, 다시 누르면 이어서 받습니다. 그 뒤로는 하루 한 번 누르면 새 거래일만 받습니다.</div>
      {job?.missing?.length ? (
        <Notice tone="neg">
          <b>이 버튼만으로는 준비를 끝낼 수 없습니다.</b> 아래 항목을 <Link to="/settings">설정 화면</Link>에서 입력하고 앱을 다시 시작한 뒤 다시 누르세요.
          <ul className="list">{job.missing.map((x, i) => <li key={i}><span className="dot warn">!</span><span>{x}</span></li>)}</ul>
        </Notice>
      ) : null}
      {job?.errors?.length ? <ul className="list">{job.errors.map((x, i) => <li key={i}><span className="dot warn">!</span><span>{x}</span></li>)}</ul> : null}
      {err && <Notice tone="neg">{err}</Notice>}
    </div>
  );
}

/** Shown instead of "no opportunities" when the scanner's data is not ready. */
export function NotReady({ r, onChange }: { r: ReadinessInfo; onChange?: () => void }) {
  return (
    <Card title="데이터를 준비하는 중입니다" icon="⏳" tone="warn" explain="데이터가 준비되지 않아 추천 종목이 없는 것처럼 보일 수 있습니다. 이 목록이 비어 있어도 ‘살 종목이 없다’는 뜻이 아닙니다.">
      {r.mode === "LIVE" && <SyncControl onChange={onChange} />}
      <ProgressBars p={r.progress} />
      <ul className="list">{r.scanner_reasons.map((x, i) => <li key={i}><span className="dot warn">!</span><span>{x}</span></li>)}</ul>
    </Card>
  );
}
