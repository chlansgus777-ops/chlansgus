import { Fragment, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { QuoteFeedStatus } from "../components/LivePrice";
import { Card, Err, Loading, Tabs } from "../components/ui";
import { useQuoteStatus } from "../quotes";
import Health from "./Health";
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

interface S { mode: string; keys_configured: Record<string, boolean>; weights: Record<string, number>; decision: Record<string, number>; entry: Record<string, number>; scanner: Record<string, unknown>; calibration: Record<string, number>; sector_models: { id: string; name: string; rationale: string; primary_multiple: string; fundamental: { metric: string; label: string; weight: number; bad: number; good: number }[] }[]; note: string;
  llm?: { provider: string; available: boolean; base_url: string | null; fast_model: string; deep_model: string }; scheduler?: { enabled: boolean; interval_minutes: number } }

type Tab = "data" | "quotes" | "auto" | "ai" | "status" | "model";

/** Saves a few non-secret values through the same setup endpoint (restart applies them). */
function useSave(onSaved: () => void) {
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const save = async (values: Record<string, string>) => {
    if (busy) return;
    setBusy(true); setErr(null); setMsg(null);
    try { const r = await api.put<{ note: string }>("/settings/setup", { values }); setMsg(r.note); onSaved(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  return { save, msg, err, busy };
}

function Feedback({ msg, err }: { msg: string | null; err: string | null }) {
  return <>{msg && <div role="status" className="caption">{msg}</div>}{err && <div role="alert" className="neg">{err}</div>}</>;
}

function AutoRefresh({ d, onSaved }: { d: S; onSaved: () => void }) {
  const f = useSave(onSaved);
  const on = !!d.scheduler?.enabled;
  return (
    <Card title="자동 갱신 · 모의 매매 기록">
      <div className="muted">켜면 앱이 열려 있는 동안 {d.scheduler?.interval_minutes ?? 60}분마다 시장을 다시 스캔하고, 매수 신호는 모의 매매(실제 주문 없음)로 기록해 성과 화면의 표본이 쌓입니다. 시세 스트림은 이 설정과 상관없이 항상 켜져 있습니다.</div>
      <div className="row" style={{ marginTop: 12 }}>
        <b>현재: {on ? "켜짐" : "꺼짐"}</b>
        <button disabled={f.busy} onClick={() => void f.save({ MARKETLENS_SCHEDULER: on ? "0" : "1" })}>{on ? "자동 갱신 끄기" : "자동 갱신 켜기"}</button>
      </div>
      <div className="caption" style={{ marginTop: 6 }}>앱을 다시 시작하면 적용됩니다. 무료 데이터 요청 한도 안에서만 호출합니다.</div>
      <Feedback msg={f.msg} err={f.err} />
    </Card>
  );
}

function AiReview({ d, onSaved }: { d: S; onSaved: () => void }) {
  const f = useSave(onSaved);
  const [url, setUrl] = useState(d.llm?.base_url ?? "http://127.0.0.1:11434/v1");
  const [fast, setFast] = useState("");
  const [deep, setDeep] = useState("");
  const llm = d.llm;
  return (
    <Card title="AI 검토 (선택)">
      <div className="muted">AI 검토는 규칙 기반 판단을 바꾸지 않고, 근거를 다시 읽어 위험을 지적하거나 비중을 낮추기만 합니다. 유료 API 없이 쓰려면 이 컴퓨터에 설치한 로컬 모델(예: Ollama)의 OpenAI 호환 주소를 넣으세요.</div>
      <div style={{ marginTop: 8 }}>현재: <b>{llm ? (llm.available ? `사용 가능 · ${llm.provider}` : `사용 안 함 (${llm.provider})`) : "정보 없음"}</b>{llm ? <span className="caption"> · 모델 {llm.fast_model} / {llm.deep_model}</span> : null}</div>
      <label htmlFor="ai-url">로컬 모델 서버 주소 (이 컴퓨터만 허용)</label>
      <input id="ai-url" value={url} onChange={(e) => setUrl(e.target.value)} autoComplete="off" />
      <label htmlFor="ai-fast">빠른 모델 이름</label>
      <input id="ai-fast" value={fast} onChange={(e) => setFast(e.target.value)} placeholder="예: qwen2.5:7b" autoComplete="off" />
      <label htmlFor="ai-deep">깊은 검토 모델 이름</label>
      <input id="ai-deep" value={deep} onChange={(e) => setDeep(e.target.value)} placeholder="예: qwen2.5:14b" autoComplete="off" />
      <div className="row" style={{ marginTop: 10 }}>
        <button disabled={f.busy} onClick={() => void f.save({ LLM_PROVIDER: "openai_compatible", OPENAI_BASE_URL: url, FAST_MODEL: fast, DEEP_MODEL: deep })}>로컬 모델 사용</button>
        <button className="ghost" disabled={f.busy} onClick={() => void f.save({ LLM_PROVIDER: "none" })}>AI 검토 끄기</button>
      </div>
      <div className="caption" style={{ marginTop: 6 }}>유료 AI(Anthropic·OpenAI) 키는 ‘데이터 연결’ 탭이 아니라 설정 파일에서만 넣을 수 있습니다. 앱을 다시 시작하면 적용됩니다.</div>
      <Feedback msg={f.msg} err={f.err} />
    </Card>
  );
}

function Quotes() {
  const { link, status } = useQuoteStatus();
  const s = status;
  return (
    <Card title="실시간 시세">
      <div className="row"><QuoteFeedStatus /></div>
      {s ? (
        <div className="kv" style={{ marginTop: 10 }}>
          <span className="k">공급원</span><span>{s.coverage}</span>
          <span className="k">구독</span><span>{s.subscribed.length}/{s.max_symbols}{s.subscribed.length ? ` — ${s.subscribed.join(", ")}` : ""}</span>
          {s.over_limit.length > 0 && <><span className="k">한도 초과(스트림 제외)</span><span className="warn">{s.over_limit.join(", ")}</span></>}
          <span className="k">연결</span><span>{s.streaming ? (s.connected ? `연결됨${s.connected_since ? ` · ${new Date(s.connected_since).toLocaleTimeString("ko-KR")}부터` : ""}` : "끊김 · 재연결 중") : "스트림 없음(스냅샷만)"} · 앱 연결 {link === "open" ? "정상" : link === "retrying" ? "재연결 중" : "연결 중"}</span>
          <span className="k">수신</span><span>체결 {s.trades.toLocaleString("ko-KR")}건 · 메시지 {s.messages.toLocaleString("ko-KR")} · 순서 뒤바뀐 체결 {s.out_of_order} · 재연결 {Math.max(0, s.connects - 1)}회 · REST 스냅샷 {s.snapshot_calls}회</span>
          <span className="k">제공자 지연</span><span>{s.provider_latency_ms.n ? `체결→수신 중앙값 ${s.provider_latency_ms.p50}ms · p95 ${s.provider_latency_ms.p95}ms · 최대 ${s.provider_latency_ms.max}ms (${s.provider_latency_ms.n}건)` : "측정값 없음"}</span>
          {s.last_error && <><span className="k">최근 오류</span><span className="muted">{s.last_error}</span></>}
        </div>
      ) : <div className="muted" style={{ marginTop: 8 }}>시세 상태를 받는 중…</div>}
      <div className="caption" style={{ marginTop: 10 }}>보고 있는 종목 → 보유 종목 → 관심 종목 순으로 구독합니다. 화면의 현재가는 참고용이며, 매수 판단·수량은 분석의 신선도 규칙(현재가 20분 이내)을 그대로 따릅니다.</div>
    </Card>
  );
}

export default function Settings() {
  const s = useApi<S>("/settings");
  const [params, setParams] = useSearchParams();
  const t = params.get("tab");
  const tab: Tab = t === "quotes" || t === "auto" || t === "ai" || t === "status" || t === "model" ? t : "data";
  if (s.state === "loading") return <Loading what="설정" />;
  if (!s.data) return <Err error={s.error} retry={s.reload} />;
  const d = s.data;
  const kv = (o: Record<string, unknown>) => <div className="kv">{Object.entries(o).map(([k, v]) => <Fragment key={k}><span className="k">{k}</span><span>{typeof v === "object" ? JSON.stringify(v) : String(v)}</span></Fragment>)}</div>;
  return (
    <div className="grid">
      <div className="page-head enter"><div><h1>설정</h1><div className="t-sub">데이터 연결, 실시간 시세, 자동 갱신, AI 검토, 연결 상태를 한곳에서 봅니다. 저장한 키는 다시 표시하지 않습니다.</div></div></div>
      <div className="row spread">
        <Tabs<Tab> label="설정 항목" value={tab} onChange={(v) => setParams(v === "data" ? {} : { tab: v }, { replace: true })}
              items={[["data", "데이터 연결"], ["quotes", "실시간 시세"], ["auto", "자동 갱신"], ["ai", "AI 검토"], ["status", "연결 상태·데이터 준비"], ["model", "모델 설정"]]} />
      </div>
      {tab === "data" && (
        <>
          <SetupCard configured={d.keys_configured} mode={d.mode} onSaved={s.reload} />
          <Card title="모드 · API 키"><div>모드: <b>{d.mode === "MOCK" ? "모의 데이터(MOCK)" : "실데이터(LIVE)"}</b></div>{kv(Object.fromEntries(Object.entries(d.keys_configured).map(([k, v]) => [k, v ? "설정됨" : "없음"])))}<div className="muted">{d.note}</div></Card>
        </>
      )}
      {tab === "quotes" && <Quotes />}
      {tab === "auto" && <AutoRefresh d={d} onSaved={s.reload} />}
      {tab === "ai" && <AiReview d={d} onSaved={s.reload} />}
      {tab === "status" && <Health />}
      {tab === "model" && (
        <>
          <div className="caption">모델 설정은 읽기 전용입니다(config/*.toml). 모든 변경은 버전으로 기록됩니다.</div>
          <div className="grid g3">
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
        </>
      )}
    </div>
  );
}
