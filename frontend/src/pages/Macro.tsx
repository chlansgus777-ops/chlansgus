import { Card, Empty, Err, Loading, Quality } from "../components/ui";
import { useApi } from "../components/useApi";
import { num, pct, stamp } from "../format";
import { REGIME_KO, ko } from "../i18n";

interface Series { series_id: string; latest: { value: number | null; source: string; source_ts: string | null; quality: string; note?: string | null }; change_20d: number | null; pct_change_20d: number | null }
interface MacroResp { available: boolean; reason?: string; as_of?: string; series: Record<string, Series>; regimes: { regime: string; score: number; confidence: number; active: boolean; evidence: string[] }[]; primary_regime: string; factor_moves: { factor: string; move: number; evidence: string }[]; yield_curve_2s10s?: number | null }

export const MARKET_SERIES = ["SPX", "NASDAQ_COMP", "NDX", "RUT", "SOX", "VIX", "BREADTH_ABOVE_200D", "HY_SPREAD"];
export const MACRO_SERIES = ["FED_FUNDS", "US2Y", "US10Y", "US30Y", "CPI_YOY", "CORE_CPI_YOY", "PCE_YOY", "CORE_PCE_YOY", "PAYROLLS_CHG", "UNEMPLOYMENT", "GDP_QOQ_SAAR", "USD_INDEX", "USDKRW", "WTI", "BRENT", "GOLD"];

const SERIES_KO: Record<string, string> = {
  SPX: "S&P 500", NASDAQ_COMP: "나스닥 종합", NDX: "나스닥 100", RUT: "러셀 2000", SOX: "필라델피아 반도체", VIX: "VIX(변동성)", BREADTH_ABOVE_200D: "200일선 위 종목 비율",
  HY_SPREAD: "하이일드 스프레드(%p)", FED_FUNDS: "연방기금금리(%)", US2Y: "미 2년물(%)", US10Y: "미 10년물(%)", US30Y: "미 30년물(%)", CPI_YOY: "CPI 전년비(%)",
  CORE_CPI_YOY: "근원 CPI 전년비(%)", PCE_YOY: "PCE 전년비(%)", CORE_PCE_YOY: "근원 PCE 전년비(%)", PAYROLLS_CHG: "비농업 고용 증감(천명)", UNEMPLOYMENT: "실업률(%)",
  GDP_QOQ_SAAR: "GDP 성장률(연율, %)", USD_INDEX: "달러 지수", USDKRW: "원/달러 환율(원)", WTI: "WTI 유가($)", BRENT: "브렌트 유가($)", GOLD: "금($)",
};

export function SeriesTable({ data, ids }: { data: MacroResp; ids: string[] }) {
  return (
    <table><thead><tr><th>지표</th><th>값</th><th>20일 변화</th><th>출처</th><th>기준 시점</th><th>품질</th></tr></thead>
      <tbody>{ids.map((id) => { const s = data.series[id]; return <tr key={id}><td>{SERIES_KO[id] ?? id}</td><td>{s ? num(s.latest.value) : <span className="neg">없음(MISSING)</span>}</td><td>{s ? (s.pct_change_20d !== null ? pct(s.pct_change_20d) : num(s.change_20d)) : "N/A"}</td><td>{s?.latest.source ?? "—"}</td><td title={s?.latest.note ?? ""}>{stamp(s?.latest.source_ts)}</td><td><Quality q={s?.latest.quality ?? "MISSING"} /></td></tr>; })}</tbody></table>
  );
}

export function useMacro() {
  return useApi<MacroResp>("/macro");
}

export default function Macro() {
  const m = useMacro();
  if (m.state === "loading") return <Loading what="거시 경제" />;
  if (!m.data) return <Err error={m.error} retry={m.reload} />;
  if (!m.data.available) return <div className="warn" role="alert">거시 데이터 없음: {m.data.reason}</div>;
  const d = m.data;
  return (
    <div className="grid">
      <h3>주요 국면: {ko(REGIME_KO, d.primary_regime)}</h3>
      <div className="grid g2">
        <Card title="금리 · 물가 · 고용 · 환율 · 원자재"><SeriesTable data={d} ids={MACRO_SERIES} /><div className="muted">장단기 금리차(10년−2년): {num(d.yield_curve_2s10s ?? null)}%p · 월간/분기 지표는 발표 시각이 없어 전일 기준 공개값(ALFRED 빈티지)만 사용</div></Card>
        <Card title="시장 국면 (강도 · 신뢰 · 근거)">
          <table><tbody>{d.regimes.map((r) => <tr key={r.regime}><td className={r.active ? "pos" : "muted"}>{r.active ? "● " : "○ "}{ko(REGIME_KO, r.regime)}</td><td>{num(r.score)}</td><td>{num(r.confidence)}</td><td style={{ whiteSpace: "normal" }} className="muted">{r.evidence.join("; ")}</td></tr>)}</tbody></table>
        </Card>
      </div>
      <Card title="기업 노출도로 전달되는 거시 요인 변화">
        {d.factor_moves.length ? <ul className="list">{d.factor_moves.map((f) => <li key={f.factor}>{f.factor}: {num(f.move)} — {f.evidence}</li>)}</ul> : <Empty>없음</Empty>}
      </Card>
    </div>
  );
}
