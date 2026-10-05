import { sectorKo } from "../i18n";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { num, pct, price, stamp } from "../format";
import { RISK_KO, ko } from "../i18n";
import type { OppRow } from "../types";
import { useQuote, useViewQuotes } from "../quotes";
import { LivePrice } from "./LivePrice";
import { LiveZone } from "./LiveZone";
import { Action, Quality, StatusBadge, Vetoes } from "./ui";
import { BUY_SCORE, SMALL_SCORE } from "../thresholds";

type Key = "rank" | "ticker" | "action" | "score" | "price" | "max_buy" | "rr" | "current_status" | "risk";

/** The columns that help choose between candidates; session and data-quality words that were the same on every row
 * moved to the header / appear only where a row differs. */
const COLS: [Key, string, string, boolean][] = [
  ["rank", "순위", "", false], ["ticker", "종목", "", false], ["action", "판단", "추천 행동. 만료된 매수 신호는 취소선으로 표시", false],
  ["score", "점수", `0~100 규칙 점수(AI가 올릴 수 없음). ${BUY_SCORE} 이상 매수·${SMALL_SCORE} 이상 소량 매수 후보 — 상승 확률이 아님`, true],
  ["price", "현재가", "위: 최신 시세(앱 공용 스트림) · 아래: 분석 시점 가격(USD)", true], ["max_buy", "최대 매수가", "이 가격을 넘으면 손익비 2 미만 → 대기", true],
  ["rr", "손익비", "(목표가−현재가)÷(현재가−손절가) — 도달 확률이 아님", true], ["current_status", "현재 유효성", "추천 이후 거래일 경과·현재가로 다시 판정", false],
  ["risk", "이벤트 위험", "가까운 실적·일정의 위험 수준", false],
];

/** The latest quote when the app has one for this name; otherwise the analysis-time price, labelled as such — never
 * a bare dash above it. */
export function PriceCell({ r, quiet = false }: { r: OppRow; quiet?: boolean }) {
  const { row } = useQuote(r.ticker);
  // ``quiet``: the scan-wide "price not current" reason is explained above the table — not repeated per row
  const stale = r.price_quality !== "FRESH" && r.price_quality !== "DELAYED";
  const basis = <span className="caption" style={{ whiteSpace: "nowrap" }} title={`분석 기준가 · 출처 ${r.price_source ?? "N/A"} · ${stamp(r.price_timestamp)}${stale ? " · 분석 때 현재가가 최신이 아니어서 판단에 쓰지 않음" : ""}`}>{r.price != null ? <>분석 {price(r.price)}</> : row?.price != null ? "분석 때 현재가 없음" : "분석 시점"}{r.price_quality !== "FRESH" && !(quiet && stale) ? <> <Quality q={r.price_quality} /></> : null}</span>;
  return (
    <span style={{ display: "inline-flex", flexDirection: "column", alignItems: "flex-end", gap: 1 }}>
      {row?.price != null ? <LivePrice ticker={r.ticker} size="sm" showState={false} />
        : r.price != null ? <span style={{ whiteSpace: "nowrap" }}>{price(r.price)}</span>
        : <span className="muted" title="아직 받은 시세가 없습니다(장이 열리면 실시간으로 표시)">—</span>}
      {basis}
    </span>
  );
}

/** Most of a scan's rows withheld for the same reason — the price was not current when the scan ran. Said once above
 * the table (with why and what to do) instead of three identical badges on every row (owner report 2026-09-28:
 * "전부 오래됨·데이터부족·거부권 — 뭘 보라는 거야, 오류야?"). */
/** Vetoes that already say the data is missing / stale — a separate data-quality chip would repeat them. */
const DATA_VETOES = new Set(["STALE_PRICE", "STALE_CORE_DATA", "MISSING_CORE_DATA", "SEVERE_DATA_CONFLICT"]);

export function stalePriceCause(rows: OppRow[]): { n: number; total: number; session: string | null } | null {
  const n = rows.filter((r) => r.vetoes.includes("STALE_PRICE")).length;
  if (n < 3 || n < rows.length * 0.6) return null;
  return { n, total: rows.length, session: (() => { const r = rows.find((x) => x.vetoes.includes("STALE_PRICE")); return r?.scan_session ?? r?.session ?? null; })() };
}

const STALE_WHY: Record<string, string> = {
  PREMARKET: "스캔한 시각이 미국 프리마켓이었습니다. 프리마켓에는 종목 대부분의 체결이 드물어 20분 안의 체결가가 없었고, 어제 종가를 지금 가격으로 쓰지 않는 안전 규칙 때문에 판단을 보류했습니다.",
  AFTER_HOURS: "스캔한 시각이 미국 애프터마켓이었습니다. 시간외에는 종목 대부분의 체결이 드물어 20분 안의 체결가가 없었고, 오래된 가격으로 판단하지 않도록 보류했습니다.",
  OVERNIGHT: "스캔한 시각이 미국 야간 시간대였습니다. 이때는 새 체결이 없어 지금 가격을 확인할 수 없었습니다.",
  CLOSED: "장이 닫힌 뒤 마지막 종가를 받지 못했습니다 — 시세 공급자 연결이나 키를 확인하세요.",
  REGULAR: "정규장인데도 현재가를 받지 못했습니다 — 시세 공급자(Finnhub) 연결이나 키를 확인하세요.",
};

/** ``session``: the US session NOW — a rescan gives a verdict in the regular session and while the market is fully
 * closed (the last close is then the current price); in pre-market / after-hours it would be held again. */
export function StalePriceNote({ c, session = null, onRescan, busy = false }: { c: { n: number; total: number; session: string | null }; session?: string | null; onRescan?: () => void; busy?: boolean }) {
  const rescanNow = session === "REGULAR" || session === "CLOSED";
  return (
    <div className="ribbon info" role="note" data-testid="stale-price-note">
      <span className="cap">판단 보류 이유</span>
      <div className="msg">
        <b>{c.total}개 중 {c.n}개가 ‘데이터 부족’인 것은 오류가 아니라, 스캔할 때 현재가가 최신이 아니었기 때문입니다.</b>{" "}
        {STALE_WHY[c.session ?? ""] ?? "스캔할 때 20분 안의 체결가가 없어 매수 판단을 보류했습니다."}{" "}
        점수·가격 계획은 참고로 볼 수 있습니다. 판단이 나오는 시간에 다시 스캔하세요: 정규장(한국 시간 밤 10:30~새벽 5:00) 또는 장이 완전히 닫힌 뒤(한국 시간 오전 9:00~오후 5:00·주말, 종가 기준) — 서머타임이 아니면 각각 1시간 늦습니다.
        표에서는 이 공통 사유를 줄마다 반복하지 않습니다.
        {rescanNow && onRescan ? <div style={{ marginTop: 8 }}><button className="primary sm" disabled={busy} onClick={onRescan} data-testid="rescan-now">{busy ? <><span className="spin" />스캔 중…</> : session === "REGULAR" ? "지금 정규장 — 다시 스캔하기" : "지금 장 마감 — 종가 기준으로 다시 스캔하기"}</button></div> : null}
      </div>
    </div>
  );
}

export function OppTable({ rows, compact = false, commonStale = false }: { rows: OppRow[]; compact?: boolean; commonStale?: boolean }) {
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
  // the names in the list join the live quote stream (first 40): their verdicts are re-checked with a current price
  // every second instead of reading "현재가 재확인 필요", and a stale analysis among them is redone in the background
  useViewQuotes(sorted.slice(0, 40).map((r) => r.ticker));
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            {cols.map(([k, l, help, numeric]) => (
              <th key={k} data-col={k} title={help} className={`sortable${numeric ? " num" : ""}`} aria-sort={sort === k ? (asc ? "ascending" : "descending") : "none"}
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
              {cols.map(([k, , , numeric]) => <td key={k} data-col={k} className={numeric ? "num" : undefined}>{cell(r, k, commonStale)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function cell(r: OppRow, k: Key, commonStale = false) {
  switch (k) {
    case "rank":
      return <span className="muted">{r.rank ?? "—"}</span>;
    case "ticker":
      return (
        <div className="tk-cell">
          <Link to={`/stocks/${r.ticker}`}>{r.ticker}</Link>
          <span className="co" title={r.company}>{r.company}{r.sector_known === false ? " · 업종 불명확" : r.sector ? ` · ${sectorKo(r.sector)}` : ""}</span>
        </div>
      );
    case "action":
    {
      // the scan-wide "price not current" reason is explained above the table: only what differs stays on the row
      const vetoes = commonStale ? r.vetoes.filter((v) => v !== "STALE_PRICE") : r.vetoes;
      const priceOnly = commonStale && r.vetoes.includes("STALE_PRICE") && r.price_quality !== "FRESH" && r.price_quality !== "DELAYED";
      return <div className="row tight"><Action a={r.action} status={r.current_status} quality={r.execution_quality ?? r.data_quality} /><Vetoes v={vetoes} />{r.data_quality !== "FRESH" && r.data_quality !== "DELAYED" && !priceOnly && !r.vetoes.some((v) => DATA_VETOES.has(v)) ? <Quality q={r.data_quality} /> : null}</div>;
    }
    case "score":
      return <span className="score-mini"><b>{num(r.score, 1)}</b><span className="bar"><span style={{ display: "block", height: "100%", width: `${Math.max(0, Math.min(100, r.score))}%`, borderRadius: 99, background: r.score >= 80 ? "var(--buy)" : r.score >= 72 ? "rgba(114,184,255,.6)" : "var(--faint)" }} /></span></span>;
    case "price":
      return <PriceCell r={r} quiet={commonStale} />;
    case "max_buy": {
      if (r.max_buy == null) return <span className="muted" title="분석 때 현재가가 없어 가격 계획을 만들지 않음">—</span>;
      const d = r.price != null && r.max_buy != null ? r.max_buy / r.price - 1 : null;
      // an undecided name's plan is reference only — muted, never styled like an actionable price
      if (r.action === "DATA INSUFFICIENT") return <span className="muted" title="판단 보류 종목의 가격 계획은 참고용입니다 — 실행 근거가 아닙니다">{price(r.max_buy)}<span className="caption" style={{ display: "block" }}>참고용</span></span>;
      return <span>{price(r.max_buy)}{d != null ? <span className={`caption ${d < 0 ? "warn" : ""}`} style={{ display: "block" }}>{d < 0 ? `${pct(-d, 1, false)} 초과` : `여유 ${pct(d, 1, false)}`}</span> : null}</span>;
    }
    case "rr":
      return r.rr == null ? <span className="muted" title="분석 때 현재가가 없어 계산하지 않음">—</span> : <span className={r.action === "DATA INSUFFICIENT" ? "muted" : r.rr < 2 ? "warn" : undefined}>{num(r.rr, 2)}</span>;
    case "current_status":
      // the live verdict when this very recommendation is the one judged on the stream; else the stored status
      return <span className="stack-tight"><LiveZone ticker={r.ticker} recId={r.id} compact /><StatusBadgeUnlessLive r={r} /></span>;
    case "risk":
      return r.catalyst ? <span title={r.catalyst_date ?? ""}><span className={r.risk === "EXTREME" || r.risk === "HIGH" ? "neg" : r.risk === "MEDIUM" ? "warn" : "muted"}>{ko(RISK_KO, r.risk, "N/A")}</span> <span className="caption">· {r.catalyst.length > 22 ? `${r.catalyst.slice(0, 22)}…` : r.catalyst}</span></span>
        : <span className={r.risk === "EXTREME" || r.risk === "HIGH" ? "neg" : r.risk === "MEDIUM" ? "warn" : "muted"}>{ko(RISK_KO, r.risk, "N/A")}</span>;
    default:
      return null;
  }
}

function StatusBadgeUnlessLive({ r }: { r: OppRow }) {
  const { row } = useQuote(r.ticker);
  if (row?.judge && row.judge.rec_id === r.id && row.judge.zone !== "NO_PLAN") return null;
  return <StatusBadge s={r.current_status} reason={r.current_status_reason} action={r.action} />;
}
