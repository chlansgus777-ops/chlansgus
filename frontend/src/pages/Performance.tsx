import { Fragment } from "react";
import { useState } from "react";
import { api } from "../api";
import { Card, Err, LineChart, Loading } from "../components/ui";
import { useApi } from "../components/useApi";
import { num, pct } from "../format";

interface IC { factor: string; horizon: number; ic: number | null; ir: number | null; samples: number; periods: number }
interface Perf {
  as_of: string; samples: number; mature_20d: number;
  recommendation_hit_rate: Record<string, { n: number; hit_rate: number | null; avg_return: number | null }>;
  all_recommendations: Record<string, { n: number; hit_rate: number | null; avg_return: number | null }>;
  score_buckets: Record<string, [string, number, number | null][]>;
  confidence_buckets: { bucket: string; n: number; avg_return_20d: number | null }[];
  factor_ic: IC[]; paper: Record<string, number | null>; paper_equity_curve: number[];
  sector_performance: Record<string, { n: number; avg_return: number; win_rate: number }>;
  regime_performance: Record<string, { n: number; avg_return: number; win_rate: number }>;
}
interface Cal { runs: { id: number; status: string; candidate: string | null; created_at: string }[]; models: { version: string; status: string; weights: Record<string, number> }[]; production_weights: Record<string, number>; production_version: string }

export default function Performance() {
  const [period, setPeriod] = useState("all");
  const p = useApi<Perf>(`/performance?period=${period}`, [period]);
  const c = useApi<Cal>("/calibration");
  const [busy, setBusy] = useState("");
  const act = async (path: string) => { setBusy(path); try { await api.post(path); p.reload(); c.reload(); } finally { setBusy(""); } };
  if (p.loading && !p.data) return <Loading what="performance" />;
  if (!p.data) return <Err error={p.error} />;
  const x = p.data;
  const factors = [...new Set(x.factor_ic.map((f) => f.factor))];
  return (
    <div className="grid">
      <div className="row spread">
        <h1>Model Performance</h1>
        <div className="row">
          {["30d", "90d", "1y", "all"].map((k) => <button key={k} disabled={k === period} onClick={() => setPeriod(k)}>{k.toUpperCase()}</button>)}
          <button disabled={!!busy} onClick={() => act("/evaluation/run")}>Update outcomes & paper</button>
          <button disabled={!!busy} onClick={() => act("/calibration/run")}>Run calibration cycle</button>
        </div>
      </div>
      <div className="grid g4">
        <Card title="Samples"><div className="big-action">{x.samples}</div><div className="muted">{x.mature_20d} matured at 20D (look-ahead safe)</div></Card>
        <Card title="Hit rate 20D (bullish recs)"><div className="big-action">{pct(x.recommendation_hit_rate["20"]?.hit_rate ?? null, 0, false)}</div><div className="muted">n={x.recommendation_hit_rate["20"]?.n ?? 0}</div></Card>
        <Card title="Paper win rate / PF"><div className="big-action">{pct(x.paper["win_rate"] ?? null, 0, false)}</div><div className="muted">profit factor {num(x.paper["profit_factor"] ?? null)}</div></Card>
        <Card title="Excess vs SPY / Max DD"><div className="big-action">{pct(x.paper["excess_vs_benchmark"] ?? null)}</div><div className="muted">max DD {pct(x.paper["max_drawdown"] ?? null)}</div></Card>
      </div>
      <Card title="Paper equity curve"><LineChart values={x.paper_equity_curve} /></Card>
      <div className="grid g2">
        <Card title="Recommendation outcomes (bullish recs)">
          <table><thead><tr><th>Horizon</th><th>n</th><th>Hit rate</th><th>Avg return</th></tr></thead>
            <tbody>{["1", "5", "20", "60"].map((h) => { const r = x.recommendation_hit_rate[h]; return <tr key={h}><td>{h}D</td><td>{r?.n}</td><td>{pct(r?.hit_rate ?? null, 0, false)}</td><td>{pct(r?.avg_return ?? null, 2)}</td></tr>; })}</tbody></table>
        </Card>
        <Card title="Paper trading metrics">
          <div className="kv">{Object.entries(x.paper).map(([k, v]) => <Fragment key={k}><span className="k">{k}</span><span>{v === null ? "N/A" : k === "win_rate" ? pct(v, 1, false) : k.includes("return") || k.includes("drawdown") || k.includes("mae") || k.includes("mfe") || k.includes("excess") || k === "expectancy" ? pct(v) : k === "trades" || k === "closed" ? num(v, 0) : num(v)}</span></Fragment>)}</div>
        </Card>
        <Card title="Score bucket performance (20D)">
          <table><thead><tr><th>Score</th><th>n</th><th>Avg 20D</th></tr></thead><tbody>{(x.score_buckets["20"] ?? []).map(([b, n, r]) => <tr key={b}><td>{b}</td><td>{n}</td><td>{pct(r, 2)}</td></tr>)}</tbody></table>
        </Card>
        <Card title="Confidence bucket performance (20D)">
          <table><thead><tr><th>Confidence</th><th>n</th><th>Avg 20D</th></tr></thead><tbody>{x.confidence_buckets.map((b) => <tr key={b.bucket}><td>{b.bucket}</td><td>{b.n}</td><td>{pct(b.avg_return_20d, 2)}</td></tr>)}</tbody></table>
        </Card>
        <Card title="Sector performance (paper)"><table><tbody>{Object.entries(x.sector_performance).map(([k, v]) => <tr key={k}><td>{k}</td><td>n={v.n}</td><td>{pct(v.avg_return, 2)}</td><td>win {pct(v.win_rate, 0, false)}</td></tr>)}</tbody></table></Card>
        <Card title="Regime performance (paper)"><table><tbody>{Object.entries(x.regime_performance).map(([k, v]) => <tr key={k}><td>{k}</td><td>n={v.n}</td><td>{pct(v.avg_return, 2)}</td><td>win {pct(v.win_rate, 0, false)}</td></tr>)}</tbody></table></Card>
      </div>
      <Card title="Factor IC (Spearman) / IR — N/A until enough matured samples">
        <table><thead><tr><th>Factor</th>{[5, 20, 60].map((h) => <th key={h}>IC {h}D</th>)}{[5, 20, 60].map((h) => <th key={`ir${h}`}>IR {h}D</th>)}<th>Samples (20D)</th></tr></thead>
          <tbody>{factors.map((f) => { const g = (h: number) => x.factor_ic.find((i) => i.factor === f && i.horizon === h); return <tr key={f}><td>{f}</td>{[5, 20, 60].map((h) => <td key={h}>{num(g(h)?.ic ?? null, 3)}</td>)}{[5, 20, 60].map((h) => <td key={`ir${h}`}>{num(g(h)?.ir ?? null, 2)}</td>)}<td>{g(20)?.samples}</td></tr>; })}</tbody></table>
      </Card>
      {c.data && (
        <Card title={`Calibration — production ${c.data.production_version}`}>
          <div className="muted">Weights change only after ≥ min samples, a shadow period with out-of-sample IC improvement, no worse hit-rate/downside, and ≤ 5% relative change per cycle.</div>
          <div className="row">{Object.entries(c.data.production_weights).map(([k, v]) => <span key={k} className="pill">{k}: {v}</span>)}</div>
          <table><thead><tr><th>Run</th><th>Status</th><th>Candidate</th><th>When</th></tr></thead><tbody>{c.data.runs.map((r) => <tr key={r.id}><td>{r.id}</td><td>{r.status}</td><td>{r.candidate ?? "—"}</td><td>{r.created_at}</td></tr>)}</tbody></table>
        </Card>
      )}
    </div>
  );
}
