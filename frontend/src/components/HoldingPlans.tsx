import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Card, Notice } from "./ui";
import { useApi, usePoll } from "./useApi";
import { day, shares } from "../format";

/** One holding under the owner's saved rules (marketlens.domain.habit_diagnosis.holding_plan). */
export interface Plan {
  symbol: string; name: string; market: string | null; currency: string; quantity: number; avg_price: number; price: number | null; price_at: string | null;
  price_source: string | null; pnl_pct: number | null; stop: number; stop_source: string; take1: number; take1_fraction: number; take1_done: boolean;
  trail: number | null; high_since_open: number | null; add_mode: string; add_level: number | null; add_qty: number; adds_done: number; max_adds: number;
  opened: string | null; app_action: string | null; app_stop: number | null; app_target: number | null; rules_saved: boolean; notes: string[]; rule_conflicts?: string[];
  action: "STOP" | "TRAIL" | "TAKE1" | "ADD" | "HOLD" | "NO_PRICE"; action_ko: string; detail: string;
}
export interface PlansView { plans: Plan[]; rules: Record<string, number | string | boolean | null>; rules_saved: boolean; rule_conflicts?: string[]; connected: boolean; source?: "toss" | "app"; at: string }

export const ACTION_TONE: Record<Plan["action"], string> = { STOP: "neg", TRAIL: "sell", TAKE1: "pos", ADD: "buy", HOLD: "", NO_PRICE: "muted" };
const ACTION_ICON: Record<Plan["action"], string> = { STOP: "■", TRAIL: "▼", TAKE1: "▲", ADD: "+", HOLD: "●", NO_PRICE: "?" };

/** An amount in its own currency — never converted or summed across currencies here. */
export function money(v: number | null | undefined, cur: string): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  if (cur === "KRW") return `${Math.round(v).toLocaleString("ko-KR")}원`;
  const s = Math.abs(v).toLocaleString("ko-KR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${v < 0 ? "-" : ""}${cur === "USD" ? "$" : ""}${s}${cur !== "USD" ? ` ${cur}` : ""}`;
}

/** A percentage the backend already gives in percent (−4.2 = −4.2 %). */
export function pp(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

export function ActionBadge({ p, big }: { p: Pick<Plan, "action" | "action_ko" | "symbol">; big?: boolean }) {
  return <span className={`plan-act ${ACTION_TONE[p.action]}${big ? " big" : ""}`} data-testid={`plan-action-${p.symbol}`} data-icon={ACTION_ICON[p.action]}>{p.action_ko}</span>;
}

export interface Level { key: string; label: string; value: number; tone: "neg" | "pos" | "buy" | "sell" | "neutral" | "now" }

/** The plan on one price line: where the stop, the cost, the add level, the take-profit and the trailing stop are,
 * and where the price is now — the distance to each next action at a glance. */
export function PriceLadder({ levels, currency, testId }: { levels: Level[]; currency: string; testId?: string }) {
  const vals = levels.map((l) => l.value).filter((v) => Number.isFinite(v));
  if (vals.length < 2) return null;
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const span = hi - lo || hi * 0.02 || 1;
  const pos = (v: number) => 4 + 92 * ((v - lo) / span);
  const stop = levels.find((l) => l.key === "stop");
  const take = levels.find((l) => l.key === "take1");
  const sorted = [...levels].filter((l) => l.tone !== "now").sort((a, b) => a.value - b.value);
  const now = levels.find((l) => l.tone === "now");
  // labels go below the track in rows, each label in the first row where it clears the one before it
  const last: number[] = [];
  const row = sorted.map((l) => {
    const x = pos(l.value);
    let r = last.findIndex((v) => x - v >= 19);
    if (r < 0) r = last.length < 3 ? last.length : last.indexOf(Math.min(...last));
    last[r] = x;
    return r;
  });
  const edge = (x: number) => (x < 12 ? " l" : x > 88 ? " r" : "");
  return (
    <div className={`pladder rows-${Math.max(1, last.length)}${now ? " has-now" : ""}`} data-testid={testId} role="img"
      aria-label={levels.map((l) => `${l.label} ${money(l.value, currency)}`).join(", ")}>
      <div className="pl-track">
        {stop && <div className="pl-zone neg" style={{ left: 0, width: `${pos(stop.value)}%` }} />}
        {take && <div className="pl-zone pos" style={{ left: `${pos(take.value)}%`, right: 0 }} />}
      </div>
      {sorted.map((l, i) => (
        <div key={l.key} className={`pl-tick ${l.tone} r${row[i]}${edge(pos(l.value))}`} style={{ left: `${pos(l.value)}%` }}>
          <span className="lbl"><b>{l.label}</b>{money(l.value, currency)}</span>
        </div>
      ))}
      {now && <div className={`pl-tick now${edge(pos(now.value))}`} style={{ left: `${pos(now.value)}%` }}><span className="lbl"><b>지금</b>{money(now.value, currency)}</span><span className="pl-dot" /></div>}
    </div>
  );
}

export function planLevels(p: Plan): Level[] {
  const lv: Level[] = [{ key: "stop", label: "손절", value: p.stop, tone: "neg" }, { key: "avg", label: "평단", value: p.avg_price, tone: "neutral" }];
  if (!p.take1_done) lv.push({ key: "take1", label: `익절 ${Math.round(p.take1_fraction * 100)}%`, value: p.take1, tone: "pos" });
  if (p.trail !== null) lv.push({ key: "trail", label: "추적 손절", value: p.trail, tone: "sell" });
  const clash = (p.rule_conflicts?.length ?? 0) > 0 && !p.take1_done;  // the add would sit on the take-profit: not a level
  if (!clash && p.add_level !== null && p.add_mode !== "none" && p.adds_done < p.max_adds) lv.push({ key: "add", label: "추가매수", value: p.add_level, tone: "buy" });
  if (p.price !== null) lv.push({ key: "now", label: "지금", value: p.price, tone: "now" });
  return lv;
}

function addRule(p: Plan): ReactNode {
  if (p.add_mode === "none") return "안 함";
  if ((p.rule_conflicts?.length ?? 0) > 0 && !p.take1_done) return <span className="warn">규칙 충돌<small>익절과 같은 가격 — 고치세요</small></span>;
  if (p.adds_done >= p.max_adds) return <>완료<small>{p.adds_done}/{p.max_adds}회</small></>;
  return <>{p.add_level !== null ? `${money(p.add_level, p.currency)} 이상` : "가격 조건 없음"}<small>{shares(p.add_qty)}주 · {p.max_adds - p.adds_done}회 남음</small></>;
}

export function PlanCard({ p }: { p: Plan }) {
  const tone = ACTION_TONE[p.action];
  return (
    <article className={`plan-card ${tone}`} data-testid={`plan-${p.symbol}`}>
      <header>
        <div className="who">
          {p.market === "US" ? <Link to={`/stocks/${p.symbol}`}><b>{p.symbol}</b></Link> : <b>{p.name || p.symbol}</b>}
          <span className="caption">{p.market === "US" && p.name !== p.symbol ? p.name : p.symbol} · {shares(p.quantity)}주</span>
        </div>
        <ActionBadge p={p} big />
      </header>
      <p className="plan-detail">{p.detail}</p>
      <PriceLadder levels={planLevels(p)} currency={p.currency} testId={`ladder-${p.symbol}`} />
      <dl className="plan-facts">
        <div><dt>손익</dt><dd className={(p.pnl_pct ?? 0) > 0 ? "pos" : (p.pnl_pct ?? 0) < 0 ? "neg" : ""}>{pp(p.pnl_pct)}<small>평단 {money(p.avg_price, p.currency)}</small></dd></div>
        <div><dt>손절가</dt><dd>{money(p.stop, p.currency)}<small>{p.stop_source}</small></dd></div>
        <div><dt>1차 익절</dt><dd>{p.take1_done ? "실행함" : money(p.take1, p.currency)}<small>{p.take1_done ? (p.trail !== null ? `추적 손절 ${money(p.trail, p.currency)}` : "") : `${Math.round(p.take1_fraction * 100)}% 매도`}</small></dd></div>
        <div><dt>추가매수</dt><dd>{addRule(p)}</dd></div>
      </dl>
      <footer className="caption">
        {p.price_source ?? "가격 없음"}{p.opened ? ` · 첫 매수 ${day(p.opened)}` : ""}
        {p.notes.map((n, i) => <div key={i}>· {n}</div>)}
      </footer>
    </article>
  );
}

export interface Preview { price: number | null; appStop: number | null; appTarget: number | null }

const num = (v: unknown, d: number) => (typeof v === "number" && Number.isFinite(v) ? v : d);

/** 내 규칙으로 산다면: a name not held yet, bought at today's price under the saved rules — where the stop, the first
 * take-profit and the add level would be, what one share risks, and how the app's own stop compares. */
export function RulePreview({ ticker, rules, saved, pv }: { ticker: string; rules: PlansView["rules"]; saved: boolean; pv: Preview }) {
  const px = pv.price;
  if (px === null || !Number.isFinite(px) || px <= 0) return null;
  const stopPct = num(rules.stop_pct, -7), takePct = num(rules.take1_pct, 10), frac = num(rules.take1_fraction, 0.5), trailPct = num(rules.trail_pct, -8);
  const addMode = String(rules.add_mode ?? "winners_only"), addPct = num(rules.add_trigger_pct, 5), addFrac = num(rules.add_fraction, 0.5), maxAdds = num(rules.max_adds, 1);
  const ruleStop = px * (1 + stopPct / 100);
  const app = pv.appStop !== null && pv.appStop > 0 && pv.appStop < px ? pv.appStop : null;
  const useApp = rules.use_app_stop === true && app !== null && app > ruleStop;
  const stop = useApp ? app! : ruleStop;
  const take = px * (1 + takePct / 100);
  const add = addMode === "winners_only" ? px * (1 + addPct / 100) : null;
  const rr = (take - px) / (px - stop);
  const levels: Level[] = [
    { key: "stop", label: "손절", value: stop, tone: "neg" }, { key: "avg", label: "매수가", value: px, tone: "neutral" },
    { key: "take1", label: `익절 ${Math.round(frac * 100)}%`, value: take, tone: "pos" },
    ...(add !== null && maxAdds > 0 ? [{ key: "add", label: "추가매수", value: add, tone: "buy" as const }] : []),
    ...(app !== null && !useApp && Math.abs(app - stop) / px > 0.004 ? [{ key: "app", label: "앱 손절", value: app, tone: "sell" as const }] : []),
  ];
  return (
    <article className="plan-card preview" data-testid={`preview-${ticker}`}>
      <header>
        <div className="who"><b>지금 {money(px, "USD")}에 산다면</b><span className="caption">{saved ? "저장한 내 규칙" : "기본 규칙(아직 저장 전)"} 기준 · 매수 후에는 실제 평단으로 다시 계산</span></div>
        <span className="plan-act" data-icon="◇">매수 전 계획</span>
      </header>
      <PriceLadder levels={levels} currency="USD" testId={`preview-ladder-${ticker}`} />
      <dl className="plan-facts">
        <div><dt>손절가</dt><dd className="neg">{money(stop, "USD")}<small>1주당 {money(stop - px, "USD")} · {useApp ? "앱 손절가" : `${stopPct}%`}</small></dd></div>
        <div><dt>1차 익절</dt><dd className="pos">{money(take, "USD")}<small>{Math.round(frac * 100)}% 매도 · 나머지 고점 {trailPct}% 추적</small></dd></div>
        <div><dt>추가매수</dt><dd>{addMode === "none" || maxAdds <= 0 ? "안 함" : add !== null ? `${money(add, "USD")} 이상` : "가격 조건 없음"}<small>{addMode === "none" || maxAdds <= 0 ? "규칙상 추가매수 없음" : `첫 매수의 ${Math.round(addFrac * 100)}% · ${maxAdds}회까지`}</small></dd></div>
        <div><dt>손익비</dt><dd>{Number.isFinite(rr) ? rr.toFixed(2) : "—"}<small>1차 익절까지 이익 ÷ 손절까지 손실</small></dd></div>
      </dl>
      <footer className="caption">
        {app === null ? "앱 분석 손절가가 없어 비교하지 않았습니다." : useApp ? `앱 분석 손절가 ${money(app, "USD")}가 규칙 손절보다 높아 그것을 씁니다.`
          : app < ruleStop ? `앱 분석 손절가 ${money(app, "USD")}는 내 규칙 손절보다 아래 — 내 규칙이 먼저 닿습니다.`
          : `앱 분석 손절가 ${money(app, "USD")}가 내 규칙 손절보다 위입니다. 규칙에서 '앱 분석 손절가 사용'을 켜면 그 가격을 씁니다.`}
        {" "}산 뒤에는 이 가격들에 닿을 때 알림(종 아이콘)으로 알려드립니다.
      </footer>
    </article>
  );
}

/** 내 규칙: 지금 할 일 — the account's holdings under the owner's own mechanical rules. ``ticker``: one name (stock
 * page; ``preview`` shows the plan for buying it now when it is not held); ``actionsOnly``: only the holdings whose rule
 * says to act now (home). MarketLens never places the order. */
export function HoldingPlans({ ticker, actionsOnly, preview }: { ticker?: string; actionsOnly?: boolean; preview?: Preview }) {
  const p = useApi<PlansView>(ticker ? `/habits/plans?ticker=${encodeURIComponent(ticker)}` : "/habits/plans", [ticker]);
  usePoll(p.reload, 15_000);
  const x = p.data && Array.isArray(p.data.plans) ? p.data : null;  // an older server (or none): nothing to show
  if (x && ticker && preview && !x.plans.length && preview.price !== null && x.rules) {
    return (
      <Card title="내 규칙으로 산다면" testId="plan-preview" explain="아직 보유하지 않은 종목입니다. 지금 가격에 산다고 가정하고 '내 매매 규칙'을 적용한 매수 전 계획입니다. MarketLens는 주문을 넣지 않습니다."
        right={<Link className="btn sm" to="/performance?view=mine#rules">규칙 바꾸기</Link>}>
        <RulePreview ticker={ticker} rules={x.rules} saved={x.rules_saved} pv={preview} />
      </Card>
    );
  }
  if (!x || !x.plans.length) return null;
  const rows = actionsOnly ? x.plans.filter((r) => r.action !== "HOLD") : x.plans;
  if (actionsOnly && !rows.length) return null;
  const acting = x.plans.filter((r) => r.action !== "HOLD" && r.action !== "NO_PRICE").length;
  const title = ticker ? "내 규칙: 이 종목 지금 할 일" : actionsOnly ? "내 규칙상 지금 할 일" : "내 규칙: 보유 종목 지금 할 일";
  return (
    <Card title={title} testId={ticker ? "plan-one" : actionsOnly ? "plan-actions" : "plans"}
      explain="저장한 '내 매매 규칙'을 지금 가격에 그대로 적용한 결과입니다. 가격이 손절·익절·추가매수 가격에 닿으면 알림(종 아이콘)으로도 알려드립니다. MarketLens는 주문을 넣지 않습니다 — 실행은 직접 하세요."
      right={<div className="row tight">{!ticker && <span className="caption">{acting ? `행동 필요 ${acting}종목` : "모두 보유 유지"}</span>}<Link className="btn sm" to="/performance?view=mine#rules">규칙 바꾸기</Link></div>}>
      {(x.rule_conflicts?.length ?? 0) > 0 && <div data-testid="rule-conflict"><Notice tone="warn">{x.rule_conflicts![0]} <Link to="/performance?view=mine#rules">규칙 고치기 →</Link></Notice></div>}
      {x.source === "app" && !actionsOnly && <Notice tone="info">토스증권이 연결되지 않아 이 앱에 입력한 보유(직접 입력·거래 기록) 기준으로 계산했습니다. 매수 이후 고점·추가매수 횟수는 거래 기록이 있을 때만 반영됩니다.</Notice>}
      {!x.rules_saved && <Notice tone="info">아직 내 규칙을 저장하지 않아 기본값(평단 −7% 손절, +10%에서 절반 익절, 수익 중일 때만 추가매수)으로 계산했습니다. <Link to="/performance?view=mine#rules">내 거래 기록으로 만든 제안값 보기 →</Link></Notice>}
      <div className={`plan-grid${rows.length === 1 ? " one" : ""}`}>{rows.map((r) => <PlanCard key={r.symbol} p={r} />)}</div>
    </Card>
  );
}
