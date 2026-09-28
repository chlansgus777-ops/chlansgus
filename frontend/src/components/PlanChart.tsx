/** The price-plan chart — the visual centre of the stock page (product overhaul 2026-09-28).
 * Closing prices with the backend's plan drawn over them as zones, not only lines: the buy zone (a band), the loss
 * area under the stop, the max-buy threshold and the targets. Hover, touch or the arrow keys move a crosshair whose
 * tooltip gives the day's close and its distance to the stop and the max buy price. Every level is a backend value on
 * today's share basis; nothing is estimated here. */
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { price } from "../format";

export interface PlanLevels { stop?: number | null; maxBuy?: number | null; zoneLow?: number | null; ideal?: number | null; t1?: number | null; t2?: number | null }
interface Pt { day: string; close: number }

const C = { price: "#cfc6ff", stop: "#ff7a98", maxBuy: "#f3c96a", zone: "#72b8ff", target: "#5fe0bd", now: "#ffffff" };

function niceTicks(lo: number, hi: number, n = 5): number[] {
  const span = hi - lo || 1;
  const raw = span / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n + 1) ?? raw;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Number(v.toFixed(10)));
  return out;
}

/** Labels of close levels are pushed apart vertically so they never print over each other (the lines stay exact). */
export function spreadLabels(ys: { key: string; y: number }[], gap: number, top: number, bottom: number): Map<string, number> {
  const sorted = [...ys].sort((a, b) => a.y - b.y);
  const out = new Map<string, number>();
  let prev = -Infinity;
  for (const { key, y } of sorted) { const ly = Math.max(y, prev + gap, top); out.set(key, ly); prev = ly; }
  const over = prev - bottom;
  if (over > 0) {
    let next = Infinity;
    for (const { key } of [...sorted].reverse()) { const ly = Math.min((out.get(key) ?? 0) - over, next - gap); out.set(key, ly); next = ly; }
  }
  return out;
}

const MONTH = (d: string) => `${Number(d.slice(5, 7))}월`;

export function PlanChart({ data, levels, height = 300, label }: { data: Pt[]; levels: PlanLevels; height?: number; label?: string }) {
  const box = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(760);
  const [hover, setHover] = useState<number | null>(null);
  const gid = useId().replace(/:/g, "");
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => { if (e) setW(Math.max(300, Math.round(e.contentRect.width))); });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const narrow = w < 560;
  const m = { l: narrow ? 40 : 52, r: narrow ? 92 : 124, t: 14, b: 28 };
  const iw = w - m.l - m.r;
  const ih = height - m.t - m.b;
  const lv = levels;
  const levelVals = [lv.stop, lv.maxBuy, lv.zoneLow, lv.ideal, lv.t1, lv.t2].filter((v): v is number => typeof v === "number" && Number.isFinite(v));
  const closes = data.map((p) => p.close);
  const lo0 = Math.min(...closes, ...levelVals);
  const hi0 = Math.max(...closes, ...levelVals);
  const pad = (hi0 - lo0 || hi0 * 0.05 || 1) * 0.06;
  const lo = lo0 - pad, hi = hi0 + pad;
  const x = useCallback((i: number) => m.l + (data.length <= 1 ? iw / 2 : (i / (data.length - 1)) * iw), [data.length, iw, m.l]);
  const y = useCallback((v: number) => m.t + (1 - (v - lo) / (hi - lo)) * ih, [lo, hi, ih, m.t]);
  const path = useMemo(() => data.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.close).toFixed(1)}`).join(""), [data, x, y]);
  if (data.length < 2) return <div className="empty">가격 이력이 부족해 차트를 그리지 않습니다.</div>;
  const area = `${path}L${x(data.length - 1).toFixed(1)},${(m.t + ih).toFixed(1)}L${x(0).toFixed(1)},${(m.t + ih).toFixed(1)}Z`;
  const ticks = niceTicks(lo, hi, narrow ? 4 : 5);
  const months: { i: number; label: string }[] = [];
  data.forEach((p, i) => { if (i > 0 && p.day.slice(5, 7) !== data[i - 1]!.day.slice(5, 7)) months.push({ i, label: MONTH(p.day) }); });
  const zoneTop = lv.maxBuy ?? null;
  const zoneBottom = lv.zoneLow ?? lv.ideal ?? null;
  const lastI = data.length - 1;
  const last = data[lastI]!;
  const labelDefs: { key: string; v: number; text: string; color: string }[] = [];
  if (lv.t2 != null) labelDefs.push({ key: "t2", v: lv.t2, text: `2차 목표 ${price(lv.t2)}`, color: C.target });
  if (lv.t1 != null) labelDefs.push({ key: "t1", v: lv.t1, text: `1차 목표 ${price(lv.t1)}`, color: C.target });
  if (lv.maxBuy != null) labelDefs.push({ key: "mb", v: lv.maxBuy, text: `최대 매수 ${price(lv.maxBuy)}`, color: C.maxBuy });
  if (zoneBottom != null && zoneTop != null && Math.abs(zoneBottom - zoneTop) / zoneTop > 0.002) labelDefs.push({ key: "zl", v: zoneBottom, text: `구간 하단 ${price(zoneBottom)}`, color: C.zone });
  if (lv.stop != null) labelDefs.push({ key: "st", v: lv.stop, text: `손절 ${price(lv.stop)}`, color: C.stop });
  const ly = spreadLabels(labelDefs.map((d) => ({ key: d.key, y: y(d.v) + 4 })), 15, m.t + 8, m.t + ih + 4);
  const h = hover == null ? null : data[Math.max(0, Math.min(lastI, hover))]!;
  const hi_ = hover == null ? null : Math.max(0, Math.min(lastI, hover));
  const prev = hi_ != null && hi_ > 0 ? data[hi_ - 1]! : null;
  const onMove = (clientX: number) => {
    const r = box.current?.getBoundingClientRect();
    if (!r) return;
    const px = clientX - r.left;
    const i = Math.round(((px - m.l) / iw) * (data.length - 1));
    setHover(Math.max(0, Math.min(lastI, i)));
  };
  const dist = (a: number, b: number | null | undefined) => (b == null ? null : (b / a - 1) * 100);
  const tipLeft = hi_ == null ? 0 : Math.min(Math.max(x(hi_) + 14, m.l), w - 196);
  const summary = label ?? `최근 ${data.length}거래일 종가 차트. 마지막 종가 ${price(last.close)}${lv.maxBuy != null ? `, 최대 매수가 ${price(lv.maxBuy)}` : ""}${lv.stop != null ? `, 손절 기준 ${price(lv.stop)}` : ""}${lv.t1 != null ? `, 1차 목표 ${price(lv.t1)}` : ""}.`;
  return (
    <div className="chart" ref={box} tabIndex={0} role="img" aria-label={summary}
         onMouseMove={(e) => onMove(e.clientX)} onMouseLeave={() => setHover(null)}
         onTouchStart={(e) => e.touches[0] && onMove(e.touches[0].clientX)} onTouchMove={(e) => e.touches[0] && onMove(e.touches[0].clientX)} onTouchEnd={() => setHover(null)}
         onKeyDown={(e) => {
           if (e.key === "ArrowLeft") { e.preventDefault(); setHover((v) => Math.max(0, (v ?? lastI) - 1)); }
           else if (e.key === "ArrowRight") { e.preventDefault(); setHover((v) => Math.min(lastI, (v ?? lastI - 1) + 1)); }
           else if (e.key === "Home") { e.preventDefault(); setHover(0); }
           else if (e.key === "End") { e.preventDefault(); setHover(lastI); }
           else if (e.key === "Escape") setHover(null);
         }} onBlur={() => setHover(null)}>
      <svg width={w} height={height} viewBox={`0 0 ${w} ${height}`}>
        <defs>
          <linearGradient id={`a${gid}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#a494f8" stopOpacity="0.28" /><stop offset="1" stopColor="#a494f8" stopOpacity="0" />
          </linearGradient>
          <linearGradient id={`s${gid}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={C.stop} stopOpacity="0.13" /><stop offset="1" stopColor={C.stop} stopOpacity="0.02" />
          </linearGradient>
          <filter id={`g${gid}`} x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="3" /></filter>
        </defs>
        {ticks.map((t) => (
          <g key={t}>
            <line className="grid-line" x1={m.l} x2={m.l + iw} y1={y(t)} y2={y(t)} />
            <text className="axis" x={m.l - 8} y={y(t) + 4} textAnchor="end">{t >= 1000 ? t.toLocaleString("en-US") : t % 1 ? t.toFixed(t < 10 ? 2 : 1) : t}</text>
          </g>
        ))}
        {months.map((mo) => <text key={mo.i} className="axis" x={x(mo.i)} y={height - 8} textAnchor="middle">{mo.label}</text>)}
        {/* the plan as zones */}
        {lv.stop != null && <rect x={m.l} y={y(lv.stop)} width={iw} height={Math.max(0, m.t + ih - y(lv.stop))} fill={`url(#s${gid})`} />}
        {zoneTop != null && zoneBottom != null && (
          <rect x={m.l} y={y(Math.max(zoneTop, zoneBottom))} width={iw} height={Math.max(2, Math.abs(y(zoneBottom) - y(zoneTop)))} fill={C.zone} fillOpacity="0.1" stroke={C.zone} strokeOpacity="0.35" strokeDasharray="2 4" />
        )}
        {lv.t2 != null && <line x1={m.l} x2={m.l + iw} y1={y(lv.t2)} y2={y(lv.t2)} stroke={C.target} strokeOpacity="0.55" strokeDasharray="6 5" />}
        {lv.t1 != null && <line x1={m.l} x2={m.l + iw} y1={y(lv.t1)} y2={y(lv.t1)} stroke={C.target} strokeDasharray="6 5" />}
        {lv.maxBuy != null && <line x1={m.l} x2={m.l + iw} y1={y(lv.maxBuy)} y2={y(lv.maxBuy)} stroke={C.maxBuy} strokeWidth="1.3" />}
        {lv.stop != null && <line x1={m.l} x2={m.l + iw} y1={y(lv.stop)} y2={y(lv.stop)} stroke={C.stop} strokeWidth="1.4" strokeDasharray="7 4" />}
        {/* price */}
        <path d={area} fill={`url(#a${gid})`} />
        <path d={path} className="price-line" stroke={C.price} opacity="0.55" filter={`url(#g${gid})`} />
        <path d={path} className="price-line" />
        <circle cx={x(lastI)} cy={y(last.close)} r="9" fill="#a494f8" opacity="0.25" className="pulse-ring" />
        <circle cx={x(lastI)} cy={y(last.close)} r="4.2" fill={C.now} stroke="#6a5bdc" strokeWidth="2" />
        {/* level labels at the right edge */}
        {labelDefs.map((d) => (
          <g key={d.key}>
            <line x1={m.l + iw} x2={m.l + iw + 6} y1={y(d.v)} y2={ly.get(d.key)! - 4} stroke={d.color} strokeOpacity="0.6" />
            <text className="level-label" x={m.l + iw + 9} y={ly.get(d.key)} fill={d.color}>{narrow ? price(d.v) : d.text}</text>
          </g>
        ))}
        {/* crosshair */}
        {h && hi_ != null && (
          <g>
            <line className="crosshair" x1={x(hi_)} x2={x(hi_)} y1={m.t} y2={m.t + ih} />
            <circle cx={x(hi_)} cy={y(h.close)} r="5" fill="#0d1220" stroke={C.price} strokeWidth="2" />
          </g>
        )}
      </svg>
      {h && hi_ != null && (
        <div className="tooltip" style={{ left: tipLeft, top: 10 }} aria-hidden="true">
          <div className="d">{h.day} 종가</div>
          <div className="p">{price(h.close)}</div>
          {prev && <div className="r"><span>전일 대비</span><span className={h.close >= prev.close ? "pos" : "neg"}>{h.close >= prev.close ? "▲" : "▼"} {Math.abs((h.close / prev.close - 1) * 100).toFixed(2)}%</span></div>}
          {lv.maxBuy != null && <div className="r"><span>최대 매수가까지</span><span>{fmtDist(dist(h.close, lv.maxBuy))}</span></div>}
          {lv.stop != null && <div className="r"><span>손절 기준까지</span><span>{fmtDist(dist(h.close, lv.stop))}</span></div>}
        </div>
      )}
      <div className="chart-legend" aria-hidden="true">
        {zoneTop != null && <span><i className="band" style={{ background: "rgba(114,184,255,.14)", borderColor: "rgba(114,184,255,.5)" }} />매수 구간</span>}
        {lv.maxBuy != null && <span><i style={{ borderColor: C.maxBuy }} />최대 매수가(넘으면 대기)</span>}
        {lv.stop != null && <span><i className="dash" style={{ borderColor: C.stop }} />손절 기준(종가)</span>}
        {lv.t1 != null && <span><i className="dash" style={{ borderColor: C.target }} />목표</span>}
        <span className="muted">← → 키로 날짜 이동</span>
      </div>
    </div>
  );
}

function fmtDist(v: number | null): string {
  if (v == null) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
}

/** Small trend line for lists (no axes). */
export function Sparkline({ values, width = 96, height = 30 }: { values: number[]; width?: number; height?: number }) {
  if (values.length < 2) return null;
  const lo = Math.min(...values), hi = Math.max(...values);
  const x = (i: number) => (i / (values.length - 1)) * width;
  const y = (v: number) => height - 3 - ((v - lo) / (hi - lo || 1)) * (height - 6);
  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const up = values[values.length - 1]! >= values[0]!;
  const c = up ? "#5fe0bd" : "#ff7a98";
  return (
    <svg className="spark" width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <path d={`${d}L${width},${height}L0,${height}Z`} fill={c} opacity="0.08" />
      <path d={d} fill="none" stroke={c} strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
