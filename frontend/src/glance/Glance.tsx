import { useEffect, useRef, useState } from "react";
import { useApi, usePoll, useSettling } from "../components/useApi";
import { useQuote, useViewQuotes } from "../quotes";
import { quantityShown } from "../advice";
import { splitCandidates, type Dash } from "../pages/Dashboard";
import type { LiveJudgement } from "../components/liveBoard";
import type { StockDetail, SystemInfo } from "../types";
import { FOCUS_STORAGE_KEY, closeGlance, focusSymbol, isDesktop, openAnalyze, saveOnTop, savedOnTop, setAlwaysOnTop, setFocusSymbol } from "./desktop";
import "./glance.css";

/** GLANCE MODE (owner 2026-10-04): a quiet desktop object, not a dashboard. Four questions only — the market's state,
 * what to do with the focus symbol, the price to buy at or below, and whether anything material changed since the last
 * analysis. Every value is read from the existing engine (the same endpoints the full app polls); nothing is decided
 * here. A row without data is hidden, never filled with N/A. Static unless a value actually changes. */
export default function Glance() {
  const sys = useApi<SystemInfo>("/system");
  const dash = useApi<Dash>("/dashboard");
  const macro = useSettling<MacroResp>("/macro");  // re-asks until the first fetch settles, like the Market page
  usePoll(sys.reload, 60_000);
  usePoll(dash.reload, 60_000);
  usePoll(dash.reload, 3_000, !!dash.data?.regime_status?.pending);  // the regime still loading on the server
  usePoll(macro.reload, 300_000);
  const focus = useFocus(dash.data);
  useViewQuotes(["SPY", "QQQ", focus]);
  const delayed = !!(sys.error || dash.error || macro.error);
  if (!sys.data && !dash.data) {
    return (
      <Shell session={null} delayed={false}>
        <div className="gl-loading"><span className="gl-brand">MARKETLENS</span><span>Loading market state…</span></div>
      </Shell>
    );
  }
  return (
    <Shell session={sys.data?.market?.session ?? null} delayed={delayed}>
      <MarketState d={dash.data} />
      <Metrics m={macro.data} />
      <div className="gl-rule" />
      {focus ? <Focus ticker={focus} /> : <div className="gl-quiet">관심 종목을 열면 여기에 표시됩니다</div>}
    </Shell>
  );
}

// ------------------------------------------------------------------ frame
const SESSION: Record<string, { label: string; tone: string }> = {
  REGULAR: { label: "OPEN", tone: "on" }, PREMARKET: { label: "PRE", tone: "pre" }, AFTER_HOURS: { label: "AFTER", tone: "pre" }, CLOSED: { label: "CLOSED", tone: "off" },
};

function Shell({ session, delayed, children }: { session: string | null; delayed: boolean; children: React.ReactNode }) {
  const s = session ? SESSION[session] : null;
  const [menu, setMenu] = useState(false);
  return (
    <div className={`glance${isDesktop() ? " desk" : ""}`} data-testid="glance">
      <header className="gl-head" data-tauri-drag-region>
        <span className="gl-brand" data-tauri-drag-region>MARKETLENS</span>
        <span className="gl-session" data-tauri-drag-region title={delayed ? "일부 데이터 지연" : undefined}>
          {s ? s.label : ""}<i className={`gl-dot ${delayed ? "warn" : s?.tone ?? "off"}`} aria-hidden />
        </span>
        <nav className="gl-ctrl" aria-label="Glance 제어">
          <button type="button" aria-label="Glance 설정" onClick={() => setMenu((v) => !v)}>•••</button>
          <button type="button" aria-label="전체 MarketLens 열기" onClick={() => void openAnalyze("/")}>↗</button>
          <button type="button" aria-label="Glance 닫기" onClick={() => void closeGlance()}>×</button>
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
    <div className="gl-menu" role="dialog" aria-label="Glance 설정">
      {isDesktop() && (
        <label className="gl-opt"><span>Always on top</span>
          <button type="button" role="switch" aria-checked={top} className={top ? "on" : ""}
            onClick={() => { const v = !top; setTop(v); saveOnTop(v); void setAlwaysOnTop(v); }}>{top ? "ON" : "OFF"}</button>
        </label>
      )}
      <form className="gl-opt" onSubmit={(e) => { e.preventDefault(); if (sym.trim()) { setFocusSymbol(sym.trim()); window.dispatchEvent(new Event("ml-focus")); } onClose(); }}>
        <span>Focus</span><input aria-label="Focus 종목" value={sym} onChange={(e) => setSym(e.target.value.toUpperCase())} placeholder="NVDA" maxLength={12} />
      </form>
    </div>
  );
}

// ------------------------------------------------------------------ market
interface MacroResp { available?: boolean; pending?: boolean; refreshing?: boolean; series?: Record<string, { latest: { value: number | null; quality: string }; pct_change_20d: number | null }> }

const STATE: Record<string, { label: string; tone: string }> = {
  "Risk On": { label: "RISK-ON", tone: "pos" }, "AI Momentum": { label: "RISK-ON", tone: "pos" }, "Multiple Expansion": { label: "RISK-ON", tone: "pos" },
  Neutral: { label: "NEUTRAL", tone: "" }, "Risk Off": { label: "RISK-OFF", tone: "neg" }, "Credit Stress": { label: "RISK-OFF", tone: "neg" },
  "Inflation Shock": { label: "CAUTION", tone: "warn" }, "Growth Scare": { label: "CAUTION", tone: "warn" }, "Multiple Compression": { label: "CAUTION", tone: "warn" },
};

function MarketState({ d }: { d: Dash | null | undefined }) {
  const primary = d?.regime?.primary;
  const st = primary ? STATE[primary] : undefined;
  if (!st) return <section className="gl-market"><div className="gl-label">MARKET</div><div className="gl-state muted">—</div></section>;
  const reading = d?.regime?.readings?.find((r) => r.regime === primary);
  const conf = reading && Number.isFinite(reading.confidence) ? Math.round(reading.confidence <= 1 ? reading.confidence * 100 : reading.confidence) : null;
  const sub = primary && primary !== "Neutral" && !["Risk On", "Risk Off"].includes(primary) ? primary : null;
  return (
    <button type="button" className="gl-market" onClick={() => void openAnalyze("/market")} data-testid="glance-market">
      <div className="gl-label">MARKET</div>
      <div className={`gl-state ${st.tone}`}>{st.label}</div>
      {(conf !== null || sub) && <div className="gl-meta">{sub}{sub && conf !== null ? " · " : ""}{conf !== null ? `Confidence ${conf}` : ""}</div>}
    </button>
  );
}

function Metrics({ m }: { m: MacroResp | null | undefined }) {
  const spy = useQuote("SPY").row;
  const qqq = useQuote("QQQ").row;
  const val = (id: string) => {
    const s = m?.series?.[id];
    return s && s.latest.value !== null && !["MISSING", "CONFLICTING"].includes(s.latest.quality) ? s.latest.value : null;
  };
  const vix = val("VIX"), tnx = val("US10Y");
  const items: [string, string, string][] = [];
  if (spy?.change_pct != null) items.push(["SPY", signed(spy.change_pct), tone(spy.change_pct)]);
  if (qqq?.change_pct != null) items.push(["QQQ", signed(qqq.change_pct), tone(qqq.change_pct)]);
  if (vix !== null) items.push(["VIX", vix.toFixed(1), ""]);
  if (tnx !== null) items.push(["10Y", `${tnx.toFixed(2)}%`, ""]);
  if (!items.length) return null;
  return <div className="gl-metrics" data-testid="glance-metrics">{items.map(([k, v, t]) => <div key={k}><span>{k}</span><b className={t}>{v}</b></div>)}</div>;
}

// ------------------------------------------------------------------ focus symbol
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

function Focus({ ticker }: { ticker: string }) {
  const live = useApi<{ live: (LiveJudgement & { rec_id: number }) | null }>(`/stocks/${encodeURIComponent(ticker)}/live`, [ticker]);
  const detail = useApi<StockDetail>(`/stocks/${encodeURIComponent(ticker)}`, [ticker]);
  usePoll(live.reload, 15_000);
  usePoll(detail.reload, 600_000);
  const q = useQuote(ticker).row;
  const j = live.data?.live ?? null;
  const rec = detail.data?.recommendation;
  const action = j?.action ?? rec?.action ?? null;
  const flash = useFlash(action);
  if (!action) {
    return (
      <button type="button" className="gl-focus" onClick={() => void openAnalyze(`/stocks/${ticker}`)} data-testid="glance-focus">
        <div className="gl-ticker">{ticker}</div>
        <div className="gl-unavail">{live.state === "loading" || detail.state === "loading" ? "Loading…" : "DATA UNAVAILABLE"}</div>
      </button>
    );
  }
  const price = q?.price ?? j?.price ?? rec?.price ?? null;
  const chg = q?.change_pct ?? null;
  const buyBelow = j?.max_buy ?? rec?.max_buy ?? null;
  const invalid = j?.stop ?? rec?.stop ?? null;
  const status = j?.current_status ?? rec?.current_status ?? null;
  const actionable = j ? j.actionable_now : rec?.actionable_now;
  const plan = detail.data?.position_plan;
  const size = plan?.available && plan.amount && quantityShown(status, actionable) ? plan.amount : null;
  const recheck = ["BUY", "BUY SMALL", "ADD"].includes(action) && (status !== "CURRENT" || actionable === false);
  // material changes against the previous analysis only (the brief's other "changed" lines are context facts)
  const changes = (detail.data?.brief?.changed ?? []).filter((c) => c.label === "지난 분석 대비" && c.kind === "CALC").length;
  const hist = (detail.data?.price_history ?? []).slice(-60).map((p) => p.close);
  return (
    <>
      <button type="button" className={`gl-focus${flash ? " flash" : ""}`} onClick={() => void openAnalyze(`/stocks/${ticker}`)} data-testid="glance-focus">
        <div className="gl-ticker">{ticker}</div>
        <div className={`gl-action ${ACTION_TONE[action] ?? ""}`} data-testid="glance-action">{action}</div>
        {recheck && <div className="gl-hint warn">{status === "CURRENT" ? "Price outside plan" : "Re-check needed"}</div>}
        {price !== null && <div className="gl-price"><b>${fmt(price)}</b>{chg !== null && <span className={tone(chg)}>{signed(chg)}</span>}</div>}
        {hist.length > 5 && <Sparkline values={hist} />}
      </button>
      <dl className="gl-rows" data-testid="glance-rows">
        {buyBelow !== null && <div className="lead"><dt>BUY BELOW</dt><dd>${fmt(buyBelow)}</dd></div>}
        {size !== null && <div><dt>MAX SIZE</dt><dd>${Math.round(size).toLocaleString("en-US")}</dd></div>}
        {invalid !== null && <div className="neg"><dt>INVALID</dt><dd>${fmt(invalid)}</dd></div>}
      </dl>
      <div className="gl-rule" />
      <button type="button" className={`gl-changes${changes ? " on" : ""}`} onClick={() => void openAnalyze(`/stocks/${ticker}`)} data-testid="glance-changes">
        {detail.data ? (changes ? <><i className="gl-dot accent" aria-hidden />{changes} {changes === 1 ? "THING" : "THINGS"} CHANGED <span className="chev">›</span></> : "NO MATERIAL CHANGE") : ""}
      </button>
    </>
  );
}

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
  const w = 280, h = 34;
  const pts = values.map((v, i) => `${((i / (values.length - 1)) * w).toFixed(1)},${(h - 2 - ((v - lo) / (hi - lo || 1)) * (h - 4)).toFixed(1)}`).join(" ");
  const up = values[values.length - 1]! >= values[0]!;
  return <svg className={`gl-spark ${up ? "up" : "down"}`} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden><polyline points={pts} fill="none" strokeWidth="1.5" vectorEffect="non-scaling-stroke" /></svg>;
}

const fmt = (v: number) => v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const signed = (v: number) => `${v > 0 ? "+" : ""}${(v * 100).toFixed(2)}%`;
const tone = (v: number) => (v > 0 ? "pos" : v < 0 ? "neg" : "");
