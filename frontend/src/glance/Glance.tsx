import { useEffect, useRef, useState } from "react";
import { useApi, usePoll, useSettling } from "../components/useApi";
import { effectiveState, useQuote, useViewQuotes, type QuoteRow } from "../quotes";
import { quantityShown } from "../advice";
import { ACTION_INFO, REGIME_KO } from "../i18n";
import { splitCandidates, type Dash } from "../pages/Dashboard";
import type { LiveJudgement } from "../components/liveBoard";
import type { StockDetail, SystemInfo } from "../types";
import { FOCUS_STORAGE_KEY, closeGlance, focusSymbol, isDesktop, openAnalyze, saveOnTop, savedOnTop, setAlwaysOnTop, setFocusSymbol } from "./desktop";
import "./glance.css";

/** GLANCE MODE (owner 2026-10-04, redesign v2): a quiet desktop object. The eye lands on the focus symbol — its price
 * and the engine's call as one group — then on the market as background and the plan's three prices. Every value is
 * read from the existing engine (the same endpoints the full app polls); nothing is decided here. An optional value
 * without data folds its row away, never N/A. Static unless a value actually changes. */
export default function Glance() {
  const sys = useApi<SystemInfo>("/system");
  const dash = useApi<Dash>("/dashboard");
  const macro = useSettling<MacroResp>("/macro");  // re-asks until the first fetch settles, like the Market page
  usePoll(sys.reload, 60_000);
  usePoll(dash.reload, 60_000);
  usePoll(dash.reload, 3_000, !!dash.data?.regime_status?.pending);  // the regime still loading on the server
  usePoll(macro.reload, 300_000);
  const focus = useFocus(dash.data);
  const others = candidates(dash.data, focus);
  useViewQuotes(["SPY", "QQQ", focus, ...others]);
  const delayed = !!(sys.error || dash.error || macro.error);
  if (!sys.data && !dash.data) {
    return (
      <Shell session={null} delayed={false}>
        <div className="gl-loading" aria-busy="true">
          <div className="gl-ph band" />
          <div className="gl-ph w40" /><div className="gl-ph w70" /><div className="gl-ph spark" />
          <p>시장 상태를 불러오는 중…</p>
        </div>
      </Shell>
    );
  }
  return (
    <Shell session={sys.data?.market?.session ?? null} delayed={delayed}>
      <Market d={dash.data} m={macro.data} />
      {focus ? <Focus ticker={focus} others={others} /> : <div className="gl-quiet">종목 화면을 열면 그 종목을 여기서 따라갑니다</div>}
    </Shell>
  );
}

// ------------------------------------------------------------------ frame
const SESSION: Record<string, { label: string; tone: string }> = {
  REGULAR: { label: "장중", tone: "on" }, PREMARKET: { label: "장전", tone: "pre" }, AFTER_HOURS: { label: "장후", tone: "pre" },
  OVERNIGHT: { label: "야간", tone: "pre" }, CLOSED: { label: "장 마감", tone: "off" },
};

function Shell({ session, delayed, children }: { session: string | null; delayed: boolean; children: React.ReactNode }) {
  const s = session ? SESSION[session] : null;
  const [menu, setMenu] = useState(false);
  return (
    <div className={`glance${isDesktop() ? " desk" : ""}`} data-testid="glance">
      <header className="gl-head" data-tauri-drag-region>
        <span className="gl-brand" data-tauri-drag-region><i aria-hidden />MarketLens</span>
        <span className="gl-session" data-tauri-drag-region title={delayed ? "일부 데이터 갱신 지연 — 마지막 값을 표시합니다" : undefined}>
          {delayed ? "갱신 지연" : s?.label ?? ""}<i className={`gl-dot ${delayed ? "warn" : s?.tone ?? "off"}`} aria-hidden />
        </span>
        <nav className="gl-ctrl" aria-label="Glance 제어">
          <button type="button" aria-label="Glance 설정" aria-expanded={menu} onClick={() => setMenu((v) => !v)}>
            <svg viewBox="0 0 16 16" aria-hidden><circle cx="3.5" cy="8" r="1.2" /><circle cx="8" cy="8" r="1.2" /><circle cx="12.5" cy="8" r="1.2" /></svg>
          </button>
          <button type="button" aria-label="전체 MarketLens 열기" onClick={() => void openAnalyze("/")}>
            <svg viewBox="0 0 16 16" aria-hidden><path d="M6 4h6v6M12 4l-7.5 7.5" /></svg>
          </button>
          <button type="button" aria-label="Glance 닫기" onClick={() => void closeGlance()}>
            <svg viewBox="0 0 16 16" aria-hidden><path d="M4.5 4.5l7 7M11.5 4.5l-7 7" /></svg>
          </button>
        </nav>
      </header>
      {menu && <Menu onClose={() => setMenu(false)} />}
      <div className="gl-body">{children}</div>
    </div>
  );
}

function Menu({ onClose }: { onClose: () => void }) {
  const [top, setTop] = useState(savedOnTop);
  const [sym, setSym] = useState(focusSymbol() ?? "");
  return (
    <div className="gl-menu" role="dialog" aria-label="Glance 설정" onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}>
      {isDesktop() && (
        <div className="gl-opt"><span>항상 위에 표시</span>
          <button type="button" role="switch" aria-checked={top} aria-label="항상 위에 표시" className={top ? "on" : ""}
            onClick={() => { const v = !top; setTop(v); saveOnTop(v); void setAlwaysOnTop(v); }}><i /></button>
        </div>
      )}
      <form className="gl-opt" onSubmit={(e) => { e.preventDefault(); if (sym.trim()) followSymbol(sym.trim()); onClose(); }}>
        <span>따라갈 종목</span><input aria-label="따라갈 종목" value={sym} onChange={(e) => setSym(e.target.value.toUpperCase())} placeholder="NVDA" maxLength={12} />
      </form>
    </div>
  );
}

function followSymbol(t: string) {
  setFocusSymbol(t);
  window.dispatchEvent(new Event("ml-focus"));
}

// ------------------------------------------------------------------ market (the background)
interface MacroResp { available?: boolean; pending?: boolean; refreshing?: boolean; series?: Record<string, { latest: { value: number | null; quality: string }; pct_change_20d: number | null }> }

/** The engine's regime folded to four plain states; the regime's own name stays beside it when it says more. */
const STATE: Record<string, { label: string; tone: string }> = {
  "Risk On": { label: "위험 선호", tone: "pos" }, "AI Momentum": { label: "위험 선호", tone: "pos" }, "Multiple Expansion": { label: "위험 선호", tone: "pos" },
  "Liquidity Expansion": { label: "위험 선호", tone: "pos" },
  Neutral: { label: "중립", tone: "" }, "Defensive Rotation": { label: "주의", tone: "warn" }, "Liquidity Tightening": { label: "주의", tone: "warn" },
  "Commodity Shock": { label: "주의", tone: "warn" }, "Inflation Shock": { label: "주의", tone: "warn" }, "Growth Scare": { label: "주의", tone: "warn" },
  "Multiple Compression": { label: "주의", tone: "warn" }, "Risk Off": { label: "위험 회피", tone: "neg" }, "Credit Stress": { label: "위험 회피", tone: "neg" },
};
const BASIC = new Set(["Risk On", "Risk Off", "Neutral"]);
const regimeShort = (r: string) => (REGIME_KO[r] ?? r).replace(/\s*\(.*\)$/, "");

function Market({ d, m }: { d: Dash | null | undefined; m: MacroResp | null | undefined }) {
  const spy = useQuote("SPY").row;
  const qqq = useQuote("QQQ").row;
  const primary = d?.regime?.primary;
  const st = primary ? STATE[primary] : undefined;
  const reading = d?.regime?.readings?.find((r) => r.regime === primary);
  // the regime's confidence is how much of its macro data was available (0..1), not a probability
  const conf = st && reading && Number.isFinite(reading.confidence) ? Math.round(reading.confidence <= 1 ? reading.confidence * 100 : reading.confidence) : null;
  const val = (id: string) => {
    const s = m?.series?.[id];
    return s && s.latest.value !== null && !["MISSING", "CONFLICTING"].includes(s.latest.quality) ? s.latest.value : null;
  };
  const vix = val("VIX"), tnx = val("US10Y");
  // SPY / QQQ are the ETFs' own moves, labelled as such (not the indices)
  const items: { k: string; v: string; t: string }[] = [];
  if (spy?.change_pct != null) items.push({ k: "SPY", v: signed(spy.change_pct), t: tone(spy.change_pct) });
  if (qqq?.change_pct != null) items.push({ k: "QQQ", v: signed(qqq.change_pct), t: tone(qqq.change_pct) });
  if (vix !== null) items.push({ k: "VIX", v: vix.toFixed(1), t: "" });
  if (tnx !== null) items.push({ k: "10년물", v: `${tnx.toFixed(2)}%`, t: "" });
  return (
    <section className="gl-market">
      <button type="button" className="gl-mstate" onClick={() => void openAnalyze("/market")} data-testid="glance-market"
        title={conf !== null ? "데이터 충족도: 국면 판단에 쓴 거시 데이터가 얼마나 갖춰졌는지(확률 아님)" : undefined}>
        <span className="gl-mk">미국 시장</span>
        {st ? <span className={`gl-mv ${st.tone}`}><i aria-hidden />{st.label}</span> : <span className="gl-mv muted">판단 대기</span>}
        {st && primary && !BASIC.has(primary) && <span className="gl-msub">{regimeShort(primary)}</span>}
        {conf !== null && <span className="gl-mconf">데이터 {conf}%</span>}
        <span className="gl-chev" aria-hidden>›</span>
      </button>
      {items.length > 0 && (
        <dl className="gl-metrics" data-testid="glance-metrics">
          {items.map((x) => <div key={x.k}><dt>{x.k}</dt><dd className={x.t}>{x.v}</dd></div>)}
        </dl>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ focus symbol (the centre)
/** The symbol to follow: the last stock page opened (shared storage, updated live from the main window), else the
 * first buy the engine currently stands behind. */
function useFocus(d: Dash | null | undefined): string | null {
  const [stored, setStored] = useState<string | null>(focusSymbol);
  useEffect(() => {
    const on = (e: Event) => { if (!(e instanceof StorageEvent) || e.key === FOCUS_STORAGE_KEY) setStored(focusSymbol()); };
    window.addEventListener("storage", on);
    window.addEventListener("ml-focus", on);
    return () => { window.removeEventListener("storage", on); window.removeEventListener("ml-focus", on); };
  }, []);
  if (stored) return stored;
  const first = d ? splitCandidates(d.top_opportunities ?? []).valid[0] : undefined;
  return first?.ticker ?? null;
}

const ACTION_TONE: Record<string, string> = {
  BUY: "pos", "BUY SMALL": "pos", ADD: "pos", HOLD: "", WAIT: "", WATCH: "warn", REDUCE: "neg", SELL: "neg", AVOID: "neg",
};
const BULL = new Set(["BUY", "BUY SMALL", "ADD"]);

function Focus({ ticker, others }: { ticker: string; others: string[] }) {
  const live = useApi<{ live: (LiveJudgement & { rec_id: number }) | null }>(`/stocks/${encodeURIComponent(ticker)}/live`, [ticker]);
  const detail = useApi<StockDetail>(`/stocks/${encodeURIComponent(ticker)}`, [ticker]);
  usePoll(live.reload, 15_000);
  usePoll(detail.reload, 600_000);
  const quote = useQuote(ticker);
  const q = quote.row;
  const j = live.data?.live ?? null;
  const rec = detail.data?.recommendation;
  const action = j?.action ?? rec?.action ?? null;
  const flash = useFlash(action);
  const name = rec?.company && rec.company.toUpperCase() !== ticker ? rec.company : null;
  const open = () => void openAnalyze(`/stocks/${ticker}`);
  if (!action) {
    const loading = live.state === "loading" || detail.state === "loading";
    return (
      <section className="gl-focus" data-testid="glance-focus">
        <button type="button" className="gl-fhead" onClick={open}>
          <span className="gl-name"><b>{name ?? ticker}</b>{name && <small>{ticker}</small>}</span>
        </button>
        <p className="gl-unavail">{loading ? "불러오는 중…" : "분석 데이터 없음 — 종목 화면에서 분석을 시작할 수 있습니다"}</p>
        {others.length > 0 && <Others list={others} />}
      </section>
    );
  }
  const price = q?.price ?? j?.price ?? rec?.price ?? null;
  const chg = q?.change_pct ?? null;
  const cur = currency(ticker);
  const buyBelow = j?.max_buy ?? rec?.max_buy ?? null;
  const target = j?.target1 ?? rec?.target ?? null;
  const invalid = j?.stop ?? rec?.stop ?? null;
  const rr = j?.rr ?? rec?.rr ?? null;
  const status = j?.current_status ?? rec?.current_status ?? null;
  const actionable = j ? j.actionable_now : rec?.actionable_now;
  const plan = detail.data?.position_plan;
  const size = plan?.available && plan.amount && quantityShown(status, actionable) ? plan : null;
  const recheck = BULL.has(action) && (status !== "CURRENT" || actionable === false);
  // material changes against the previous analysis only (the brief's other "changed" lines are context facts)
  const changes = (detail.data?.brief?.changed ?? []).filter((c) => c.label === "지난 분석 대비" && c.kind === "CALC").length;
  const hist = (detail.data?.price_history ?? []).slice(-60).map((p) => p.close);
  const fresh = freshness(q, quote.link, j?.at ?? null);
  const dist = (lv: number | null) => (lv !== null && price !== null && price > 0 ? lv / price - 1 : null);
  const levels = [
    { k: "매수 상한", v: buyBelow, cls: "lead", title: "이 가격 이하에서만 계획대로 매수(최대 매수가)" },
    { k: "목표", v: target, cls: "", title: "1차 목표가" },
    { k: "무효화", v: invalid, cls: "neg", title: "종가가 이 아래로 마감하면 분석 무효(손절 기준가 · 종가 기준) — 주문 가격이 아닙니다" },
  ].filter((x) => x.v !== null);
  return (
    <section className={`gl-focus${flash ? " flash" : ""}`} data-testid="glance-focus">
      <button type="button" className="gl-fhead" onClick={open} aria-label={`${ticker} 종목 화면 열기`}>
        <span className="gl-name"><b>{name ?? ticker}</b>{name && <small>{ticker}</small>}</span>
        <span className={`gl-action ${ACTION_TONE[action] ?? ""}`} data-testid="glance-action" title={ACTION_INFO[action]?.help}>
          {ACTION_INFO[action]?.label ?? action}<small>{action}</small>
        </span>
      </button>
      {price !== null && (
        <div className="gl-price">
          <b>{money(price, cur)}</b>
          {chg !== null && <span className={tone(chg)}>{signed(chg)}</span>}
        </div>
      )}
      <div className={`gl-fresh ${fresh.tone}`}>{fresh.text}{recheck && <em>{status === "CURRENT" ? "가격이 계획 범위 밖" : "재확인 필요"}</em>}</div>
      {hist.length > 5 && <Sparkline values={hist} />}
      {levels.length > 0 && (
        <dl className="gl-levels" data-testid="glance-rows">
          {levels.map((x) => (
            <div key={x.k} className={x.cls} title={x.title}>
              <dt>{x.k}</dt><dd className={money(x.v!, cur).length > 9 ? "long" : undefined}>{money(x.v!, cur)}</dd>
              {dist(x.v) !== null && <span className="gl-dist">{signed(dist(x.v)!, 1)}</span>}
            </div>
          ))}
        </dl>
      )}
      {(size || rr !== null) && (
        <p className="gl-meta" data-testid="glance-meta">
          {size && <span title="상세 화면의 권장 매수 금액(비중 한도 반영)">권장 매수 <b>{money(size.amount!, cur, 0)}</b>{size.shares ? <> · {size.shares}주</> : null}</span>}
          {rr !== null && <span title="현재가 기준 손익비 = 목표까지 상승폭 ÷ 무효화까지 하락폭 (엔진 계산)">손익비 <b>{rr.toFixed(1)}</b></span>}
        </p>
      )}
      {others.length > 0 && <Others list={others} />}
      <button type="button" className={`gl-changes${changes ? " on" : ""}`} onClick={open} data-testid="glance-changes">
        {detail.data ? (changes ? <><i aria-hidden />지난 분석 대비 {changes}건 변화<span className="gl-chev" aria-hidden>›</span></> : "중요한 변화 없음") : ""}
      </button>
    </section>
  );
}

/** How fresh the shown price is, from the quote stream's own state — an old price never reads as live. */
function freshness(q: QuoteRow | undefined, link: Parameters<typeof effectiveState>[1], judgedAt: string | null): { text: string; tone: string } {
  const s = effectiveState(q, link);
  const at = q?.trade_time ?? q?.received_time ?? judgedAt;
  const t = at ? new Date(at) : null;
  const hm = t && !Number.isNaN(t.getTime()) ? t.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false }) : null;
  const when = hm ? ` · ${hm}` : "";
  switch (s) {
    case "LIVE": case "QUIET": return { text: `실시간${when}`, tone: "" };
    case "CLOSED_LAST": return { text: `마감 · 마지막 체결가${when}`, tone: "" };
    case "EXTENDED_NO_TRADE": return { text: `직전 체결가${when}`, tone: "" };
    case "DELAYED": return { text: `지연 시세${when}`, tone: "warn" };
    case "RECONNECTING": return { text: `재연결 중 · 마지막 값${when}`, tone: "warn" };
    default: return { text: judgedAt ? `판단 시점 가격${when}` : "시세 미수신", tone: "warn" };
  }
}

// ------------------------------------------------------------------ other candidates (optional)
function candidates(d: Dash | null | undefined, focus: string | null): string[] {
  if (!d) return [];
  return splitCandidates(d.top_opportunities ?? []).valid.map((r) => r.ticker).filter((t) => t !== focus).slice(0, 3);
}

function Others({ list }: { list: string[] }) {
  return (
    <nav className="gl-others" aria-label="다른 후보" data-testid="glance-others">
      <span className="gl-ok">후보</span>
      {list.map((t) => <Other key={t} t={t} />)}
    </nav>
  );
}

function Other({ t }: { t: string }) {
  const c = useQuote(t).row?.change_pct ?? null;
  return (
    <button type="button" onClick={() => followSymbol(t)} title={`${t}를 따라가기`}>
      {t}{c !== null && <span className={tone(c)}>{signed(c, 1)}</span>}
    </button>
  );
}

// ------------------------------------------------------------------ bits
/** A one-time accent when the action changes (WAIT → BUY); never a loop. */
function useFlash(v: string | null): boolean {
  const prev = useRef(v);
  const [on, setOn] = useState(false);
  useEffect(() => {
    if (prev.current !== null && v !== null && prev.current !== v) {
      setOn(true);
      const id = window.setTimeout(() => setOn(false), 600);
      prev.current = v;
      return () => window.clearTimeout(id);
    }
    prev.current = v;
    return undefined;
  }, [v]);
  return on;
}

function Sparkline({ values }: { values: number[] }) {
  const lo = Math.min(...values), hi = Math.max(...values);
  const w = 300, h = 44;
  const xy = values.map((v, i) => [(i / (values.length - 1)) * w, h - 3 - ((v - lo) / (hi - lo || 1)) * (h - 6)] as const);
  const line = xy.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const up = values[values.length - 1]! >= values[0]!;
  return (
    <figure className={`gl-spark ${up ? "up" : "down"}`} aria-label={`최근 ${values.length}거래일 종가 흐름`}>
      <figcaption>종가 {values.length}일</figcaption>
      <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden>
        <polygon points={`0,${h} ${line} ${w},${h}`} className="area" />
        <polyline points={line} fill="none" vectorEffect="non-scaling-stroke" />
      </svg>
    </figure>
  );
}

const currency = (t: string): "USD" | "KRW" => (/^\d{6}(\.K[SQ])?$/.test(t) ? "KRW" : "USD");
const money = (v: number, cur: "USD" | "KRW", digits = 2) =>
  cur === "KRW" ? `₩${Math.round(v).toLocaleString("ko-KR")}` : `$${v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
const signed = (v: number, d = 2) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(d)}%`;
const tone = (v: number) => (v > 0 ? "pos" : v < 0 ? "neg" : "");
