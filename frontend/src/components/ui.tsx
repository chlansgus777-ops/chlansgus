import type { ReactNode } from "react";
import { actionClass } from "../format";
import type { Evidence } from "../types";

export function Action({ a }: { a: string | null | undefined }) {
  return <span className={actionClass(a)}>{a ?? "N/A"}</span>;
}

export function Quality({ q }: { q: string | null | undefined }) {
  return <span className={`q-${q ?? "MISSING"}`}>{q ?? "MISSING"}</span>;
}

export function Card({ title, children, right }: { title?: string; children: ReactNode; right?: ReactNode }) {
  return (
    <div className="card">
      {(title || right) && (
        <div className="row spread" style={{ marginBottom: 8 }}>
          {title ? <h2>{title}</h2> : <span />}
          {right}
        </div>
      )}
      {children}
    </div>
  );
}

export function Loading({ what }: { what?: string }) {
  return <div className="muted">Loading {what ?? ""}…</div>;
}

export function Err({ error }: { error: string | null }) {
  return error ? <div className="err">⚠ {error}</div> : null;
}

export function Bar({ value, max = 1, color }: { value: number; max?: number; color?: string }) {
  const w = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className="bar">
      <div style={{ width: `${w}%`, background: color }} />
    </div>
  );
}

export function EvidenceChips({ ids, index }: { ids: string[] | undefined; index: Map<string, Evidence> }) {
  if (!ids || ids.length === 0) return null;
  return (
    <span>
      {ids.map((id) => {
        const e = index.get(id);
        const tip = e ? `${e.label}: ${e.value ?? "N/A"}\nsource: ${e.source} · ${e.quality}${e.source_ts ? `\n${e.source_ts}` : ""}` : `${id} (not in evidence)`;
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
  if (values.length < 2) return <div className="muted">Not enough data</div>;
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
  return <span className={cls}>{s}</span>;
}
