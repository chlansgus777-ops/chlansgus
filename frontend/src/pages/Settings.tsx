import { Fragment } from "react";
import { Card, Err, Loading } from "../components/ui";
import { useApi } from "../components/useApi";

interface S { mode: string; keys_configured: Record<string, boolean>; weights: Record<string, number>; decision: Record<string, number>; entry: Record<string, number>; scanner: Record<string, unknown>; calibration: Record<string, number>; sector_models: { id: string; name: string; rationale: string; primary_multiple: string; fundamental: { metric: string; label: string; weight: number; bad: number; good: number }[] }[]; note: string }

export default function Settings() {
  const s = useApi<S>("/settings");
  if (s.loading && !s.data) return <Loading what="settings" />;
  if (!s.data) return <Err error={s.error} />;
  const d = s.data;
  const kv = (o: Record<string, unknown>) => <div className="kv">{Object.entries(o).map(([k, v]) => <Fragment key={k}><span className="k">{k}</span><span>{typeof v === "object" ? JSON.stringify(v) : String(v)}</span></Fragment>)}</div>;
  return (
    <div className="grid">
      <h1>Settings <span className="muted" style={{ fontSize: 13 }}>(read-only; edit config/*.toml and .env — every change is versioned)</span></h1>
      <div className="grid g3">
        <Card title="Mode & API keys"><div>Mode: <b>{d.mode}</b></div>{kv(Object.fromEntries(Object.entries(d.keys_configured).map(([k, v]) => [k, v ? "configured" : "missing"])))}<div className="muted">{d.note}</div></Card>
        <Card title="Score weights">{kv(d.weights)}</Card>
        <Card title="Decision thresholds (hysteresis)">{kv(d.decision)}</Card>
        <Card title="Entry engine">{kv(d.entry)}</Card>
        <Card title="Scanner">{kv(d.scanner)}</Card>
        <Card title="Calibration">{kv(d.calibration)}</Card>
      </div>
      <Card title="Sector models">
        {d.sector_models.map((m) => <details key={m.id}><summary>{m.name} — {m.rationale} (primary multiple: {m.primary_multiple})</summary>
          <table><thead><tr><th>Metric</th><th>Weight</th><th>Bad (→0)</th><th>Good (→1)</th></tr></thead><tbody>{m.fundamental.map((r) => <tr key={r.metric}><td>{r.label}</td><td>{r.weight}</td><td>{r.bad}</td><td>{r.good}</td></tr>)}</tbody></table></details>)}
      </Card>
    </div>
  );
}
