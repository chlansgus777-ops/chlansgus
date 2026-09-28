import { useCallback, useState } from "react";
import { api } from "../api";
import { day, price, shares } from "../format";
import { Card, Empty, Err, Notice } from "./ui";
import { useApi } from "./useApi";

export type Kind = "BUY" | "SELL" | "DIVIDEND" | "SPLIT";
export const KIND_KO: Record<Kind, string> = { BUY: "매수", SELL: "매도", DIVIDEND: "배당", SPLIT: "주식분할" };

interface TradeRow { id: number; ticker: string; day: string; kind: Kind; quantity: number; price: number; fees: number; amount: number; split_from: number; split_to: number; note: string }
interface Security {
  security: string; ticker: string | null; last_ticker: string; error: string | null;
  position: { quantity: number; avg_cost: number; cost_basis: number; realized_pnl: number; dividends: number; fees: number; splits_applied: string[] } | null;
  trades: TradeRow[];
}
export interface LedgerView { securities: Security[]; realized_pnl: number; dividends: number; note: string }

export interface TradeForm { ticker: string; day: string; kind: Kind; quantity: string; price: string; fees: string; amount: string; split_from: string; split_to: string }
export const EMPTY_FORM: TradeForm = { ticker: "", day: "", kind: "BUY", quantity: "", price: "", fees: "", amount: "", split_from: "", split_to: "" };

/** The request body for a form, or the reason it cannot be sent (checked again by the server against the whole record). */
export function tradeBody(f: TradeForm, today: string): { body?: Record<string, unknown>; error?: string } {
  const ticker = f.ticker.trim().toUpperCase();
  if (!/^[A-Z][A-Z0-9.-]{0,9}$/.test(ticker)) return { error: "종목 코드를 입력하세요 (예: NVDA)." };
  if (!/^\d{4}-\d{2}-\d{2}$/.test(f.day)) return { error: "체결일을 입력하세요." };
  if (f.day > today) return { error: "미래 날짜의 거래는 적을 수 없습니다." };
  const n = (s: string) => (s.trim() === "" ? 0 : Number(s));
  const fees = n(f.fees);
  if (!Number.isFinite(fees) || fees < 0) return { error: "수수료는 0 이상의 숫자여야 합니다." };
  const body: Record<string, unknown> = { ticker, day: f.day, kind: f.kind, fees };
  if (f.kind === "BUY" || f.kind === "SELL") {
    const q = n(f.quantity), p = n(f.price);
    if (!Number.isFinite(q) || q <= 0) return { error: "수량은 0보다 커야 합니다." };
    if (!Number.isFinite(p) || p <= 0) return { error: "가격은 0보다 커야 합니다." };
    Object.assign(body, { quantity: q, price: p });
  } else if (f.kind === "DIVIDEND") {
    const a = n(f.amount);
    if (!Number.isFinite(a) || a <= 0) return { error: "받은 배당 총액은 0보다 커야 합니다." };
    Object.assign(body, { amount: a });
  } else {
    const a = n(f.split_from), b = n(f.split_to);
    if (!Number.isFinite(a) || a <= 0 || !Number.isFinite(b) || b <= 0) return { error: "분할 전·후 주식 수는 모두 0보다 커야 합니다 (예: 1 → 10)." };
    Object.assign(body, { split_from: a, split_to: b });
  }
  return { body };
}

function describe(t: TradeRow): string {
  if (t.kind === "DIVIDEND") return `배당 ${price(t.amount)}`;
  if (t.kind === "SPLIT") return `분할 ${shares(t.split_from)} → ${shares(t.split_to)}`;
  return `${KIND_KO[t.kind]} ${shares(t.quantity)}주 × ${price(t.price)}${t.fees ? ` (수수료 ${price(t.fees)})` : ""}`;
}

export function Ledger({ today, onChange, prefill }: { today: string; onChange: () => void; prefill?: string | null }) {
  const v = useApi<LedgerView>("/transactions");
  // opened from a stock page ("거래 기록하기"): the ticker and today are filled in, the form is in view
  const [f, setF] = useState<TradeForm>(() => prefill ? { ...EMPTY_FORM, ticker: prefill.toUpperCase(), day: today } : EMPTY_FORM);
  const formRef = useCallback((el: HTMLFormElement | null) => {
    if (el && prefill) { el.scrollIntoView({ block: "center" }); el.querySelector<HTMLInputElement>('input[aria-label="거래 수량"]')?.focus(); }
  }, [prefill]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<unknown>) => {
    setErr(null); setBusy(true);
    try { await fn(); v.reload(); onChange(); return true; } catch (e) { setErr(e instanceof Error ? e.message : String(e)); return false; } finally { setBusy(false); }
  };
  const set = (k: keyof TradeForm) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  return (
    <Card title="거래 기록" explain="거래 기록이 있는 종목은 보유 수량·평단을 거래에서 계산합니다(위의 수동 입력 줄은 쓰지 않음). 주식분할은 저장된 분할 기록이 자동으로 반영되고, 분할 실행일의 체결은 분할 후 기준입니다.">
      <form className="stack" ref={formRef} onSubmit={(e) => {
        e.preventDefault();
        const r = tradeBody(f, today);
        if (r.error) { setErr(r.error); return; }
        void run(() => api.post("/transactions", r.body)).then((ok) => { if (ok) setF({ ...EMPTY_FORM, kind: f.kind, day: f.day }); });
      }}>
        <div className="row">
          <select aria-label="거래 종류" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value as Kind })}>
            {(Object.keys(KIND_KO) as Kind[]).map((k) => <option key={k} value={k}>{KIND_KO[k]}</option>)}
          </select>
          <input aria-label="체결일" type="date" max={today} value={f.day} onChange={set("day")} />
          <input aria-label="거래 종목 코드" placeholder="티커" value={f.ticker} onChange={set("ticker")} />
          {(f.kind === "BUY" || f.kind === "SELL") && <>
            <input aria-label="거래 수량" placeholder="몇 주" value={f.quantity} onChange={set("quantity")} />
            <input aria-label="체결 가격" placeholder="체결가(1주, USD)" value={f.price} onChange={set("price")} />
            <input aria-label="거래 수수료" placeholder="수수료(USD)" value={f.fees} onChange={set("fees")} />
          </>}
          {f.kind === "DIVIDEND" && <input aria-label="배당 총액" placeholder="받은 총액(USD)" value={f.amount} onChange={set("amount")} />}
          {f.kind === "SPLIT" && <>
            <input aria-label="분할 전" placeholder="분할 전 주식 수" value={f.split_from} onChange={set("split_from")} />
            <input aria-label="분할 후" placeholder="분할 후 주식 수" value={f.split_to} onChange={set("split_to")} />
          </>}
          <button className="primary" disabled={busy}>기록</button>
        </div>
        {f.kind === "SPLIT" && <div className="caption">저장된 분할 기록에 없는 분할만 적으세요. 같은 날·같은 비율의 분할은 한 번만 반영됩니다.</div>}
      </form>
      <Err error={err} />
      {v.state === "loading" ? <div className="caption">불러오는 중…</div> : !v.data ? <Err error={v.error} retry={v.reload} /> : !v.data.securities.length ? <Empty>아직 거래 기록이 없습니다.</Empty> : (
        <div className="stack" style={{ marginTop: 10 }}>
          <div className="caption">실현 손익 합계 {price(v.data.realized_pnl)} · 배당 합계 {price(v.data.dividends)}</div>
          {v.data.securities.map((c) => (
            <div key={c.security}>
              <div className="row spread">
                <b>{c.ticker ?? `${c.last_ticker} (옛 회사)`}</b>
                {c.position && <span className="caption">보유 {shares(c.position.quantity)}주 · 평단 {price(c.position.avg_cost)} · 실현 {price(c.position.realized_pnl)} · 배당 {price(c.position.dividends)}{c.position.splits_applied.length ? ` · 분할 ${c.position.splits_applied.length}건 반영` : ""}</span>}
              </div>
              {c.error && <Notice tone="warn">거래 기록으로 보유를 계산할 수 없음 — {c.error}</Notice>}
              {c.ticker === null && <Notice tone="warn">이 회사는 지금 쓰는 티커를 알 수 없습니다(티커가 다른 회사에 재사용됨 등). 가격이 없어 평가에서 빠집니다.</Notice>}
              <table><tbody>{c.trades.map((t) => (
                <tr key={t.id}><td>{day(t.day)}</td><td>{t.ticker}</td><td>{describe(t)}</td>
                  <td><button disabled={busy} onClick={() => { if (window.confirm(`${day(t.day)} ${describe(t)} 기록을 지울까요?`)) void run(() => api.del(`/transactions/${t.id}`)); }}>삭제</button></td></tr>
              ))}</tbody></table>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
