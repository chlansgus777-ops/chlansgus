import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { num, pct, price } from "../format";
import type { OppRow } from "../types";
import { Action, Quality } from "./ui";

type Key = keyof OppRow;

const COLS: [Key, string][] = [
  ["rank", "Rank"], ["ticker", "Ticker"], ["company", "Company"], ["sector", "Sector"], ["price", "Price"], ["session", "Session"],
  ["score", "Score"], ["confidence", "Conf."], ["action", "Action"], ["ideal_entry", "Ideal Entry"], ["max_buy", "Max Buy"],
  ["target", "Target"], ["downside", "Downside"], ["rr", "R/R"], ["catalyst", "Catalyst"], ["risk", "Risk"], ["data_quality", "Data"],
];

export function OppTable({ rows, compact = false }: { rows: OppRow[]; compact?: boolean }) {
  const [sort, setSort] = useState<Key>("rank");
  const [asc, setAsc] = useState(true);
  const sorted = useMemo(() => {
    const r = [...rows];
    r.sort((a, b) => {
      const x = a[sort];
      const y = b[sort];
      if (x === y) return 0;
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      return (x < y ? -1 : 1) * (asc ? 1 : -1);
    });
    return r;
  }, [rows, sort, asc]);
  const cols = compact ? COLS.filter(([k]) => ["rank", "ticker", "company", "price", "score", "confidence", "action", "max_buy", "rr", "risk"].includes(k)) : COLS;
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            {cols.map(([k, l]) => (
              <th key={k} onClick={() => { if (sort === k) setAsc(!asc); else { setSort(k); setAsc(true); } }}>
                {l}{sort === k ? (asc ? " ▲" : " ▼") : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.id}>
              {cols.map(([k]) => (
                <td key={k}>{cell(r, k)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function cell(r: OppRow, k: Key) {
  switch (k) {
    case "ticker":
      return <Link to={`/stocks/${r.ticker}`}><b>{r.ticker}</b></Link>;
    case "company":
      return <span title={r.company}>{r.company.length > 26 ? `${r.company.slice(0, 26)}…` : r.company}</span>;
    case "price":
      return <span title={`${r.price_source ?? ""} ${r.price_timestamp ?? ""}`}>{price(r.price)} <Quality q={r.price_quality} /></span>;
    case "score":
      return <b>{num(r.score, 1)}</b>;
    case "confidence":
      return `${num(r.confidence, 0)}%`;
    case "action":
      return <><Action a={r.action} />{r.vetoes.length > 0 && <span className="warn" title={r.vetoes.join(", ")}> ⛔</span>}</>;
    case "ideal_entry": case "max_buy": case "target":
      return price(r[k]);
    case "downside":
      return <span className="neg">{pct(r.downside)}</span>;
    case "rr":
      return num(r.rr, 2);
    case "catalyst":
      return r.catalyst ? <span title={r.catalyst_date ?? ""}>{r.catalyst.length > 24 ? `${r.catalyst.slice(0, 24)}…` : r.catalyst}</span> : "—";
    case "risk":
      return <span className={r.risk === "EXTREME" || r.risk === "HIGH" ? "neg" : r.risk === "MEDIUM" ? "warn" : "pos"}>{r.risk ?? "N/A"}</span>;
    case "data_quality":
      return <Quality q={r.data_quality} />;
    default: {
      const v = r[k];
      return v === null || v === undefined ? "N/A" : String(v);
    }
  }
}
