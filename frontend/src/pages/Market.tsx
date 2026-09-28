import { useSearchParams } from "react-router-dom";
import { Err, Loading, Quality, Tabs } from "../components/ui";
import { num, pct, stamp } from "../format";
import { REGIME_KO, ko } from "../i18n";
import CalendarPage from "./Calendar";
import Issues from "./Issues";
import { MACRO_SERIES, MARKET_SERIES, SERIES_KO, SeriesTable, useMacro } from "./Macro";

type Tab = "issues" | "calendar" | "macro" | "indices";
const REGIME_HELP: Record<string, string> = {
  "Risk On": "투자자들이 위험을 감수하는 분위기 — 성장주·경기민감주에 우호적", "Risk Off": "투자자들이 위험을 피하는 분위기 — 방어주·현금 선호",
  "AI Momentum": "AI 관련 종목이 상대적으로 강한 환경", "Inflation Shock": "물가 상승 압력이 커 금리·밸류에이션에 부담", "Growth Scare": "경기 둔화 우려가 커진 환경",
  "Credit Stress": "회사채 금리차가 벌어지며 자금 조달 환경이 나빠짐", "Multiple Compression": "금리 상승 등으로 주가 배수가 낮아지는 환경",
  "Multiple Expansion": "금리 하락 등으로 주가 배수가 높아지는 환경", Neutral: "뚜렷한 방향 없이 종목별로 움직이는 환경", Unknown: "거시 지표를 받지 못해 시장 국면을 판단하지 않았습니다",
};
const KEY = ["SPX", "NASDAQ_COMP", "VIX", "US10Y", "USD_INDEX", "WTI"];

/** The market in one place (product overhaul 2026-09-28: 이슈·캘린더 and 시장·거시 were separate menus): the mood
 * first, then the issues, the calendar and the indicator tables as tabs kept in the URL. */
export default function Market() {
  const [params, setParams] = useSearchParams();
  const t = params.get("tab");
  const tab: Tab = t === "calendar" || t === "macro" || t === "indices" ? t : "issues";
  const m = useMacro();
  const d = m.data;
  return (
    <div className="grid">
      <div className="page-head enter">
        <div><h1>시장</h1><div className="t-sub">지금 시장 분위기와, 뉴스·일정·금리·환율이 종목에 어떤 바람을 만드는지 봅니다. 사실(기사·지표)과 MarketLens의 해석을 나눠 표시합니다.</div></div>
      </div>
      {m.state === "loading" || d?.pending ? <Loading what="시장 분위기(거시 지표)" rows={0} /> : !d ? <Err error={m.error} retry={m.reload} /> : !d.available ? (
        <div className="ribbon warn" role="alert"><span className="cap">⚠ 거시 데이터 없음</span><div className="msg">{d.reason} — 다른 값으로 대체하지 않습니다. 시장 국면은 판단하지 않습니다.</div></div>
      ) : (
        <section className="today enter" aria-label="시장 분위기">
          <div className="t-kicker">지금 시장 분위기{d.as_of ? ` · ${stamp(d.as_of)} 기준` : ""}{d.refreshing ? " · 새로 받는 중" : d.refresh_error ? " · 새로 받기 실패(이전 값)" : ""}</div>
          <div className="big" style={{ marginTop: 6 }}><span className="count-l" style={{ fontSize: 30, letterSpacing: "-0.03em" }}>{ko(REGIME_KO, d.primary_regime)}</span></div>
          <p className="lead">{REGIME_HELP[d.primary_regime] ?? "거시 지표로 판단한 현재 환경"}{d.regimes.filter((r) => r.active && r.regime !== d.primary_regime).length ? ` · 함께 나타난 국면: ${d.regimes.filter((r) => r.active && r.regime !== d.primary_regime).map((r) => ko(REGIME_KO, r.regime)).join(", ")}` : ""}</p>
          <div className="facts" style={{ gridTemplateColumns: "repeat(6, minmax(0, 1fr))" }}>
            {KEY.map((id) => {
              const s = d.series[id];
              const ch = s ? (s.pct_change_20d !== null ? s.pct_change_20d : null) : null;
              return (
                <div key={id}>
                  <div className="t">{SERIES_KO[id] ?? id}</div>
                  <div className="v">{s ? num(s.latest.value) : <span className="danger">없음</span>}</div>
                  <div className="s">{s ? (ch !== null ? <span className={ch >= 0 ? "pos" : "neg"}>20일 {pct(ch)}</span> : s.change_20d !== null ? `20일 ${num(s.change_20d)}` : "변화 없음") : "MISSING"}{s && s.latest.quality !== "FRESH" ? <> · <Quality q={s.latest.quality} /></> : null}</div>
                </div>
              );
            })}
          </div>
        </section>
      )}
      <div className="row spread">
        <Tabs<Tab> label="시장 정보" value={tab} onChange={(v) => setParams(v === "issues" ? {} : { tab: v }, { replace: true })}
              items={[["issues", "이슈"], ["calendar", "일정"], ["macro", "거시 지표"], ["indices", "지수·시장 폭"]]} />
      </div>
      {tab === "issues" ? <Issues /> : tab === "calendar" ? <CalendarPage /> : !d ? null : !d.available ? null : tab === "macro" ? (
        <div className="grid">
          <section className="card flush"><div style={{ padding: "6px 8px 8px" }}><SeriesTable data={d} ids={MACRO_SERIES} /></div></section>
          <div className="caption">장단기 금리차(10년−2년): {num(d.yield_curve_2s10s ?? null)}%p · 월간·분기 지표는 발표 시각이 없어 전일 기준 공개값(ALFRED 빈티지)만 사용합니다.</div>
          <section className="card">
            <div className="card-head"><h3 className="t-card">시장 국면 판정 근거</h3><span className="caption">강도 · 신뢰도 · 근거 지표</span></div>
            <div className="scroll"><table><tbody>{d.regimes.map((r) => <tr key={r.regime}><td className={r.active ? "pos" : "muted"} style={{ whiteSpace: "nowrap" }}>{r.active ? "● " : "○ "}{ko(REGIME_KO, r.regime)}</td><td className="num">{num(r.score)}</td><td className="num">{num(r.confidence)}</td><td style={{ whiteSpace: "normal" }} className="caption">{r.evidence.join("; ")}</td></tr>)}</tbody></table></div>
          </section>
          {d.factor_moves.length > 0 && (
            <section className="card">
              <div className="card-head"><h3 className="t-card">기업 노출도로 전달되는 거시 요인 변화</h3></div>
              <ul className="list">{d.factor_moves.map((f) => <li key={f.factor}><span className={`dot ${f.move > 0 ? "pos" : f.move < 0 ? "neg" : "info"}`}>{f.move > 0 ? "↑" : f.move < 0 ? "↓" : "■"}</span><span>{f.factor}: {num(f.move)} — <span className="caption">{f.evidence}</span></span></li>)}</ul>
            </section>
          )}
        </div>
      ) : (
        <section className="card flush"><div style={{ padding: "6px 8px 8px" }}><SeriesTable data={d} ids={MARKET_SERIES} /></div></section>
      )}
    </div>
  );
}
