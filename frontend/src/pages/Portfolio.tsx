import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Card, ConfirmButton, Donut, Empty, Err, Loading, Notice, Ribbon, StaleData, Term } from "../components/ui";
import { Ledger } from "../components/Ledger";
import { LivePrice } from "../components/LivePrice";
import { ITrash } from "../components/icons";
import { refreshQuoteSubscriptions } from "../quotes";
import { useApi, usePoll } from "../components/useApi";
import { day, num, parseAmount, pct, price, shares, usdWithKo } from "../format";


interface HoldingV { ticker: string; quantity: number; cost_basis: number; price: number | null; price_day: string | null; market_value: number | null; unrealized_pnl: number | null; unrealized_pct: number | null; weight: number | null; sector: string; split_adjusted?: number; source?: "manual" | "ledger"; realized_pnl?: number; dividends?: number }
interface Pf {
  valuation_day: string | null; cash: number; invested_value: number; nav: number; unrealized_pnl: number; holdings: HoldingV[];
  sector_weights: Record<string, number>; theme_weights: Record<string, number>; hhi: number; beta: number | null;
  correlations: [string, string, number][]; missing_prices: string[]; notes: string[]; currency: string; note: string;
  /** EMPTY | COMPLETE | PARTIAL | UNAVAILABLE — an unvalued account is never shown as a cash-only total */
  valuation_status?: string;
  history_pending?: string[];
  unused_manual?: { ticker: string; quantity: number }[];
  /** false: nobody entered the cash yet — the amount is the sizing assumption, never shown as the user's money */
  cash_entered?: boolean;
}

/** Plain-language reading of the portfolio (only from the numbers above). */
export function interpret(x: Pf): { tone: "info" | "warn"; text: string }[] {
  const out: { tone: "info" | "warn"; text: string }[] = [];
  if (!x.holdings.length) return out;
  const cashW = x.nav > 0 ? x.cash / x.nav : 0;
  const top = [...x.holdings].filter((h) => h.weight !== null).sort((a, b) => (b.weight ?? 0) - (a.weight ?? 0))[0];
  const topSector = Object.entries(x.sector_weights).sort((a, b) => b[1] - a[1])[0];
  const ai = x.theme_weights["AI"];
  if (ai !== undefined && ai >= 0.3) out.push({ tone: "warn", text: `현재 포트폴리오는 AI/반도체 비중이 ${pct(ai, 0, false)}로 높습니다. 같은 테마 종목을 더하면 함께 오르내릴 위험이 커집니다.` });
  if (topSector && topSector[1] >= 0.3) out.push({ tone: "warn", text: `${topSector[0]} 업종에 ${pct(topSector[1], 0, false)}가 몰려 있습니다(한도 30%).` });
  if (top && (top.weight ?? 0) >= 0.1) out.push({ tone: "warn", text: `${top.ticker} 한 종목이 ${pct(top.weight, 0, false)}를 차지합니다(종목당 한도 10%). 추가매수보다 분산을 고려하세요.` });
  const hi = x.correlations.filter(([, , c]) => c >= 0.75);
  if (hi.length) out.push({ tone: "warn", text: `${hi.map(([a, b]) => `${a}·${b}`).join(", ")}는 거의 같이 움직입니다. 사실상 같은 종목을 여러 개 가진 효과입니다.` });
  if (cashW < 0.05) out.push({ tone: "warn", text: `현금이 ${pct(cashW, 1, false)}뿐이라 새 종목은 소액만 가능합니다.` });
  else out.push({ tone: "info", text: `현금 비중은 ${pct(cashW, 0, false)}입니다.` });
  if (x.beta !== null) out.push({ tone: "info", text: `시장이 1% 움직일 때 이 포트폴리오는 평균 약 ${num(x.beta, 2)}% 움직였습니다.` });
  if (out.every((o) => o.tone === "info")) out.unshift({ tone: "info", text: "업종·종목 쏠림 없이 비교적 고르게 분산되어 있습니다." });
  return out;
}

/** New York calendar date (trades are dated by the exchange's day). */
export function nyToday(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

export default function Portfolio() {
  const p = useApi<Pf>("/portfolio");
  usePoll(p.reload, 3_000, !!p.data?.history_pending?.length); // prices the store lacked are being fetched in the background
  const [params] = useSearchParams();
  const [row, setRow] = useState({ ticker: "", quantity: "", cost: "" });
  const [cash, setCash] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [sellFor, setSellFor] = useState<string | null>(null);
  if (p.state === "loading") return <Loading what="포트폴리오 재계산" steps={["보유 종목 종가 확인", "평가금액·손익 계산", "쏠림·상관관계 점검"]} />;
  if (!p.data) return <Err error={p.error} retry={p.reload} />;
  const save = async (body: unknown): Promise<boolean> => {
    setErr(null);
    try { await api.put("/portfolio", body); p.reload(); refreshQuoteSubscriptions(); return true; } catch (e) { setErr(e instanceof Error ? e.message : String(e)); return false; }
  };
  // removing an entered line = saving it with quantity 0 (the trade records are never touched here)
  const removeManual = async (tickers: string[], tag: string) => {
    if (busy) return;
    setBusy(tag);
    try { await save({ holdings: tickers.map((t) => ({ ticker: t, quantity: 0, cost_basis: 0 })) }); } finally { setBusy(null); }
  };
  const removeHolding = async (ticker: string) => {
    if (busy) return;
    setBusy(ticker);
    setErr(null);
    try { await api.del(`/portfolio/holdings/${encodeURIComponent(ticker)}`); p.reload(); refreshQuoteSubscriptions(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  };
  // "수정": the entered line goes back into the form below, in view, quantity focused
  const edit = (h: HoldingV) => {
    setRow({ ticker: h.ticker, quantity: String(h.quantity), cost: String(h.cost_basis) });
    requestAnimationFrame(() => {
      const el = document.querySelector<HTMLInputElement>('input[aria-label="보유 수량"]');
      el?.scrollIntoView?.({ block: "center" });
      el?.focus();
      el?.select();
    });
  };
  const x = p.data;
  const unused = x.unused_manual ?? [];
  const assumed = x.cash_entered === false;
  const existing = row.ticker.trim() ? x.holdings.find((h) => h.ticker === row.ticker.trim().toUpperCase()) : undefined;
  const reading = interpret(x);
  const unvalued = x.valuation_status === "UNAVAILABLE";
  const empty = !x.holdings.length;  // no holdings: nothing to diversify and no P&L — never "좋음" or a green ▲ $0  // holdings exist but none could be valued (review 2026-09-28 F10)
  const weights: [string, number][] = [...x.holdings.filter((h) => (h.market_value ?? 0) > 0).map((h) => [h.ticker, h.market_value ?? 0] as [string, number]), ["현금", x.cash]];
  const pnlPct = x.invested_value - x.unrealized_pnl > 0 ? x.unrealized_pnl / (x.invested_value - x.unrealized_pnl) : null;
  return (
    <div className="grid">
      <div className="page-head"><div><h1>내 포트폴리오</h1><div className="t-sub">{x.note}</div></div></div>
      <StaleData error={p.error} at={p.fetchedAt} retry={p.reload} />
      {(x.history_pending?.length ?? 0) > 0 && <Notice tone="info">가격 이력을 받는 중: {x.history_pending?.join(", ")} — 받는 대로 평가금액을 다시 계산합니다.</Notice>}
      {x.missing_prices.length > 0 && (
        <Ribbon tone="warn" cap="가격 없음" testId="missing-prices">
          <b>{x.missing_prices.join(", ")}</b>의 종가를 받지 못해 평가금액·비중·손익 계산에서 뺐습니다. 매입가로 대신 계산하지 않습니다. 그래서 아래 합계와 비중은 이 종목을 뺀 값입니다.
        </Ribbon>
      )}
      {unused.length > 0 && (
        <Ribbon tone="info" cap="겹친 입력" testId="unused-manual">
          <b>{unused.map((u) => u.ticker).join(", ")}</b>는 거래 기록이 있어 보유 수량·평단을 거래 기록으로 계산하고 있습니다. 위에서 직접 입력한 줄({unused.map((u) => `${u.ticker} ${shares(u.quantity)}주`).join(", ")})은 계산에 쓰이지 않으니 지워도 결과가 바뀌지 않습니다.{" "}
          <button className="sm" disabled={busy === "unused"} onClick={() => void removeManual(unused.map((u) => u.ticker), "unused")}>{busy === "unused" ? "지우는 중…" : "직접 입력한 줄 지우기"}</button>
        </Ribbon>
      )}
      {x.notes.filter((n) => !(x.missing_prices.length && n.startsWith("가격 데이터 없는 보유 종목")) && !unused.some((u) => n.startsWith(`${u.ticker}: 수동 입력 줄(`))).map((n, i) => <Notice key={i} tone="warn">{n}</Notice>)}
      <Err error={err} />
      <div className="g4">
        {unvalued ? (
          <Card title="총 평가금액" testId="nav-unavailable"><div className="t-key muted">평가 불가</div><div className="caption">보유 종목의 공통 거래일 종가가 없어 합계를 계산하지 않았습니다(현금 {price(x.cash)}만으로 표시하지 않음).</div></Card>
        ) : assumed && empty ? (
          <Card title="총 평가금액"><div className="t-key muted">—</div><div className="caption">현금과 보유 종목을 입력하면 계산합니다</div></Card>
        ) : <Card title="총 평가금액"><div className="t-key">{price(x.nav)}</div><div className="caption">{Math.abs(x.nav) >= 1e8 ? `${usdWithKo(x.nav)} · ` : ""}{empty ? "현금만" : `기준일 ${day(x.valuation_day)} 종가`}{x.valuation_status === "PARTIAL" ? " · 일부 종목 제외" : ""}{assumed ? ` · 현금은 가정값 ${price(x.cash)}` : ""}</div></Card>}
        <Card title="평가손익">{unvalued ? <><div className="t-key muted">평가 불가</div><div className="caption">가격을 확인한 뒤 계산합니다</div></> : empty ? <><div className="t-key muted">—</div><div className="caption">보유 종목이 없어 손익이 없습니다</div></>
          : <><div className={`t-key ${x.unrealized_pnl > 0 ? "pos" : x.unrealized_pnl < 0 ? "neg" : ""}`}>{x.unrealized_pnl > 0 ? "▲ " : x.unrealized_pnl < 0 ? "▼ " : ""}{price(x.unrealized_pnl)}</div><div className="caption">매입금액 대비 {pct(pnlPct)}{x.missing_prices.length ? " · 가격 없는 종목 제외" : ""}</div></>}</Card>
        {assumed ? <Card title="현금" testId="cash-not-entered"><div className="t-key muted">미입력</div><div className="caption">매수 수량은 가정 금액 {price(x.cash)} 기준으로 계산합니다 — 아래에서 실제 현금을 입력하세요</div></Card> : <Card title="현금"><div className="t-key">{price(x.cash)}</div><div className="caption">{unvalued ? "전체 대비 비중 계산 불가" : empty ? "전부 현금" : `전체의 ${pct(x.nav > 0 ? x.cash / x.nav : null, 0, false)}`}</div></Card>}
        <Card title="분산 정도">{unvalued ? <><div className="t-key muted">판단 불가</div><div className="caption">평가금액이 없어 집중도를 계산할 수 없습니다</div></> : empty ? <><div className="t-key muted">해당 없음</div><div className="caption">보유 종목이 생기면 집중도·베타를 계산합니다</div></>
          : <><div className="t-key">{x.hhi < 0.15 ? "좋음" : x.hhi < 0.3 ? "보통" : "쏠림"}</div><div className="caption"><Term k="hhi">집중도(HHI)</Term> {num(x.hhi, 2)} · <Term k="beta">베타</Term> {num(x.beta, 2)}</div></>}</Card>
      </div>
      {!empty && (
        <Card title="보유 종목" explain="평가액·손익·비중은 모든 종목을 같은 거래일 종가로 계산합니다. ‘최신 시세’는 표시용입니다." testId="holdings">
<div className="scroll"><table><thead><tr><th>종목</th><th>수량</th><th>매입 단가</th><th>종가(기준일)</th><th title="표시용 최신 시세 — 평가액·손익은 모든 종목 같은 거래일 종가 기준">최신 시세</th><th>평가액</th><th>평가손익</th><th>비중</th><th>섹터</th><th className="row-actions"><span className="sr-only">정리</span></th></tr></thead>
            <tbody>{x.holdings.map((h) => <tr key={h.ticker} className="nowrap-row"><td><Link to={`/stocks/${h.ticker}`}><b>{h.ticker}</b></Link></td><td>{shares(h.quantity)}{h.source === "ledger" ? <div className="caption" title={`거래 기록에서 계산 · 실현 손익 ${price(h.realized_pnl ?? 0)} · 배당 ${price(h.dividends ?? 0)}`}>거래 기록 기준</div> : null}{h.split_adjusted && h.split_adjusted !== 1 ? <span className="caption" title="입력한 뒤 주식분할이 있어 수량과 매입 단가를 오늘 기준으로 환산했습니다"> 분할 반영 ×{num(h.split_adjusted, 2)}</span> : null}</td><td>{price(h.cost_basis)}</td><td>{h.price === null ? <span className="warn">가격 없음 · 평가 제외</span> : <>{price(h.price)}<div className="caption">{day(h.price_day)}</div></>}</td>
              <td><LivePrice ticker={h.ticker} size="sm" /></td>
              <td>{h.market_value === null ? "—" : price(h.market_value)}</td><td className={h.unrealized_pnl === null ? "" : h.unrealized_pnl >= 0 ? "pos" : "neg"}>{h.unrealized_pnl === null ? "계산 안 함" : <>{h.unrealized_pnl >= 0 ? "▲" : "▼"} {price(h.unrealized_pnl)} ({pct(h.unrealized_pct)})</>}</td><td>{h.weight === null ? "—" : pct(h.weight, 1, false)}</td><td className="wrap" style={{ minWidth: 110 }}>{h.sector}</td>
              <td className="row-actions"><div className="row tight" style={{ flexWrap: "nowrap", justifyContent: "flex-end" }}>
                {h.source === "ledger"
                  ? <button type="button" className="sm" title="일부나 전부를 팔았다면 매도를 기록하세요 — 수량·평단·실현 손익이 계산됩니다" onClick={() => setSellFor(`${h.ticker}:${h.quantity}:${Date.now()}`)}>매도 기록</button>
                  : <button type="button" className="sm" title="수량·매입 단가를 고칩니다" onClick={() => edit(h)}>수정</button>}
                <ConfirmButton label={busy === h.ticker ? "삭제 중…" : "삭제"} icon={<ITrash />} busy={busy === h.ticker} ariaLabel={`${h.ticker} 포트폴리오에서 삭제`} testId={`remove-${h.ticker}`}
                  confirm={h.source === "ledger" ? <>{h.ticker} 거래 기록을 모두 지울까요? <span className="caption">판 것이면 ‘매도 기록’이 맞습니다</span></> : <>{h.ticker} {shares(h.quantity)}주를 뺄까요?</>}
                  onConfirm={() => void removeHolding(h.ticker)} />
              </div></td></tr>)}</tbody></table></div>
          {x.correlations.length > 0 && <div className="caption" style={{ marginTop: 8 }}><Term k="correlation">상관계수</Term>: {x.correlations.map(([a, b, c]) => `${a}↔${b} ${num(c, 2)}`).join(" · ")}</div>}
        </Card>
      )}
      <div className="g2">
        <Card title="한 줄 해석" icon="✎">
          {reading.length ? <ul className="list">{reading.map((r, i) => <li key={i}><span className={`dot ${r.tone === "warn" ? "warn" : "info"}`}>{r.tone === "warn" ? "!" : "i"}</span><span>{r.text}</span></li>)}</ul> : <Empty hint="아래에서 종목 코드·수량·매입 단가를 입력하세요.">아직 보유 종목이 없습니다.</Empty>}
        </Card>
        <Card title="비중 한눈에 보기" icon="◔">
          {x.holdings.length ? <Donut parts={weights} /> : <Empty>보유 종목을 입력하면 비중 차트가 보입니다.</Empty>}
          {Object.keys(x.sector_weights).length > 0 && (
            <div className="sector-mix" data-testid="sector-mix">
              <div className="t-kicker">업종 쏠림 <span className="caption">(30% 넘으면 주의)</span></div>
              {Object.entries(x.sector_weights).sort((a, b) => b[1] - a[1]).map(([s, w]) => (
                <div key={s} className="mix-row"><span>{s}</span><div className="bar"><div style={{ width: `${Math.min(100, w * 100)}%`, background: w > 0.3 ? "var(--warn)" : undefined }} /></div><b className={w > 0.3 ? "warn" : ""}>{pct(w, 1, false)}</b></div>
              ))}
            </div>
          )}
        </Card>
      </div>
      <Card title="보유 종목 직접 입력" explain="증권사 기록 없이 보유만 빠르게 입력합니다. 고치려면 보유 표의 ‘수정’, 빼려면 ‘삭제’를 누르세요. 거래 기록이 있는 종목은 거래 기록이 우선입니다. MarketLens는 실제 주문을 넣지 않습니다.">
        <form className="form-grid holding" onSubmit={(e) => {
          e.preventDefault();
          const q = parseAmount(row.quantity), c = parseAmount(row.cost);
          if (!/^[A-Z][A-Z0-9.-]{0,9}$/.test(row.ticker.trim().toUpperCase())) { setErr("종목 코드를 확인하세요 (예: NVDA, BRK.B)."); return; }
          if (row.quantity.trim() === "" || !Number.isFinite(q) || q <= 0) { setErr("수량은 0보다 커야 합니다. 종목을 빼려면 보유 표의 ‘삭제’를 누르세요."); return; }
          if (row.cost.trim() === "" || !Number.isFinite(c) || c <= 0) { setErr("평균 매입 단가는 0보다 커야 합니다."); return; }
          void save({ holdings: [{ ticker: row.ticker.trim().toUpperCase(), quantity: q, cost_basis: c }] }).then((ok) => { if (ok) setRow({ ticker: "", quantity: "", cost: "" }); });
        }}>
          <label><span>종목 코드</span><input aria-label="보유 종목 코드" placeholder="예: NVDA" value={row.ticker} onChange={(e) => setRow({ ...row, ticker: e.target.value })} /></label>
          <label><span>수량(주)</span><input aria-label="보유 수량" inputMode="decimal" placeholder="0" value={row.quantity} onChange={(e) => setRow({ ...row, quantity: e.target.value })} /></label>
          <label><span>평균 매입 단가(USD)</span><input aria-label="매입 단가(USD)" inputMode="decimal" placeholder="0.00" value={row.cost} onChange={(e) => setRow({ ...row, cost: e.target.value })} /></label>
          <button className="primary">{existing?.source === "manual" ? "수정 저장" : "보유 저장"}</button>
        </form>
        {existing?.source === "manual" && <div className="caption" data-testid="holding-edit-hint">이미 입력한 {existing.ticker} {shares(existing.quantity)}주 · 평단 {price(existing.cost_basis)}을(를) 이 값으로 바꿉니다.</div>}
        {existing?.source === "ledger" && <Notice tone="warn">{existing.ticker}는 거래 기록으로 계산하는 종목이라 여기 입력한 줄은 쓰이지 않습니다. 아래 ‘거래 기록’에 매수·매도를 적으세요.</Notice>}
        <form className="form-grid cash" onSubmit={(e) => { e.preventDefault(); const v = parseAmount(cash); if (cash.trim() !== "" && Number.isFinite(v) && v >= 0) void save({ cash: v }).then((ok) => { if (ok) setCash(""); }); else setErr("현금은 0 이상의 숫자여야 합니다."); }}>
          <label><span>현금(USD) <em className="caption">{assumed ? `미입력 · 가정 ${price(x.cash)}` : `지금 ${price(x.cash)}`}</em></span><input aria-label="현금(USD)" inputMode="decimal" value={cash} onChange={(e) => setCash(e.target.value)} placeholder="예: 25000" /></label>
          <button>현금 저장</button>
        </form>
      </Card>
      <Ledger today={nyToday()} prefill={sellFor ?? params.get("trade")} onChange={() => { p.reload(); refreshQuoteSubscriptions(); }} />

    </div>
  );
}
