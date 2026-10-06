import { Link } from "react-router-dom";
import { Buddy } from "./icons";
import { Err } from "./ui";
import { usePoll, useSettling } from "./useApi";
import { pct, price } from "../format";

/** 이번 달 할 일 (owner 2026-10-06: "쉽고 직관적이게") — the one routine on top of home: sell these, buy these (shares and
 * about how much), leave the rest, next change on this date. The app never orders; the risk line is always there. */
type BuyStatus = "OK" | "CASH" | "SECTOR" | "TOO_PRICEY" | "WAIT";
type Buy = { ticker: string; rank: number | null; price: number | null; shares: number | null; amount: number | null; target_amount: number | null;
  sector?: string; status?: BuyStatus; reason?: string | null };
type Sell = { ticker: string; shares: number; price: number | null; proceeds?: number | null };
type Conflict = { ticker: string; strategy: string; rule: string; basis: string };
type Todo = { ticker: string | null; kind: "RULE" | "CONFLICT" | "SELL" | "BUY"; text: string; source: string };
export type Funding = { cash: number; sell_proceeds: number; available: number; planned: number; needed_full: number | null; shortfall: number | null;
  cost_rate: number; outside_value: number; outside_share: number | null };
export type Routine = {
  state: "COMPUTING" | "PREPARING" | "WAIT" | "BLOCKED" | "ACT" | "DONE"; headline: string; when?: string; reasons?: string[]; risk?: string;
  strategy?: string; next_rebalance?: string; next_execute?: string; days_to_next?: number; rebalance_day?: string; execute_day?: string;
  buy?: Buy[]; sell?: Sell[]; keep?: string[]; outside?: string[]; conflicts?: Conflict[]; deviations?: { ticker: string; kind: string; detail: string }[];
  today?: Todo[]; funding?: Funding; rules_known?: boolean;
  account?: { total: number | null; cash: number; known: boolean; slot: number | null; unpriced: string[] }; note?: string;
  warning?: string | null; pending?: boolean; refreshing?: boolean; error?: string | null;
};

const shares = (n: number) => `${n.toLocaleString("en-US", { maximumFractionDigits: 4 })}주`;

/** What one buy line says: its shares, or why it is cut or not made (cash, sector limit, price, account total). */
function buyText(b: Buy, slotKnown: boolean): string {
  if (b.status === "SECTOR") return "업종 한도로 사지 않음";
  if (b.status === "CASH") return b.shares ? `${shares(b.shares)}만` : "현금 부족";
  if (b.status === "TOO_PRICEY") return "1주 가격이 5%보다 큼";
  if (b.status === "WAIT" || b.shares == null) return slotKnown ? "가격 없음" : "수량 계산 대기";
  return b.shares > 0 ? shares(b.shares) : "1주 가격이 5%보다 큼";
}

/** 쓸 수 있는 돈 → 계획한 매수 → 모자란 금액: the routine's buys are sized inside the account's cash, never beyond it. */
function FundingLine({ f }: { f: Funding }) {
  return (
    <div className="routine-fund" data-testid="routine-funding">
      <div><span className="muted">쓸 수 있는 돈</span> <b>{price(f.available)}</b>
        <span className="muted"> = 현금 {price(f.cash)}{f.sell_proceeds > 0 ? ` + 판 돈 ${price(f.sell_proceeds)}(비용 ${pct(f.cost_rate, 2, false)} 뺌)` : ""}</span></div>
      <div><span className="muted">계획한 매수</span> <b>{price(f.planned)}</b><span className="muted"> (비용 포함)</span></div>
      {f.shortfall != null && f.shortfall > 0.5 && (
        <div className="routine-short" data-testid="routine-shortfall">
          전략대로 다 사려면 {price(f.needed_full)} 필요 — <b>{price(f.shortfall)} 모자람</b>
          {f.outside_share != null && f.outside_share > 0.005 ? ` · 전략 밖 보유가 계좌의 ${pct(f.outside_share, 0, false)}` : ""}
        </div>
      )}
    </div>
  );
}

export function RoutineCard() {
  const r = useSettling<Routine>("/routine");
  const x = r.data;
  // the shares follow the price: an open routine asks again every minute (a held price, a fill at the broker)
  usePoll(r.reload, 60_000, !!x && ["ACT", "BLOCKED", "WAIT"].includes(x.state));
  const failed = (!x && !!r.error) || (!!x?.error && x.state === "COMPUTING");
  const mood = x?.state === "DONE" ? "happy" : x?.state === "ACT" ? "calm" : "sleepy";
  return (
    <section className="routine card enter" data-testid="routine" aria-labelledby="routine-h">
      <div className="routine-top">
        <Buddy mood={mood} className="buddy routine-buddy" />
        <div className="routine-title">
          <div className="eyebrow">이번 달 루틴 · {x?.strategy ?? "대형주 모멘텀"}</div>
          <h2 id="routine-h">{failed ? "이번 달 목록을 불러오지 못했습니다" : x ? x.headline : "불러오는 중…"}</h2>
          {x?.when && <div className="routine-when">{x.when}</div>}
        </div>
        {x?.days_to_next != null && <div className="routine-dday" title={`다음 교체 ${x.next_rebalance}`}><b>D-{x.days_to_next}</b><span>다음 교체</span></div>}
      </div>

      {failed && <Err error={x?.error ?? r.error} retry={r.reload} />}
      {x?.warning && <div className="routine-warn" role="alert" data-testid="routine-warning">{x.warning}</div>}
      {x?.today && x.today.length > 0 && (
        <div className="routine-today" data-testid="routine-today">
          <div className="t-kicker">오늘 확인할 일</div>
          <ol>{x.today.map((t, i) => <li key={i} className={`k-${t.kind.toLowerCase()}`}><span>{t.text}</span><span className="muted">{t.source}</span></li>)}</ol>
        </div>
      )}
      {x?.conflicts && x.conflicts.length > 0 && (
        <div className="routine-conflicts" data-testid="routine-conflicts">
          <div className="t-kicker">판단이 엇갈리는 종목 — 합치지 않고 둘 다 보여줍니다</div>
          {x.conflicts.map((c) => (
            <div key={c.ticker} className="routine-conflict">
              <Link to={`/stocks/${c.ticker}`}><b>{c.ticker}</b></Link>
              <div><span className="muted">전략</span> {c.strategy}</div>
              <div><span className="muted">내 규칙</span> {c.rule}</div>
              <div className="caption">{c.basis}</div>
            </div>
          ))}
        </div>
      )}
      {x?.state === "PREPARING" && <div className="routine-prep">{(x.reasons ?? []).map((t) => <div key={t}>{t}</div>)}</div>}

      {(x?.sell?.length || x?.buy?.length) ? (
        <ol className="routine-steps">
          {x.sell && x.sell.length > 0 && (
            <li>
              <div className="step-h"><span className="step-n" aria-hidden>1</span>팔기 — 이번 달 목록에서 빠진 종목</div>
              <ul className="routine-list">
                {x.sell.map((s) => <li key={s.ticker}><Link to={`/stocks/${s.ticker}`}>{s.ticker}</Link><span>{shares(s.shares)} 전부</span><span className="muted">{s.proceeds != null ? `약 ${price(s.proceeds)} 들어옴` : price(s.price)}</span></li>)}
              </ul>
            </li>
          )}
          {x.buy && x.buy.length > 0 && (
            <li>
              <div className="step-h"><span className="step-n" aria-hidden>{x.sell?.length ? 2 : 1}</span>사기 — 종목당 계좌의 5%{x.account?.slot != null ? ` (약 ${price(x.account.slot)})` : ""}</div>
              <ul className="routine-list">
                {x.buy.map((b) => (
                  <li key={b.ticker} className={b.status && b.status !== "OK" ? `cut s-${b.status.toLowerCase()}` : undefined} data-testid={`routine-buy-${b.ticker}`}>
                    <Link to={`/stocks/${b.ticker}`}>{b.ticker}</Link>
                    <span>{buyText(b, x.account?.slot != null)}</span>
                    <span className="muted">{b.amount != null && b.shares ? `약 ${price(b.amount)}` : price(b.price)}</span>
                    {b.reason && b.status !== "OK" && <span className="muted why">{b.reason}</span>}
                  </li>
                ))}
              </ul>
              {x.funding && x.account?.total != null && <FundingLine f={x.funding} />}
            </li>
          )}
        </ol>
      ) : null}

      {x?.state === "ACT" || x?.state === "DONE" || x?.state === "BLOCKED" ? (
        <div className="routine-foot">
          {x.keep && x.keep.length > 0 && <div><span className="muted">그대로 두기</span> {x.keep.join(", ")}</div>}
          {x.outside && x.outside.length > 0 && <div><span className="muted">전략 밖 보유 종목(이 루틴과 무관)</span> {x.outside.join(", ")}</div>}
          {x.deviations && x.deviations.length > 0 && (
            <div className="muted" data-testid="routine-deviations">계좌 한도 때문에 전략과 달라지는 거래 {x.deviations.length}건 — 전략의 모의기록은 규칙대로 두고, 이 계좌는 위 수량대로 합니다.</div>
          )}
          {x.rules_known === false && <div className="muted">내 규칙(손절·익절)을 불러오지 못해 판단 충돌은 확인하지 못했습니다.</div>}
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
