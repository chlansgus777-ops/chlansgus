import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { ago, num, pct, shares, stamp, won } from "../format";
import { Card, ConfirmButton, Notice, Ribbon } from "./ui";
import { useApi } from "./useApi";

/** The 토스증권 account as the backend reports it (GET /api/broker/toss; also inside /api/portfolio as ``broker``). */
export interface TossView {
  enabled: boolean; configured: boolean; active: boolean; version: number;
  account: { seq: number; masked: string } | null; synced_at: string | null; age_s: number | null; stale: boolean;
  error: { kind: string; text: string; at?: string } | null; last_attempt: string | null; interval_s: number;
  prefs: { cash: "toss_usd" | "toss_usd_krw" | "manual" }; key_store?: "keychain" | "env_file";
  fills: { count: number; complete: boolean; since: string | null; synced_at: string | null } | null;
  domestic?: TossHolding[]; other?: TossHolding[];
  daily_bars?: { active: boolean; session: string | null; kept: number; rejected: Record<string, string>; error: { kind: string; text: string } | null; last_run: string | null };
  totals?: Record<string, { purchase: string; value: string; pnl: string; daily_pnl: string }>;
  cash?: { USD: string | null; KRW: string | null }; fx?: { rate: string | null; mid: string | null; at: string | null } | null;
}
export interface TossHolding {
  symbol: string; name: string; market: string; currency: string; quantity: string; last_price: string; avg_price: string;
  purchase_amount: string; market_value: string; pnl: string; pnl_rate: string; daily_pnl: string; daily_rate: string;
}
interface Fill { order_id: string; symbol: string; side: "BUY" | "SELL"; status: string; quantity: string; avg_price: string | null; amount: string | null;
  commission: string | null; tax: string | null; currency: string; ordered_at: string; filled_at: string | null }

const n = (v: string | null | undefined): number | null => (v === null || v === undefined || v === "" ? null : Number(v));
const CASH_KO = { toss_usd: "토스 달러 예수금", toss_usd_krw: "토스 달러 + 원화 예수금(환산)", manual: "직접 입력한 현금" } as const;

/** "1분마다" / "10분마다" — the backend's own schedule (a session open: every minute). */
function every(s: number): string { return s <= 60 ? "장중 1분마다" : `${Math.round(s / 60)}분마다`; }

/** Connection card: steps and the key form when not connected; account, freshness, errors and actions when connected. */
export function TossCard({ view, onChange, compact = false }: { view: TossView | null | undefined; onChange: () => void; compact?: boolean }) {
  const [id, setId] = useState("");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState<null | "connect" | "sync" | "off" | "prefs">(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(!compact);
  const [nowMs, setNow] = useState(Date.now());
  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), 15_000); return () => window.clearInterval(t); }, []);
  if (!view) return null;
  const run = async (tag: NonNullable<typeof busy>, fn: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(tag); setErr(null);
    // the backend's own Korean text says what to do (a key, the allowed IP, Toss being down) — no generic HTTP prefix
    try { await fn(); onChange(); } catch (e) {
      // a failed sync is recorded by the backend: reload the view so the light, the ribbon and the time all say it once
      if (tag === "sync") onChange();
      else setErr(e instanceof ApiError && e.detail ? e.detail : e instanceof Error ? e.message : String(e));
    } finally { setBusy(null); }
  };
  if (!view.enabled) {
    return compact ? null : (
      <Card title="토스증권 계좌 연결" testId="toss-card">
        <div className="muted">실데이터(LIVE) 모드에서 연결할 수 있습니다. 모의 데이터의 가격으로 실제 계좌를 평가하면 틀린 숫자가 나오기 때문입니다.</div>
      </Card>
    );
  }
  if (!view.configured) {
    if (compact && !open) {
      return (
        <div className="toss-promo" data-testid="toss-card">
          <span><b>토스증권 연결</b> — 프리마켓·정규장·애프터마켓·야간 모두 <b>1초 실시간 시세</b>, 보유 종목·평단·예수금 자동 반영(조회만, 주문 없음).</span>
          <button className="sm primary" onClick={() => setOpen(true)}>연결하기</button>
        </div>
      );
    }
    return (
      <Card title="토스증권 계좌 연결" testId="toss-card" explain="공급자가 제공하는 시세를 약 1초 간격으로 확인하고, 보유·예수금을 자동 갱신합니다. 새 체결이 없거나 지연·휴장이면 가격은 그대로 유지됩니다. 주문 기능은 없습니다.">
        <ol className="steps">
          <li>토스증권 <b>PC 웹(WTS)</b>에 로그인 → <b>설정 &gt; Open API</b>에서 <b>client_id</b>와 <b>client_secret</b>을 발급받습니다. 권한을 고를 수 있으면 <b>조회만</b> 고르세요.</li>
          <li>같은 화면의 <b>허용 IP 관리</b>에 <b>이 PC의 공인 IP</b>를 등록합니다. (집 인터넷 IP가 바뀌면 다시 등록)</li>
          <li>아래에 두 값을 붙여 넣고 <b>연결</b>을 누릅니다. 키가 맞는지 먼저 확인한 뒤에만 저장합니다.</li>
        </ol>
        <form className="form-grid toss" onSubmit={(e) => { e.preventDefault(); void run("connect", () => api.put("/broker/toss/credentials", { client_id: id.trim(), client_secret: secret.trim() }).then(() => { setId(""); setSecret(""); })); }}>
          <label><span>client_id</span><input aria-label="토스 client_id" autoComplete="off" spellCheck={false} value={id} onChange={(e) => setId(e.target.value)} placeholder="c_…" /></label>
          <label><span>client_secret</span><input aria-label="토스 client_secret" type="password" autoComplete="new-password" spellCheck={false} value={secret} onChange={(e) => setSecret(e.target.value)} /></label>
          <button className="primary" disabled={busy !== null || !id.trim() || !secret.trim()}>{busy === "connect" ? "토스증권에 연결 확인 중…" : "연결"}</button>
        </form>
        <div className="caption">키는 {view.key_store === "env_file" ? "이 PC의 MarketLens 전용 설정 파일" : "Windows 자격 증명 관리자"}에만 저장하고, 화면·로그에 다시 표시하지 않습니다. 같은 키를 다른 프로그램에서도 쓰면 서로의 접속을 끊으니 MarketLens 전용 키를 발급하세요.</div>
        {err && <div role="alert" className="neg" data-testid="toss-error">{err}</div>}
      </Card>
    );
  }
  const age = view.synced_at ? ago(view.synced_at, nowMs) : "아직 없음";
  const e = view.error;
  const body = (
    <>
      <div className="toss-line" data-testid="toss-status">
        <span className={`light ${e || view.stale ? "warn" : "pos"}`} aria-hidden />
        <b>토스증권 {view.account?.masked ?? ""}</b>
        <span className="caption">보유·현금 {e ? `마지막 성공 동기화 ${age}` : `${age} 동기화`}{view.synced_at ? ` (${stamp(view.synced_at)})` : ""} · 자동 {every(view.interval_s)}</span>
        <span className="spacer" />
        <button className="sm" disabled={busy !== null} onClick={() => void run("sync", () => api.post("/broker/toss/sync"))}>{busy === "sync" ? "받는 중…" : "지금 동기화"}</button>
        {compact && <button className="sm ghost" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? "접기" : "설정"}</button>}
      </div>
      {e && <Ribbon tone={["UNAVAILABLE", "RATE_LIMITED", "BAD_DATA"].includes(e.kind) ? "info" : "warn"} cap="동기화 실패" testId="toss-sync-error">{e.text}{view.synced_at ? ` 지금 보이는 보유 현황은 ${age === "방금" ? "방금" : `${age}에`} 받은 것입니다.` : ""}</Ribbon>}
      {view.daily_bars?.active && <DailyBars d={view.daily_bars} />}
      {!e && view.stale && <Notice tone="warn">마지막 동기화가 {age}입니다. 인터넷 연결을 확인하거나 ‘지금 동기화’를 누르세요.</Notice>}
      {(!compact || open) && (
        <div className="toss-settings">
          <label className="row tight"><span>매수 수량 계산에 쓰는 현금</span>
            <select aria-label="현금 기준" value={view.prefs.cash} disabled={busy !== null} onChange={(ev) => void run("prefs", () => api.put("/broker/toss/prefs", { cash: ev.target.value }))}>
              {Object.entries(CASH_KO).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
          {view.cash && <div className="caption">토스 예수금(매수 가능 현금): 달러 ${num(n(view.cash.USD), 2)} · 원화 {won(n(view.cash.KRW))}{view.fx?.rate ? ` · 환율 ${num(n(view.fx.rate), 2)}원(참고)` : ""}</div>}
          <div className="row tight">
            <ConfirmButton label={busy === "off" ? "해제 중…" : "연결 해제"} busy={busy === "off"} testId="toss-disconnect"
              confirm={<>토스 키와 토스에서 받은 보유·체결 기록을 이 PC에서 지울까요? <span className="caption">직접 입력한 보유와 거래 기록은 그대로입니다</span></>}
              onConfirm={() => void run("off", () => api.del("/broker/toss"))} />
            <span className="caption">키 보관: {view.key_store === "env_file" ? "MarketLens 전용 설정 파일" : "Windows 자격 증명 관리자"}</span>
          </div>
        </div>
      )}
      {err && <div role="alert" className="neg" data-testid="toss-error">{err}</div>}
    </>
  );
  return compact ? <div className="toss-bar" data-testid="toss-card">{body}</div> : <Card title="토스증권 계좌 연결" testId="toss-card">{body}</Card>;
}

/** Domestic (KRX) holdings of the account, in won as Toss computed them — outside the dollar totals and the analysis. */
/** 일봉(일별 가격 기록)의 출처: 보유·관심·분석 후보는 토스증권 일봉이 먼저, 기존 기록과 맞지 않거나 실패하면 Polygon 그대로. */
function DailyBars({ d }: { d: NonNullable<TossView["daily_bars"]> }) {
  const off = Object.entries(d.rejected);
  return (
    <div className="caption" data-testid="toss-daily-bars">
      일봉: 토스증권 {d.kept}종목{d.session ? ` (${d.session} 장까지)` : ""} · 그 밖의 종목과 시장 전체는 Polygon
      {off.length > 0 && <span title={off.map(([t, why]) => `${t}: ${why}`).join("\n")}> · Polygon 유지 {off.length}종목({off.slice(0, 3).map(([t]) => t).join(", ")}{off.length > 3 ? " 외" : ""})</span>}
      {d.error && <span> · 일봉 요청 잠시 멈춤: {d.error.text}</span>}
    </div>
  );
}

export function DomesticHoldings({ items, syncedAt }: { items: TossHolding[]; syncedAt: string | null }) {
  if (!items.length) return null;
  const total = items.reduce((a, h) => a + (n(h.market_value) ?? 0), 0);
  const pnl = items.reduce((a, h) => a + (n(h.pnl) ?? 0), 0);
  return (
    <Card title="국내 주식 (토스증권)" testId="domestic-holdings"
      explain="MarketLens는 미국 주식만 분석합니다. 국내 주식은 합계·비중·매수 수량 계산에 넣지 않고, 토스증권이 계산한 원화 금액 그대로 보여 줍니다.">
      <div className="scroll"><table><thead><tr><th>종목</th><th>수량</th><th>평균 매입가</th><th>현재가</th><th>평가금액</th><th>손익</th><th>오늘</th></tr></thead>
        <tbody>{items.map((h) => {
          const p = n(h.pnl) ?? 0, d = n(h.daily_pnl) ?? 0;
          return (
            <tr key={h.symbol} className="nowrap-row"><td><b>{h.name}</b><div className="caption">{h.symbol}</div></td><td>{shares(n(h.quantity))}</td><td>{won(n(h.avg_price))}</td><td>{won(n(h.last_price))}</td>
              <td>{won(n(h.market_value))}</td><td className={p > 0 ? "pos" : p < 0 ? "neg" : ""}>{p > 0 ? "▲" : p < 0 ? "▼" : ""} {won(Math.abs(p))} ({pct(n(h.pnl_rate))})</td>
              <td className={d > 0 ? "pos" : d < 0 ? "neg" : ""}>{pct(n(h.daily_rate))}</td></tr>
          );
        })}</tbody></table></div>
      <div className="caption">합계 {won(total)} · 손익 <span className={pnl > 0 ? "pos" : pnl < 0 ? "neg" : ""}>{won(pnl)}</span> · 토스증권 기준 {stamp(syncedAt)}</div>
    </Card>
  );
}

/** Executions read from the account (closed orders with a fill), newest first — read-only. */
export function TossFills() {
  const [open, setOpen] = useState(false);
  const f = useApi<{ fills: Fill[]; status: TossView }>(open ? "/broker/toss/fills?limit=300" : null);
  const fills = f.data?.fills ?? [];
  const st = f.data?.status.fills;
  return (
    <Card title="토스증권 체결 내역" testId="toss-fills" explain="토스증권이 보낸 체결(조회만). 보유 수량·평단은 토스 계좌 값을 그대로 쓰므로 여기서 고칠 것은 없습니다."
      right={<button className="sm" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? "접기" : "보기"}</button>}>
      {!open ? <div className="caption">최근 1년의 체결을 받아 둡니다. 눌러서 펼치세요.</div>
        : f.state === "loading" && !f.data ? <div className="caption">불러오는 중…</div>
        : !fills.length ? <div className="caption">받은 체결이 없습니다.</div>
        : <>
          <div className="scroll"><table><thead><tr><th>체결 시각</th><th>종목</th><th>구분</th><th>수량</th><th>체결가</th><th>금액</th><th>수수료·세금</th></tr></thead>
            <tbody>{fills.map((x) => {
              const fee = (n(x.commission) ?? 0) + (n(x.tax) ?? 0);
              const money = (v: number | null) => x.currency === "KRW" ? won(v) : v === null ? "N/A" : `$${num(v, 2)}`;
              return (
                <tr key={x.order_id} className="nowrap-row"><td>{stamp(x.filled_at ?? x.ordered_at)}</td><td><b>{x.symbol}</b></td>
                  <td className={x.side === "BUY" ? "pos" : "neg"}>{x.side === "BUY" ? "매수" : "매도"}{x.status !== "FILLED" ? <span className="caption"> (일부 체결)</span> : null}</td>
                  <td>{shares(n(x.quantity))}</td><td>{money(n(x.avg_price))}</td><td>{money(n(x.amount))}</td><td>{money(fee)}</td></tr>
              );
            })}</tbody></table></div>
          <div className="caption">{fills.length.toLocaleString("ko-KR")}건{st && !st.complete ? " · 체결이 많아 오래된 일부는 받지 않았습니다" : ""}{st?.since ? ` · ${st.since}부터` : ""} · 오픈API로 넣을 수 없는 시간외 종가 주문은 토스 명세상 목록에 없습니다</div>
        </>}
    </Card>
  );
}
