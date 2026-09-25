import type { ReactNode } from "react";
import { actionClass, stamp } from "../format";
import { GLOSSARY, tip } from "../glossary";
import { ACTION_INFO, BULLISH, DATA_TYPE_KO, QUALITY_INFO, STANCE_KO, STATUS_INFO, VETO_KO, actionLabel } from "../i18n";
import { useMode } from "../mode";
import type { Evidence, FreshnessCheck } from "../types";

/** Action badge. A bullish action that is no longer current (or was based on stale data) is shown as an
 * expired signal — it must never look like a normal, actionable BUY. */
export function Action({ a, status, quality, lg }: { a: string | null | undefined; status?: string | null; quality?: string | null; lg?: boolean }) {
  const info = a ? ACTION_INFO[a] : undefined;
  const expired = !!a && BULLISH.has(a) && ((status && status !== "CURRENT") || (quality && !["FRESH", "DELAYED"].includes(quality)));
  if (expired) {
    return (
      <span className={`badge a-EXPIRED${lg ? " lg" : ""}`} title={`${info?.help ?? ""}\n\n⚠ ${STATUS_INFO[status ?? "EXPIRED"]?.help ?? "현재 유효하지 않은 추천"}`} data-testid="action-expired">
        <s>{actionLabel(a)}</s> · {STATUS_INFO[status ?? "EXPIRED"]?.label ?? "만료"}
      </span>
    );
  }
  return <span className={`${actionClass(a)}${lg ? " lg" : ""}`} title={info?.help ?? ""}>{actionLabel(a)}</span>;
}

export function Quality({ q }: { q: string | null | undefined }) {
  const k = q ?? "MISSING";
  const info = QUALITY_INFO[k];
  return <span className={`q-${k}`} title={info?.help ?? ""}>{info ? `${info.label}(${k})` : k}</span>;
}

export function StatusBadge({ s, reason }: { s: string | null | undefined; reason?: string | null }) {
  if (!s) return null;
  const info = STATUS_INFO[s];
  return <span className={`status s-${s}`} title={reason ?? info?.help ?? ""}>{info?.label ?? s}</span>;
}

export function Vetoes({ v }: { v: string[] }) {
  if (!v.length) return null;
  return <span className="neg" title={v.map((x) => VETO_KO[x] ?? x).join(", ")}>⛔ {v.map((x) => VETO_KO[x] ?? x).join(", ")}</span>;
}

export function Card({ title, children, right, icon, tone, explain, className }: { title?: string; children: ReactNode; right?: ReactNode; icon?: string; tone?: "pos" | "neg" | "warn"; explain?: string; className?: string }) {
  return (
    <section className={`card${tone ? ` tone-${tone}` : ""}${className ? ` ${className}` : ""}`}>
      {(title || right) && (
        <div className="card-head">
          <div>
            {title ? <h2>{icon && <span className="icon" aria-hidden>{icon}</span>}{title}</h2> : null}
            {explain && <div className="explain">{explain}</div>}
          </div>
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

/** A technical term with a plain-language tooltip (see glossary.ts). */
export function Term({ k, children }: { k: string; children?: ReactNode }) {
  const e = GLOSSARY[k];
  return <span className="term" title={tip(k)}>{children ?? e?.name ?? k}<span className="i" aria-hidden>ⓘ</span></span>;
}

export function Notice({ tone = "info", icon, children }: { tone?: "info" | "warn" | "neg"; icon?: string; children: ReactNode }) {
  return <div className={`notice ${tone}`} role={tone === "info" ? "note" : "alert"}><span aria-hidden>{icon ?? (tone === "info" ? "ℹ" : "⚠")}</span><div>{children}</div></div>;
}

export function Loading({ what, steps }: { what?: string; steps?: string[] }) {
  return (
    <div className="empty" role="status">
      <span className="spinner" /> {what ? `${what} 불러오는 중…` : "불러오는 중…"}
      {steps && <div className="hint">{steps.join(" → ")}</div>}
    </div>
  );
}

export function Err({ error, retry }: { error: string | null; retry?: () => void }) {
  if (!error) return null;
  return (
    <div className="err" role="alert">
      ⚠ {error}
      {retry && <button style={{ marginLeft: 8 }} onClick={retry}>다시 시도</button>}
    </div>
  );
}

export function Empty({ children, hint }: { children: ReactNode; hint?: string }) {
  return <div className="empty" data-testid="empty">{children}{hint && <div className="hint">{hint}</div>}</div>;
}

export function Bar({ value, max = 1, color }: { value: number; max?: number; color?: string }) {
  const w = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className="bar" aria-label={`${Math.round(w)}%`}>
      <div style={{ width: `${w}%`, background: color }} />
    </div>
  );
}

/** Per-type data freshness (why the data is or is not usable). */
export function FreshnessTable({ checks }: { checks: FreshnessCheck[] }) {
  if (!checks.length) return <Empty>신선도 정보 없음</Empty>;
  return (
    <table>
      <thead><tr><th>데이터</th><th>상태</th><th>기준 시점</th><th>설명</th></tr></thead>
      <tbody>
        {checks.map((c) => (
          <tr key={c.data_type}>
            <td>{DATA_TYPE_KO[c.data_type] ?? c.data_type}</td>
            <td><Quality q={c.quality} /></td>
            <td>{c.effective ? (c.effective.includes("T") ? stamp(c.effective) : c.effective) : "N/A"}{c.published ? <div className="muted">공개 {c.published.includes("T") ? stamp(c.published) : c.published}</div> : null}</td>
            <td style={{ whiteSpace: "normal" }}>{c.reason_ko}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Evidence IDs are audit detail: shown in advanced mode only (beginners see the sentence, not the ID). */
export function EvidenceChips({ ids, index }: { ids: string[] | undefined; index: Map<string, Evidence> }) {
  const { mode } = useMode();
  if (mode !== "advanced" || !ids || ids.length === 0) return null;
  return (
    <span>
      {ids.map((id) => {
        const e = index.get(id);
        const tip = e ? `${e.label}: ${e.value ?? "N/A"}${e.unit ? ` ${e.unit}` : ""}\n출처: ${e.source} · ${e.quality}${e.source_ts ? `\n${stamp(e.source_ts)}` : ""}` : `${id} (근거 목록에 없음)`;
        return (
          <span className="pill" key={id} title={tip}>
            {id.split("_").slice(0, 3).join("_")}
          </span>
        );
      })}
    </span>
  );
}

export function LineChart({ values, height = 120, color = "var(--accent)" }: { values: number[]; height?: number; color?: string }) {
  if (values.length < 2) return <Empty>데이터가 충분하지 않음</Empty>;
  const w = 600;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * w},${height - ((v - min) / span) * (height - 10) - 5}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${height}`} width="100%" height={height} preserveAspectRatio="none" role="img" aria-label="line chart">
      <polyline fill="none" stroke={color} strokeWidth="2" points={pts} />
    </svg>
  );
}

export function Stance({ s }: { s: string }) {
  const cls = s === "positive" ? "pos" : s === "negative" ? "neg" : "muted";
  return <span className={cls}>{STANCE_KO[s] ?? s}</span>;
}

/** Data-source banner: MOCK and LIVE must never be confused. */
export function ModeBanner({ mode }: { mode: string }) {
  if (mode === "MOCK") {
    return <div className="banner mock" data-testid="banner-mock">모의 데이터(MOCK) — 가상의 가격·재무 데이터입니다. 실제 시장 데이터가 아니며 투자 판단에 사용할 수 없습니다.</div>;
  }
  return <div className="banner live" data-testid="banner-live">실데이터(LIVE) — 없는 데이터는 ‘없음(MISSING)’으로 표시하며 다른 값으로 대체하지 않습니다. 모든 가격은 미국 달러(USD) 기준입니다.</div>;
}

/** Price ladder: stop · ideal entry · max buy · current · targets on one line (all USD). */
/** Merge plan levels that would sit on top of each other (e.g. 이상적 == 최대 매수) into one label, and
 * alternate label rows for close neighbours (the card can be ~300px wide, so ~22% of the track per label). */
export function ladderMarks(marks: [string, number, string][], lo: number, hi: number, minGap = 0.22): { label: string; pos: number; cls: string; row: number }[] {
  const span = hi - lo || 1;
  const sorted = [...marks].sort((a, b) => a[1] - b[1]);
  const out: { label: string; pos: number; cls: string; row: number }[] = [];
  for (const [l, v, c] of sorted) {
    const pos = (v - lo) / span;
    const last = out[out.length - 1];
    if (last && Math.abs(pos - last.pos) < 0.02) { last.label = `${last.label}·${l}`; continue; }
    out.push({ label: l, pos, cls: c, row: 0 });
  }
  out.forEach((m, i) => { const prev = out[i - 1]; if (prev && m.pos - prev.pos < minGap) m.row = 1 - prev.row; });
  return out;
}

export function PriceLadder({ now, stop, ideal, maxBuy, t1, t2 }: { now: number; stop: number; ideal: number; maxBuy: number; t1: number; t2: number }) {
  const lo = Math.min(stop, now) * 0.995;
  const hi = Math.max(t2, now) * 1.005;
  const marks = ladderMarks([["손절", stop, "neg"], ["이상적", ideal, ""], ["최대 매수", maxBuy, "warn"], ["1차 목표", t1, "pos"], ["2차 목표", t2, "pos"]], lo, hi);
  const pctPos = (p: number) => `${Math.min(100, Math.max(0, p * 100))}%`;
  return (
    <div className="ladder" aria-label="가격 계획 막대">
      <div className="track" />
      {marks.map((m) => <div key={m.label} className={`mark ${m.cls}${m.row ? " up" : ""}`} style={{ left: pctPos(m.pos) }}><span>{m.label}</span><i /></div>)}
      <div className="mark now" style={{ left: pctPos((now - lo) / (hi - lo)), top: 44 }}><i /><span>현재 ${now.toFixed(2)}</span></div>
    </div>
  );
}

/** Simple SVG donut for weights. */
export function Donut({ parts, size = 140 }: { parts: [string, number][]; size?: number }) {
  const total = parts.reduce((a, [, v]) => a + v, 0) || 1;
  const colors = ["#5b8def", "#2ec27e", "#f2b447", "#8f8cf6", "#f0616d", "#3fb8c9", "#c98f5b", "#9aa4b2"];
  const r = size / 2 - 12;
  const c = 2 * Math.PI * r;
  let acc = 0;
  return (
    <div className="row" style={{ alignItems: "center", gap: 18 }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label="비중 도넛 차트">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--surface-3)" strokeWidth="16" />
        {parts.map(([k, v], i) => {
          const len = (v / total) * c;
          const el = <circle key={k} cx={size / 2} cy={size / 2} r={r} fill="none" stroke={colors[i % colors.length]} strokeWidth="16" strokeDasharray={`${len} ${c - len}`} strokeDashoffset={-acc} transform={`rotate(-90 ${size / 2} ${size / 2})`} />;
          acc += len;
          return el;
        })}
      </svg>
      <ul className="list" style={{ gap: 4 }}>
        {parts.map(([k, v], i) => <li key={k}><span style={{ width: 10, height: 10, borderRadius: 3, background: colors[i % colors.length], display: "inline-block", marginTop: 6 }} />{k} <b style={{ marginLeft: "auto" }}>{((v / total) * 100).toFixed(1)}%</b></li>)}
      </ul>
    </div>
  );
}

/** Close-price line with the plan levels drawn as labelled horizontal lines. */
export function PriceChart({ closes, levels, height = 170 }: { closes: number[]; levels: [string, number, string][]; height?: number }) {
  if (closes.length < 2) return <Empty>가격 이력이 부족해 차트를 그릴 수 없습니다.</Empty>;
  const w = 640;
  const all = [...closes, ...levels.map(([, v]) => v)];
  const min = Math.min(...all) * 0.99;
  const max = Math.max(...all) * 1.01;
  const y = (v: number) => height - ((v - min) / (max - min)) * (height - 16) - 8;
  const pts = closes.map((v, i) => `${(i / (closes.length - 1)) * (w - 90)},${y(v)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${height}`} width="100%" height={height} role="img" aria-label="가격 차트와 매수 계획">
      {levels.map(([l, v, c]) => (
        <g key={l}>
          <line x1="0" x2={w - 90} y1={y(v)} y2={y(v)} stroke={c} strokeDasharray="4 4" strokeWidth="1" opacity="0.8" />
          <text x={w - 86} y={y(v) + 4} fill={c} fontSize="11">{l} ${v.toFixed(2)}</text>
        </g>
      ))}
      <polyline fill="none" stroke="var(--accent)" strokeWidth="2" points={pts} />
    </svg>
  );
}
