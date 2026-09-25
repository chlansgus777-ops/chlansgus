import { Fragment } from "react";
import { Card, Err, Loading } from "../components/ui";
import { useApi } from "../components/useApi";

interface S { mode: string; keys_configured: Record<string, boolean>; weights: Record<string, number>; decision: Record<string, number>; entry: Record<string, number>; scanner: Record<string, unknown>; calibration: Record<string, number>; sector_models: { id: string; name: string; rationale: string; primary_multiple: string; fundamental: { metric: string; label: string; weight: number; bad: number; good: number }[] }[]; note: string }

export default function Settings() {
  const s = useApi<S>("/settings");
  if (s.state === "loading") return <Loading what="설정" />;
  if (!s.data) return <Err error={s.error} retry={s.reload} />;
  const d = s.data;
  const kv = (o: Record<string, unknown>) => <div className="kv">{Object.entries(o).map(([k, v]) => <Fragment key={k}><span className="k">{k}</span><span>{typeof v === "object" ? JSON.stringify(v) : String(v)}</span></Fragment>)}</div>;
  return (
    <div className="grid">
      <h1>설정 <span className="muted" style={{ fontSize: 13 }}>(읽기 전용 — config/*.toml 과 .env 를 수정하세요. 모든 변경은 버전으로 기록됩니다)</span></h1>
      <div className="grid g3">
        <Card title="모드 · API 키"><div>모드: <b>{d.mode === "MOCK" ? "모의 데이터(MOCK)" : "실데이터(LIVE)"}</b></div>{kv(Object.fromEntries(Object.entries(d.keys_configured).map(([k, v]) => [k, v ? "설정됨" : "없음"])))}<div className="muted">{d.note}</div></Card>
        <Card title="점수 가중치">{kv(d.weights)}</Card>
        <Card title="판정 기준 (히스테리시스)">{kv(d.decision)}</Card>
        <Card title="진입 엔진">{kv(d.entry)}</Card>
        <Card title="스캐너">{kv(d.scanner)}</Card>
        <Card title="가중치 보정">{kv(d.calibration)}</Card>
      </div>
      <Card title="업종별 모델">
        {d.sector_models.map((m) => <details key={m.id}><summary>{m.name} — {m.rationale} (핵심 배수: {m.primary_multiple})</summary>
          <table><thead><tr><th>지표</th><th>가중치</th><th>나쁨(→0)</th><th>좋음(→1)</th></tr></thead><tbody>{m.fundamental.map((r) => <tr key={r.metric}><td>{r.label}</td><td>{r.weight}</td><td>{r.bad}</td><td>{r.good}</td></tr>)}</tbody></table></details>)}
      </Card>
    </div>
  );
}
