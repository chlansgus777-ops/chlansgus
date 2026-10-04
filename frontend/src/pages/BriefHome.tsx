import { Link } from "react-router-dom";
import { Err, Loading } from "../components/ui";
import { useApi, usePoll } from "../components/useApi";
import { useLiveRows } from "../components/liveBoard";
import { useQuote } from "../quotes";
import { setViewMode } from "../components/viewMode";
import { PriceLadder, money, planLevels, type Level, type Plan, type PlansView } from "../components/HoldingPlans";
import { pct, price } from "../format";
import type { OppRow } from "../types";
import { splitCandidates, type Dash } from "./Dashboard";

/** 간략 홈 (owner 2026-10-04: "보고 바로 판단", then "텅 빈 부분 싫고 직관성이 없어"): one to-do list first — what to sell,
 * what to buy, in that order — then the buy ideas and the holdings as cards that DRAW their plan (stop · buy price ·
 * target · now on one bar), and the market as a temperature gauge. Every number is the backend's; 자세히 has the rest. */
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
  const acts = mine.filter((p) => p.action !== "HOLD" && p.action !== "NO_PRICE");
  const w = weather(x.regime?.primary);
  const today = new Date().toLocaleDateString("ko-KR", { month: "long", day: "numeric", weekday: "long" });
  const nTodo = acts.length + buy.length;
  const pnl = mine.length ? mine.reduce((s, p) => s + (p.pnl_pct ?? 0), 0) / mine.length : null;
  return (
    <div className="bh-page" data-testid="brief-home">
      {/* ① 오늘: the headline, three numbers, the market gauge */}
      <section className={`bh-hero tone-${w.tone}`}>
        <div className="bh-hero-main">
          <div className="bh-date">{today}</div>
          <h1>{nTodo ? <>오늘 할 일 <em>{nTodo}가지</em></> : "오늘은 할 일이 없어요"}</h1>
          <p className="bh-lead">{nTodo ? [acts.length ? `내 종목 ${acts.length}개 정리` : null, buy.length ? `새로 살 만한 종목 ${buy.length}개` : null].filter(Boolean).join(" · ")
            : mine.length ? "가진 종목은 모두 계획 안에 있어요. 그대로 두면 됩니다." : "조건을 통과한 종목이 나오면 알림으로 알려드려요."}</p>
          <div className="bh-stats">
            <Stat k="보유 종목" v={`${mine.length}개`} />
            <Stat k="평균 수익률" v={pnl == null ? "—" : `${pnl >= 0 ? "+" : ""}${pnl.toFixed(1)}%`} tone={pnl == null ? "" : pnl >= 0 ? "pos" : "neg"} />
            <Stat k="정리할 종목" v={`${acts.length}개`} tone={acts.length ? "warn" : ""} />
            <Stat k="살 만한 종목" v={`${buy.length}개`} tone={buy.length ? "pos" : ""} />
          </div>
        </div>
        <Gauge w={w} />
      </section>

      {/* ② 오늘 할 일: sells first (they protect money), then buys */}
      <section className="bh-sec" data-testid="brief-feed">
        <div className="bh-sec-head"><h2>오늘 할 일</h2><span className="caption">위에서부터 순서대로</span></div>
        {nTodo ? (
          <ol className="feed">
            {acts.map((p) => <FeedSell key={p.symbol} p={p} />)}
            {buy.map((r) => <FeedBuy key={r.id} r={r} />)}
          </ol>
        ) : (
          <div className="feed-empty"><span className="ok">✓</span><div><b>지금은 할 일이 없어요</b><span>가격이 손절·익절·매수 가격에 닿으면 종 아이콘으로 바로 알려드려요.</span></div></div>
        )}
      </section>

      {/* ③ 살 만한 종목: each card draws its plan */}
      <section className="bh-sec" data-testid="brief-buy">
        <div className="bh-sec-head"><h2>살 만한 종목</h2><Link to="/stocks?tab=candidates" className="caption">전체 후보 →</Link></div>
        {buy.length ? <div className="card-grid">{buy.map((r) => <BuyCard key={r.id} r={r} />)}</div> : (
          <div className="feed-empty"><span className="wait">⏸</span><div><b>오늘은 조건을 모두 통과한 종목이 없어요</b><span>억지로 사지 않는 것도 판단이에요. 지켜볼 종목은 '전체 후보'에 있어요.</span></div></div>
        )}
      </section>

      {/* ④ 내 종목 */}
      <section className="bh-sec" data-testid="brief-mine">
        <div className="bh-sec-head"><h2>내 종목</h2><Link to="/performance?view=mine#rules" className="caption">손절·익절 기준 바꾸기 →</Link></div>
        {mine.length ? <div className="card-grid">{mine.map((p) => <HoldCard key={p.symbol} p={p} />)}</div> : (
          <div className="feed-empty"><span className="wait">＋</span><div><b>아직 보유 종목이 없어요</b><span>토스증권을 연결하거나 <Link to="/portfolio">포트폴리오</Link>에 입력하면 종목마다 팔 때·더 살 때를 알려드려요.</span></div></div>
        )}
      </section>

      {x.upcoming_catalysts.length > 0 && (
        <section className="bh-sec slim" data-testid="brief-events">
          <div className="bh-sec-head"><h2>다가오는 일정</h2></div>
          <div className="ev-strip">{x.upcoming_catalysts.slice(0, 6).map((e) => (
            <div key={e.event_id} className="ev"><span className={`dd${e.days_until <= 2 ? " soon" : ""}`}>{e.days_until === 0 ? "오늘" : `D-${e.days_until}`}</span><span className="t">{plainEvent(e.title)}</span></div>
          ))}</div>
        </section>
      )}

      <div className="bh-foot">점수·근거·차트까지 모두 보려면 <button type="button" className="linkish" onClick={() => setViewMode("full")}>자세히 보기</button>로 바꾸세요. MarketLens는 주문을 넣지 않습니다.</div>
    </div>
  );
}

function Stat({ k, v, tone = "" }: { k: string; v: string; tone?: string }) {
  return <div className="bh-stat"><span>{k}</span><b className={tone}>{v}</b></div>;
}

/** 시장 온도: a half-dial from 조심 to 좋음 with the needle on today's reading. */
function Gauge({ w }: { w: Weather }) {
  const a = Math.PI * (1 - w.level);  // 0 = left (조심) … 1 = right (좋음)
  const nx = 100 + 70 * Math.cos(a), ny = 100 - 70 * Math.sin(a);
  return (
    <div className="bh-gauge" aria-label={`시장 ${w.word}`}>
      <svg viewBox="0 0 200 118" width="200" height="118" aria-hidden>
        <defs><linearGradient id="gauge-g" x1="0" x2="1"><stop offset="0" stopColor="var(--down)" /><stop offset="0.5" stopColor="var(--wait)" /><stop offset="1" stopColor="var(--up)" /></linearGradient></defs>
        <path d="M 18 100 A 82 82 0 0 1 182 100" fill="none" stroke="rgba(255,255,255,0.07)" strokeWidth="16" strokeLinecap="round" />
        <path d="M 18 100 A 82 82 0 0 1 182 100" fill="none" stroke="url(#gauge-g)" strokeWidth="16" strokeLinecap="round" opacity={w.tone === "muted" ? 0.25 : 0.9} />
        {w.tone !== "muted" && <><line x1="100" y1="100" x2={nx} y2={ny} stroke="#fff" strokeWidth="4" strokeLinecap="round" /><circle cx="100" cy="100" r="8" fill="#fff" /></>}
      </svg>
      <div className="g-word"><span className={`wx ${w.tone}`}>{w.icon} 시장 {w.word}</span></div>
      <div className="g-say">{w.say}</div>
    </div>
  );
}

const SELL_ICON: Record<string, string> = { STOP: "↓", TRAIL: "↘", TAKE1: "↗", ADD: "+" };
const DO: Record<Plan["action"], { word: string; tone: string }> = {
  STOP: { word: "전부 팔기", tone: "neg" }, TRAIL: { word: "남은 것 팔기", tone: "sell" }, TAKE1: { word: "절반 팔기", tone: "pos" },
  ADD: { word: "더 사도 됨", tone: "buy" }, HOLD: { word: "그대로 두기", tone: "" }, NO_PRICE: { word: "가격 확인 중", tone: "muted" },
};

function sellLine(p: Plan): string {
  const cur = p.currency;
  return p.action === "STOP" ? `손절가 ${money(p.stop, cur)} 아래로 내려왔어요 — 더 잃기 전에 정리`
    : p.action === "TRAIL" ? `고점에서 ${money(p.trail, cur)} 아래로 내려왔어요 — 남은 수익 지키기`
    : p.action === "TAKE1" ? `목표 ${money(p.take1, cur)}에 닿았어요 — ${Math.round(p.take1_fraction * 100)}% 팔아 수익 확정`
    : p.action === "ADD" ? `수익 중이에요 — ${p.add_qty}주까지 더 살 수 있어요`
    : p.action === "HOLD" ? `손절 ${money(p.stop, cur)} · 익절 ${p.take1_done ? "완료" : money(p.take1, cur)} 사이 — 기다리기`
    : "지금 가격을 아직 못 받았어요";
}

function FeedSell({ p }: { p: Plan }) {
  const d = DO[p.action];
  const body = (
    <>
      <span className={`f-ico ${d.tone}`}>{SELL_ICON[p.action] ?? "•"}</span>
      <div className="f-body"><b>{p.symbol} <span className={`f-verb ${d.tone}`}>{d.word}</span></b><span>{sellLine(p)}</span></div>
      <span className="f-price">{money(p.price, p.currency)}</span>
    </>
  );
  return <li className={`feed-row ${d.tone}`} data-testid={`brief-feed-${p.symbol}`}>{p.market === "US" ? <Link to={`/stocks/${p.symbol}`}>{body}</Link> : <div>{body}</div>}</li>;
}

const VERDICT: Record<string, { word: string; tone: string }> = {
  BUY: { word: "사도 됨", tone: "pos" }, "BUY SMALL": { word: "조금만 사기", tone: "buy" }, ADD: { word: "더 사도 됨", tone: "buy" },
};

function FeedBuy({ r }: { r: OppRow }) {
  const { row } = useQuote(r.ticker);
  const now = row?.price ?? r.price;
  const v = VERDICT[r.action] ?? { word: r.action_ko, tone: "" };
  return (
    <li className="feed-row buyrow" data-testid={`brief-feed-${r.ticker}`}>
      <Link to={`/stocks/${r.ticker}`}>
        <span className={`f-ico ${v.tone}`}>★</span>
        <div className="f-body"><b>{r.ticker} <span className={`f-verb ${v.tone}`}>{v.word}</span></b><span>{r.max_buy != null ? `${price(r.max_buy)} 이하에서 사고, ${price(r.stop)} 아래로 마감하면 팔기` : "가격 계획 확인"}</span></div>
        <span className="f-price">{price(now)}</span>
      </Link>
    </li>
  );
}

function BuyCard({ r }: { r: OppRow }) {
  const { row } = useQuote(r.ticker);
  const now = row?.price ?? r.price;
  const v = VERDICT[r.action] ?? { word: r.action_ko, tone: "" };
  const high = r.max_buy ?? null;
  const ok = now != null && high != null ? now <= high : null;
  const toStop = now != null && r.stop != null ? r.stop / now - 1 : null;
  const toTarget = now != null && r.target != null ? r.target / now - 1 : null;
  const levels: Level[] = [
    ...(r.stop != null ? [{ key: "stop", label: "손절", value: r.stop, tone: "neg" as const }] : []),
    ...(high != null ? [{ key: "avg", label: "사는 가격", value: high, tone: "buy" as const }] : []),
    ...(r.target != null ? [{ key: "take1", label: "목표", value: r.target, tone: "pos" as const }] : []),
    ...(now != null ? [{ key: "now", label: "지금", value: now, tone: "now" as const }] : []),
  ];
  return (
    <Link to={`/stocks/${r.ticker}`} className={`v-card ${v.tone}`} data-testid={`brief-buy-${r.ticker}`}>
      <div className="vc-head">
        <div className="vc-who"><b>{r.ticker}</b><span>{r.company.replace(/\s*\(MOCK\)$/, "")}</span></div>
        <span className={`verdict-chip ${v.tone}`}>{v.word}</span>
      </div>
      <div className="vc-now"><b>{price(now)}</b><span className={ok === false ? "warn" : "pos"}>{ok == null ? "" : ok ? "지금 사도 되는 가격" : "지금은 비쌈 — 기다리기"}</span></div>
      <PriceLadder levels={levels} currency="USD" testId={`brief-ladder-${r.ticker}`} />
      <div className="vc-nums">
        <div><span>사는 가격</span><b>{high != null ? `${price(high)} 이하` : "—"}</b></div>
        <div className="neg"><span>손절</span><b>{price(r.stop)}</b><small>{pct(toStop)}</small></div>
        <div className="pos"><span>목표</span><b>{price(r.target)}</b><small>{pct(toTarget)}</small></div>
      </div>
      {r.key_reason && <div className="vc-why">{r.key_reason}</div>}
      {r.action === "BUY SMALL" && <div className="vc-note">위험 요소가 있어 평소의 절반 정도만</div>}
    </Link>
  );
}

function HoldCard({ p }: { p: Plan }) {
  const d = DO[p.action];
  const pnlTone = (p.pnl_pct ?? 0) >= 0 ? "pos" : "neg";
  const inner = (
    <>
      <div className="vc-head">
        <div className="vc-who"><b>{p.name && p.name !== p.symbol ? p.symbol : p.symbol}</b><span>{p.name && p.name !== p.symbol ? p.name : `${p.quantity}주`}</span></div>
        <span className={`do-chip ${d.tone}`}>{d.word}</span>
      </div>
      <div className="vc-now"><b>{money(p.price, p.currency)}</b><span className={pnlTone}>{p.pnl_pct == null ? "" : `${p.pnl_pct >= 0 ? "+" : ""}${p.pnl_pct.toFixed(1)}%`}</span></div>
      <PriceLadder levels={planLevels(p)} currency={p.currency} testId={`brief-hold-ladder-${p.symbol}`} />
      <div className="vc-why">{sellLine(p)}</div>
    </>
  );
  return p.market === "US"
    ? <Link to={`/stocks/${p.symbol}`} className={`v-card hold ${d.tone}`} data-testid={`brief-todo-${p.symbol}`}>{inner}</Link>
    : <div className={`v-card hold ${d.tone}`} data-testid={`brief-todo-${p.symbol}`}>{inner}</div>;
}

/** "XYZ earnings" → "XYZ 실적 발표" (the calendar's titles are the source's English) */
function plainEvent(t: string): string {
  return t.replace(/^\(MOCK\)\s*/, "").replace(/\s*earnings\b/i, " 실적 발표").replace(/\bFOMC decision\b/i, "FOMC 금리 결정")
    .replace(/\bCPI release\b/i, "CPI 물가 발표").replace(/\bPCE release\b/i, "PCE 물가 발표").replace(/\bPayrolls\b/i, "고용 지표").replace(/\bFDA decision\b/i, "FDA 승인 결정");
}

interface Weather { word: string; say: string; tone: string; icon: string; level: number }
function weather(regime: string | undefined): Weather {
  const good = new Set(["Risk On", "AI Momentum", "Multiple Expansion"]);
  const bad = new Set(["Risk Off", "Credit Stress", "Growth Scare", "Inflation Shock", "Multiple Compression"]);
  if (regime && good.has(regime)) return { word: "좋음", say: "위험을 감수하는 분위기 — 계획대로 사도 괜찮아요", tone: "pos", icon: "☀", level: 0.82 };
  if (regime && bad.has(regime)) return { word: "조심", say: "불안한 분위기 — 사더라도 작게, 손절은 꼭", tone: "neg", icon: "☂", level: 0.18 };
  if (!regime || regime === "Unknown") return { word: "확인 중", say: "시장 지표를 아직 받지 못했어요", tone: "muted", icon: "…", level: 0.5 };
  return { word: "보통", say: "뚜렷한 방향 없음 — 종목별 계획만 지키기", tone: "neutral", icon: "⛅", level: 0.5 };
}
