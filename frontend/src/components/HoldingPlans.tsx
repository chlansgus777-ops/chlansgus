import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Card, Notice } from "./ui";
import { useApi, usePoll } from "./useApi";
import { shares } from "../format";

/** One holding under the owner's saved rules (marketlens.domain.habit_diagnosis.holding_plan). */
export interface Plan {
  symbol: string; name: string; market: string | null; currency: string; quantity: number; avg_price: number; price: number | null; price_at: string | null;
  price_source: string | null; pnl_pct: number | null; stop: number; stop_source: string; take1: number; take1_fraction: number; take1_done: boolean;
  trail: number | null; high_since_open: number | null; add_mode: string; add_level: number | null; add_qty: number; adds_done: number; max_adds: number;
  opened: string | null; app_action: string | null; app_stop: number | null; app_target: number | null; rules_saved: boolean; notes: string[];
  action: "STOP" | "TRAIL" | "TAKE1" | "ADD" | "HOLD" | "NO_PRICE"; action_ko: string; detail: string;
}
export interface PlansView { plans: Plan[]; rules: Record<string, number | string | boolean | null>; rules_saved: boolean; connected: boolean; at: string }

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
  if (p.add_level !== null && p.add_mode !== "none" && p.adds_done < p.max_adds) lv.push({ key: "add", label: "추가매수", value: p.add_level, tone: "buy" });
  if (p.price !== null) lv.push({ key: "now", label: "지금", value: p.price, tone: "now" });
  return lv;
}

function addRule(p: Plan): ReactNode {
  if (p.add_mode === "none") return "안 함";
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
        {p.price_source ?? "가격 없음"}{p.opened ? ` · 첫 매수 ${p.opened}` : ""}
        {p.notes.map((n, i) => <div key={i}>· {n}</div>)}
      </footer>
    </article>
  );
}

/** 내 규칙: 지금 할 일 — the account's holdings under the owner's own mechanical rules. ``ticker``: one name (stock
 * page); ``actionsOnly``: only the holdings whose rule says to act now (home). MarketLens never places the order. */
export function HoldingPlans({ ticker, actionsOnly }: { ticker?: string; actionsOnly?: boolean }) {
  const p = useApi<PlansView>(ticker ? `/habits/plans?ticker=${encodeURIComponent(ticker)}` : "/habits/plans", [ticker]);
  usePoll(p.reload, 15_000);
  const x = p.data;
  if (!x || !x.connected || !x.plans.length) return null;
  const rows = actionsOnly ? x.plans.filter((r) => r.action !== "HOLD") : x.plans;
  if (actionsOnly && !rows.length) return null;
  const acting = x.plans.filter((r) => r.action !== "HOLD" && r.action !== "NO_PRICE").length;
  const title = ticker ? "내 규칙: 이 종목 지금 할 일" : actionsOnly ? "내 규칙상 지금 할 일" : "내 규칙: 보유 종목 지금 할 일";
  return (
    <Card title={title} testId={ticker ? "plan-one" : actionsOnly ? "plan-actions" : "plans"}
      explain="저장한 '내 매매 규칙'을 지금 가격에 그대로 적용한 결과입니다. MarketLens는 주문을 넣지 않습니다 — 실행은 직접 하세요."
      right={<div className="row tight">{!ticker && <span className="caption">{acting ? `행동 필요 ${acting}종목` : "모두 보유 유지"}</span>}<Link className="btn sm" to="/performance?view=mine#rules">규칙 바꾸기</Link></div>}>
      {!x.rules_saved && <Notice tone="info">아직 내 규칙을 저장하지 않아 기본값(평단 −7% 손절, +10%에서 절반 익절, 수익 중일 때만 추가매수)으로 계산했습니다. <Link to="/performance?view=mine#rules">내 거래 기록으로 만든 제안값 보기 →</Link></Notice>}
      <div className={`plan-grid${rows.length === 1 ? " one" : ""}`}>{rows.map((r) => <PlanCard key={r.symbol} p={r} />)}</div>
    </Card>
  );
}
