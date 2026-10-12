import { Link } from "react-router-dom";
import { actionableBuys, buyText, RoutineCard, shares, type Routine } from "../components/RoutineCard";
import { usePoll, useSettling } from "../components/useApi";

/** 이번 달 목록 (owner spec 2026-10-12): what to sell and what to buy, with the shares — nothing else. The account's
 * cash, limits, holdings outside the routine and clashes with my own rules sit under 설정 → 고급 기능 → 루틴 전체. */
export default function RoutineList() {
  const r = useSettling<Routine>("/routine");
  const x = r.data;
  // the shares follow the price: an open routine asks again every minute
  usePoll(r.reload, 60_000, !!x && ["ACT", "BLOCKED", "WAIT"].includes(x.state));
  const sells = x?.sell ?? [];
  const buys = x ? actionableBuys(x) : [];
  const slotKnown = x?.account?.slot != null;
  return (
    <div className="m-page" data-testid="routine-list">
      <Link to="/" className="m-link">← 홈</Link>
      <h1 className="m-big">이번 달 목록</h1>
      {x?.warning && <p className="m-warn" role="alert" data-testid="routine-warning">{x.warning}</p>}
      {!x ? <p className="m-body">{r.error ? "목록을 불러오지 못했습니다" : "불러오는 중…"}</p>
        : !sells.length && !buys.length ? <p className="m-body">{x.headline}</p>
        : (
          <div className="m-lists">
            {sells.length > 0 && (
              <section className="m-card" data-testid="routine-sells">
                <h2 className="m-sub">팔 종목 {sells.length}</h2>
                <ul className="m-list">{sells.map((s) => <li key={s.ticker}><span>{s.ticker}</span><span>{shares(s.shares)} 전부</span></li>)}</ul>
              </section>
            )}
            {buys.length > 0 && (
              <section className="m-card" data-testid="routine-buys">
                <h2 className="m-sub">살 종목 {buys.length}</h2>
                <ul className="m-list">{buys.map((b) => (
                  <li key={b.ticker} data-testid={`routine-buy-${b.ticker}`}><span>{b.ticker}</span><span>{buyText(b, slotKnown)}</span></li>
                ))}</ul>
              </section>
            )}
          </div>
        )}
    </div>
  );
}

/** 고급 기능 → 이번 달 루틴 전체: the full card (today's checks, the account's cash and limits, holdings outside the
 * routine, clashes with my own stop and take-profit rules). */
export function RoutineFull() {
  return (
    <div className="grid">
      <div className="page-head"><div><h1>이번 달 루틴 전체</h1><div className="t-sub">현금·한도 계산, 전략 밖 보유 종목, 내 규칙과 엇갈리는 종목까지 모두 봅니다.</div></div></div>
      <RoutineCard />
    </div>
  );
}
