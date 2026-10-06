import { Link } from "react-router-dom";
import { Card, Empty } from "./ui";
import { useApi, useSettling } from "./useApi";
import { pct, price } from "../format";

/** 전략 신호 (docs/strategies/STRATEGIES.md): rule-based strategies apart from the composite score. Every signal says
 * which strategy and version, preliminary or confirmed, why (each entry condition with its numbers), when it would be
 * executed, how it exits, the time its price is from, and how far the strategy is verified — a backtest is shown as a
 * simulation, never as real performance. */
export type Validation = {
  status: "VERIFYING" | "NOT_ADOPTED" | "FORWARD"; status_ko: string; checks?: Record<string, boolean> | null;
  backtest: { cagr: number | null; max_drawdown: number | null; sharpe: number | null; exposure: number | null; trades: number | null; win_rate: number | null;
    expectancy: number | null; avg_sessions: number | null; matched_spy_cagr: number | null; stress_expectancy: number | null } | null;
};
export type Cond = { rule: string; ok: boolean; value: number | null; ref: number | null };
export type Signal = {
  strategy: string; name: string; version: string; ticker: string; kind: "confirmed" | "preliminary"; kind_ko: string; signal_day: string; close: number;
  conditions: Cond[]; execute_at: string; execute_ts?: string | null; exit: string[]; max_hold: number | null; price_ts: string | null; bars_through: string; validation: Validation;
};
export type StrategyInfo = { id: string; name: string; version: string; entry: string[]; exit: string[]; max_hold: number | null } & Validation;
export type Scan = { session: string; computed_at: string; names: number; names_without_last_bar: number; spy_up: boolean | null; confirmed: Signal[];
  preliminary: Signal[]; held: Record<string, Record<string, number>>; strategies: StrategyInfo[]; preliminary_note: string };
export type StrategiesResp = { ready: boolean; refreshing: boolean; pending?: boolean; error: string | null; computed_at: string | null; scan: Scan | null; strategies: (Validation & { id: string })[] | null };

const CHECK_KO: Record<string, string> = {
  cagr_beats_exposure_matched_spy: "연복리 > 노출 맞춘 SPY", sharpe_beats_spy_total_return: "샤프 > SPY(배당 포함)", both_halves_positive: "두 구간 모두 플러스",
  stress_expectancy_positive: "비용 2배에서도 거래당 기대수익 > 0", top10_trades_under_half: "상위 10거래 의존 50% 미만", at_least_100_trades: "거래 100회 이상",
};

export function StatusBadge({ v }: { v: Validation }) {
  const tone = v.status === "FORWARD" ? "ok" : v.status === "NOT_ADOPTED" ? "muted" : "info";
  return <span className={`st-badge st-${tone}`} data-testid="strategy-status">{v.status_ko}</span>;
}

function Conds({ c }: { c: Cond[] }) {
  return (
    <ul className="st-conds">
      {c.map((x) => (
        <li key={x.rule} className={x.ok ? "ok" : "no"}>
          <span className="mark" aria-hidden>{x.ok ? "✓" : "·"}</span>{x.rule}
          {x.value != null && <span className="num"> {x.value.toLocaleString("en-US", { maximumFractionDigits: 2 })}{x.ref != null ? ` / 기준 ${x.ref.toLocaleString("en-US", { maximumFractionDigits: 2 })}` : ""}</span>}
        </li>
      ))}
    </ul>
  );
}

/** The backtest of a strategy, labelled as what it is (a simulation with costs), never as performance earned. */
export function BacktestLine({ v }: { v: Validation }) {
  const b = v.backtest;
  if (!b) return <div className="caption">과거 검증 결과가 아직 없습니다(검증 중).</div>;
  return (
    <div className="caption st-bt">
      과거 시뮬레이션(비용 0.15%/회 차감, 실거래 아님): 연 {pct(b.cagr, 1)} · 노출 맞춘 SPY {pct(b.matched_spy_cagr, 1)} · 최대 낙폭 {pct(b.max_drawdown, 0)} ·
      거래당 기대수익 {pct(b.expectancy, 2)} (비용 2배 {pct(b.stress_expectancy, 2)}) · 승률 {pct(b.win_rate, 0, false)} · {b.trades ?? 0}회
    </div>
  );
}

/** A confirmed signal's open has passed when the instant of that open is behind now — not when its date is before
 * the UTC date (that missed the same day after 9:30 New York, and flipped at 09:00 KST). */
export function openPassed(s: Pick<Signal, "kind" | "execute_ts">, now: number = Date.now()): boolean {
  if (s.kind !== "confirmed" || !s.execute_ts) return false;
  const t = Date.parse(s.execute_ts);
  return Number.isFinite(t) && now > t;
}

function SignalRow({ s }: { s: Signal }) {
  const exec = s.execute_at.slice(0, 10);
  const passed = openPassed(s);
  return (
    <div className={`st-sig ${s.kind}`} data-testid="strategy-signal">
      <div className="st-sig-head">
        <Link to={`/stocks/${s.ticker}`}><b>{s.ticker}</b></Link>
        <span className="st-name">{s.name} <span className="caption">{s.version}</span></span>
        <span className={`st-kind ${s.kind}`}>{s.kind_ko}</span>
        <StatusBadge v={s.validation} />
      </div>
      <div className="st-sig-body">
        <div>
          <div className="t-kicker">진입 근거 · {s.signal_day} 종가 {price(s.close)}</div>
          <Conds c={s.conditions} />
        </div>
        <div className="st-sig-meta">
          <div><span className="k">실행 예정</span>{passed ? <span className="warn">체결 시점({exec}) 지남 — 기록만</span> : s.execute_at}</div>
          <div><span className="k">청산</span>{s.exit.join(" · ")}</div>
          <div><span className="k">가격 기준</span>{s.kind === "confirmed" ? `${s.bars_through} 종가(확정)` : `장중 ${s.price_ts ? new Date(s.price_ts).toLocaleTimeString("ko-KR") : ""} 가격(예비)`}</div>
          {s.validation.status === "NOT_ADOPTED" && <div className="caption">미채택 전략 — 알림을 보내지 않고 기록만 합니다.</div>}
        </div>
      </div>
    </div>
  );
}

export function StrategyBoard() {
  const r = useSettling<StrategiesResp>("/strategies");
  const f = useApi<Forward>("/strategies/forward");
  const s = r.data?.scan && Array.isArray(r.data.scan.strategies) ? r.data.scan : null;
  if (!r.data) return <Card title="전략 신호"><Empty>{r.error ? "불러오지 못했습니다." : "계산하는 중…"}</Empty></Card>;
  if (!s) return <Card title="전략 신호"><Empty>{r.data.refreshing ? "전체 종목의 일봉으로 전략을 계산하는 중입니다." : r.data.error ?? "아직 계산 결과가 없습니다."}</Empty></Card>;
  const adopted = s.confirmed.filter((x) => x.validation.status !== "NOT_ADOPTED");
  const recordOnly = s.confirmed.filter((x) => x.validation.status === "NOT_ADOPTED");
  return (
    <div className="grid">
      <div className="st-strats">
        {s.strategies.map((st) => (
          <Card key={st.id} title={`${st.name} · ${st.version}`} right={<StatusBadge v={st} />} testId={`strategy-${st.id}`}>
            <div className="st-rules">
              <div><div className="t-kicker">진입 (종가 확정 → 다음 거래일 시가)</div><ul>{st.entry.map((x) => <li key={x}>{x}</li>)}</ul></div>
              <div><div className="t-kicker">청산{st.max_hold ? ` · 최대 ${st.max_hold}거래일` : " · 기간 제한 없음"}</div><ul>{st.exit.map((x) => <li key={x}>{x}</li>)}</ul></div>
            </div>
            <BacktestLine v={st} />
            {st.checks && <div className="st-checks">{Object.entries(st.checks).map(([k, ok]) => <span key={k} className={ok ? "ok" : "no"}>{ok ? "✓" : "✗"} {CHECK_KO[k] ?? k}</span>)}</div>}
            {Object.keys(s.held[st.id] ?? {}).length > 0 && <div className="caption">보류: {Object.entries(s.held[st.id] ?? {}).map(([k, n]) => `${k} ${n}종목`).join(" · ")}</div>}
          </Card>
        ))}
      </div>
      <Card title={`확정 신호 · ${s.session} 종가`} explain={`${s.names}종목 계산 · 마지막 일봉이 없는 종목 ${s.names_without_last_bar}개 제외 · SPY 200일선 ${s.spy_up == null ? "판단 불가" : s.spy_up ? "위" : "아래"} · 계산 ${new Date(s.computed_at).toLocaleString("ko-KR")}`}>
        {adopted.length ? adopted.map((x) => <SignalRow key={`${x.strategy}${x.ticker}`} s={x} />)
          : <Empty>{recordOnly.length ? "알릴 신호가 없습니다(아래는 미채택 전략의 기록)." : "오늘 종가 기준 조건을 모두 충족한 종목이 없습니다."}</Empty>}
        {recordOnly.length > 0 && (
          <details className="disclosure" style={{ marginTop: 10 }}>
            <summary>미채택 전략의 신호 {recordOnly.length}개<span className="hint">· 검증 기준 미달 — 기록만, 매수 신호로 보지 마세요</span></summary>
            {recordOnly.map((x) => <SignalRow key={`${x.strategy}${x.ticker}`} s={x} />)}
          </details>
        )}
      </Card>
      {s.preliminary.length > 0 && (
        <Card title="예비 신호 (장중)" explain={s.preliminary_note}>
          {s.preliminary.map((x) => <SignalRow key={`p${x.strategy}${x.ticker}`} s={x} />)}
        </Card>
      )}
      <ForwardCard f={f.data} />
    </div>
  );
}

export type Forward = { started: string | null; session: string; note: string; by_strategy: Record<string, { signals: number; closed: number; open: number; win_rate: number | null; avg_ret: number | null }>;
  trades: { strategy: string; ticker: string; signal_day: string; state: string; state_ko: string; entry_day?: string; entry?: number; exit_day?: string; exit?: number; ret?: number; ret_open?: number; sessions?: number; reason?: string }[] };

function ForwardCard({ f }: { f: Forward | undefined | null }) {
  if (!f) return null;
  return (
    <Card title="전진 모의운영" explain={f.note} testId="strategy-forward">
      <div className="caption">{f.started ? `${f.started}부터 · 규칙을 고정한 뒤의 신호만` : "아직 기록된 확정 신호가 없습니다. 장 마감 뒤 신호가 나오면 여기에 쌓입니다."}</div>
      <div className="st-fwd">
        {Object.entries(f.by_strategy).map(([k, v]) => (
          <div key={k}><b>{k}</b> 신호 {v.signals} · 청산 {v.closed} · 보유 {v.open}{v.closed ? ` · 승률 ${pct(v.win_rate, 0, false)} · 평균 ${pct(v.avg_ret, 2)}` : ""}</div>
        ))}
      </div>
      {f.trades.length > 0 && (
        <table className="st-fwd-table"><thead><tr><th>전략</th><th>종목</th><th>신호일</th><th>상태</th><th className="num">모의 수익(비용 차감 · 보유 중은 진입 비용만)</th></tr></thead>
          <tbody>{f.trades.slice(0, 30).map((t) => (
            <tr key={`${t.strategy}${t.ticker}${t.signal_day}`}><td>{t.strategy}</td><td><Link to={`/stocks/${t.ticker}`}>{t.ticker}</Link></td><td>{t.signal_day}</td><td>{t.state_ko}</td>
              <td className="num">{t.ret != null ? pct(t.ret, 2) : t.ret_open != null ? <span className="caption">평가 {pct(t.ret_open, 2)}</span> : "—"}</td></tr>
          ))}</tbody></table>
      )}
    </Card>
  );
}

/** One stock: each strategy's state at the last close (signal / none with the unmet conditions / held with why). */
type TickerResp = { ticker: string; session: string; note: string; strategies: { strategy: string; name: string; version: string; exit: string[]; max_hold: number | null; validation: Validation;
  state: "SIGNAL" | "NONE" | "HELD"; reasons?: string[]; conditions?: Cond[]; execute_at?: string | null; preliminary?: boolean; bars_through?: string }[] };

export function StockStrategies({ ticker }: { ticker: string }) {
  const r = useApi<TickerResp>(`/stocks/${ticker}/strategies`, [ticker]);
  if (!r.data || !Array.isArray(r.data.strategies) || r.data.ticker !== ticker) return null;  // another name's or a malformed answer: nothing
  return (
    <Card title="전략 판정" sub explain={`${r.data.session} 종가 기준 · ${r.data.note} 종합 점수와 별개입니다.`} testId="stock-strategies">
      <div className="st-stock">
        {r.data.strategies.map((s) => (
          <div key={s.strategy} className={`st-stock-row ${s.state.toLowerCase()}`}>
            <div className="st-sig-head">
              <span className="st-name">{s.name} <span className="caption">{s.version}</span></span>
              <span className={`st-kind ${s.state === "SIGNAL" ? "confirmed" : "none"}`}>{s.state === "SIGNAL" ? "확정 신호" : s.state === "HELD" ? "보류" : "신호 없음"}</span>
              {s.preliminary && <span className="st-kind preliminary">장중 예비 신호</span>}
              <StatusBadge v={s.validation} />
            </div>
            {s.state === "HELD" ? <div className="caption warn">{(s.reasons ?? []).join(" · ")}</div> : <Conds c={s.conditions ?? []} />}
            {s.state === "SIGNAL" && <div className="caption">실행 예정 {s.execute_at} 시가 · 청산: {s.exit.join(" · ")}</div>}
          </div>
        ))}
      </div>
    </Card>
  );
}

/** Home: today's confirmed signals by strategy, with how far each strategy is verified. */
export function StrategyToday() {
  const r = useSettling<StrategiesResp>("/strategies");
  const s = r.data?.scan && Array.isArray(r.data.scan.strategies) ? r.data.scan : null;
  return (
    <Card title="오늘의 전략 신호" right={<Link to="/strategies" className="row tight">전략 보기 →</Link>} testId="strategy-today"
          explain="규칙이 명확한 전략(A 눌림목 · C 돌파)의 종가 확정 신호입니다. 종합 점수와 따로 계산합니다.">
      {!s ? <Empty>{r.data?.refreshing || !r.data ? "계산하는 중…" : "아직 계산 결과가 없습니다."}</Empty> : (
        <div className="st-today">
          {s.strategies.map((st) => {
            const n = s.confirmed.filter((x) => x.strategy === st.id).length;
            return (
              <div key={st.id} className="st-today-row">
                <span className="st-name">{st.name}</span><StatusBadge v={st} />
                <span className="num">{n ? `${n}종목` : "신호 없음"}</span>
                <span className="caption">{st.status === "NOT_ADOPTED" ? "기록만 · 알림 없음" : st.status === "VERIFYING" ? "검증 결과 대기" : "전진 모의운영 중"}</span>
              </div>
            );
          })}
          <div className="caption">{s.session} 종가 기준 · 다음 거래일 시가 실행 가정 · 실제 주문은 넣지 않습니다.</div>
        </div>
      )}
    </Card>
  );
}
