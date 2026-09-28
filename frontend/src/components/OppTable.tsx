import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { num, pct, price, stamp } from "../format";
import { RISK_KO, ko } from "../i18n";
import type { OppRow } from "../types";
import { useQuote } from "../quotes";
import { LivePrice } from "./LivePrice";
import { Action, Quality, StatusBadge, Vetoes } from "./ui";

type Key = "rank" | "ticker" | "action" | "score" | "price" | "max_buy" | "rr" | "current_status" | "risk";

/** The columns that help choose between candidates; session and data-quality words that were the same on every row
 * moved to the header / appear only where a row differs. */
const COLS: [Key, string, string, boolean][] = [
  ["rank", "순위", "", false], ["ticker", "종목", "", false], ["action", "판단", "추천 행동. 만료된 매수 신호는 취소선으로 표시", false],
  ["score", "점수", "0~100 규칙 점수(AI가 올릴 수 없음). 80 이상 매수·72 이상 소량 매수 후보 — 상승 확률이 아님", true],
  ["price", "현재가", "위: 최신 시세(앱 공용 스트림) · 아래: 분석 시점 가격(USD)", true], ["max_buy", "최대 매수가", "이 가격을 넘으면 손익비 2 미만 → 대기", true],
  ["rr", "손익비", "(목표가−현재가)÷(현재가−손절가) — 도달 확률이 아님", true], ["current_status", "현재 유효성", "추천 이후 거래일 경과·현재가로 다시 판정", false],
  ["risk", "이벤트 위험", "가까운 실적·일정의 위험 수준", false],
];

/** The latest quote when the app has one for this name; otherwise the analysis-time price, labelled as such — never
 * a bare dash above it. */
export function PriceCell({ r }: { r: OppRow }) {
  const { row } = useQuote(r.ticker);
  const basis = <span className="caption" style={{ whiteSpace: "nowrap" }} title={`분석 기준가 · 출처 ${r.price_source ?? "N/A"} · ${stamp(r.price_timestamp)}`}>{row?.price != null ? <>분석 {price(r.price)}</> : "분석 시점"}{r.price_quality !== "FRESH" ? <> <Quality q={r.price_quality} /></> : null}</span>;
  return (
    <span style={{ display: "inline-flex", flexDirection: "column", alignItems: "flex-end", gap: 1 }}>
      {row?.price != null ? <LivePrice ticker={r.ticker} size="sm" showState={false} /> : <span style={{ whiteSpace: "nowrap" }}>{price(r.price)}</span>}
      {basis}
    </span>
  );
}

export function OppTable({ rows, compact = false }: { rows: OppRow[]; compact?: boolean }) {
  const [sort, setSort] = useState<Key>("rank");
  const [asc, setAsc] = useState(true);
  const nav = useNavigate();
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
  const cols = compact ? COLS.filter(([k]) => k !== "risk") : COLS;
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            {cols.map(([k, l, help, numeric]) => (
              <th key={k} title={help} className={`sortable${numeric ? " num" : ""}`} aria-sort={sort === k ? (asc ? "ascending" : "descending") : "none"}
                  tabIndex={0} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (sort === k) setAsc(!asc); else { setSort(k); setAsc(true); } } }}
                  onClick={() => { if (sort === k) setAsc(!asc); else { setSort(k); setAsc(true); } }}>
                {l}{sort === k ? <span className="dir">{asc ? "▲" : "▼"}</span> : null}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.id} className={r.actionable_now === false ? "row-expired" : ""} onDoubleClick={() => nav(`/stocks/${r.ticker}`)}>
              {cols.map(([k, , , numeric]) => <td key={k} className={numeric ? "num" : undefined}>{cell(r, k)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function cell(r: OppRow, k: Key) {
  switch (k) {
    case "rank":
      return <span className="muted">{r.rank ?? "—"}</span>;
    case "ticker":
      return (
        <div className="tk-cell">
          <Link to={`/stocks/${r.ticker}`}>{r.ticker}</Link>
          <span className="co" title={r.company}>{r.company}{r.sector_known === false ? " · 업종 불명확" : r.sector ? ` · ${r.sector}` : ""}</span>
        </div>
      );
    case "action":
      return <div className="row tight"><Action a={r.action} status={r.current_status} quality={r.data_quality} /><Vetoes v={r.vetoes} />{r.data_quality !== "FRESH" && r.data_quality !== "DELAYED" ? <Quality q={r.data_quality} /> : null}</div>;
    case "score":
      return <span className="score-mini"><b>{num(r.score, 1)}</b><span className="bar"><span style={{ display: "block", height: "100%", width: `${Math.max(0, Math.min(100, r.score))}%`, borderRadius: 99, background: r.score >= 80 ? "var(--buy)" : r.score >= 72 ? "rgba(114,184,255,.6)" : "var(--faint)" }} /></span></span>;
    case "price":
      return <PriceCell r={r} />;
    case "max_buy": {
      const d = r.price != null && r.max_buy != null ? r.max_buy / r.price - 1 : null;
      return <span>{price(r.max_buy)}{d != null ? <span className={`caption ${d < 0 ? "warn" : ""}`} style={{ display: "block" }}>{d < 0 ? `${pct(-d, 1, false)} 초과` : `여유 ${pct(d, 1, false)}`}</span> : null}</span>;
    }
    case "rr":
      return <span className={r.rr != null && r.rr < 2 ? "warn" : undefined}>{num(r.rr, 2)}</span>;
    case "current_status":
      return <StatusBadge s={r.current_status} reason={r.current_status_reason} />;
    case "risk":
      return r.catalyst ? <span title={r.catalyst_date ?? ""}><span className={r.risk === "EXTREME" || r.risk === "HIGH" ? "neg" : r.risk === "MEDIUM" ? "warn" : "muted"}>{ko(RISK_KO, r.risk, "N/A")}</span> <span className="caption">· {r.catalyst.length > 22 ? `${r.catalyst.slice(0, 22)}…` : r.catalyst}</span></span>
        : <span className={r.risk === "EXTREME" || r.risk === "HIGH" ? "neg" : r.risk === "MEDIUM" ? "warn" : "muted"}>{ko(RISK_KO, r.risk, "N/A")}</span>;
    default:
      return null;
  }
}
