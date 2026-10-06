import { Link } from "react-router-dom";
import { Buddy } from "./icons";
import { useApi } from "./useApi";
import { price } from "../format";

/** 이번 달 할 일 (owner 2026-10-06: "쉽고 직관적이게") — the one routine on top of home: sell these, buy these (shares and
 * about how much), leave the rest, next change on this date. The app never orders; the risk line is always there. */
type Buy = { ticker: string; rank: number | null; price: number | null; shares: number | null; amount: number | null; target_amount: number };
type Sell = { ticker: string; shares: number; price: number | null };
export type Routine = {
  state: "COMPUTING" | "PREPARING" | "ACT" | "DONE"; headline: string; when?: string; reasons?: string[]; risk?: string;
  strategy?: string; next_rebalance?: string; next_execute?: string; days_to_next?: number; rebalance_day?: string; execute_day?: string;
  buy?: Buy[]; sell?: Sell[]; keep?: string[]; outside?: string[];
  account?: { total: number; cash: number; known: boolean; slot: number; unpriced: string[] }; note?: string;
};

const shares = (n: number) => `${n.toLocaleString("en-US", { maximumFractionDigits: 4 })}주`;

export function RoutineCard() {
  const r = useApi<Routine>("/routine");
  const x = r.data;
  const mood = x?.state === "DONE" ? "happy" : x?.state === "ACT" ? "calm" : "sleepy";
  return (
    <section className="routine card enter" data-testid="routine" aria-labelledby="routine-h">
      <div className="routine-top">
        <Buddy mood={mood} className="buddy routine-buddy" />
        <div className="routine-title">
          <div className="eyebrow">이번 달 루틴 · {x?.strategy ?? "대형주 모멘텀"}</div>
          <h2 id="routine-h">{x ? x.headline : "불러오는 중…"}</h2>
          {x?.when && <div className="routine-when">{x.when}</div>}
        </div>
        {x?.days_to_next != null && <div className="routine-dday" title={`다음 교체 ${x.next_rebalance}`}><b>D-{x.days_to_next}</b><span>다음 교체</span></div>}
      </div>

      {x?.state === "PREPARING" && <div className="routine-prep">{(x.reasons ?? []).map((t) => <div key={t}>{t}</div>)}</div>}

      {(x?.sell?.length || x?.buy?.length) ? (
        <ol className="routine-steps">
          {x.sell && x.sell.length > 0 && (
            <li>
              <div className="step-h"><span className="step-n" aria-hidden>1</span>팔기 — 이번 달 목록에서 빠진 종목</div>
              <ul className="routine-list">
                {x.sell.map((s) => <li key={s.ticker}><Link to={`/stocks/${s.ticker}`}>{s.ticker}</Link><span>{shares(s.shares)} 전부</span><span className="muted">{price(s.price)}</span></li>)}
              </ul>
            </li>
          )}
          {x.buy && x.buy.length > 0 && (
            <li>
              <div className="step-h"><span className="step-n" aria-hidden>{x.sell?.length ? 2 : 1}</span>사기 — 종목당 계좌의 5%{x.account ? ` (약 ${price(x.account.slot)})` : ""}</div>
              <ul className="routine-list">
                {x.buy.map((b) => (
                  <li key={b.ticker}>
                    <Link to={`/stocks/${b.ticker}`}>{b.ticker}</Link>
                    <span>{b.shares != null ? (b.shares > 0 ? shares(b.shares) : "1주 가격이 5%보다 큼") : "가격 없음"}</span>
                    <span className="muted">{b.amount != null && b.shares ? `약 ${price(b.amount)}` : price(b.price)}</span>
                  </li>
                ))}
              </ul>
            </li>
          )}
        </ol>
      ) : null}

      {x?.state === "ACT" || x?.state === "DONE" ? (
        <div className="routine-foot">
          {x.keep && x.keep.length > 0 && <div><span className="muted">그대로 두기</span> {x.keep.join(", ")}</div>}
          {x.outside && x.outside.length > 0 && <div><span className="muted">전략 밖 보유 종목(이 루틴과 무관)</span> {x.outside.join(", ")}</div>}
          <div className="muted">{x.note}</div>
        </div>
      ) : null}
      <div className="routine-risk">
        <span>{x?.risk ?? "고위험 · 앱은 주문하지 않습니다"}</span>
        <Link to="/strategies">전체 목록·위험 자세히 →</Link>
      </div>
    </section>
  );
}
