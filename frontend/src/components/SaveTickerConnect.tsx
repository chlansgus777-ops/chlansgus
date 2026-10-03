import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Card } from "./ui";
import { invalidateApi, useApi, usePoll } from "./useApi";
import { SaveTickerBrowserConnect } from "./SaveTickerBrowserConnect";

interface Connection {
  status: "IDLE" | "CONNECTING" | "CONNECTED" | "FAILED"; authenticated: boolean;
  error?: string | null; retry_in_s: number; items_fetched: number; saved: boolean;
}

export function SaveTickerConnect({ mode }: { mode: string }) {
  const [advanced, setAdvanced] = useState(false);
  return <><SaveTickerBrowserConnect mode={mode} /><details onToggle={(e) => setAdvanced(e.currentTarget.open)}>
    <summary>고급 연결 · 서버에서 직접 이메일 로그인</summary>
    {advanced ? <SaveTickerHttpConnect mode={mode} /> : null}
  </details></>;
}

export function SaveTickerHttpConnect({ mode }: { mode: string }) {
  const connection = useApi<Connection>("/saveticker/connection");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [accepted, setAccepted] = useState<Connection | null>(null);
  const [acceptedAt, setAcceptedAt] = useState(0);
  const guard = useRef(false);
  const current = accepted && (connection.fetchedAt ?? 0) <= acceptedAt ? accepted : connection.data;
  useEffect(() => {
    if (current?.status === "CONNECTED") { setError(null); setEmail(""); setPassword(""); }
  }, [current?.status]);
  usePoll(connection.reload, current?.status === "CONNECTING" || (current?.retry_in_s ?? 0) > 0 ? 1_000 : 15_000);
  const connecting = busy || current?.status === "CONNECTING";
  const connect = async () => {
    if (guard.current || connecting) return;
    guard.current = true; setBusy(true); setError(null);
    try {
      const result = await api.post<Connection>("/saveticker/connection", { email, password, remember });
      setAccepted(result); setAcceptedAt(Date.now()); setPassword(""); setEmail(""); connection.reload();
    } catch (ex) {
      // A response may be lost after the server accepts the job: recover status, never auto-resubmit login.
      setError(ex instanceof Error ? ex.message : "연결 요청 응답을 확인하지 못했습니다. 서버의 작업 상태를 다시 확인합니다.");
      invalidateApi(["/saveticker/connection"]);
      connection.reload();
    } finally { guard.current = false; setBusy(false); }
  };
  return <Card title="SaveTicker 연결">
    <p className="muted">이메일·비밀번호로 정상 로그인해 보조 뉴스를 수집합니다. 시세 연결이나 매수 신호가 아닙니다.</p>
    {mode !== "LIVE" ? <div role="status" className="ribbon warn">실제 수집은 LIVE 모드에서만 사용할 수 있습니다.</div> : null}
    <form onSubmit={(e) => { e.preventDefault(); void connect(); }}>
      <div className="field-grid">
        <label htmlFor="saveticker-email">SaveTicker 이메일</label>
        <div className="field"><input id="saveticker-email" type="email" autoComplete="username" required disabled={connecting || mode !== "LIVE"}
          value={email} onChange={(e) => setEmail(e.target.value)} /></div>
        <label htmlFor="saveticker-password">SaveTicker 비밀번호</label>
        <div className="field"><input id="saveticker-password" type="password" autoComplete="current-password" required disabled={connecting || mode !== "LIVE"}
          value={password} onChange={(e) => setPassword(e.target.value)} /></div>
      </div>
      <label className="caption"><input type="checkbox" checked={remember} disabled={connecting} onChange={(e) => setRemember(e.target.checked)} /> 다음 실행에도 자동 연결하도록 계정 저장</label>
      <div className="caption">저장하지 않으면 이번 실행에서만 연결합니다. 저장 시 OS 자격 증명 관리자, 미지원 환경에서는 이 설치의 비공개 .env를 사용합니다. 브라우저 로그인 정보는 복사하지 않습니다.</div>
      <div className="row" style={{ marginTop: 12 }}><button className="primary" type="submit" disabled={connecting || mode !== "LIVE" || (current?.retry_in_s ?? 0) > 0}>
        {connecting ? "로그인·뉴스 수집 확인 중…" : (current?.retry_in_s ?? 0) > 0 ? `${current!.retry_in_s}초 뒤 재시도` : "로그인하고 뉴스 받기"}</button>
        <button type="button" className="ghost" onClick={connection.reload} disabled={connection.loading}>상태 확인</button></div>
    </form>
    {current?.status === "CONNECTED" ? <div role="status" className="caption">정상 로그인·수집 확인 · 최근 목록 {current.items_fetched}건 · {current.saved ? "다음 실행 자동 연결" : "이번 실행에서만 연결"}</div> : null}
    {error || current?.error || connection.error ? <div role="alert" className="neg">{error || current?.error || connection.error}</div> : null}
    {current?.error?.includes("403") ? <div className="caption">서버 접근이 거절된 상태입니다. 이 응답만으로 비밀번호 오류를 판단할 수 없습니다. 브라우저에서 로그인해도 서버의 보안 검증이 자동으로 해결되지는 않습니다.</div> : null}
  </Card>;
}
