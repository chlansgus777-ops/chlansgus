import { useEffect, useState, type ReactNode } from "react";
import { api } from "../api";

/** Waits until the local backend reports ready (the desktop sidecar can take a few seconds to unpack and
 * migrate the database) instead of showing connection errors on every page. */
export function Startup({ children, timeoutMs = 90_000, intervalMs = 750 }: { children: ReactNode; timeoutMs?: number; intervalMs?: number }) {
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let alive = true;
    const started = Date.now();
    const poll = async () => {
      while (alive && Date.now() - started < timeoutMs) {
        try {
          const r = await api.get<{ ready: boolean }>("/health/ready", { retries: 0 });
          if (r.ready) { if (alive) setReady(true); return; }
        } catch {
          // not up yet
        }
        await new Promise((res) => setTimeout(res, intervalMs));
      }
      if (alive) setFailed("백엔드가 시작되지 않았습니다. 이미 실행 중인 MarketLens가 있거나(중복 실행), 포트가 사용 중이거나, 설치가 손상되었을 수 있습니다. 로그: %LOCALAPPDATA%\\MarketLens\\logs");
    };
    void poll();
    return () => { alive = false; };
  }, [attempt, timeoutMs, intervalMs]);
  if (ready) return <>{children}</>;
  return (
    <div className="startup" role="status">
      <div className="brand">◎ MarketLens</div>
      {failed ? (
        <div className="err" role="alert">⚠ {failed} <button onClick={() => { setFailed(null); setAttempt((a) => a + 1); }}>다시 시도</button></div>
      ) : (
        <div className="muted">백엔드 시작 중… (첫 실행은 데이터베이스 준비로 조금 더 걸릴 수 있습니다)</div>
      )}
    </div>
  );
}
