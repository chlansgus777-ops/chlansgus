import { Link } from "react-router-dom";
import { READINESS_KO } from "../i18n";
import { Card, Notice } from "./ui";

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

/** Shown instead of "no opportunities" when the scanner's data is not ready. */
export function NotReady({ r }: { r: ReadinessInfo }) {
  return (
    <Card title="데이터를 준비하는 중입니다" icon="⏳" tone="warn" explain="데이터가 준비되지 않아 추천 종목이 없는 것처럼 보일 수 있습니다. 이 목록이 비어 있어도 ‘살 종목이 없다’는 뜻이 아닙니다.">
      <ProgressBars p={r.progress} />
      <ul className="list">{r.scanner_reasons.map((x, i) => <li key={i}><span className="dot warn">!</span><span>{x}</span></li>)}</ul>
    </Card>
  );
}
