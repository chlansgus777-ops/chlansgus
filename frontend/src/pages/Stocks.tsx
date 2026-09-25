import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { OppTable } from "../components/OppTable";
import { Card } from "../components/ui";
import { useApi } from "../components/useApi";
import type { Opportunities } from "../types";

export default function Stocks() {
  const [t, setT] = useState("");
  const nav = useNavigate();
  const o = useApi<Opportunities>("/opportunities");
  return (
    <div className="grid">
      <h1>Stocks</h1>
      <Card title="Analyze any US-listed ticker">
        <form className="row" onSubmit={(e) => { e.preventDefault(); if (t.trim()) nav(`/stocks/${t.trim().toUpperCase()}`); }}>
          <input placeholder="Ticker, e.g. NVDA" value={t} onChange={(e) => setT(e.target.value)} />
          <button type="submit">Analyze</button>
        </form>
      </Card>
      {o.data && <Card title="Latest scan candidates"><OppTable rows={o.data.rows} compact /></Card>}
    </div>
  );
}
