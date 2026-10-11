import { Link } from "react-router-dom";
import { EvidenceTable, randomLine, span, useEvidence, worstLine, ym, type Curve, type Evidence as Ev } from "../components/HomeTop";
import { Card, Err, Loading, Term } from "../components/ui";
import { useApi } from "../components/useApi";
import { pct, price } from "../format";

/** 추천 근거 (owner spec 2026-10-11 §2-4): why the routine — its past record next to S&P 500, a dollar deposit and random
 * buying under the same rules; year by year; the worst case; how it was computed; and what the app's own record has
 * done since (losers included). Nothing here promises a return. */

/** Whole percent, with a tiny value shown as 0% rather than "−0%". */
const pct0 = (v: number | null | undefined) => (v != null && Math.abs(v) < 0.005 ? "0%" : pct(v, 0));
const tone = (v: number | null | undefined) => (v == null || Math.abs(v) < 1e-9 ? "" : v > 0 ? "pos" : "neg");

const LINES: { key: "routine" | "spy" | "random" | "deposit"; label: string; color: string }[] = [
  { key: "routine", label: "이번 달 루틴", color: "var(--up)" },
  { key: "spy", label: "S&P 500 그냥 보유", color: "var(--buy)" },
  { key: "random", label: "무작위로 고르기(가운데)", color: "var(--hold)" },
  { key: "deposit", label: "달러 예금", color: "var(--wait)" },
];

function curveOf(e: Ev, k: (typeof LINES)[number]["key"]): Curve | undefined {
  return k === "random" ? e.random?.median_run : e[k];
}

/** $1,000 put in at the start, month by month, for the four ways (each line labelled at its end). */
function GrowthChart({ e }: { e: Ev }) {
  const W = 760, H = 300, L = 56, R = 120, T = 14, B = 28;
  const series = LINES.map((l) => ({ ...l, pts: curveOf(e, l.key)?.monthly ?? [] })).filter((s) => s.pts.length > 1);
  if (!series.length) return null;
  const n = Math.max(...series.map((s) => s.pts.length));
  const max = Math.max(...series.flatMap((s) => s.pts.map((p) => p[1])));
  const x = (i: number) => L + (i / (n - 1)) * (W - L - R);
  const y = (v: number) => T + (1 - v / max) * (H - T - B);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => Math.floor((max * f) / 100) * 100);
  const years = series[0]!.pts.map((p, i) => [p[0], i] as const).filter(([m]) => m.endsWith("-01"));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="ev-chart" role="img" aria-label="처음에 $1,000을 넣었을 때 방법별 금액 변화">
      {ticks.map((t) => <g key={t}><line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className="grid" /><text x={L - 8} y={y(t) + 4} textAnchor="end" className="ax">${(t * 10).toLocaleString("en-US")}</text></g>)}
      {years.filter((_, i) => i % 2 === 0).map(([m, i]) => <text key={m} x={x(i)} y={H - 8} textAnchor="middle" className="ax">{m.slice(0, 4)}</text>)}
      {series.map((s) => (
        <g key={s.key}>
          <polyline fill="none" stroke={s.color} strokeWidth={s.key === "routine" ? 2.6 : 1.8} points={s.pts.map((p, i) => `${x(i)},${y(p[1])}`).join(" ")} />
          <text x={W - R + 6} y={y(s.pts[s.pts.length - 1]![1]) + 4} className="lbl" fill={s.color}>${(s.pts[s.pts.length - 1]![1] * 10).toLocaleString("en-US", { maximumFractionDigits: 0 })}</text>
        </g>
      ))}
    </svg>
  );
}

function YearTable({ e }: { e: Ev }) {
  const years = Object.keys(e.routine?.by_year ?? {});
  return (
    <div className="table-wrap">
      <table className="ev-years">
        <thead><tr><th>연도</th>{LINES.map((l) => <th key={l.key}>{l.label}</th>)}</tr></thead>
        <tbody>
          {years.map((y) => (
            <tr key={y}><td>{y}{y === e.period?.end.slice(0, 4) ? ` (${Number(e.period.end.slice(5, 7))}월까지)` : ""}</td>
              {LINES.map((l) => { const v = curveOf(e, l.key)?.by_year[y]; return <td key={l.key} className={v != null && Math.abs(v) < 0.005 ? "" : tone(v)}>{pct0(v)}</td>; })}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface Book { book?: { holdings?: { ticker: string; rank: number | null; momentum: number | null; sector: string }[];
  forward?: { started: string | null; first_rebalance?: string; through?: string; return?: number | null; spy_price_return?: number | null; max_drawdown?: number | null;
    closed_trades?: number; trades?: { ticker: string; entry_day: string; exit_day: string; ret: number }[]; open: string[]; note: string } } }

/** What the app's own picks did after they were made — the losers listed as plainly as the winners. */
function ForwardRecord() {
  const q = useApi<Book>("/strategies/momentum");
  const f = q.data?.book?.forward;
  if (!f) return <div className="caption">{q.error ? "기록을 불러오지 못했습니다." : "불러오는 중…"}</div>;
  if (!f.started) return (
    <div className="stack" style={{ gap: 6 }} data-testid="forward-empty">
      <div><b>아직 쌓인 기록이 없습니다.</b> {f.first_rebalance ? `${ym(f.first_rebalance)} ${Number(f.first_rebalance.slice(8, 10))}일` : "다음 달 말"} 목록부터 그 시점에 바로 저장하고, 이후 실제 가격으로 성적을 매깁니다.</div>
      <div className="caption">저장한 뒤에는 고치지 않습니다. 잘 맞은 종목과 틀린 종목을 모두 여기에 보여 드립니다.</div>
    </div>
  );
  const trades = f.trades ?? [];
  const wins = trades.filter((t) => t.ret > 0).length;
  return (
    <div className="stack" style={{ gap: 10 }} data-testid="forward-record">
      <div className="ev-fwd">
        <div><div className="t-kicker">기록 시작</div><b>{f.started}</b></div>
        <div><div className="t-kicker">그 뒤 루틴</div><b className={tone(f.return)}>{pct(f.return, 1)}</b></div>
        <div><div className="t-kicker">같은 기간 S&P 500</div><b className={tone(f.spy_price_return)}>{pct(f.spy_price_return, 1)}</b></div>
        <div><div className="t-kicker"><Term k="worst_fall">최대 하락</Term></div><b className="neg">{pct(f.max_drawdown, 1)}</b></div>
      </div>
      {trades.length > 0 ? <>
        <div className="caption">끝난 거래 {trades.length}건 중 수익 {wins}건 · 손실 {trades.length - wins}건</div>
        <div className="table-wrap"><table><thead><tr><th>종목</th><th>산 날</th><th>판 날</th><th>결과</th></tr></thead>
          <tbody>{trades.map((t) => <tr key={`${t.ticker}-${t.entry_day}`}><td><Link to={`/stocks/${t.ticker}`}>{t.ticker}</Link></td><td>{t.entry_day}</td><td>{t.exit_day}</td><td className={tone(t.ret)}>{t.ret > 0 ? "맞음 " : "틀림 "}{pct(t.ret, 1)}</td></tr>)}</tbody></table></div>
      </> : <div className="caption">아직 끝난 거래가 없습니다. 지금 들고 있는 종목: {f.open.join(", ") || "없음"}</div>}
      <div className="caption">{f.note}</div>
    </div>
  );
}

/** Why each name of this month's list, in one line. */
function Reasons() {
  const q = useApi<Book>("/strategies/momentum");
  const hs = q.data?.book?.holdings ?? [];
  if (!hs.length) return <div className="caption">{q.error ? "목록을 불러오지 못했습니다." : "이번 달 목록이 아직 없습니다."}</div>;
  return (
    <ul className="ev-reasons" data-testid="ev-reasons">
      {[...hs].sort((a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9)).map((h) => (
        <li key={h.ticker}><Link to={`/stocks/${h.ticker}`}><b>{h.ticker}</b></Link> {h.rank != null && h.momentum != null
          ? <>큰 회사 500곳 중 지난 1년 상승률 <b>{h.rank}위</b> (<span className={tone(h.momentum)}>{pct(h.momentum, 0)}</span>, 최근 1달 제외)</>
          : <span className="muted">이번 달 순위 자료가 없어 지난번 목록대로 들고 있는 종목</span>}</li>
      ))}
    </ul>
  );
}

export default function Evidence() {
  const q = useEvidence();
  const e = q.data;
  if (q.state === "loading") return <Loading what="추천 근거" rows={3} />;
  if (!e) return <Err error={q.error} retry={q.reload} />;
  const ok = e.available && e.routine && e.spy && e.deposit && e.random;
  return (
    <div className="grid">
      <div className="page-head enter"><div><h1>추천 근거</h1><div className="t-sub">이번 달 루틴을 왜 보여 드리는지, 과거에 어땠는지, 가장 나빴을 때는 어땠는지 숨김없이 보여 드립니다. 앞으로의 수익을 약속하지 않습니다.</div></div></div>
      {!ok ? <Card title="과거 성적"><div className="caption">{e.reason ?? "과거 성적이 아직 없습니다."}</div></Card> : <>
        <Card title="한눈에 보기" explain={<>{ym(e.period!.start)} ~ {ym(e.period!.end)}, 처음에 똑같은 돈을 넣었다고 계산했습니다 (<Term k="backtest">과거 시뮬레이션</Term>).</>}>
          <EvidenceTable e={e} />
          <div className="ev-points"><div>{randomLine(e)}</div></div>
        </Card>
        <Card title="$1,000을 넣었다면" explain="달마다 계좌 금액이 어떻게 변했는지입니다. 선 끝의 숫자가 마지막 금액입니다.">
          <GrowthChart e={e} />
          <div className="ev-legend">{LINES.map((l) => <span key={l.key}><i style={{ background: l.color }} />{l.label}</span>)}</div>
        </Card>
        <Card title="⚠ 가장 나빴을 때" tone="neg" testId="ev-worst">
          <div className="ev-risk" style={{ marginTop: 0 }}>{worstLine(e)}</div>
          <div className="kv" style={{ marginTop: 12 }}>
            <span className="k">1년 사이 가장 크게 잃은 때</span><span className="neg">{pct(e.routine!.worst_12m?.return, 0)} ({ym(e.routine!.worst_12m?.start)} ~ {ym(e.routine!.worst_12m?.end)})</span>
            <span className="k">S&P 500의 같은 값</span><span className="neg">{pct(e.spy!.worst_12m?.return, 0)} ({ym(e.spy!.worst_12m?.start)} ~ {ym(e.spy!.worst_12m?.end)})</span>
            <span className="k"><Term k="swing">출렁임 크기</Term> (1년 기준)</span><span>루틴 {pct(e.routine!.volatility, 0, false)} · S&P 500 {pct(e.spy!.volatility, 0, false)}</span>
            <span className="k">손해 본 해</span><span>{Object.entries(e.routine!.by_year).filter(([, v]) => v < -0.005).map(([y, v]) => `${y}년 ${pct0(v)}`).join(", ")}</span>
          </div>
          <div className="caption" style={{ marginTop: 10 }}>$10,000을 넣었다면 가장 나빴을 때 약 {price(10_000 * (1 + e.routine!.max_drawdown))}까지 줄어든 셈입니다. 이만큼 떨어져도 팔지 않고 버틸 수 있을 만큼만 넣으세요.</div>
        </Card>
        <Card title="연도별 성적" explain="해마다 실제로는 크게 오르내렸습니다. 연평균은 이것을 고르게 편 값입니다.">
          <YearTable e={e} />
        </Card>
      </>}
      <Card title="이번 달 종목은 왜?" explain="루틴은 회사 실적이 아니라 ‘지난 1년 주가가 얼마나 올랐는지’ 하나로 고릅니다. 많이 오른 주식이 한동안 더 오르는 경향을 이용하지만, 흐름이 꺾이면 함께 크게 떨어집니다.">
        <Reasons />
      </Card>
      <Card title="앱이 실제로 낸 루틴의 기록" explain="과거 계산이 아니라, 앱이 목록을 낸 뒤 실제 가격으로 매긴 성적입니다. 실제 주문이 아닌 모의 기록입니다.">
        <ForwardRecord />
      </Card>
      {ok && <Card title="계산 방법" testId="ev-method">
        <div className="kv">
          <span className="k">기간</span><span>{e.period!.start} ~ {e.period!.end} (미국 거래일, 시작 금액 $100,000)</span>
          <span className="k">고르는 대상</span><span>미국 상장 보통주 8,551개(나중에 상장폐지된 회사 포함) 중 그 달 20일 평균 거래대금 상위 500곳</span>
          <span className="k">규칙</span><span>매달 마지막 거래일 종가로 지난 12개월(최근 1개월 제외) 상승률 상위 20곳을 고르고, 다음 거래일 시가에 같은 금액(계좌의 5%)씩 삼. 업종당 최대 5종목</span>
          <span className="k"><Term k="reshuffle">종목 교체</Term> 주기</span><span>한 달에 한 번. 40위 밖으로 밀리면 다음 거래일 시가에 팜</span>
          <span className="k">수수료·비용</span><span>사고팔 때마다 {pct(e.source?.cost, 2, false)} (수수료 + 호가 차이). 2배로 잡아도 연평균 +18.8%</span>
          <span className="k">세금·환전</span><span>반영하지 않음 (한국 거주자의 해외주식 양도소득세 22%(연 250만 원 공제)와 환전 수수료는 빠져 있음)</span>
          <span className="k">배당</span><span>받은 것으로 계산. 상장폐지되면 마지막 가격의 70%만 돌려받은 것으로 계산</span>
          <span className="k">비교 기준</span><span>S&P 500: SPY를 사서 배당을 다시 넣음 · 달러 예금: {e.deposit!.series} · 무작위: 같은 500곳·같은 규칙에서 순위만 제비뽑기, {e.random!.seeds}번 반복</span>
          <span className="k">한계</span><span>같은 기간 자료로 여러 규칙을 시험해 본 뒤 고른 규칙이라 실제보다 좋게 나왔을 수 있습니다. 이익의 약 83%가 5개 종목(CVNA·APP·RKLB·ASTS·SMCI)에서 나왔습니다.</span>
          <span className="k">자료 출처</span><span>GitHub Actions backtest-data 실행 {e.source?.workflow_run} · 자료 지문 {e.source?.data_sha256.slice(0, 12)}…</span>
        </div>
        <div className="caption" style={{ marginTop: 10 }}>최대 하락 회복 기간: 루틴 {e.routine!.drawdown?.recovered ? span(e.routine!.drawdown.peak, e.routine!.drawdown.recovered) : "회복 전"}, S&P 500 {e.spy!.drawdown?.recovered ? span(e.spy!.drawdown.peak, e.spy!.drawdown.recovered) : "회복 전"}. 앱은 주문하지 않으며, 투자 판단과 책임은 본인에게 있습니다.</div>
      </Card>}
    </div>
  );
}
