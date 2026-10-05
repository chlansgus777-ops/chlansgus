import { sectorKo } from "../i18n";
import { Fragment, useState } from "react";
import { BacktestSection } from "../components/Backtest";
import MyTrading from "./MyTrading";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Card, Disclosure, Empty, Err, LineChart, Loading, Ribbon, StaleData, Tabs } from "../components/ui";
import { useStatus } from "../components/status";
import { useApi } from "../components/useApi";
import { day, num, pct, price } from "../format";
import { COMPONENT_KO, REGIME_KO, ko } from "../i18n";

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

const PERIOD_KO: Record<string, string> = { "30d": "최근 30일", "90d": "최근 90일", "1y": "최근 1년", all: "전체" };
const CAL_KO: Record<string, string> = { INSUFFICIENT_SAMPLES: "표본 부족(가중치 변경 없음)", NO_SIGNAL: "유의한 신호 없음", SHADOW_STARTED: "섀도 모델 시작", SHADOW_CONTINUES: "섀도 검증 계속", PROMOTED: "승격(운영 반영)" };

/** Rule of thumb shown with the progress bar: below this many settled, independent results a hit rate is mostly
 * noise. A display threshold only — no calculation uses it. */
const SAMPLE_GOAL = 30;
type Period = "30d" | "90d" | "1y" | "all";

/** 성과: whether the app's recommendations worked (추천 성과) and why the owner's own trades did not (내 매매 진단). */
export default function Performance() {
  const [params, setParams] = useSearchParams();
  const view = params.get("view") === "mine" ? "mine" : "recs";
  return (
    <div className="grid">
      <div className="page-head enter">
        <div><h1>성과</h1><div className="t-sub">{view === "mine"
          ? "내 매수·매도 기록으로 왜 수익이 안 나는지, 무엇을 고칠지, 보유 종목은 규칙상 지금 무엇을 할지 봅니다."
          : "MarketLens의 매수 계열 추천을 그대로 따랐다면 어땠는지 — 결과가 확정된(20거래일 경과) 추천만 셉니다."}</div></div>
      </div>
      <div className="perf-switch">
        <Tabs<"recs" | "mine"> label="성과 보기" value={view} onChange={(v) => setParams(v === "mine" ? { view: "mine" } : {}, { replace: true })}
          items={[["recs", "추천 성과"], ["mine", "내 매매 진단"]]} />
      </div>
      {view === "mine" ? <MyTrading /> : <RecommendationPerformance />}
    </div>
  );
}

function RecommendationPerformance() {
  const [params, setParams] = useSearchParams();  // the period lives in the URL: back/refresh keep it
  const q = params.get("period");
  const period: Period = q === "30d" || q === "90d" || q === "1y" ? q : "all";
  const setPeriod = (v: Period) => setParams(v === "all" ? {} : { period: v }, { replace: true });
  const p = useApi<Perf>(`/performance?period=${period}`, [period]);
  const st = useStatus();
  const mode = st?.system.data?.mode;
  const c = useApi<Cal>("/calibration");
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const act = async (path: string) => {
    if (busy) return;  // one run at a time
    setBusy(path); setErr(null);
    try { await api.post(path); p.reload(); c.reload(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(""); }
  };
  if (p.state === "loading") return <Loading what="성과" />;
  if (!p.data) return <Err error={p.error} retry={p.reload} />;
  const x = p.data;
  const factors = [...new Set(x.factor_ic.map((f) => f.factor))];
  const acct = x.paper_account;
  const settled = x.recommendation_hit_rate["20"]?.n_independent ?? 0;
  const hasResults = x.mature_20d > 0 || settled > 0;
  const progress = Math.min(1, settled / SAMPLE_GOAL);
  return (
    <div className="grid">
      <div className="row" style={{ justifyContent: "flex-end", marginTop: -8 }}>
        <div className="row">
          <button disabled={!!busy} onClick={() => act("/evaluation/run")}>{busy === "/evaluation/run" ? <><span className="spin" />갱신 중…</> : "결과·모의투자 갱신"}</button>
        </div>
      </div>
      <Err error={err} />
      <StaleData error={p.error} at={p.fetchedAt} retry={p.reload} />
      {mode === "MOCK" && <Ribbon tone="danger" cap="모의 데이터 성과" testId="perf-mock">모의(MOCK) 데이터로 만든 추천과 가격으로 계산한 결과입니다. 실전 성과가 아니며, 실데이터(LIVE) 성과와 섞어 보지 마세요. {x.paper_disclaimer}</Ribbon>}
      {mode === "LIVE" && <Ribbon tone="info" cap="실데이터 기반 모의투자" testId="perf-live">실데이터(LIVE) 가격으로 추천을 따라 했을 때를 계산한 모의투자(Paper)입니다. 실제 주문·체결이 아닙니다. {x.paper_disclaimer}</Ribbon>}
      <section className="today enter" aria-label="표본 진행" data-testid="perf-progress">
        <div className="t-kicker">믿을 만한 성과가 되기까지</div>
        <div className="big" style={{ marginTop: 6 }}><span className="count-l" style={{ fontSize: 30 }}>{settled.toLocaleString("ko-KR")}</span><span className="muted" style={{ fontSize: 16 }}> / {SAMPLE_GOAL} 결과 확정 추천</span></div>
        <div className="bar" style={{ marginTop: 10, height: 8 }} role="progressbar" aria-valuemin={0} aria-valuemax={SAMPLE_GOAL} aria-valuenow={settled} aria-label="결과가 확정된 독립 표본 수">
          <div style={{ width: `${progress * 100}%` }} />
        </div>
        <p className="lead" style={{ marginTop: 10 }}>
          {settled === 0 ? "아직 결과가 확정된 추천이 없습니다. 추천 후 20거래일이 지나야 결과를 셉니다 — 지금 보이는 숫자로 모델을 판단하지 마세요."
            : settled < SAMPLE_GOAL ? `표본이 ${settled}개뿐이라 적중률·수익률은 아직 우연의 영향이 큽니다(참고 기준 ${SAMPLE_GOAL}개).`
            : "표본이 참고 기준을 넘었습니다. 그래도 과거 결과이며 앞으로의 상승 확률이 아닙니다."}
        </p>
      </section>
      <div className="row spread">
        <Tabs<Period> label="기간" value={period} onChange={setPeriod} items={[["30d", "30일"], ["90d", "90일"], ["1y", "1년"], ["all", "전체"]]} />
      </div>
      <Card title="이 성과를 읽기 전에" icon="ℹ" testId="perf-basis">
        <div className="kv">
          <span className="k">기간 · 기준일</span><span>{PERIOD_KO[period] ?? period} · {day(x.as_of)}</span>
          <span className="k">추천 표본</span><span>전체 {x.samples.toLocaleString("ko-KR")}개 · 독립 표본 {x.independent_samples.toLocaleString("ko-KR")}개(같은 날 반복 추천은 1개로 계산)</span>
          <span className="k">결과 확정(20거래일 경과)</span><span>{x.mature_20d.toLocaleString("ko-KR")}개</span>
          <span className="k">아직 결과 대기(미성숙)</span><span>{Math.max(0, x.samples - x.mature_20d).toLocaleString("ko-KR")}개 — 성과 계산에서 빠집니다</span>
          <span className="k">모의투자에서 평가하지 못한 신호</span><span>{x.paper_skipped}개(가격 없음 등)</span>
          <span className="k">비교 기준(벤치마크)</span><span>SPY(S&P 500 ETF)</span>
        </div>
      </Card>
      <BacktestSection />
      {hasResults ? (
        <>
          <div className="grid g4">
            <Card title="20일 적중률 (매수 계열)"><div className="big-action">{pct(x.recommendation_hit_rate["20"]?.hit_rate ?? null, 0, false)}</div><div className="muted">독립 표본 n={settled}</div></Card>
            <Card title="20일 평균 수익률"><div className="big-action">{pct(x.recommendation_hit_rate["20"]?.avg_return ?? null, 2)}</div><div className="muted">같은 기간 SPY 대비 초과 {pct(x.paper["excess_vs_benchmark"] ?? null)}</div></Card>
            <Card title="모의 계좌"><div className="big-action">{acct ? price(acct.equity.length ? acct.equity[acct.equity.length - 1]![1] : acct.cash) : "갱신 전"}</div><div className="muted">{acct ? `시작 ${price(acct.starting_capital)} · 실현 ${price(acct.realized_pnl)} · 평가 ${price(acct.unrealized_pnl)} · ${day(acct.as_of)}` : "‘결과·모의투자 갱신’을 누르세요"}</div></Card>
            <Card title="최대 낙폭"><div className="big-action">{pct(x.paper["max_drawdown"] ?? null)}</div><div className="muted">계좌 평가액 기준 · 생략된 신호 {x.paper_skipped}</div></Card>
          </div>
          {acct && acct.stale_marks.length > 0 && <div className="warn">⚠ 일부 종목은 해당일 가격이 없어 이전 종가로 평가했습니다: {acct.stale_marks.join(", ")}</div>}
          <Card title="모의 계좌 평가금액 추이 (매일 종가 기준, 현금 + 보유 평가액)"><LineChart values={x.paper_equity_curve} /></Card>
        </>
      ) : null}
      <Disclosure title="세부 통계" hint="기간별 적중률 · 점수·신뢰도 구간 · 업종·국면별" open={hasResults}>
        <div className="grid g2">
          <Card title="추천 결과 (매수 계열)">
            <table><thead><tr><th>기간</th><th className="num">독립 표본</th><th className="num">적중률</th><th className="num">평균 수익률</th></tr></thead>
              <tbody>{["1", "5", "20", "60"].map((h) => { const r = x.recommendation_hit_rate[h]; return <tr key={h}><td>{h}거래일</td><td className="num">{r?.n_independent ?? 0}</td><td className="num">{pct(r?.hit_rate ?? null, 0, false)}</td><td className="num">{pct(r?.avg_return ?? null, 2)}</td></tr>; })}</tbody></table>
          </Card>
          <Card title="모의투자 지표">
            <div className="kv">{Object.entries(x.paper).map(([k, v]) => <Fragment key={k}><span className="k">{PAPER_KO[k] ?? k}</span><span>{v === null ? "—" : k === "win_rate" ? pct(v, 1, false) : k.includes("return") || k.includes("drawdown") || k.includes("mae") || k.includes("mfe") || k.includes("excess") || k === "expectancy" ? pct(v) : k === "trades" || k === "closed" ? num(v, 0) : num(v)}</span></Fragment>)}</div>
            {!Object.keys(x.paper).length && <Empty>모의 거래가 아직 없습니다.</Empty>}
          </Card>
          <Card title="점수 구간별 성과 (20거래일)">
            {(x.score_buckets["20"] ?? []).length ? <table><thead><tr><th>점수</th><th className="num">n</th><th className="num">평균 20일</th></tr></thead><tbody>{(x.score_buckets["20"] ?? []).map(([b, n, r]) => <tr key={b}><td>{b}</td><td className="num">{n}</td><td className="num">{pct(r, 2)}</td></tr>)}</tbody></table> : <Empty>결과가 확정된 표본 없음</Empty>}
          </Card>
          <Card title="신뢰도 구간별 성과 (20거래일)">
            {x.confidence_buckets.some((b) => b.n > 0) ? <table><thead><tr><th>신뢰도</th><th className="num">n</th><th className="num">평균 20일</th></tr></thead><tbody>{x.confidence_buckets.map((b) => <tr key={b.bucket}><td>{b.bucket}</td><td className="num">{b.n}</td><td className="num">{pct(b.avg_return_20d, 2)}</td></tr>)}</tbody></table> : <Empty>결과가 확정된 표본 없음</Empty>}
          </Card>
          <Card title="업종별 성과 (모의투자)">{Object.keys(x.sector_performance).length ? <table><tbody>{Object.entries(x.sector_performance).map(([k, v]) => <tr key={k}><td>{sectorKo(k)}</td><td className="num">n={v.n}</td><td className="num">{pct(v.avg_return, 2)}</td><td className="num">승률 {pct(v.win_rate, 0, false)}</td></tr>)}</tbody></table> : <Empty>청산된 거래 없음</Empty>}</Card>
          <Card title="시장 국면별 성과 (모의투자)">{Object.keys(x.regime_performance).length ? <table><tbody>{Object.entries(x.regime_performance).map(([k, v]) => <tr key={k}><td>{ko(REGIME_KO, k)}</td><td className="num">n={v.n}</td><td className="num">{pct(v.avg_return, 2)}</td><td className="num">승률 {pct(v.win_rate, 0, false)}</td></tr>)}</tbody></table> : <Empty>청산된 거래 없음</Empty>}</Card>
        </div>
      </Disclosure>
      <Disclosure title="연구용 · 요인 IC와 가중치 보정" hint="모델이 스스로 가중치를 바꾸는 조건과 기록">
        <Card title="요인 IC(스피어만) / IR — 성숙 표본이 충분해질 때까지 —">
          {factors.length ? <div className="scroll"><table><thead><tr><th>요인</th>{[5, 20, 60].map((h) => <th key={h} className="num">IC {h}일</th>)}{[5, 20, 60].map((h) => <th key={`ir${h}`} className="num">IR {h}일</th>)}<th className="num">표본(20일)</th></tr></thead>
            <tbody>{factors.map((f) => { const g = (h: number) => x.factor_ic.find((i) => i.factor === f && i.horizon === h); return <tr key={f}><td title={f}>{COMPONENT_KO[f] ?? f}</td>{[5, 20, 60].map((h) => <td key={h} className="num">{num(g(h)?.ic ?? null, 3)}</td>)}{[5, 20, 60].map((h) => <td key={`ir${h}`} className="num">{num(g(h)?.ir ?? null, 2)}</td>)}<td className="num">{g(20)?.samples}</td></tr>; })}</tbody></table></div> : <Empty>요인 표본이 아직 없습니다.</Empty>}
          {x.rolling_ic_total_20d.length > 0 && <div className="muted">종합 점수 롤링 IC(20일): {x.rolling_ic_total_20d.map(([d, ic, n]) => `${d} ${ic === null ? "N/A" : ic.toFixed(2)}(n=${n})`).join(" · ")}</div>}
        </Card>
        {c.data && (
          <Card title={`가중치 보정 — 운영 모델 ${c.data.production_version}`} right={<button className="sm" disabled={!!busy} onClick={() => act("/calibration/run")}>{busy === "/calibration/run" ? "보정 중…" : "보정 1회 실행"}</button>}>
            <div className="muted">가중치는 ① 겹치지 않는 독립 성숙 표본이 최소 기준 이상이고 ② 섀도(비운영) 기간의 표본 외 IC가 개선되며 ③ 적중률·하방 위험이 나빠지지 않을 때만, 회당 상대 5% 이내로 바뀝니다.</div>
            <div className="row">{Object.entries(c.data.production_weights).map(([k, v]) => <span key={k} className="pill" title={k}>{COMPONENT_KO[k] ?? k}: {v}</span>)}</div>
            {c.data.runs[0]?.payload?.segments && <SegmentTable segments={c.data.runs[0].payload.segments} />}
            {c.data.runs.length ? <table><thead><tr><th>실행</th><th>결과</th><th>후보 모델</th><th>시각</th></tr></thead><tbody>{c.data.runs.map((r) => <tr key={r.id}><td>{r.id}</td><td>{CAL_KO[r.status] ?? r.status}</td><td>{r.candidate ?? "—"}</td><td>{day(r.created_at)}</td></tr>)}</tbody></table> : <Empty>보정 실행 기록 없음</Empty>}
          </Card>
        )}
      </Disclosure>
    </div>
  );
}
