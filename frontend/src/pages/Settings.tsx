import { Fragment, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { QuoteFeedStatus } from "../components/LivePrice";
import { TossCard, type TossView } from "../components/TossConnect";
import { Card, Disclosure, Err, Loading, Tabs } from "../components/ui";
import { DiagnosticsCard } from "../components/Diagnostics";
import { clientLatency, useQuoteStatus } from "../quotes";
import Health from "./Health";
import { useApi } from "../components/useApi";
import { PhoneSettings } from "../components/Phone";
import { SaveTickerConnect } from "../components/SaveTickerConnect";

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
      <div className="field-grid">
        <label htmlFor="setup-mode">데이터 모드</label>
        <div className="field">
          <select id="setup-mode" value={newMode} onChange={(e) => setNewMode(e.target.value)}>
            <option value="MOCK">모의 데이터(MOCK) — 키 없이 둘러보기</option>
            <option value="LIVE">실데이터(LIVE) — 아래 무료 키 필요</option>
          </select>
        </div>
        {FIELDS.map((f) => (
          <div key={f.key} className="field-row">
            <label htmlFor={`setup-${f.key}`}>{f.label} <span className={`key-state ${configured[f.key] ? "on" : ""}`}>({configured[f.key] ? "설정됨" : "없음"})</span></label>
            <div className="field">
              <input id={`setup-${f.key}`} type={f.secret ? "password" : "text"} autoComplete="off" value={vals[f.key] ?? ""}
                     placeholder={configured[f.key] ? "바꾸려면 새 값을 입력" : "입력"} onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })} />
              <div className="help">{f.help}</div>
            </div>
          </div>
        ))}
      </div>
      <div className="row" style={{ marginTop: 14 }}><button className="primary" disabled={busy} onClick={save}>{busy ? "저장 중…" : "저장"}</button></div>
      {msg && <div role="status">{msg}</div>}
      {err && <div role="alert" className="neg">{err}</div>}
    </Card>
  );
}

interface S { mode: string; keys_configured: Record<string, boolean>; weights: Record<string, number>; decision: Record<string, number>; entry: Record<string, number>; scanner: Record<string, unknown>; calibration: Record<string, number>; sector_models: { id: string; name: string; rationale: string; primary_multiple: string; fundamental: { metric: string; label: string; weight: number; bad: number; good: number }[] }[]; note: string;
  scheduler?: { enabled: boolean; interval_minutes: number } }

type Tab = "data" | "quotes" | "auto" | "phone" | "status" | "diag";

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
      <div className="muted">켜 두면(기본) 앱이 열려 있는 동안 정규장에는 {d.scheduler?.interval_minutes ?? 30}분마다, 장이 닫힌 뒤에는 종가 기준으로 한 번 시장 전체를 다시 분석합니다. 프리마켓·시간외에는 판단에 쓸 현재가가 없어 스캔하지 않습니다(직전의 좋은 결과를 덮어쓰지 않도록). 끝나면 모든 화면이 저절로 바뀌고, 매수 신호는 모의 매매(실제 주문 없음)로 기록됩니다.
        매수 구간·손절·목표 판정은 이 설정과 상관없이 체결이 올 때마다 바뀝니다.</div>
      <div className="row" style={{ marginTop: 12 }}>
        <b>현재: {on ? "켜짐" : "꺼짐"}</b>
        <button disabled={f.busy} onClick={() => void f.save({ MARKETLENS_SCHEDULER: on ? "0" : "1" })}>{on ? "자동 갱신 끄기" : "자동 갱신 켜기"}</button>
      </div>
      <div className="caption" style={{ marginTop: 6 }}>앱을 다시 시작하면 적용됩니다. 무료 데이터 요청 한도 안에서만 호출합니다.</div>
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
          <span className="k">화면 반영 지연</span><span>{(() => { const xs = [...clientLatency].sort((a, b) => a - b); return xs.length ? `백엔드 수신→이 화면 중앙값 ${Math.round(xs[Math.floor(xs.length / 2)]!)}ms · p95 ${Math.round(xs[Math.min(xs.length - 1, Math.floor(xs.length * 0.95))]!)}ms (${xs.length}건, 이 창에서 측정)` : "아직 측정값 없음"; })()}</span>
          <span className="k">제공자 지연</span><span>{s.provider_latency_ms.n ? `체결→수신 중앙값 ${s.provider_latency_ms.p50}ms · p95 ${s.provider_latency_ms.p95}ms · 최대 ${s.provider_latency_ms.max}ms (${s.provider_latency_ms.n}건)` : "측정값 없음"}</span>
          {s.last_error && <><span className="k">최근 오류</span><span className="muted">{s.last_error}</span></>}
        </div>
      ) : <div className="muted" style={{ marginTop: 8 }}>시세 상태를 받는 중…</div>}
      <div className="caption" style={{ marginTop: 10 }}>보고 있는 종목 → 보유 종목 → 관심 종목 → 후보 목록 순으로 구독합니다. 체결이 올 때마다 저장된 가격 계획(최대 매수가·손절·목표·손익비)과 비교해 ‘지금 매수 구간’, ‘손절 기준 도달’ 같은 판정을 바로 바꾸고, 경계를 넘으면 알림을 띄웁니다. ‘지금 매수 구간’은 20분 안의 체결가가 있을 때만 나옵니다.</div>
      <DesktopAlerts />
    </Card>
  );
}

export default function Settings() {
  const s = useApi<S>("/settings");
  const [params, setParams] = useSearchParams();
  const t = params.get("tab");
  const tab: Tab = t === "model" ? "status" : t === "quotes" || t === "auto" || t === "status" || t === "phone" || t === "diag" ? t : "data";
  if (s.state === "loading") return <Loading what="설정" />;
  if (!s.data) return <Err error={s.error} retry={s.reload} />;
  const d = s.data;
  const kv = (o: Record<string, unknown>) => <div className="kv">{Object.entries(o).map(([k, v]) => <Fragment key={k}><span className="k">{k}</span><span>{typeof v === "object" ? JSON.stringify(v) : String(v)}</span></Fragment>)}</div>;
  return (
    <div className="grid">
      <div className="page-head enter"><div><h1>설정</h1><div className="t-sub">데이터 연결, 실시간 시세, 자동 갱신, 연결 상태를 한곳에서 봅니다. 저장한 키는 다시 표시하지 않습니다.</div></div></div>
      <div className="row spread">
        <Tabs<Tab> label="설정 항목" value={tab} onChange={(v) => setParams(v === "data" ? {} : { tab: v }, { replace: true })}
              items={[["data", "데이터 연결"], ["quotes", "실시간 시세"], ["auto", "자동 갱신"], ["phone", "폰 연결"], ["status", "연결 상태·데이터 준비"], ["diag", "진단"]]} />
      </div>
      {tab === "data" && (
        <>
          <TossSettings />
          <SaveTickerConnect mode={d.mode} />
          <SetupCard configured={d.keys_configured} mode={d.mode} onSaved={s.reload} />
        </>
      )}
      {tab === "quotes" && <Quotes />}
      {tab === "diag" && <DiagnosticsCard />}
      {tab === "phone" && <PhoneSettings />}
      {tab === "auto" && <AutoRefresh d={d} onSaved={s.reload} />}
      {tab === "status" && (
        <>
          <Health />
          <Disclosure title="개발자용 · 모델 설정값" hint="읽기 전용(config/*.toml) · 모든 변경은 버전으로 기록됩니다" testId="model-settings">
            <div className="grid">
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
            </div>
          </Disclosure>
        </>
      )}
    </div>
  );
}

/** Pop-up notifications from the operating system for live alerts while the app is in the background. */
function DesktopAlerts() {
  const supported = typeof Notification !== "undefined";
  const [perm, setPerm] = useState(supported ? Notification.permission : "denied");
  if (!supported) return null;
  return (
    <div className="row" style={{ marginTop: 12 }} data-testid="desktop-alerts">
      <b>바탕화면 알림</b>
      <span className="caption">{perm === "granted" ? "켜짐 — 앱이 뒤에 있을 때 매수 구간 진입·손절·목표 도달을 알려 드립니다" : perm === "denied" ? "이 창에서 차단됨 — 운영체제·브라우저 설정에서 허용하세요" : "앱이 뒤에 있을 때도 실시간 알림을 받으려면 켜세요"}</span>
      {perm === "default" && <button type="button" className="sm" onClick={() => void Notification.requestPermission().then(setPerm)}>알림 켜기</button>}
    </div>
  );
}

/** The 토스증권 connection, also reachable from 설정 (the portfolio screen shows the same card compact). */
function TossSettings() {
  const t = useApi<TossView>("/broker/toss");
  return t.data ? <TossCard view={t.data} onChange={t.reload} /> : null;
}
