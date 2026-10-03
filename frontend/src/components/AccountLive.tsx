import { useApi, usePoll } from "./useApi";
import { pct, price, won } from "../format";

/** GET /portfolio/live — the account now, as the 토스증권 app counts it (backend application/account_live.py): Toss's own
 * purchase amount, value, P&L and today's P&L of its last sync, moved by the live price; in won at Toss's current rate. */
export interface AccountRow {
  ticker: string; quantity: number; avg_price: number; source: string; basis: "TOSS" | "PRICE"; price: number | null; price_at: string | null; live: boolean;
  purchase: number | null; value: number | null; pnl: number | null; pnl_rate: number | null; daily: number | null; daily_rate: number | null;
  value_krw?: number | null; pnl_krw?: number | null; daily_krw?: number | null;
}
export interface AccountLive {
  at: string; live: boolean; live_at: string | null; synced_at: string | null; fx: { rate: number; source: string; at: string | null } | null;
  rows: AccountRow[]; notes: string[];
  totals: { count: number; valued: number; daily_count: number; value: number | null; purchase: number | null; pnl: number | null; pnl_rate: number | null;
    daily: number | null; daily_rate: number | null; value_krw?: number | null; pnl_krw?: number | null; daily_krw?: number | null };
}

export function useAccountLive(active = true, poll = true) {
  const a = useApi<AccountLive>(active ? "/portfolio/live" : null);
  usePoll(a.reload, 1_000, active && poll);  // one polling owner; row subscribers share its cached response
  if (!a.data || !Array.isArray(a.data.rows) || !a.data.totals) return null;
  return a.error ? { ...a.data, live: false, rows: a.data.rows.map((r) => ({ ...r, live: false })),
    notes: [...a.data.notes, "연결을 확인할 수 없어 마지막 계좌 값을 표시합니다."] } : a.data;
}

export function kstClock(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("ko-KR", { timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

const tone = (v: number | null | undefined) => (v == null ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "");
const arrow = (v: number | null | undefined) => (v == null ? "" : v > 0 ? "▲ " : v < 0 ? "▼ " : "");
export const signedWon = (v: number | null | undefined) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${won(Math.abs(v))}`);

/** One figure in dollars with its won value under it. */
function Figure({ label, usd, krw, rate, signed, testId }: { label: string; usd: number | null; krw?: number | null; rate?: number | null; signed?: boolean; testId?: string }) {
  return (
    <div className="acct-fig" data-testid={testId}>
      <div className="t-kicker">{label}</div>
      <div className={`t-key ${signed ? tone(usd) : ""}`}>{usd == null ? <span className="muted">—</span> : <>{signed ? arrow(usd) : ""}{price(signed ? Math.abs(usd) : usd)}{rate != null ? <small> {pct(rate, 2)}</small> : null}</>}</div>
      {krw != null && <div className={`caption ${signed ? tone(krw) : ""}`}>{signed ? signedWon(krw) : won(krw)}</div>}
    </div>
  );
}

/** The account now (US stocks), every second: value, total P&L and today's P&L — the numbers the Toss app shows. */
export function AccountLiveCard({ a }: { a: AccountLive }) {
  const t = a.totals;
  const toss = a.rows.some((r) => r.basis === "TOSS");
  return (
    <section className="card acct-live" aria-label="실시간 계좌" data-testid="account-live">
      <div className="acct-head">
        <h3>{toss ? "토스증권 계좌 · 미국 주식" : "내 보유 · 미국 주식"}</h3>
        <span className="caption">{a.live ? <><span className="live-dot" aria-hidden />실시간 {kstClock(a.live_at)}</> : "최신 가격 대기 중"}{a.synced_at ? ` · 토스 동기화 ${kstClock(a.synced_at)}` : ""}</span>
      </div>
      <div className="acct-figs">
        <Figure label="평가금액" usd={t.value} krw={t.value_krw} testId="acct-value" />
        <Figure label="총 손익" usd={t.pnl} krw={t.pnl_krw} rate={t.pnl_rate} signed testId="acct-pnl" />
        <Figure label="오늘 손익" usd={t.daily} krw={t.daily_krw} rate={t.daily_rate} signed testId="acct-daily" />
      </div>
      <div className="caption">
        {toss ? "토스증권 계좌 종목은 토스증권이 계산한 매입금액·손익·오늘 손익에 그 뒤 가격 변화만 더했습니다(토스 앱과 같은 기준). " : ""}
        {a.fx ? `원화 환산: ${a.fx.source} ${a.fx.rate.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}원 · 환율 기준 ${a.fx.at ? new Date(a.fx.at).toLocaleString("ko-KR") : "시각 미확인"}` : "원화 환율이 없어 달러로만 표시합니다."}
      </div>
      {a.notes.map((n, i) => <div key={i} className="caption warn">{n}</div>)}
    </section>
  );
}
