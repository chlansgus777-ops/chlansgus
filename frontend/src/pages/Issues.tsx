import { useState } from "react";
import { Link } from "react-router-dom";
import { Card, Err, Loading } from "../components/ui";
import { useApi } from "../components/useApi";
import { num, stamp } from "../format";
import type { CompanyIssueImpact } from "../types";

interface IssueJ { issue_id: string; title: string; category: string; summary: string; publish_time: string; sources: string[]; confirmed_status: string; importance: number; confidence: number; market_awareness: number; primary_effects: { node_id: string; direction: number; mechanism: string[] }[] }
interface Resp { available: boolean; reason?: string; issues: { issue: IssueJ; affected_stocks: { ticker: string; hops: number; swing: number }[]; affected_sectors: string[] }[]; injection_flags: Record<string, string[]> }
interface Detail { issue: IssueJ; impacts: CompanyIssueImpact[]; direct: string[]; indirect: string[] }

export default function Issues() {
  const r = useApi<Resp>("/issues");
  const [sel, setSel] = useState<string | null>(null);
  const d = useApi<Detail>(sel ? `/issues/${sel}` : null);
  if (r.loading && !r.data) return <Loading what="issues" />;
  if (!r.data) return <Err error={r.error} />;
  if (!r.data.available) return <div className="warn">News/issue data unavailable: {r.data.reason}</div>;
  return (
    <div className="grid">
      <h1>Issues (structured events, not headlines)</h1>
      {Object.keys(r.data.injection_flags).length > 0 && <div className="warn">⚠ {Object.keys(r.data.injection_flags).length} article(s) contained prompt-injection patterns and were treated strictly as untrusted data.</div>}
      <Card>
        <table><thead><tr><th>Issue</th><th>Category</th><th>Importance</th><th>Confidence</th><th>Awareness</th><th>Status</th><th>Sectors</th><th>Stocks (2–6W impact)</th></tr></thead>
          <tbody>{r.data.issues.map(({ issue: i, affected_stocks, affected_sectors }) => (
            <tr key={i.issue_id} onClick={() => setSel(i.issue_id)} style={{ cursor: "pointer" }}>
              <td style={{ whiteSpace: "normal", minWidth: 280 }}><b>{i.title}</b><div className="muted">{stamp(i.publish_time)}</div></td><td>{i.category}</td><td>{num(i.importance)}</td><td>{num(i.confidence)}</td><td>{num(i.market_awareness)}</td><td>{i.confirmed_status}</td>
              <td style={{ whiteSpace: "normal" }}>{affected_sectors.join(", ")}</td>
              <td style={{ whiteSpace: "normal" }}>{affected_stocks.slice(0, 8).map((s) => <span key={s.ticker} className={s.swing > 0 ? "pos" : "neg"}>{s.ticker}{s.hops ? `(${s.hops}h)` : ""} {num(s.swing, 0)} </span>)}</td>
            </tr>))}</tbody></table>
      </Card>
      {sel && d.data && (
        <Card title={`Impact detail — ${d.data.issue.title}`}>
          <div>Direct: {d.data.direct.join(", ") || "—"} · Indirect (via exposure graph): {d.data.indirect.join(", ") || "—"}</div>
          <table><thead><tr><th>Ticker</th><th>Exposure path</th><th>Today</th><th>1–5D</th><th>2–6W</th><th>1–4Q</th><th>Mechanism</th></tr></thead>
            <tbody>{d.data.impacts.map((x) => <tr key={x.ticker}><td><Link to={`/stocks/${x.ticker}`}>{x.ticker}</Link></td><td>{x.exposure_path.join(" → ")}</td>
              {x.horizons.map((h) => <td key={h.horizon} className={h.impact_score > 0 ? "pos" : h.impact_score < 0 ? "neg" : ""}>{num(h.impact_score, 1)}</td>)}
              <td style={{ whiteSpace: "normal" }} className="muted">{x.horizons[3]?.mechanism.join(" → ")}</td></tr>)}</tbody></table>
        </Card>
      )}
    </div>
  );
}
