import { Card, Empty, LineChart } from "./ui";
import { useApi } from "./useApi";
import { pct } from "../format";
import { COMPONENT_KO } from "../i18n";

/** 과거 검증 (PREREGISTRATION §12: shown whether or not anything was adopted) — the committed summary of the measured
 * 2016+ run, with where it came from. Never sample numbers: without the file the section says it is not ready. */
type El = { verdict: string | null; mean_ic: number | null; holm_p: number | null; weeks: number | null; coverage_mean: number | null; mean_ic_h20: number | null };
type Decision = { adopted?: boolean; why?: string; proposed?: Record<string, number> | null; candidate?: Record<string, number> | null; log?: string[] } | null;
export type BacktestSummary = {
  available: boolean; reason?: string; disclaimer?: string;
  source?: { results_sha256: string; data_sha256: string; commit: string; workflow_run: string | null; finished_at: string | null };
  window?: [string, string]; weeks?: number; weeks_used_h60?: number;
  data?: { weeks: number; verdict: string } | null;
  periods?: Record<"train" | "gap" | "holdout", { first: string | null; last: string | null; weeks: number }> | null;
  elements?: Record<string, El>;
  quintiles?: { assumption: string; rows: Record<string, { mean_ret: number | null; excess_spy: number | null; hit_rate_vs_spy: number | null }>; top_minus_bottom: { mean: number | null; ci90_block12: [number | null, number | null]; weeks: number } | null };
  strategy?: { assumption: string; start?: string; end?: string; cagr?: number | null; spy_cagr?: number | null; excess_cagr?: number | null; sharpe?: number | null; max_drawdown?: number | null; trades?: number | null; equity_weekly?: [string, number][] };
  application?: { s12: Decision; s13: Decision; final_weights: Record<string, number> | null; operating_weights: Record<string, number> | null; changed: boolean | null };
  leak_checks?: { truncated_all_equal: boolean | null; shuffle_mean_ic: number | null };
};

const SIGNAL_KO: Record<string, string> = { high52: "52주 신고가 근접도", rs_rank: "상대강도 순위", fscore: "F-스코어", ear: "실적 발표 후 추세" };

/** A component's name without the screen's "(검증 중)" note: here the validation itself is shown. */
const compName = (k: string): string => (COMPONENT_KO[k] ?? k).replace(/\(.*\)$/, "");

export function elementName(el: string): string {
  if (el === "score") return "총점";
  if (el === "sell_score") return "매도 판단 점수";
  if (el.startsWith("component.")) return compName(el.slice(10));
  if (el.startsWith("signal.")) return `${SIGNAL_KO[el.slice(7)] ?? el.slice(7)} (참고)`;
  return el;
}

function verdictTone(v: string | null): string {
  if (!v) return "muted";
  if (v.endsWith("유효")) return "pos";
  if (v.endsWith("약함")) return "warn";
  if (v.endsWith("효과 없음")) return "neg";
  return "muted";
}

const ORDER = (el: string) => (el === "score" ? 0 : el === "sell_score" ? 1 : el.startsWith("component.") ? 2 : 3);

function decisionLine(d: Decision, what: string): string {
  if (!d) return `${what}: 계산 안 함`;
  return `${what}: ${d.adopted ? "채택" : "채택 안 함"} — ${d.why ?? ""}`;
}

export function BacktestView({ b }: { b: BacktestSummary }) {
  if (!b.available) return <Card title="과거 검증 (2016년~)" testId="backtest"><Empty>{b.reason ?? "과거 검증 결과가 아직 없습니다."}</Empty></Card>;
  const els = Object.entries(b.elements ?? {}).sort((x, y) => ORDER(x[0]) - ORDER(y[0]) || x[0].localeCompare(y[0]));
  const q = b.quintiles;
  const st = b.strategy;
  const qMax = Math.max(0.0001, ...Object.values(q?.rows ?? {}).map((r) => Math.abs(r.excess_spy ?? 0)));
  const app = b.application;
  const changed = app?.changed && app.final_weights && app.operating_weights
    ? Object.keys(app.final_weights).filter((k) => app.final_weights![k] !== app.operating_weights![k]) : [];
  return (
    <Card title={`과거 검증 (${b.window?.[0]?.slice(0, 4) ?? "?"}~${b.window?.[1]?.slice(0, 4) ?? "?"} · 매주 · 앱 규칙 그대로)`} testId="backtest"
          explain={<>{b.disclaimer} 그때 공개된 자료만으로 매주 분석하고, 그 뒤의 실제 수익과 비교했습니다. 자료 {b.data?.weeks ?? "?"}주 · 판정 {b.data?.verdict ?? "?"}.</>}>
      <div className="bt-kpis">
        <div><div className="t">앱 규칙 전략 연수익</div><div className={`v ${((st?.cagr ?? 0) >= 0) ? "pos" : "neg"}`}>{pct(st?.cagr ?? null, 1)}</div><div className="s">SPY {pct(st?.spy_cagr ?? null, 1)} · 초과 {pct(st?.excess_cagr ?? null, 1)}</div></div>
        <div><div className="t">최대 낙폭</div><div className="v neg">{pct(st?.max_drawdown ?? null, 1)}</div><div className="s">샤프 {st?.sharpe == null ? "N/A" : st.sharpe.toFixed(2)} · 거래 {st?.trades ?? "N/A"}회</div></div>
        <div><div className="t">점수 상위 − 하위 5분위</div><div className={`v ${((q?.top_minus_bottom?.mean ?? 0) >= 0) ? "pos" : "neg"}`}>{pct(q?.top_minus_bottom?.mean ?? null, 2)}</div>
          <div className="s">60거래일 · 90% 구간 {pct(q?.top_minus_bottom?.ci90_block12?.[0] ?? null, 1)} ~ {pct(q?.top_minus_bottom?.ci90_block12?.[1] ?? null, 1)}</div></div>
      </div>
      {st?.equity_weekly && st.equity_weekly.length > 1 && (
        <div className="bt-chart"><div className="t-sub">앱 규칙 전략 평가금액 ($100,000 시작 · {st.assumption})</div><LineChart values={st.equity_weekly.map((p) => p[1])} height={110} /></div>
      )}
      {q && (
        <div className="bt-quint" aria-label="점수 5분위별 SPY 대비 초과 수익">
          <div className="t-sub">점수 5분위별 60거래일 SPY 대비 초과 수익 ({q.assumption})</div>
          {["1", "2", "3", "4", "5"].map((k) => {
            const v = q.rows[k]?.excess_spy ?? null;
            return (
              <div key={k} className="qrow">
                <span className="qk">{k === "5" ? "상위 5" : k === "1" ? "하위 1" : k}</span>
                <span className="qbar"><span className={v != null && v < 0 ? "neg" : "pos"} style={{ width: `${Math.min(100, (Math.abs(v ?? 0) / qMax) * 100)}%` }} /></span>
                <span className="qv">{pct(v, 2)}</span>
              </div>
            );
          })}
        </div>
      )}
      <table className="bt-els">
        <thead><tr><th>요소</th><th className="num">60일 IC</th><th className="num">20일 IC</th><th>판정</th></tr></thead>
        <tbody>{els.map(([k, e]) => (
          <tr key={k} className={k.startsWith("signal.") ? "ref" : ""}>
            <td>{elementName(k)}</td><td className="num">{e.mean_ic == null ? "N/A" : e.mean_ic.toFixed(3)}</td>
            <td className="num">{e.mean_ic_h20 == null ? "N/A" : e.mean_ic_h20.toFixed(3)}</td>
            <td><span className={`verdict ${verdictTone(e.verdict)}`}>{e.verdict ?? "N/A"}</span></td>
          </tr>))}</tbody>
      </table>
      <div className="bt-apply">
        <div>{decisionLine(app?.s12 ?? null, "가중치 조정(12절)")}</div>
        <div>{decisionLine(app?.s13 ?? null, "수익 신호 반영(13절)")}</div>
        <div className="muted">{changed.length ? `운영 가중치 변경: ${changed.map((k) => `${compName(k)} ${app!.operating_weights![k]}→${Math.round(app!.final_weights![k]! * 10) / 10}`).join(", ")}` : "운영 가중치는 바뀌지 않았습니다."}</div>
      </div>
      <div className="bt-src muted">결과 {b.source?.results_sha256.slice(0, 12)} · 자료 {b.source?.data_sha256.slice(0, 12)} · 코드 {b.source?.commit?.slice(0, 7)}{b.source?.workflow_run ? ` · 실행 #${b.source.workflow_run}` : ""}
        {b.leak_checks?.truncated_all_equal != null ? ` · 미래 자료 누수 검사 ${b.leak_checks.truncated_all_equal ? "통과" : "실패"}` : ""}</div>
    </Card>
  );
}

export function BacktestSection() {
  const r = useApi<BacktestSummary>("/performance/backtest");
  return r.data ? <BacktestView b={r.data} /> : null;
}
