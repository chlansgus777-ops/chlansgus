import { useState } from "react";
import { OppTable } from "../components/OppTable";
import { Card, Err, Loading } from "../components/ui";
import { useApi } from "../components/useApi";
import { stamp } from "../format";
import type { Opportunities as Opp } from "../types";

export default function Opportunities() {
  const o = useApi<Opp>("/opportunities");
  const [filter, setFilter] = useState("");
  const [action, setAction] = useState("ALL");
  if (o.loading && !o.data) return <Loading what="opportunities" />;
  if (!o.data) return <Err error={o.error} />;
  const rows = o.data.rows.filter((r) => (action === "ALL" || r.action === action) && (filter === "" || `${r.ticker} ${r.company} ${r.sector}`.toLowerCase().includes(filter.toLowerCase())));
  return (
    <div className="grid">
      <h1>Opportunity Scanner</h1>
      <Card right={<span className="muted">{o.data.scan ? `scan #${o.data.scan.id} · ${stamp(o.data.scan.as_of)} · ${o.data.scan.scoring_model_version}` : "no scan"}</span>}>
        <div className="row" style={{ marginBottom: 10 }}>
          <input placeholder="Filter ticker / company / sector" value={filter} onChange={(e) => setFilter(e.target.value)} />
          <select value={action} onChange={(e) => setAction(e.target.value)}>
            {["ALL", "BUY", "BUY SMALL", "ADD", "HOLD", "WATCH", "WAIT", "REDUCE", "SELL", "DATA INSUFFICIENT"].map((a) => <option key={a}>{a}</option>)}
          </select>
          <span className="muted">{rows.length} candidates</span>
        </div>
        <OppTable rows={rows} />
      </Card>
    </div>
  );
}
