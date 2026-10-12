import { useEffect } from "react";
import { Link } from "react-router-dom";
import { recordHomeShown } from "../components/Diagnostics";
import { useAccountLive } from "../components/AccountLive";
import { useEvidence } from "../components/HomeTop";
import { actionableBuys, type Routine } from "../components/RoutineCard";
import { useApi, useSettling } from "../components/useApi";
import { pct, price } from "../format";

/** Home (owner spec 2026-10-12 "미니멀 개편"): this month's routine and nothing else — ① my total and my return,
 * ② this month's to-do in one sentence with the list one tap away, ③ the method's past record in one line.
 * Everything else lives under 설정 → 고급 기능. Fits one screen at 1440px and 390px without scrolling. */

const tone = (v: number | null | undefined) => (v == null || Math.abs(v) < 1e-9 ? "" : v > 0 ? "pos" : "neg");

/** "2종목 팔고 14종목 사세요" — the month's action as one sentence; outside a change, a short sentence for the state. */
export function todoSentence(x: Routine): string {
  const buys = actionableBuys(x);
  const sells = x.sell ?? [];
  if (x.state === "ACT" && (buys.length || sells.length)) {
    if (sells.length && buys.length) return `${sells.length}종목 팔고 ${buys.length}종목 사세요`;
    return sells.length ? `${sells.length}종목 파세요` : `${buys.length}종목 사세요`;
  }
  return ({ PREPARING: "목록을 준비하는 중입니다", COMPUTING: "목록을 계산하는 중입니다", WAIT: "살 수량을 계산하는 중입니다",
    BLOCKED: "지금 계좌로는 살 수 없습니다", DONE: "이번 달 할 일 없음" } as Record<string, string>)[x.state]
    ?? ((x.headline ?? "").replace(/^이번 달 할 일:\s*/, "") || "목록을 준비하는 중입니다");
}

/** Outside a plain to-do: one line saying why, so the card is never a dead end (owner check 2026-10-12). */
export function todoWhy(x: Routine): string | null {
  if (x.state === "PREPARING" || x.state === "COMPUTING") return "가격 자료를 다 받으면 저절로 나옵니다. 목록에서 진행 상황을 볼 수 있습니다.";
  if (x.state === "BLOCKED") {
    const short = x.funding?.shortfall;
    return short != null && short > 0.5 ? `다 사려면 ${price(short)}이 모자랍니다. 목록에서 이유를 보세요.` : "현금이 모자라거나 업종 한도에 걸렸습니다. 목록에서 이유를 보세요.";
  }
  if (x.state === "WAIT" || x.state === "DONE") return x.when ?? null;
  return null;
}

/** My own stop/take-profit rules firing, or clashing with the routine: never hidden behind the minimal home. */
export const ruleAlerts = (x: Routine) => (x.today ?? []).filter((t) => t.kind === "RULE" || t.kind === "CONFLICT");

function MyNumbers() {
  const a = useAccountLive();
  const p = useApi<{ cash: number; cash_entered?: boolean }>("/portfolio");
  const t = a?.totals;
  const held = (t?.count ?? 0) > 0;
  const cashKnown = !!p.data && p.data.cash_entered !== false;
  const total = held || cashKnown ? (t?.value ?? 0) + (cashKnown ? p.data!.cash : 0) : null;
  const unpriced = held ? (t?.count ?? 0) - (t?.valued ?? 0) : 0;
  return (
    <section className="m-card" data-testid="my-status" aria-labelledby="my-status-h">
      <h2 className="m-sub" id="my-status-h">내 현황</h2>
      {total == null ? (
        <p className="m-body">계좌 정보가 없습니다. <Link to="/portfolio" data-testid="status-setup">내 계좌에서 입력하기</Link></p>
      ) : (
        <div className="m-pair">
          <div><div className="m-sub">총 자산</div><div className="m-big" data-testid="status-total">{price(total)}</div></div>
          <div><div className="m-sub">수익률</div><div className={`m-big ${tone(t?.pnl_rate)}`} data-testid="status-pnl">{held && t?.pnl_rate != null ? pct(t.pnl_rate, 1) : "—"}</div></div>
        </div>
      )}
      {unpriced > 0 && <p className="m-warn" data-testid="status-unpriced">보유 {unpriced}종목은 가격을 아직 못 받아 총 자산에서 빠져 있습니다</p>}
    </section>
  );
}

function ThisMonth({ x, failed }: { x: Routine | null; failed: boolean }) {
  const why = x ? todoWhy(x) : null;
  const alerts = x ? ruleAlerts(x) : [];
  return (
    <section className="m-card" data-testid="routine-summary" aria-labelledby="todo-h">
      <div className="m-head">
        <h2 className="m-sub" id="todo-h">이번 달 할 일</h2>
        {x?.days_to_next != null && <span className="m-sub" data-testid="todo-next">다음 교체까지 {x.days_to_next}일</span>}
      </div>
      <div className="m-big" data-testid="todo-what">{x ? todoSentence(x) : failed ? "목록을 불러오지 못했습니다" : "불러오는 중…"}</div>
      {why && <p className="m-body" data-testid="todo-why">{why}</p>}
      {alerts.length > 0 && <p className="m-warn" data-testid="todo-alerts">⚠ 내 규칙 알림 {alerts.length}건 · 목록에서 확인하세요</p>}
      <p className="m-sub">왜 이 종목들? 지난 1년 가장 많이 오른 큰 회사 20곳입니다.</p>
      <Link to="/routine" className="btn primary m-btn" data-testid="routine-list-open">{x?.state === "PREPARING" || x?.state === "COMPUTING" ? "준비 상황 보기" : "목록 보기"}</Link>
    </section>
  );
}

function Record() {
  const q = useEvidence();
  const e = q.data;
  const ok = !!e?.available && !!e.routine && !!e.spy;
  return (
    <section className="m-card m-line" data-testid="evidence" aria-labelledby="ev-h">
      <h2 className="m-sub" id="ev-h">이 방법의 성적</h2>
      <p className="m-body" data-testid="ev-line">
        {ok ? <>과거 연평균 <b className={tone(e.routine!.cagr)}>{pct(e.routine!.cagr, 1)}</b> · S&amp;P 500 {pct(e.spy!.cagr, 1)} · 최악 <b className="neg">{pct(e.routine!.max_drawdown, 0)}</b></>
          : e ? e.reason ?? "과거 성적이 아직 없습니다" : q.error ? "과거 성적을 불러오지 못했습니다" : "불러오는 중…"}
      </p>
      <Link to="/evidence" className="m-link" data-testid="ev-more">자세히</Link>
    </section>
  );
}

export default function Home() {
  const r = useSettling<Routine>("/routine");
  const x = r.data ?? null;
  useEffect(() => { if (x) recordHomeShown(); }, [x]);
  return (
    <div className="m-home" data-testid="home">
      <MyNumbers />
      <ThisMonth x={x} failed={!x && !!r.error} />
      <Record />
    </div>
  );
}
