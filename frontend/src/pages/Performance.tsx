import { Fragment, useState } from "react";
import { api } from "../api";
import { Card, Empty, Err, LineChart, Loading } from "../components/ui";
import { useApi } from "../components/useApi";
import { day, num, pct, price } from "../format";
import { REGIME_KO, ko } from "../i18n";

interface IC { factor: string; horizon: number; ic: number | null; ir: number | null; samples: number; periods: number }
interface Hit { n_independent: number; hit_rate: number | null; avg_return: number | null }
interface Account { as_of: string; starting_capital: number; cash: number; realized_pnl: number; unrealized_pnl: number; max_drawdown: number | null; stale_marks: string[]; equity: [string, number][] }
interface Perf {
  as_of: string; samples: number; independent_samples: number; mature_20d: number;
  recommendation_hit_rate: Record<string, Hit>; all_recommendations: Record<string, Hit>;
  score_buckets: Record<string, [string, number, number | null][]>;
  confidence_buckets: { bucket: string; n: number; avg_return_20d: number | null }[];
  factor_ic: IC[]; rolling_ic_total_20d: [string, number | null, number][];
  paper: Record<string, number | null>; paper_account: Account | null; paper_equity_curve: number[]; paper_skipped: number; paper_disclaimer: string;
  sector_performance: Record<string, { n: number; avg_return: number; win_rate: number }>;
  regime_performance: Record<string, { n: number; avg_return: number; win_rate: number }>;
}
export interface SegmentInfo { status: string; label: string; mature_samples: number; min_samples: number; applied: boolean }
interface Cal { runs: { id: number; status: string; candidate: string | null; created_at: string; payload?: { segments?: Record<string, SegmentInfo | string> } }[]; models: { version: string; status: string; weights: Record<string, number> }[]; production_weights: Record<string, number>; production_version: string }

const PAPER_KO: Record<string, string> = {
  trades: "거래 수", closed: "청산 완료", win_rate: "승률", average_return: "평균 수익률", median_return: "중앙값 수익률", profit_factor: "손익비(PF)",
  expectancy: "기대값(거래당)", max_drawdown: "최대 낙폭(계좌 평가 기준)", average_holding_days: "평균 보유 거래일", average_mae: "평균 최대 역행(MAE)",
  average_mfe: "평균 최대 순행(MFE)", excess_vs_benchmark: "SPY 대비 초과수익",
};
const SEG_KO: Record<string, string> = { SEGMENT_INSUFFICIENT: "표본 부족 → 전체 모델", SEGMENT_NO_SIGNAL: "신호 없음 → 전체 모델", SEGMENT_PROPOSAL: "전용 가중치 제안(검토용)" };

/** Sector / regime segments of the latest calibration run. Only the one production weight set is ever
 * used for scoring; a segment "proposal" is for review, never applied. Old runs stored a plain label. */
export function SegmentTable({ segments }: { segments: Record<string, SegmentInfo | string> }) {
  const rows = Object.entries(segments);
  if (!rows.length) return null;
  return (
    <div data-testid="segments">
      <div className="muted">업종·시장 국면별 보정: 점수 계산에는 항상 하나의 운영 가중치만 씁니다. 표본이 충분한 구간은 전용 가중치를 '제안'만 하며 운영에는 적용하지 않습니다.</div>
      <table><thead><tr><th>구간</th><th>상태</th><th>성숙 표본</th><th>운영 적용</th></tr></thead>
        <tbody>{rows.map(([k, v]) => typeof v === "string"
          ? <tr key={k}><td>{k}</td><td colSpan={3} className="muted">이전 형식 기록(검증 전 표시): {v}</td></tr>
          : <tr key={k}><td>{k}</td><td title={v.label}>{SEG_KO[v.status] ?? v.status}</td><td>{v.mature_samples} / {v.min_samples}</td><td>{v.applied ? "예" : "아니오"}</td></tr>)}</tbody></table>
    </div>
  );
}

const CAL_KO: Record<string, string> = { INSUFFICIENT_SAMPLES: "표본 부족(가중치 변경 없음)", NO_SIGNAL: "유의한 신호 없음", SHADOW_STARTED: "섀도 모델 시작", SHADOW_CONTINUES: "섀도 검증 계속", PROMOTED: "승격(운영 반영)" };

export default function Performance() {
  const [period, setPeriod] = useState("all");
  const p = useApi<Perf>(`/performance?period=${period}`, [period]);
  const c = useApi<Cal>("/calibration");
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const act = async (path: string) => { setBusy(path); setErr(null); try { await api.post(path); p.reload(); c.reload(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(""); } };
  if (p.state === "loading") return <Loading what="성과" />;
  if (!p.data) return <Err error={p.error} retry={p.reload} />;
  const x = p.data;
  const factors = [...new Set(x.factor_ic.map((f) => f.factor))];
  const acct = x.paper_account;
  return (
    <div className="grid">
      <div className="row spread">
        <h1>성과 · 모의투자(PAPER)</h1>
        <div className="row">
          {[["30d", "30일"], ["90d", "90일"], ["1y", "1년"], ["all", "전체"]].map(([k, l]) => <button key={k} disabled={k === period} onClick={() => setPeriod(k!)}>{l}</button>)}
          <button disabled={!!busy} onClick={() => act("/evaluation/run")}>결과·모의투자 갱신</button>
          <button disabled={!!busy} onClick={() => act("/calibration/run")}>가중치 보정 1회 실행</button>
        </div>
      </div>
      <Err error={err} />
      <div className="banner paper" data-testid="banner-paper">모의투자(PAPER) — 실제 주문이 아닌 시뮬레이션입니다. {x.paper_disclaimer}</div>
      <div className="grid g4">
        <Card title="표본"><div className="big-action">{x.independent_samples.toLocaleString("ko-KR")}</div><div className="muted">독립 표본(같은 날 반복 추천 1건으로 계산) · 전체 {x.samples.toLocaleString("ko-KR")} · 20일 성숙 {x.mature_20d}</div></Card>
        <Card title="20일 적중률 (매수 계열 추천)"><div className="big-action">{pct(x.recommendation_hit_rate["20"]?.hit_rate ?? null, 0, false)}</div><div className="muted">독립 표본 n={x.recommendation_hit_rate["20"]?.n_independent ?? 0}</div></Card>
        <Card title="모의 계좌"><div className="big-action">{acct ? price(acct.equity.length ? acct.equity[acct.equity.length - 1]![1] : acct.cash) : "N/A"}</div><div className="muted">{acct ? `시작 ${price(acct.starting_capital)} · 실현 ${price(acct.realized_pnl)} · 평가 ${price(acct.unrealized_pnl)} · ${day(acct.as_of)}` : "갱신 전"}</div></Card>
        <Card title="최대 낙폭 / SPY 대비"><div className="big-action">{pct(x.paper["max_drawdown"] ?? null)}</div><div className="muted">초과수익 {pct(x.paper["excess_vs_benchmark"] ?? null)} · 생략된 신호 {x.paper_skipped}</div></Card>
      </div>
      {acct && acct.stale_marks.length > 0 && <div className="warn">⚠ 일부 종목은 해당일 가격이 없어 이전 종가로 평가했습니다: {acct.stale_marks.join(", ")}</div>}
      <Card title="모의 계좌 평가금액 추이 (매일 종가 기준, 현금 + 보유 평가액)"><LineChart values={x.paper_equity_curve} /></Card>
      <div className="grid g2">
        <Card title="추천 결과 (매수 계열)">
          <table><thead><tr><th>기간</th><th>독립 표본</th><th>적중률</th><th>평균 수익률</th></tr></thead>
            <tbody>{["1", "5", "20", "60"].map((h) => { const r = x.recommendation_hit_rate[h]; return <tr key={h}><td>{h}거래일</td><td>{r?.n_independent ?? 0}</td><td>{pct(r?.hit_rate ?? null, 0, false)}</td><td>{pct(r?.avg_return ?? null, 2)}</td></tr>; })}</tbody></table>
        </Card>
        <Card title="모의투자 지표">
          <div className="kv">{Object.entries(x.paper).map(([k, v]) => <Fragment key={k}><span className="k">{PAPER_KO[k] ?? k}</span><span>{v === null ? "N/A" : k === "win_rate" ? pct(v, 1, false) : k.includes("return") || k.includes("drawdown") || k.includes("mae") || k.includes("mfe") || k.includes("excess") || k === "expectancy" ? pct(v) : k === "trades" || k === "closed" ? num(v, 0) : num(v)}</span></Fragment>)}</div>
        </Card>
        <Card title="점수 구간별 성과 (20거래일)">
          {(x.score_buckets["20"] ?? []).length ? <table><thead><tr><th>점수</th><th>n</th><th>평균 20일</th></tr></thead><tbody>{(x.score_buckets["20"] ?? []).map(([b, n, r]) => <tr key={b}><td>{b}</td><td>{n}</td><td>{pct(r, 2)}</td></tr>)}</tbody></table> : <Empty>성숙한 표본 없음</Empty>}
        </Card>
        <Card title="신뢰도 구간별 성과 (20거래일)">
          <table><thead><tr><th>신뢰도</th><th>n</th><th>평균 20일</th></tr></thead><tbody>{x.confidence_buckets.map((b) => <tr key={b.bucket}><td>{b.bucket}</td><td>{b.n}</td><td>{pct(b.avg_return_20d, 2)}</td></tr>)}</tbody></table>
        </Card>
        <Card title="섹터별 성과 (모의투자)">{Object.keys(x.sector_performance).length ? <table><tbody>{Object.entries(x.sector_performance).map(([k, v]) => <tr key={k}><td>{k}</td><td>n={v.n}</td><td>{pct(v.avg_return, 2)}</td><td>승률 {pct(v.win_rate, 0, false)}</td></tr>)}</tbody></table> : <Empty>청산된 거래 없음</Empty>}</Card>
        <Card title="시장 국면별 성과 (모의투자)">{Object.keys(x.regime_performance).length ? <table><tbody>{Object.entries(x.regime_performance).map(([k, v]) => <tr key={k}><td>{ko(REGIME_KO, k)}</td><td>n={v.n}</td><td>{pct(v.avg_return, 2)}</td><td>승률 {pct(v.win_rate, 0, false)}</td></tr>)}</tbody></table> : <Empty>청산된 거래 없음</Empty>}</Card>
      </div>
      <Card title="요인 IC(스피어만) / IR — 성숙 표본이 충분해질 때까지 N/A">
        <table><thead><tr><th>요인</th>{[5, 20, 60].map((h) => <th key={h}>IC {h}일</th>)}{[5, 20, 60].map((h) => <th key={`ir${h}`}>IR {h}일</th>)}<th>표본(20일)</th></tr></thead>
          <tbody>{factors.map((f) => { const g = (h: number) => x.factor_ic.find((i) => i.factor === f && i.horizon === h); return <tr key={f}><td>{f}</td>{[5, 20, 60].map((h) => <td key={h}>{num(g(h)?.ic ?? null, 3)}</td>)}{[5, 20, 60].map((h) => <td key={`ir${h}`}>{num(g(h)?.ir ?? null, 2)}</td>)}<td>{g(20)?.samples}</td></tr>; })}</tbody></table>
        {x.rolling_ic_total_20d.length > 0 && <div className="muted">종합 점수 롤링 IC(20일): {x.rolling_ic_total_20d.map(([d, ic, n]) => `${d} ${ic === null ? "N/A" : ic.toFixed(2)}(n=${n})`).join(" · ")}</div>}
      </Card>
      {c.data && (
        <Card title={`가중치 보정 — 운영 모델 ${c.data.production_version}`}>
          <div className="muted">가중치는 ① 겹치지 않는 독립 성숙 표본이 최소 기준 이상이고 ② 섀도(비운영) 기간의 표본 외 IC가 개선되며 ③ 적중률·하방 위험이 나빠지지 않을 때만, 회당 상대 5% 이내로 바뀝니다.</div>
          <div className="row">{Object.entries(c.data.production_weights).map(([k, v]) => <span key={k} className="pill">{k}: {v}</span>)}</div>
          {c.data.runs[0]?.payload?.segments && <SegmentTable segments={c.data.runs[0].payload.segments} />}
          {c.data.runs.length ? <table><thead><tr><th>실행</th><th>결과</th><th>후보 모델</th><th>시각</th></tr></thead><tbody>{c.data.runs.map((r) => <tr key={r.id}><td>{r.id}</td><td>{CAL_KO[r.status] ?? r.status}</td><td>{r.candidate ?? "—"}</td><td>{day(r.created_at)}</td></tr>)}</tbody></table> : <Empty>보정 실행 기록 없음</Empty>}
        </Card>
      )}
    </div>
  );
}
