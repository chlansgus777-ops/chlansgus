import { useMemo, useState } from "react";
import { OppTable, StalePriceNote, stalePriceCause } from "../components/OppTable";
import { Empty, Err, Loading, StaleData } from "../components/ui";
import { NotReady, ReadinessBanner } from "../components/Readiness";
import { useApi } from "../components/useApi";
import { useStatus } from "../components/status";
import { useViewQuotes } from "../quotes";
import { api } from "../api";
import { stamp } from "../format";
import { ACTION_INFO } from "../i18n";
import type { Opportunities as Opp } from "../types";

/** The last scan's candidates (buy, wait and watch), searchable and filterable — the "후보" tab of the stock screen. */
export default function Opportunities() {
  const o = useApi<Opp>("/opportunities");
  const st = useStatus();
  const [busy, setBusy] = useState(false);
  const [scanErr, setScanErr] = useState<string | null>(null);
  // the top of the list joins the app-wide quote stream, so 현재가 is live even when the scan's price was not
  useViewQuotes((o.data?.rows ?? []).slice(0, 30).map((r) => r.ticker));
  const marketOpen = st?.system.data?.market?.session === "REGULAR";
  const rescan = async () => {
    if (busy) return;  // never a second scan (and its AI calls) from a double click
    setBusy(true); st?.setScanning(true); setScanErr(null);
    try { await api.post("/scan?committee=true"); } catch (e) { setScanErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); st?.setScanning(false); st?.refresh(); o.reload(); }
  };
  const [filter, setFilter] = useState("");
  const [action, setAction] = useState("ALL");
  const [onlyCurrent, setOnlyCurrent] = useState(false);
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const r of o.data?.rows ?? []) c[r.action] = (c[r.action] ?? 0) + 1;
    return c;
  }, [o.data]);
  if (o.state === "loading") return <Loading what="매수 후보" rows={4} />;
  if (!o.data) return <Err error={o.error} retry={o.reload} />;
  const rows = o.data.rows.filter((r) => (action === "ALL" || r.action === action)
    && (!onlyCurrent || r.current_status === "CURRENT")
    && (filter === "" || `${r.ticker} ${r.company} ${r.sector}`.toLowerCase().includes(filter.toLowerCase())));
  const scan = o.data.scan;
  const stale = stalePriceCause(o.data.rows);
  return (
    <div className="grid">
      <ReadinessBanner r={o.data.readiness} />
      <StaleData error={o.error} at={o.fetchedAt} retry={o.reload} />
      <Err error={scanErr} />
      {stale && <StalePriceNote c={stale} marketOpen={marketOpen} onRescan={() => void rescan()} busy={busy || st?.scanning} />}
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
          <span className="caption">{rows.length.toLocaleString("ko-KR")}개 · {scan ? `스캔 #${scan.id} · 분석 ${stamp(scan.as_of)} · ${scan.scoring_model_version}` : "스캔 없음"}</span>
        </div>
        <div style={{ padding: "4px 8px 8px" }}>
          {rows.length ? <OppTable rows={rows} commonStale={!!stale} /> : o.data.readiness?.scanner_status === "SCANNER_NOT_READY"
            ? <div style={{ padding: 12 }}><Empty hint="데이터 준비가 100%에 가까워지면 다시 스캔하세요.">데이터 준비 중이라 후보를 계산하지 못했습니다(‘살 종목이 없음’이 아님).</Empty></div>
            : <div style={{ padding: 12 }}><Empty>조건에 맞는 후보가 없습니다.</Empty></div>}
        </div>
      </section>
      <div className="caption">표 머리글을 누르면 정렬합니다(키보드: Tab 후 Enter). 종목을 누르면 판단 근거와 가격 계획을 봅니다. 점수·손익비는 상승 확률이 아닙니다.</div>
    </div>
  );
}
