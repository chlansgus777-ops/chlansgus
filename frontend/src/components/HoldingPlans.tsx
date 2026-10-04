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

export const ACTION_TONE: Record<Plan["action"], string> = { STOP: "neg", TRAIL: "neg", TAKE1: "pos", ADD: "info", HOLD: "", NO_PRICE: "muted" };

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

export function ActionBadge({ p }: { p: Plan }) {
  return <span className={`plan-act ${ACTION_TONE[p.action]}`} data-testid={`plan-action-${p.symbol}`}>{p.action_ko}</span>;
}

function addRule(p: Plan): string {
  if (p.add_mode === "none") return "추가매수 안 함";
  if (p.adds_done >= p.max_adds) return `추가매수 ${p.adds_done}/${p.max_adds}회 — 더 안 함`;
  return p.add_level !== null ? `${money(p.add_level, p.currency)} 이상에서 ${shares(p.add_qty)}주` : `조건 없이 ${shares(p.add_qty)}주(물타기 허용)`;
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
  const title = ticker ? "내 규칙: 이 종목 지금 할 일" : actionsOnly ? "내 규칙상 지금 할 일" : "내 규칙: 보유 종목 지금 할 일";
  return (
    <Card title={title} testId={ticker ? "plan-one" : actionsOnly ? "plan-actions" : "plans"}
      explain="저장한 '내 매매 규칙'(손절·1차 익절·추적 손절·추가매수)을 지금 가격에 그대로 적용한 결과입니다. MarketLens는 주문을 넣지 않습니다 — 실행은 직접 하세요."
      right={<Link className="caption" to="/performance?view=mine#rules">규칙 바꾸기</Link>}>
      {!x.rules_saved && <Notice tone="info">아직 내 규칙을 저장하지 않아 기본값(평단 −7% 손절, +10%에서 절반 익절, 수익 중일 때만 추가매수)으로 계산했습니다. <Link to="/performance?view=mine#rules">내 거래 기록으로 만든 제안값 보기</Link></Notice>}
      <div className="scroll"><table>
        <thead><tr><th>종목</th><th>지금 할 일</th><th className="num">평단 · 현재가</th><th className="num">손익</th><th className="num">손절가</th><th className="num">1차 익절</th><th className="num">추적 손절</th><th>추가매수</th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.symbol} data-testid={`plan-${r.symbol}`}>
            <td>{r.market === "US" ? <Link to={`/stocks/${r.symbol}`}><b>{r.symbol}</b></Link> : <b>{r.name || r.symbol}</b>}<div className="caption">{shares(r.quantity)}주</div></td>
            <td className="wrap" style={{ minWidth: 220 }}><ActionBadge p={r} /><div className="caption">{r.detail}</div>{r.notes.map((n, i) => <div key={i} className="caption muted">· {n}</div>)}</td>
            <td className="num">{money(r.avg_price, r.currency)}<div className="caption">{money(r.price, r.currency)}{r.price_source ? ` · ${r.price_source}` : ""}</div></td>
            <td className={`num ${(r.pnl_pct ?? 0) > 0 ? "pos" : (r.pnl_pct ?? 0) < 0 ? "neg" : ""}`}>{pp(r.pnl_pct)}</td>
            <td className="num">{money(r.stop, r.currency)}<div className="caption">{r.stop_source}</div></td>
            <td className="num">{r.take1_done ? <span className="caption">실행함(일부 매도 기록)</span> : <>{money(r.take1, r.currency)}<div className="caption">{Math.round(r.take1_fraction * 100)}% 매도</div></>}</td>
            <td className="num">{r.trail !== null ? money(r.trail, r.currency) : <span className="caption">1차 익절가 도달 후</span>}</td>
            <td className="wrap caption" style={{ minWidth: 150 }}>{addRule(r)}</td>
          </tr>))}
        </tbody></table></div>
    </Card>
  );
}
