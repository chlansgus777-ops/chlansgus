import { Link } from "react-router-dom";
import { Err, Loading } from "../components/ui";
import { useApi, usePoll } from "../components/useApi";
import { useLiveRows } from "../components/liveBoard";
import { useQuote } from "../quotes";
import { setViewMode } from "../components/viewMode";
import type { Plan, PlansView } from "../components/HoldingPlans";
import { money } from "../components/HoldingPlans";
import { pct, price } from "../format";
import type { OppRow } from "../types";
import { splitCandidates, type Dash } from "./Dashboard";

/** 간략 홈 (owner 2026-10-04: "주식 초보인 내가 그냥 보고 바로 판단"): three questions, each answered in one look —
 * how is the market today, what is worth buying now (at most three, each with the price to buy at, the price to give
 * up at and the target), and what to do with what I hold. Everything else is one click away in 자세히. */
export default function BriefHome() {
  const d = useApi<Dash>("/dashboard");
  const plans = useApi<PlansView>("/habits/plans");
  usePoll(plans.reload, 15_000);
  const lv = useLiveRows(d.data?.top_opportunities);
  if (d.state === "loading" && !d.data) return <Loading what="오늘 한눈에" rows={3} />;
  if (!d.data) return <Err error={d.error} retry={d.reload} />;
  const x = d.data;
  const buy = splitCandidates(lv.rows).valid.slice(0, 3);
  const mine = plans.data && Array.isArray(plans.data.plans) ? plans.data.plans : [];
  const todo = mine.filter((p) => p.action !== "HOLD" && p.action !== "NO_PRICE");
  const w = weather(x.regime?.primary);
  const today = new Date().toLocaleDateString("ko-KR", { month: "long", day: "numeric", weekday: "short" });
  const summary = [
    todo.length ? `내 종목 ${todo.length}개 행동 필요` : mine.length ? "내 종목은 그대로 두면 됨" : null,
    buy.length ? `살 만한 종목 ${buy.length}개` : "오늘은 새로 살 종목 없음",
  ].filter(Boolean).join(" · ");
  return (
    <div className="bh-page" data-testid="brief-home">
      <section className={`bh-hero tone-${w.tone}`}>
        <div className="bh-date">{today}</div>
        <h1>{summary}</h1>
        <div className="bh-weather"><span className={`wx ${w.tone}`}>{w.icon} 시장 {w.word}</span><span>{w.say}</span></div>
      </section>

      <div className="bh-cols">
        <section className="bh-sec" data-testid="brief-buy">
          <h2>지금 살 만한 종목</h2>
          {buy.length ? buy.map((r) => <BuyCard key={r.id} r={r} />) : (
            <div className="bh-empty">
              <b>오늘은 조건을 모두 통과한 종목이 없어요.</b>
              <span>억지로 사지 않는 것도 판단입니다. 가격이 내려와 조건에 들어오면 알림(종 아이콘)으로 알려드려요.</span>
              <Link to="/stocks?tab=candidates">지켜볼 종목 보기 →</Link>
            </div>
          )}
        </section>

        <section className="bh-sec" data-testid="brief-mine">
          <h2>내 종목, 지금 할 일</h2>
          {mine.length ? (
            <div className="todo-list">{[...todo, ...mine.filter((p) => !todo.includes(p))].map((p) => <TodoRow key={p.symbol} p={p} />)}</div>
          ) : (
            <div className="bh-empty">
              <b>아직 보유 종목이 없어요.</b>
              <span>토스증권을 연결하거나 포트폴리오에 직접 입력하면, 종목마다 손절·익절·추가매수 할 때를 알려드려요.</span>
              <Link to="/portfolio">보유 종목 넣기 →</Link>
            </div>
          )}
          {mine.length > 0 && <Link className="bh-more" to="/performance?view=mine#rules">손절·익절 기준 바꾸기 →</Link>}
        </section>
      </div>

      {x.upcoming_catalysts.length > 0 && (
        <section className="bh-sec slim" data-testid="brief-events">
          <h2>다가오는 일정</h2>
          <div className="ev-strip">{x.upcoming_catalysts.slice(0, 4).map((e) => (
            <div key={e.event_id} className="ev"><span className={`dd${e.days_until <= 2 ? " soon" : ""}`}>{e.days_until === 0 ? "오늘" : `D-${e.days_until}`}</span><span className="t">{plainEvent(e.title)}</span></div>
          ))}</div>
        </section>
      )}

      <div className="bh-foot">점수·근거·차트까지 모두 보려면 <button type="button" className="linkish" onClick={() => setViewMode("full")}>자세히 보기</button>로 바꾸세요. MarketLens는 주문을 넣지 않습니다.</div>
    </div>
  );
}

const VERDICT: Record<string, { word: string; tone: string }> = {
  BUY: { word: "사도 됨", tone: "pos" }, "BUY SMALL": { word: "조금만 사기", tone: "buy" }, ADD: { word: "더 사도 됨", tone: "buy" },
};

function BuyCard({ r }: { r: OppRow }) {
  const { row } = useQuote(r.ticker);
  const now = row?.price ?? r.price;
  const v = VERDICT[r.action] ?? { word: r.action_ko, tone: "" };
  const high = r.max_buy ?? null;
  const ok = now != null && high != null ? now <= high : null;
  const toStop = now != null && r.stop != null ? r.stop / now - 1 : null;
  const toTarget = now != null && r.target != null ? r.target / now - 1 : null;
  return (
    <Link to={`/stocks/${r.ticker}`} className="buy-card" data-testid={`brief-buy-${r.ticker}`}>
      <div className="bc-head">
        <div><b className="tk">{r.ticker}</b><span className="co">{r.company}</span></div>
        <span className={`verdict-chip ${v.tone}`}>{v.word}</span>
      </div>
      <div className="bc-now">지금 <b>{price(now)}</b></div>
      <dl className="bc-plan">
        <div className={ok === false ? "warn" : ""} title="이 가격 이하에서 사면 손익 계획이 맞습니다(최대 매수가)">
          <dt>사는 가격</dt><dd>{high != null ? `${price(high)} 이하` : "—"}<small>{ok == null ? "" : ok ? "지금 사도 되는 가격" : "지금은 비쌈 — 기다리기"}</small></dd></div>
        <div className="neg" title="종가가 이 가격 아래로 마감하면 판다"><dt>손절</dt><dd>{price(r.stop)}{toStop != null ? <small>{pct(toStop)}</small> : null}</dd></div>
        <div className="pos" title="1차 목표가"><dt>목표</dt><dd>{price(r.target)}{toTarget != null ? <small>{pct(toTarget)}</small> : null}</dd></div>
      </dl>
      {r.key_reason && <div className="bc-why"><span>왜?</span>{r.key_reason}</div>}
      {r.action === "BUY SMALL" && <div className="bc-note">조건은 맞지만 위험 요소가 있어 평소의 절반 정도만 권합니다.</div>}
    </Link>
  );
}

const DO: Record<Plan["action"], { word: string; tone: string }> = {
  STOP: { word: "전부 팔기", tone: "neg" }, TRAIL: { word: "남은 것 팔기", tone: "sell" }, TAKE1: { word: "절반 팔기", tone: "pos" },
  ADD: { word: "더 사도 됨", tone: "buy" }, HOLD: { word: "그대로 두기", tone: "" }, NO_PRICE: { word: "가격 확인 중", tone: "muted" },
};

function TodoRow({ p }: { p: Plan }) {
  const d = DO[p.action];
  const cur = p.currency;
  const line = p.action === "STOP" ? `손절가 ${money(p.stop, cur)} 아래로 내려왔어요`
    : p.action === "TRAIL" ? `고점에서 많이 내려왔어요 (기준 ${money(p.trail, cur)})`
    : p.action === "TAKE1" ? `목표 ${money(p.take1, cur)}에 닿았어요 — ${Math.round(p.take1_fraction * 100)}% 팔기`
    : p.action === "ADD" ? `수익 중이에요 — ${p.add_qty}주까지 더 살 수 있어요`
    : p.action === "HOLD" ? `손절 ${money(p.stop, cur)} · 익절 ${p.take1_done ? "완료" : money(p.take1, cur)}`
    : "지금 가격을 아직 못 받았어요";
  const inner = (
    <>
      <div className="tr-who"><b>{p.name && p.name !== p.symbol ? p.name : p.symbol}</b><span className={(p.pnl_pct ?? 0) >= 0 ? "pos" : "neg"}>{p.pnl_pct == null ? "" : `${p.pnl_pct >= 0 ? "+" : ""}${p.pnl_pct.toFixed(1)}%`}</span></div>
      <div className="tr-line">{line}</div>
      <span className={`do-chip ${d.tone}`}>{d.word}</span>
    </>
  );
  return p.market === "US"
    ? <Link to={`/stocks/${p.symbol}`} className={`todo-row ${d.tone}`} data-testid={`brief-todo-${p.symbol}`}>{inner}</Link>
    : <div className={`todo-row ${d.tone}`} data-testid={`brief-todo-${p.symbol}`}>{inner}</div>;
}

/** "XYZ earnings" → "XYZ 실적 발표" (the calendar's titles are the source's English) */
function plainEvent(t: string): string {
  return t.replace(/^\(MOCK\)\s*/, "").replace(/\s*earnings\b/i, " 실적 발표").replace(/\bFOMC decision\b/i, "FOMC 금리 결정")
    .replace(/\bCPI release\b/i, "CPI 물가 발표").replace(/\bPCE release\b/i, "PCE 물가 발표").replace(/\bPayrolls\b/i, "고용 지표").replace(/\bFDA decision\b/i, "FDA 승인 결정");
}

function weather(regime: string | undefined): { word: string; say: string; tone: string; icon: string } {
  const good = new Set(["Risk On", "AI Momentum", "Multiple Expansion"]);
  const bad = new Set(["Risk Off", "Credit Stress", "Growth Scare", "Inflation Shock", "Multiple Compression"]);
  if (regime && good.has(regime)) return { word: "좋음", say: "투자자들이 위험을 감수하는 분위기예요. 계획대로 사도 괜찮아요.", tone: "pos", icon: "☀" };
  if (regime && bad.has(regime)) return { word: "조심", say: "시장이 불안한 편이에요. 사더라도 작게, 손절은 꼭 지키세요.", tone: "neg", icon: "☂" };
  if (!regime || regime === "Unknown") return { word: "확인 중", say: "시장 지표를 아직 받지 못했어요.", tone: "muted", icon: "…" };
  return { word: "보통", say: "뚜렷한 방향이 없어요. 종목별로 계획을 지키면 돼요.", tone: "neutral", icon: "⛅" };
}
