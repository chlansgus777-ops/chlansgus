import { useState } from "react";
import { api } from "../api";
import { Card, Err, Loading } from "../components/ui";
import { useApi } from "../components/useApi";
import { num, pct, price } from "../format";

interface Pf { cash: number; total_value: number; holdings: { ticker: string; quantity: number; cost_basis: number; sector: string; themes: string[]; price: number | null }[]; sector_weights: Record<string, number>; note: string }

export default function Portfolio() {
  const p = useApi<Pf>("/portfolio");
  const [row, setRow] = useState({ ticker: "", quantity: "", cost: "" });
  const [cash, setCash] = useState("");
  if (p.loading && !p.data) return <Loading what="portfolio" />;
  if (!p.data) return <Err error={p.error} />;
  const save = async (body: unknown) => { await api.put("/portfolio", body); p.reload(); };
  return (
    <div className="grid">
      <h1>Portfolio <span className="muted" style={{ fontSize: 13 }}>— {p.data.note}</span></h1>
      <div className="grid g3">
        <Card title="Total value"><div className="big-action">{price(p.data.total_value)}</div></Card>
        <Card title="Cash"><div className="big-action">{price(p.data.cash)}</div>
          <form className="row" onSubmit={(e) => { e.preventDefault(); void save({ cash: Number(cash) }); }}><input value={cash} onChange={(e) => setCash(e.target.value)} placeholder="Set cash" /><button>Save</button></form></Card>
        <Card title="Sector exposure"><ul className="list">{Object.entries(p.data.sector_weights).map(([s, w]) => <li key={s}>{s}: {pct(w, 1, false)}</li>)}</ul></Card>
      </div>
      <Card title="Holdings (entered manually — used for concentration / correlation checks)">
        <table><thead><tr><th>Ticker</th><th>Qty</th><th>Cost</th><th>Price</th><th>Value</th><th>Sector</th><th>Themes</th></tr></thead>
          <tbody>{p.data.holdings.map((h) => <tr key={h.ticker}><td>{h.ticker}</td><td>{num(h.quantity, 0)}</td><td>{price(h.cost_basis)}</td><td>{price(h.price)}</td><td>{price((h.price ?? h.cost_basis) * h.quantity)}</td><td>{h.sector}</td><td>{h.themes.join(", ")}</td></tr>)}</tbody></table>
        <form className="row" style={{ marginTop: 10 }} onSubmit={(e) => { e.preventDefault(); void save({ holdings: [{ ticker: row.ticker, quantity: Number(row.quantity), cost_basis: Number(row.cost) }] }); }}>
          <input placeholder="Ticker" value={row.ticker} onChange={(e) => setRow({ ...row, ticker: e.target.value })} />
          <input placeholder="Quantity (0 removes)" value={row.quantity} onChange={(e) => setRow({ ...row, quantity: e.target.value })} />
          <input placeholder="Cost basis" value={row.cost} onChange={(e) => setRow({ ...row, cost: e.target.value })} />
          <button>Save holding</button>
        </form>
      </Card>
    </div>
  );
}
