import { Card, Err, Loading } from "../components/ui";
import { MARKET_SERIES, SeriesTable, useMacro } from "./Macro";

export default function Market() {
  const m = useMacro();
  if (m.loading && !m.data) return <Loading what="market" />;
  if (!m.data) return <Err error={m.error} />;
  if (!m.data.available) return <div className="warn">Market data unavailable: {m.data.reason}</div>;
  return (
    <div className="grid">
      <h1>Market</h1>
      <Card title="Indices, volatility, breadth, credit"><SeriesTable data={m.data} ids={MARKET_SERIES} /></Card>
      <Card title="Active regimes">
        <div className="row">{m.data.regimes.filter((r) => r.active).map((r) => <span key={r.regime} className="badge a-WATCH">{r.regime}</span>)}</div>
      </Card>
    </div>
  );
}
