import { useEffect } from "react";
import { Link } from "react-router-dom";
import { recordHomeShown } from "../components/Diagnostics";
import { useAccountLive } from "../components/AccountLive";
import { useEvidence } from "../components/HomeTop";
import { actionableBuys, type Routine } from "../components/RoutineCard";
import { useSettling } from "../components/useApi";
import { pct, price } from "../format";

/** Home (owner spec 2026-10-12 "미니멀 개편"): this month's routine and nothing else — ① my total and my return,
 * ② this month's to-do in one sentence with the list one tap away, ③ the method's past record in one line.
 * Everything else lives under 설정 → 고급 기능. Fits one screen at 1440px and 390px without scrolling. */

const tone = (v: number | null | undefined) => (v == null || Math.abs(v) < 1e-9 ? "" : v > 0 ? "pos" : "neg");

/** "2종목 팔고 14종목 사세요" — the month's action as one sentence; outside a change, the routine's own headline. */
export function todoSentence(x: Routine): string {
  const buys = actionableBuys(x);
  const sells = x.sell ?? [];
  if (x.state !== "ACT" || (!buys.length && !sells.length)) return (x.headline ?? "").replace(/^이번 달 할 일:\s*/, "") || "이번 달 목록을 준비하고 있습니다";
  if (sells.length && buys.length) return `${sells.length}종목 팔고 ${buys.length}종목 사세요`;
  return sells.length ? `${sells.length}종목 파세요` : `${buys.length}종목 사세요`;
}

function MyNumbers({ x }: { x: Routine | null }) {
  const a = useAccountLive();
  const t = a?.totals;
  const held = (t?.count ?? 0) > 0;
  const cashKnown = !!x?.account?.known;
  const total = held || cashKnown ? (t?.value ?? 0) + (cashKnown ? x?.account?.cash ?? 0 : 0) : null;
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
    </section>
  );
}

function ThisMonth({ x, failed }: { x: Routine | null; failed: boolean }) {
  return (
    <section className="m-card" data-testid="routine-summary" aria-labelledby="todo-h">
      <div className="m-head">
        <h2 className="m-sub" id="todo-h">이번 달 할 일</h2>
        {x?.days_to_next != null && <span className="m-sub" data-testid="todo-next">다음 교체까지 {x.days_to_next}일</span>}
      </div>
      <div className="m-big" data-testid="todo-what">{x ? todoSentence(x) : failed ? "목록을 불러오지 못했습니다" : "불러오는 중…"}</div>
      <p className="m-sub">왜 이 종목들? 지난 1년 가장 많이 오른 큰 회사 20곳입니다.</p>
      <Link to="/routine" className="btn primary m-btn" data-testid="routine-list-open">목록 보기</Link>
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
      <MyNumbers x={x} />
      <ThisMonth x={x} failed={!x && !!r.error} />
      <Record />
    </div>
  );
}
