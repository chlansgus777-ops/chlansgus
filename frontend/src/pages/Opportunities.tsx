import { useMemo, useState } from "react";
import { OppTable } from "../components/OppTable";
import { useLiveRows } from "../components/liveBoard";
import { Empty, Err, Loading, StaleData } from "../components/ui";
import { NotReady, ReadinessBanner } from "../components/Readiness";
import { useApi } from "../components/useApi";
import { useStatus } from "../components/status";
import { useAllQuotes, useViewQuotes, type QuoteRow } from "../quotes";
import { stamp } from "../format";
import { ACTION_INFO } from "../i18n";
import type { Opportunities as Opp } from "../types";

/** "현재 유효" now: the live verdict of this very recommendation when one is on the stream (it follows every quote),
 * else the stored status — the same answer the row's "현재 유효성" column shows. */
export function validNow(r: { ticker: string; id: number; current_status: string | null }, live: Map<string, QuoteRow>): boolean {
  const j = live.get(r.ticker)?.judge;
  return j && j.rec_id === r.id && j.zone !== "NO_PLAN" ? j.valid_now : r.current_status === "CURRENT";
}

/** The candidates (buy, wait and watch), re-judged on the live price every second, searchable and filterable — the
 * "후보" tab of the stock screen. No scan button (owner 2026-09-29): the pool is chosen in the background and the list
 * follows the price by itself. */
export default function Opportunities() {
  const o = useApi<Opp>("/opportunities");
  useStatus();
  const lv = useLiveRows(o.data?.rows);
  // the top of the list joins the app-wide quote stream (1 s)
  useViewQuotes(lv.rows.slice(0, 40).map((r) => r.ticker));
  const [filter, setFilter] = useState("");
  const [action, setAction] = useState("ALL");
  const [onlyCurrent, setOnlyCurrent] = useState(false);
  const live = useAllQuotes();
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const r of lv.rows) c[r.action] = (c[r.action] ?? 0) + 1;
    return c;
  }, [lv.rows]);
  // the quote map only matters to the "현재 유효" filter: without it the table is not rebuilt on every print
  const liveKey = onlyCurrent ? live : null;
  const rows = useMemo(() => lv.rows.filter((r) => (action === "ALL" || r.action === action)
    && (!liveKey || validNow(r, liveKey))
    && (filter === "" || `${r.ticker} ${r.company} ${r.sector}`.toLowerCase().includes(filter.toLowerCase()))), [lv.rows, action, liveKey, filter]);
  if (o.state === "loading") return <Loading what="매수 후보" rows={4} />;
  if (!o.data) return <Err error={o.error} retry={o.reload} />;
  const scan = o.data.scan;
  return (
    <div className="grid">
      <ReadinessBanner r={o.data.readiness} />
      <StaleData error={o.error} at={o.fetchedAt} retry={o.reload} />
      {o.data.readiness?.scanner_status === "SCANNER_NOT_READY" && !o.data.rows.length && <NotReady r={o.data.readiness} onChange={o.reload} />}
      <section className="card flush">
        <div className="row spread" style={{ padding: "16px 18px 12px", borderBottom: "1px solid var(--line)" }}>
          <div className="row">
            <input placeholder="종목 · 회사명 · 업종 검색" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="후보 검색" style={{ width: 240 }} />
            <select value={action} onChange={(e) => setAction(e.target.value)} aria-label="추천 필터">
              <option value="ALL">전체 판단</option>
              {Object.entries(ACTION_INFO).map(([a, i]) => <option key={a} value={a}>{i.label}({a}){counts[a] ? ` · ${counts[a]}` : ""}</option>)}
            </select>
            <label className="check"><input type="checkbox" checked={onlyCurrent} onChange={(e) => setOnlyCurrent(e.target.checked)} /> 현재 유효한 추천만</label>
          </div>
          <span className="caption live-cap">{lv.at ? <span className="live-dot" aria-hidden /> : null}{rows.length.toLocaleString("ko-KR")}개 · {lv.at ? "실시간 판정 · 1초" : "실시간 가격 기다리는 중"}{scan ? ` · 후보 선정 ${stamp(scan.as_of)}` : ""}</span>
        </div>
        <div style={{ padding: "4px 8px 8px" }}>
          {rows.length ? <OppTable rows={rows} commonStale={false} /> : o.data.readiness?.scanner_status === "SCANNER_NOT_READY"
            ? <div style={{ padding: 12 }}><Empty hint="데이터 준비가 끝나면 후보를 자동으로 고릅니다.">데이터 준비 중이라 후보를 계산하지 못했습니다(‘살 종목이 없음’이 아님).</Empty></div>
            : <div style={{ padding: 12 }}><Empty>조건에 맞는 후보가 없습니다.</Empty></div>}
        </div>
      </section>
      <div className="caption">표 머리글을 누르면 정렬합니다(키보드: Tab 후 Enter). 종목을 누르면 판단 근거와 가격 계획을 봅니다. 점수·손익비는 상승 확률이 아닙니다.</div>
    </div>
  );
}
