import { useRef, useState } from "react";
import { api, baseUrl } from "../api";
import { Card } from "./ui";
import { useApi, usePoll } from "./useApi";

interface Connection { status: string; method?: string; items_fetched: number; error?: string | null; last_received?: string | null }

export function SaveTickerBrowserConnect({ mode }: { mode: string }) {
  const state = useApi<Connection>("/saveticker/connection");
  const [link, setLink] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const guard = useRef(false);
  const data = state.data;
  usePoll(state.reload, data?.status === "WAITING_BROWSER" ? 2_000 : 15_000);
  const start = async () => {
    if (guard.current) return;
    guard.current = true; setBusy(true); setError(null);
    try {
      const base = new URL(baseUrl() || location.origin, location.href);
      if (base.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(base.hostname)) throw new Error("PC의 로컬 연결 화면에서 시작하세요.");
      const pairing = await api.post<{ key: string }>("/saveticker/browser", {});
      setLink(`${base.origin}/saveticker-bridge.html#key=${encodeURIComponent(pairing.key)}`);
      state.reload();
    } catch (e) { setError(e instanceof Error ? e.message : "브라우저 연결 준비 실패"); }
    finally { guard.current = false; setBusy(false); }
  };
  const disconnect = async () => {
    if (guard.current) return;
    guard.current = true; setBusy(true);
    try { await api.del("/saveticker/browser"); setLink(null); state.reload(); }
    catch (e) { setError(e instanceof Error ? e.message : "연결 해제 실패"); }
    finally { guard.current = false; setBusy(false); }
  };
  return <Card title="SaveTicker · 로그인한 브라우저로 연결">
    <p className="muted">SaveTicker 로그인은 Chrome·Edge에 유지합니다. 비밀번호·쿠키를 복사하지 않고, 정상 브라우저에서 받은 뉴스·실적·일정·리포트 목록·옵션 집계를 전달합니다.</p>
    <div className="row" style={{ marginTop: 12 }}>
      <button className="primary" disabled={busy || mode !== "LIVE"} onClick={start}>{busy ? "연결 준비 중…" : "브라우저 연결 시작"}</button>
      {data?.method === "browser" ? <button className="ghost" disabled={busy} onClick={disconnect}>브라우저 연결 해제</button> : null}
      <button className="ghost" disabled={state.loading} onClick={state.reload}>수집 상태 확인</button>
    </div>
    {mode !== "LIVE" ? <div className="caption">실제 뉴스 수집은 LIVE 모드에서만 사용할 수 있습니다.</div> : null}
    {link ? <div style={{ marginTop: 16 }}>
      <label htmlFor="saveticker-browser-link">Chrome·Edge에서 열 연결 주소</label>
      <input id="saveticker-browser-link" readOnly value={link} style={{ width: "100%", marginTop: 6 }} onFocus={(e) => e.target.select()} />
      <p className="caption">주소를 Chrome·Edge에 붙여 넣고, 열린 안내에 따라 확장을 설치한 뒤 ‘이 브라우저와 연결’을 누르세요. 이후에는 SaveTicker 탭이 열려 있는 동안 약 2분마다 수집합니다.</p>
      <a href={link} target="_blank" rel="noopener noreferrer">연결 안내 열기</a>
    </div> : null}
    {data?.method === "browser" ? <div role="status" className="ribbon" style={{ marginTop: 12 }}>
      {data.status === "CONNECTED" ? `브라우저 뉴스 수집 확인 · ${data.items_fetched}건 · 마지막 수신 ${data.last_received ? new Date(data.last_received).toLocaleString("ko-KR") : "확인 전"}`
        : data.status === "WAITING_BROWSER" ? "브라우저 연결 대기 · 확장을 연결하고 SaveTicker 탭에서 로그인해주세요."
        : "브라우저 수신 중단·실패 · 마지막 뉴스는 원래 시각으로 유지합니다."}
    </div> : null}
    {error || data?.error || state.error ? <div role="alert" className="neg">{error || data?.error || state.error}</div> : null}
    <p className="caption">시장 정보 전용 연결입니다. 뉴스는 약 2분, 상세·일정·리포트·옵션 집계는 약 10분마다 확인합니다. 실시간 시세·계좌·주문 권한은 없습니다. 브라우저를 닫으면 수신이 중단되며 앱 재시작 또는 24시간 후 다시 연결합니다.</p>
  </Card>;
}
