import { Link } from "react-router-dom";
import { Card, Empty } from "./ui";
import { useSettling } from "./useApi";
import { pct } from "../format";

/** 대형주 모멘텀 (M-NF-1.0) — the owner's choice after PREREGISTRATION §20. Shown with its failed checks and its risks
 * next to the list, as a monthly book the owner may follow by hand: the app never orders, and the list is not a
 * probability of a rise. */
export type MomHolding = { ticker: string; sector: string; rank: number | null; momentum: number | null; since: string | null; in_top20: boolean };
export type MomBook = {
  strategy: string; name: string; version: string; entry: string[]; exit: string[]; state: "READY" | "HELD"; reasons?: string[];
  session: string; next_rebalance: string; next_execute: string; rebalance_day?: string; execute_day?: string;
  holdings: MomHolding[]; bought: string[]; sold: string[]; note?: string;
  validation: { status: "OWNER"; status_ko: string; checks: Record<string, boolean> | null; risks: string[]; spy_cagr: number | null;
    backtest: { cagr: number | null; mdd: number | null; vol: number | null; sharpe: number | null; matched: number | null; stress_cagr: number | null; halves: number[] | null; trades: number | null } | null };
  forward: { started: string | null; note: string; first_rebalance?: string; pending?: boolean; through?: string; return?: number | null;
    spy_price_return?: number | null; max_drawdown?: number | null; closed_trades?: number; open?: string[]; invested?: number;
    late_records?: number; other_data_records?: number };
};
type Resp = { ready: boolean; refreshing: boolean; pending?: boolean; error: string | null; book: MomBook | null };

function Backtest({ b }: { b: MomBook["validation"] }) {
  const x = b.backtest;
  if (!x) return null;
  return (
    <div className="caption st-bt" data-testid="mom-backtest">
      과거 시뮬레이션 2017~2026(비용 0.15%/회 차감, 실거래 아님): 연 {pct(x.cagr, 1)} · SPY {pct(b.spy_cagr, 1)} · 최대 낙폭 {pct(x.mdd, 0)} ·
      변동성 {pct(x.vol, 0, false)} · 샤프 {x.sharpe?.toFixed(2) ?? "—"} · 2017~2023 {pct(x.halves?.[0], 1)} / 2024~2026 {pct(x.halves?.[1], 1)}
    </div>
  );
}

export function MomentumBoard() {
  const r = useSettling<Resp>("/strategies/momentum");
  const b = r.data?.book && Array.isArray(r.data.book.holdings) ? r.data.book : null;
  return (
    <Card title="대형주 모멘텀 · 이번 달 목록" testId="momentum-board"
          right={b ? <span className="st-badge st-warn" data-testid="mom-status">{b.validation.status_ko}</span> : undefined}
          explain="매달 마지막 거래일 종가에 거래대금 상위 500 종목을 '12개월 전→1개월 전 수익률'로 줄 세워 상위 20개를 담고, 40위 밖으로 밀리면 뺍니다. 다음 거래일 시가 실행 가정이며 앱은 주문하지 않습니다.">
      {!b ? <Empty>{r.data?.refreshing || r.data?.pending || !r.data ? "계산하는 중…" : r.data?.error ? `계산 실패: ${r.data.error}` : "아직 계산 결과가 없습니다."}</Empty> : (
        <div className="mom">
          <div className="mom-risk" role="note" data-testid="mom-risk">
            <b>고위험 전략입니다.</b>
            <ul>{b.validation.risks.map((x) => <li key={x}>{x}</li>)}</ul>
          </div>
          <Backtest b={b.validation} />
          {b.state === "HELD" ? <Empty mood="sleepy" hint={b.reasons?.join(" · ")}>아직 순위를 낼 수 없습니다.</Empty> : (
            <>
              <div className="mom-head">
                <span><b>{b.rebalance_day}</b> 종가 순위 · <b>{b.execute_day}</b> 시가 실행 가정</span>
                <span className="caption">다음 교체: {b.next_rebalance} 종가 → {b.next_execute} 시가</span>
              </div>
              {(b.bought.length > 0 || b.sold.length > 0) && (
                <div className="mom-changes" data-testid="mom-changes">
                  {b.bought.length > 0 && <span className="chip up">새로 담음 {b.bought.join(", ")}</span>}
                  {b.sold.length > 0 && <span className="chip down">뺌 {b.sold.join(", ")}</span>}
                </div>
              )}
              <table className="mom-table" data-testid="mom-table">
                <thead><tr><th>순위</th><th>종목</th><th>업종</th><th className="r">12-1개월 수익률</th><th>담은 달</th></tr></thead>
                <tbody>
                  {b.holdings.map((h) => (
                    <tr key={h.ticker}>
                      <td className="num">{h.rank ?? "21~40"}</td>
                      <td><Link to={`/stocks/${h.ticker}`}>{h.ticker}</Link></td>
                      <td className="caption">{h.sector}</td>
                      <td className="num r">{pct(h.momentum, 0)}</td>
                      <td className="caption">{h.since ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="caption">{b.holdings.length}종목 × 5% (업종당 최대 5종목, 빈 칸은 현금). {b.note}</div>
            </>
          )}
          <div className="mom-fwd" data-testid="mom-forward">
            <b>전진 모의운영</b>{" "}
            {b.forward.started
              ? b.forward.pending ? <span>{b.forward.started} 순위 기록 · 첫 체결 대기</span>
                : <span>{b.forward.started}부터 {pct(b.forward.return ?? null, 1)} (같은 기간 SPY 가격 {pct(b.forward.spy_price_return ?? null, 1)}) · 최대 낙폭 {pct(b.forward.max_drawdown ?? null, 0)} · 보유 {b.forward.open?.length ?? 0}종목</span>
              : <span>{b.forward.first_rebalance} 월말 순위부터 기록을 시작합니다(선택 이전으로 거슬러 기록하지 않음).</span>}
            {(b.forward.late_records ?? 0) > 0 && <div className="caption">다음 거래일 시가 이후에 처음 기록된 월 {b.forward.late_records}개는 모의 체결에서 뺐습니다(앱이 꺼져 있던 달).</div>}
            {(b.forward.other_data_records ?? 0) > 0 && <div className="caption">지금 자료와 맞지 않는 기록 {b.forward.other_data_records}개(다른 데이터 모드·공급원)는 뺐습니다.</div>}
            <div className="caption">{b.forward.note}</div>
          </div>
        </div>
      )}
    </Card>
  );
}

/** Home: the book in one line — what it holds, what changed, when it next changes. */
export function MomentumToday() {
  const r = useSettling<Resp>("/strategies/momentum");
  const b = r.data?.book && Array.isArray(r.data.book.holdings) ? r.data.book : null;
  return (
    <Card title="대형주 모멘텀" testId="momentum-today" right={<Link to="/strategies" className="row tight">목록 보기 →</Link>}
          explain="직접 선택한 전략 · 백테스트 기준 미달(고위험) · 월 1회 교체 · 앱은 주문하지 않습니다.">
      {!b ? <Empty>{r.data?.refreshing || !r.data ? "계산하는 중…" : "아직 계산 결과가 없습니다."}</Empty>
        : b.state === "HELD" ? <Empty mood="sleepy" hint={b.reasons?.join(" · ")}>아직 순위를 낼 수 없습니다.</Empty> : (
        <div className="mom-today">
          <div className="mom-tickers">{b.holdings.slice(0, 20).map((h) => <Link key={h.ticker} to={`/stocks/${h.ticker}`} className="chip">{h.ticker}</Link>)}</div>
          <div className="caption">
            {b.rebalance_day} 종가 기준 {b.holdings.length}종목
            {b.bought.length ? ` · 새로 담음 ${b.bought.length}` : ""}{b.sold.length ? ` · 뺌 ${b.sold.length}` : ""} · 다음 교체 {b.next_rebalance}
          </div>
        </div>
      )}
    </Card>
  );
}
