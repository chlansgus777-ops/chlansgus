import { Card, Err, Loading, Quality } from "../components/ui";
import { useApi } from "../components/useApi";
import { num, pct, stamp } from "../format";

interface Series { series_id: string; latest: { value: number | null; source: string; source_ts: string | null; quality: string }; change_20d: number | null; pct_change_20d: number | null }
interface MacroResp { available: boolean; reason?: string; as_of?: string; series: Record<string, Series>; regimes: { regime: string; score: number; confidence: number; active: boolean; evidence: string[] }[]; primary_regime: string; factor_moves: { factor: string; move: number; evidence: string }[]; yield_curve_2s10s?: number | null }

export const MARKET_SERIES = ["SPX", "NASDAQ_COMP", "NDX", "RUT", "SOX", "VIX", "BREADTH_ABOVE_200D", "HY_SPREAD"];
export const MACRO_SERIES = ["FED_FUNDS", "US2Y", "US10Y", "US30Y", "CPI_YOY", "CORE_CPI_YOY", "PCE_YOY", "CORE_PCE_YOY", "PAYROLLS_CHG", "UNEMPLOYMENT", "GDP_QOQ_SAAR", "USD_INDEX", "WTI", "BRENT", "GOLD"];

export function SeriesTable({ data, ids }: { data: MacroResp; ids: string[] }) {
  return (
    <table><thead><tr><th>Series</th><th>Value</th><th>20D change</th><th>Source</th><th>As of</th><th>Quality</th></tr></thead>
      <tbody>{ids.map((id) => { const s = data.series[id]; return <tr key={id}><td>{id}</td><td>{s ? num(s.latest.value) : <span className="neg">MISSING</span>}</td><td>{s ? (s.pct_change_20d !== null ? pct(s.pct_change_20d) : num(s.change_20d)) : "N/A"}</td><td>{s?.latest.source ?? "—"}</td><td>{stamp(s?.latest.source_ts)}</td><td><Quality q={s?.latest.quality ?? "MISSING"} /></td></tr>; })}</tbody></table>
  );
}

export function useMacro() {
  return useApi<MacroResp>("/macro");
}

export default function Macro() {
  const m = useMacro();
  if (m.loading && !m.data) return <Loading what="macro" />;
  if (!m.data) return <Err error={m.error} />;
  if (!m.data.available) return <div className="warn">Macro data unavailable: {m.data.reason}</div>;
  const d = m.data;
  return (
    <div className="grid">
      <h1>Macro — primary regime: {d.primary_regime}</h1>
      <div className="grid g2">
        <Card title="Rates, inflation, labour, commodities"><SeriesTable data={d} ids={MACRO_SERIES} /><div className="muted">2s10s curve: {num(d.yield_curve_2s10s ?? null)}</div></Card>
        <Card title="Market regimes (score · confidence · evidence)">
          <table><tbody>{d.regimes.map((r) => <tr key={r.regime}><td className={r.active ? "pos" : "muted"}>{r.active ? "● " : "○ "}{r.regime}</td><td>{num(r.score)}</td><td>{num(r.confidence)}</td><td style={{ whiteSpace: "normal" }} className="muted">{r.evidence.join("; ")}</td></tr>)}</tbody></table>
        </Card>
      </div>
      <Card title="Factor moves transmitted to company exposures">
        <ul className="list">{d.factor_moves.map((f) => <li key={f.factor}>{f.factor}: {num(f.move)} — {f.evidence}</li>)}</ul>
      </Card>
    </div>
  );
}
