import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Action, Card, Err, Loading } from "../components/ui";
import { useApi } from "../components/useApi";
import { num, price } from "../format";
import type { OppRow } from "../types";

export default function Watchlist() {
  const w = useApi<{ ticker: string; note: string; latest: OppRow | null }[]>("/watchlist");
  const [t, setT] = useState("");
  if (w.loading && !w.data) return <Loading what="watchlist" />;
  if (!w.data) return <Err error={w.error} />;
  return (
    <div className="grid">
      <h1>Watchlist</h1>
      <Card>
        <form className="row" onSubmit={async (e) => { e.preventDefault(); if (t) { await api.post(`/watchlist/${t.toUpperCase()}`); setT(""); w.reload(); } }}>
          <input value={t} onChange={(e) => setT(e.target.value)} placeholder="Add ticker" /><button type="submit">Add</button>
        </form>
        <table><thead><tr><th>Ticker</th><th>Price</th><th>Score</th><th>Action</th><th>Max buy</th><th /></tr></thead>
          <tbody>{w.data.map((x) => <tr key={x.ticker}><td><Link to={`/stocks/${x.ticker}`}>{x.ticker}</Link></td><td>{price(x.latest?.price)}</td><td>{num(x.latest?.score ?? null, 1)}</td><td><Action a={x.latest?.action} /></td><td>{price(x.latest?.max_buy)}</td>
            <td><button onClick={async () => { await api.del(`/watchlist/${x.ticker}`); w.reload(); }}>Remove</button></td></tr>)}</tbody></table>
      </Card>
    </div>
  );
}
