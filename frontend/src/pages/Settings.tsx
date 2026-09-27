import { Fragment, useState } from "react";
import { api } from "../api";
import { Card, Err, Loading } from "../components/ui";
import { useApi } from "../components/useApi";

/** First-run setup without editing files: values are sent once, stored by the backend (OS keychain or the
 * private .env of this installation) and never shown again. */
const FIELDS: { key: string; label: string; help: string; secret: boolean }[] = [
  { key: "SEC_USER_AGENT", label: "SEC 요청자 (이름 이메일)", help: "무료. SEC가 요청자 이름과 연락처를 요구합니다. 예: Hong Gildong hong@example.com", secret: false },
  { key: "FINNHUB_API_KEY", label: "Finnhub API 키", help: "무료 가입: finnhub.io — 시세·뉴스·실적", secret: true },
  { key: "POLYGON_API_KEY", label: "Polygon API 키", help: "무료 가입: polygon.io — 일봉·주식분할", secret: true },
  { key: "FRED_API_KEY", label: "FRED API 키", help: "무료 가입: fred.stlouisfed.org — 금리·물가 등 거시지표", secret: true },
  { key: "ALPHAVANTAGE_API_KEY", label: "Alpha Vantage API 키", help: "무료 가입: alphavantage.co — 애널리스트 추정치(하루 약 25회)", secret: true },
];

export function SetupCard({ configured, mode, onSaved }: { configured: Record<string, boolean>; mode: string; onSaved?: () => void }) {
  const [vals, setVals] = useState<Record<string, string>>({});
  const [newMode, setNewMode] = useState(mode);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true); setErr(null); setMsg(null);
    const values: Record<string, string> = { ...vals };
    if (newMode !== mode) values["MARKETLENS_MODE"] = newMode;
    try {
      const r = await api.put<{ note: string }>("/settings/setup", { values });
      setMsg(r.note); setVals({}); onSaved?.();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  return (
    <Card title="초기 설정 · API 키 (파일을 직접 고칠 필요 없음)">
      <div className="muted">빈 칸은 바꾸지 않습니다. 저장한 키는 화면에 다시 표시되지 않으며, 앱을 다시 시작하면 적용됩니다.</div>
      <label htmlFor="setup-mode">데이터 모드</label>
      <select id="setup-mode" value={newMode} onChange={(e) => setNewMode(e.target.value)}>
        <option value="MOCK">모의 데이터(MOCK) — 키 없이 둘러보기</option>
        <option value="LIVE">실데이터(LIVE) — 아래 무료 키 필요</option>
      </select>
      {FIELDS.map((f) => (
        <div key={f.key}>
          <label htmlFor={`setup-${f.key}`}>{f.label} <span className="muted">({configured[f.key] ? "설정됨" : "없음"})</span></label>
          <input id={`setup-${f.key}`} type={f.secret ? "password" : "text"} autoComplete="off" value={vals[f.key] ?? ""}
                 placeholder={configured[f.key] ? "바꾸려면 새 값을 입력" : ""} onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })} />
          <div className="muted" style={{ fontSize: 12 }}>{f.help}</div>
        </div>
      ))}
      <button disabled={busy} onClick={save}>저장</button>
      {msg && <div role="status">{msg}</div>}
      {err && <div role="alert" className="neg">{err}</div>}
    </Card>
  );
}

interface S { mode: string; keys_configured: Record<string, boolean>; weights: Record<string, number>; decision: Record<string, number>; entry: Record<string, number>; scanner: Record<string, unknown>; calibration: Record<string, number>; sector_models: { id: string; name: string; rationale: string; primary_multiple: string; fundamental: { metric: string; label: string; weight: number; bad: number; good: number }[] }[]; note: string }

export default function Settings() {
  const s = useApi<S>("/settings");
  if (s.state === "loading") return <Loading what="설정" />;
  if (!s.data) return <Err error={s.error} retry={s.reload} />;
  const d = s.data;
  const kv = (o: Record<string, unknown>) => <div className="kv">{Object.entries(o).map(([k, v]) => <Fragment key={k}><span className="k">{k}</span><span>{typeof v === "object" ? JSON.stringify(v) : String(v)}</span></Fragment>)}</div>;
  return (
    <div className="grid">
      <h1>설정 <span className="muted" style={{ fontSize: 13 }}>(모델 설정은 읽기 전용 — config/*.toml, 모든 변경은 버전으로 기록됩니다)</span></h1>
      <SetupCard configured={d.keys_configured} mode={d.mode} onSaved={s.reload} />
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
