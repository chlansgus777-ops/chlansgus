import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import type { Opportunities } from "../types";
import { IStocks } from "./icons";
import "./qs.css";

const TICKER = /^[A-Za-z][A-Za-z0-9.-]{0,9}$/;
const RECENT_KEY = "marketlens.recent";

/** Recently opened stocks (this viewer only, best effort — storage may be unavailable). */
export function recentStocks(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]");
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && TICKER.test(x)).slice(0, 8) : [];
  } catch {
    return [];
  }
}

export function rememberStock(ticker: string) {
  try {
    const t = ticker.toUpperCase();
    localStorage.setItem(RECENT_KEY, JSON.stringify([t, ...recentStocks().filter((x) => x !== t)].slice(0, 8)));
  } catch { /* storage unavailable: nothing to remember */ }
}

interface Option { ticker: string; name: string; why: string }

/** Jump to any stock from any screen: "/" focuses it; suggestions come from recent stocks, the watchlist and the last
 * scan (loaded once, on first focus); Enter opens the highlighted one or any valid ticker typed in full. */
export function QuickSearch() {
  const nav = useNavigate();
  const box = useRef<HTMLInputElement>(null);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [sel, setSel] = useState(0);
  const [pool, setPool] = useState<Option[] | null>(null);
  const load = () => {
    if (pool) return;
    const recent = recentStocks().map((t) => ({ ticker: t, name: "", why: "최근 본 종목" }));
    setPool(recent);
    Promise.allSettled([api.get<Opportunities>("/opportunities"), api.get<{ ticker: string; latest: { company?: string } | null }[]>("/watchlist")]).then(([o, w]) => {
      const out = new Map<string, Option>(recent.map((r) => [r.ticker, r]));
      if (w.status === "fulfilled" && Array.isArray(w.value)) for (const x of w.value) if (x?.ticker && !out.has(x.ticker)) out.set(x.ticker, { ticker: x.ticker, name: x.latest?.company ?? "", why: "관심 종목" });
      if (o.status === "fulfilled" && Array.isArray(o.value?.rows)) for (const r of o.value.rows) {
        const cur = out.get(r.ticker);
        if (cur) cur.name ||= r.company;
        else out.set(r.ticker, { ticker: r.ticker, name: r.company, why: `스캔 후보 #${r.rank ?? "—"}` });
      }
      setPool([...out.values()]);
    });
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      const typing = el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
      if (e.key === "/" && !typing && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); box.current?.focus(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const matches = useMemo(() => {
    const s = q.trim().toLowerCase();
    const all = pool ?? [];
    if (!s) return all.slice(0, 8);
    const starts = all.filter((o) => o.ticker.toLowerCase().startsWith(s));
    const inName = all.filter((o) => !starts.includes(o) && (o.ticker.toLowerCase().includes(s) || o.name.toLowerCase().includes(s)));
    return [...starts, ...inName].slice(0, 8);
  }, [q, pool]);
  const go = (t: string) => {
    setQ(""); setOpen(false); box.current?.blur();
    nav(`/stocks/${t.toUpperCase()}`);
  };
  const typed = q.trim().toUpperCase();
  const typedValid = TICKER.test(typed) && !matches.some((m) => m.ticker === typed);
  // a name the lists know wins over reading the same letters as a new code ("apple" → AAPL, not "APPLE")
  const typedOption: Option = { ticker: typed, name: "", why: "이 코드로 분석" };
  const items: Option[] = !typedValid ? matches : matches.length ? [...matches, typedOption] : [typedOption];
  return (
    <div className="qs" role="search">
      <IStocks width={16} height={16} aria-hidden />
      <input ref={box} value={q} placeholder="종목 이동  /" aria-label="종목 코드 또는 회사명으로 이동 (단축키 /)" autoComplete="off" spellCheck={false}
             role="combobox" aria-expanded={open && items.length > 0} aria-controls="qs-list" aria-autocomplete="list"
             aria-activedescendant={open && items[sel] ? `qs-${items[sel]!.ticker}` : undefined}
             onFocus={() => { load(); setOpen(true); }} onBlur={() => setTimeout(() => { setOpen(false); setPool(null); }, 120)}
             onChange={(e) => { setQ(e.target.value); setSel(0); setOpen(true); }}
             onKeyDown={(e) => {
               if (e.key === "ArrowDown") { e.preventDefault(); setSel((i) => Math.min(items.length - 1, i + 1)); }
               else if (e.key === "ArrowUp") { e.preventDefault(); setSel((i) => Math.max(0, i - 1)); }
               else if (e.key === "Escape") { setQ(""); setOpen(false); box.current?.blur(); }
               else if (e.key === "Enter") { const it = items[sel]; if (it) go(it.ticker); }
             }} />
      {open && items.length > 0 && (
        <ul className="qs-list" id="qs-list" role="listbox">
          {items.map((o, i) => (
            <li key={`${o.why}-${o.ticker}`} id={`qs-${o.ticker}`} role="option" aria-selected={i === sel} className={i === sel ? "on" : ""}
                onMouseDown={(e) => { e.preventDefault(); go(o.ticker); }} onMouseEnter={() => setSel(i)}>
              <b>{o.ticker}</b><span className="n">{o.name}</span><span className="w">{o.why}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
