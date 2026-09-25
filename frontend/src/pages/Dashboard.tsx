import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { OppTable } from "../components/OppTable";
import { Card, Err, Loading } from "../components/ui";
import { useApi } from "../components/useApi";
import { price, stamp } from "../format";
import type { OppRow, ScanInfo } from "../types";

interface Dash {
  scan: ScanInfo | null;
  regime: { primary: string; readings: { regime: string; score: number; confidence: number; evidence: string[] }[] };
  top_opportunities: OppRow[];
  major_risks: { ticker: string; text: string }[];
  upcoming_catalysts: { event_id: string; title: string; event_date: string; days_until: number; importance: number }[];
  portfolio: { holdings: number; cash: number };
  provider_health: { name: string; kind: string; status: string }[];
}

export default function Dashboard() {
  const d = useApi<Dash>("/dashboard");
  const [busy, setBusy] = useState(false);
  const scan = async () => {
    setBusy(true);
    try { await api.post("/scan?committee=true"); d.reload(); } finally { setBusy(false); }
  };
  if (d.loading && !d.data) return <Loading what="dashboard" />;
  if (!d.data) return <Err error={d.error} />;
  const x = d.data;
  return (
    <div className="grid">
      <div className="row spread">
        <h1>What is most attractive in the US market right now?</h1>
        <div className="row">
          <span className="muted">Last scan: {x.scan ? stamp(x.scan.as_of) : "never"}</span>
          <button disabled={busy} onClick={scan}>{busy ? "Scanning…" : "Run full-market scan"}</button>
        </div>
      </div>
      <Card title="Top opportunities" right={<Link to="/opportunities">All candidates →</Link>}>
        {x.top_opportunities.length ? <OppTable rows={x.top_opportunities} compact /> : <div className="muted">No scan yet — run a scan.</div>}
      </Card>
      <div className="grid g3">
        <Card title="Market regime">
          <div className="big-action">{x.regime.primary ?? "Unknown"}</div>
          <ul className="list">{x.regime.readings.map((r) => <li key={r.regime}>{r.regime} · score {r.score.toFixed(2)} · conf {r.confidence.toFixed(2)}</li>)}</ul>
        </Card>
        <Card title="Major risks">
          {x.major_risks.length ? <ul className="list">{x.major_risks.map((r, i) => <li key={i}><Link to={`/stocks/${r.ticker}`}>{r.ticker}</Link>: <span className="warn">{r.text}</span></li>)}</ul> : <div className="muted">None flagged</div>}
        </Card>
        <Card title="Upcoming catalysts">
          <ul className="list">{x.upcoming_catalysts.map((e) => <li key={e.event_id}>{e.event_date} · {e.title} <span className="muted">({e.days_until}d)</span></li>)}</ul>
        </Card>
      </div>
      <div className="grid g2">
        <Card title="Portfolio" right={<Link to="/portfolio">Manage →</Link>}>
          <div className="kv"><span className="k">Holdings</span><span>{x.portfolio.holdings}</span><span className="k">Cash</span><span>{price(x.portfolio.cash)}</span></div>
        </Card>
        <Card title="Provider health" right={<Link to="/health">Details →</Link>}>
          <div className="row">{x.provider_health.map((h) => <span key={h.name} className={`badge ${h.status === "HEALTHY" ? "a-BUY" : h.status === "DOWN" ? "a-SELL" : "a-WAIT"}`}>{h.kind}:{h.status}</span>)}</div>
        </Card>
      </div>
      {x.scan && (
        <Card title="Scanner pipeline (cheap → expensive)">
          <table><thead><tr><th>Stage</th><th>In</th><th>Out</th><th>Note</th></tr></thead>
            <tbody>{x.scan.stages.map((s) => <tr key={s.stage}><td>{s.stage}</td><td>{s.input_count}</td><td>{s.output_count}</td><td className="muted">{s.note}</td></tr>)}</tbody></table>
        </Card>
      )}
    </div>
  );
}
