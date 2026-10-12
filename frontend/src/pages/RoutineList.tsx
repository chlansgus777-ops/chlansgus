import { Link } from "react-router-dom";
import { actionableBuys, buyText, RoutineCard, shares, type Routine } from "../components/RoutineCard";
import { usePoll, useSettling } from "../components/useApi";
import { SyncControl } from "../components/Readiness";
import { useStatus } from "../components/status";
import { price } from "../format";
import { ruleAlerts, todoSentence } from "./Home";

/** 이번 달 목록 (owner spec 2026-10-12): what to sell and what to buy, with the shares. Owner check 2026-10-12 ("완전
 * 병신프로그램"): outside a plain to-do the page was one sentence and a dead end, so it also says why (data still coming
 * in, with the button that fetches it; cash short; sector limit), lists the buys it cannot make with their reason, what
 * to keep, and my own stop/take-profit alerts. The full calculation stays under 고급 기능 → 루틴 전체. */
export default function RoutineList() {
  const r = useSettling<Routine>("/routine");
  const x = r.data;
  const st = useStatus();
  const live = st?.system.data?.mode === "LIVE";
  // the shares follow the price: an open routine asks again every minute; a list being prepared, every 30 s
  usePoll(r.reload, x?.state === "PREPARING" || x?.state === "COMPUTING" ? 30_000 : 60_000, !!x && ["ACT", "BLOCKED", "WAIT", "PREPARING", "COMPUTING"].includes(x.state));
  const sells = x?.sell ?? [];
  const ok = x ? actionableBuys(x) : [];
  const okSet = new Set(ok.map((b) => b.ticker));
  const cut = (x?.buy ?? []).filter((b) => !okSet.has(b.ticker));
  const slotKnown = x?.account?.slot != null;
  const alerts = x ? ruleAlerts(x) : [];
  const keep = x?.keep ?? [];
  const outside = x?.outside ?? [];
  const short = x?.funding?.shortfall;
  const preparing = x?.state === "PREPARING" || x?.state === "COMPUTING";
  return (
    <div className="m-page" data-testid="routine-list">
      <Link to="/" className="m-link">← 홈</Link>
      <h1 className="m-big">이번 달 목록</h1>
      {x?.warning && <p className="m-warn" role="alert" data-testid="routine-warning">{x.warning}</p>}
      {!x ? <p className="m-body">{r.error ? "목록을 불러오지 못했습니다" : "불러오는 중…"}</p> : (
        <div className="m-lists">
          <p className="m-body" data-testid="routine-state"><b>{todoSentence(x)}</b>{x.when ? <><br /><span className="m-sub">{x.when}</span></> : null}</p>
          {preparing && (
            <section className="m-card" data-testid="routine-preparing">
              <h2 className="m-sub">아직 준비 중인 이유</h2>
              {x.reasons?.length ? <ul className="m-list">{x.reasons.map((t, i) => <li key={i}><span>{t}</span></li>)}</ul>
                : <p className="m-body">가격 자료를 받는 중입니다.</p>}
              {live && <SyncControl compact onChange={r.reload} />}
            </section>
          )}
          {alerts.length > 0 && (
            <section className="m-card" data-testid="routine-alerts">
              <h2 className="m-sub">⚠ 내 규칙 알림 {alerts.length}</h2>
              <ul className="m-list">{alerts.map((t, i) => <li key={i}><span>{t.text}</span></li>)}</ul>
            </section>
          )}
          {sells.length > 0 && (
            <section className="m-card" data-testid="routine-sells">
              <h2 className="m-sub">팔 종목 {sells.length}</h2>
              <ul className="m-list">{sells.map((s) => <li key={s.ticker}><span>{s.ticker}</span><span>{shares(s.shares)} 전부</span></li>)}</ul>
            </section>
          )}
          {(ok.length > 0 || cut.length > 0) && (
            <section className="m-card" data-testid="routine-buys">
              <h2 className="m-sub">살 종목 {ok.length}{cut.length ? ` · 못 사는 종목 ${cut.length}` : ""}</h2>
              {short != null && short > 0.5 && <p className="m-warn" data-testid="routine-short">목록대로 다 사려면 {price(short)}이 모자랍니다</p>}
              <ul className="m-list">
                {ok.map((b) => <li key={b.ticker} data-testid={`routine-buy-${b.ticker}`}><span>{b.ticker}</span><span>{buyText(b, slotKnown)}</span></li>)}
                {cut.map((b) => <li key={b.ticker} className="m-dim" data-testid={`routine-cut-${b.ticker}`}><span>{b.ticker}</span><span title={b.reason ?? undefined}>{buyText(b, slotKnown)}</span></li>)}
              </ul>
            </section>
          )}
          {(keep.length > 0 || outside.length > 0) && (
            <section className="m-card" data-testid="routine-keep">
              {keep.length > 0 && <p className="m-body"><span className="m-sub">그대로 둘 종목 {keep.length}</span><br />{keep.join(", ")}</p>}
              {outside.length > 0 && <p className="m-body"><span className="m-sub">목록 밖에서 가진 종목 {outside.length} · 팔지 말지는 직접 정하세요</span><br />{outside.join(", ")}</p>}
            </section>
          )}
          {!preparing && <Link to="/routine/full" className="m-link" data-testid="routine-full-link">계산 자세히 보기</Link>}
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
