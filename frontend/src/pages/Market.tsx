import { Card, Empty, Err, Loading } from "../components/ui";
import { REGIME_KO, ko } from "../i18n";
import { MARKET_SERIES, SeriesTable, useMacro } from "./Macro";

export default function Market() {
  const m = useMacro();
  if (m.state === "loading") return <Loading what="시장 데이터" />;
  if (!m.data) return <Err error={m.error} retry={m.reload} />;
  if (!m.data.available) return <div className="warn" role="alert">시장 데이터 없음: {m.data.reason} (다른 값으로 대체하지 않습니다)</div>;
  const active = m.data.regimes.filter((r) => r.active);
  return (
    <div className="grid">
      <h3>시장 분위기</h3>
      <Card title="지수 · 변동성 · 시장 폭 · 신용"><SeriesTable data={m.data} ids={MARKET_SERIES} /></Card>
      <Card title="현재 활성화된 시장 국면">
        {active.length ? <div className="row">{active.map((r) => <span key={r.regime} className="badge a-WATCH" title={r.evidence.join("\n")}>{ko(REGIME_KO, r.regime)}</span>)}</div> : <Empty>뚜렷한 국면 없음(중립)</Empty>}
      </Card>
    </div>
  );
}
