import { createContext, useEffect, useState, type ReactNode } from "react";
import { api } from "../api";
import { useApi, usePoll } from "./useApi";
import { Card, Notice } from "./ui";

/** The phone on the home Wi-Fi (backend application/phone.py): the PC turns it on and shows a code; a phone opens the
 * PC's address, types the code once, and then sees every screen live — read-only (changes stay on the PC). */

export interface PhoneStatus {
  enabled: boolean; port: number; addresses: string[]; listening: boolean; error: string | null;
  devices: { id: string; name: string; created: string; last_seen: string }[]; code?: string | null; code_expires_in?: number | null;
}
interface Hello { phone: boolean; paired: boolean; enabled: boolean }

const kst = (iso: string) => new Date(iso).toLocaleString("ko-KR", { timeZone: "Asia/Seoul", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });

/** Settings → 폰 연결 (on the PC). */
export function PhoneSettings() {
  const s = useApi<PhoneStatus>("/phone");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  usePoll(s.reload, 2_000, !!s.data?.enabled);  // the code's countdown, a phone that just paired
  const run = async (f: () => Promise<unknown>) => {
    setBusy(true); setErr(null);
    try { await f(); s.reload(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const d = s.data;
  return (
    <Card title="폰 연결 (안드로이드 · 집 와이파이)" testId="phone-settings"
      explain="PC가 계산하고, 같은 와이파이의 폰은 같은 화면을 실시간으로 봅니다(보기 전용). 토스증권 키와 모든 설정은 PC에만 있습니다.">
      {!d ? <div className="muted">불러오는 중…</div> : (
        <div className="grid" style={{ gap: 12 }}>
          <div className="row">
            <b>{d.enabled ? (d.listening ? "켜짐 · 폰 연결을 받는 중" : "켜짐 · 준비 중") : "꺼짐"}</b>
            <button type="button" className={d.enabled ? "" : "primary"} disabled={busy} onClick={() => void run(() => api.put("/phone", { enabled: !d.enabled }))}>
              {d.enabled ? "끄기" : "폰 연결 켜기"}
            </button>
          </div>
          {d.error && <Notice tone="neg">{d.error}</Notice>}
          {d.enabled && (
            <>
              <div className="phone-steps">
                <div><span className="n">1</span>폰에 MarketLens 앱을 설치하고 아래 주소를 입력하세요.
                  <div className="phone-addr" data-testid="phone-address">{d.addresses.length ? d.addresses.map((a) => <code key={a}>{a}:{d.port}</code>) : <span className="warn">이 PC의 와이파이 주소를 찾지 못했습니다 — 와이파이(또는 공유기 유선)에 연결돼 있는지 확인하세요</span>}</div>
                </div>
                <div><span className="n">2</span>폰 화면에서 이 연결 코드를 입력하세요.
                  <div className="phone-code" data-testid="phone-code">
                    {d.code ? <><b>{d.code.slice(0, 3)} {d.code.slice(3)}</b><span className="caption">{Math.max(0, d.code_expires_in ?? 0)}초 남음 · 한 번만 쓸 수 있음</span></> : <span className="muted">코드 없음</span>}
                    <button type="button" className="sm" disabled={busy} onClick={() => void run(() => api.post("/phone/code"))}>{d.code ? "새 코드" : "연결 코드 만들기"}</button>
                  </div>
                </div>
              </div>
              <div className="caption">처음 켤 때 Windows 방화벽 창이 뜨면 ‘개인 네트워크’에 <b>허용</b>을 누르세요. 공용 와이파이·인터넷에서는 연결되지 않습니다.</div>
              {/* independent review 2026-10-06 F08: the home-network link is plain http — say so where it is switched on */}
              <div className="caption warn" data-testid="phone-plain-http">이 연결은 암호화되지 않습니다(http). 집처럼 믿을 수 있는 와이파이에서만 켜고, 카페·회사 등 남과 같이 쓰는 와이파이에서는 꺼 두세요. 쓰지 않을 때는 끄는 것이 안전합니다.</div>
              <div>
                <div className="t-kicker">연결된 폰</div>
                {d.devices.length ? (
                  <ul className="list">{d.devices.map((x) => (
                    <li key={x.id} className="row spread"><span><b>{x.name}</b> <span className="caption">연결 {kst(x.created)} · 마지막 접속 {kst(x.last_seen)}</span></span>
                      <button type="button" className="sm" disabled={busy} onClick={() => void run(() => api.del(`/phone/devices/${x.id}`))}>연결 해제</button></li>
                  ))}</ul>
                ) : <div className="muted">아직 없습니다</div>}
              </div>
            </>
          )}
          {err && <Notice tone="neg">{err}</Notice>}
        </div>
      )}
    </Card>
  );
}

/** True on a paired phone: the screens there can look but never start anything (the PC refuses it anyway). */
export const OnPhone = createContext(false);

/** On a phone: the pairing screen until it is paired, then the app with a read-only note. On the PC: the app. */
export function PhoneGate({ children }: { children: ReactNode }) {
  const [hello, setHello] = useState<Hello | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    api.get<Hello>("/phone/hello", { retries: 0 }).then((h) => { if (live) setHello(h); }).catch(() => { if (live) setHello({ phone: false, paired: false, enabled: false }); });
    return () => { live = false; };
  }, [tick]);
  // a phone removed on the PC falls back to the pairing screen within half a minute
  useEffect(() => {
    if (!hello?.phone) return;
    const id = setInterval(() => setTick((n) => n + 1), 30_000);
    return () => clearInterval(id);
  }, [hello?.phone]);
  if (hello?.phone && !hello.paired) return <PairScreen onPaired={() => setTick((n) => n + 1)} />;
  return (
    <OnPhone.Provider value={!!hello?.phone}>
      {hello?.phone && <div className="phone-ribbon" data-testid="phone-ribbon">폰 · 보기 전용 — 종목·보유·설정을 바꾸는 것은 PC에서 하세요</div>}
      {children}
    </OnPhone.Provider>
  );
}

export function PairScreen({ onPaired }: { onPaired: () => void }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("내 폰");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    const c = code.replace(/\D/g, "");
    if (c.length !== 6) { setErr("PC 화면의 6자리 코드를 입력하세요"); return; }
    setBusy(true); setErr(null);
    try { await api.post("/phone/pair", { code: c, name: name.trim() || "내 폰" }); onPaired(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  return (
    <div className="pair-screen" data-testid="pair-screen">
      <h1>MarketLens</h1>
      <p>PC에서 <b>설정 → 폰 연결</b>을 켜면 나오는 <b>6자리 코드</b>를 입력하세요.</p>
      <form onSubmit={(e) => { e.preventDefault(); void submit(); }}>
        <input aria-label="연결 코드" inputMode="numeric" autoComplete="one-time-code" maxLength={7} placeholder="000 000" value={code}
          onChange={(e) => setCode(e.target.value.replace(/[^\d ]/g, ""))} className="pair-code" autoFocus />
        <label><span>이 폰의 이름</span><input aria-label="폰 이름" value={name} maxLength={40} onChange={(e) => setName(e.target.value)} /></label>
        <button className="primary" disabled={busy}>{busy ? "연결 중…" : "연결"}</button>
      </form>
      {err && <Notice tone="neg">{err}</Notice>}
      <p className="caption">같은 와이파이에서만 연결됩니다. 폰에서는 보기만 할 수 있고, 토스증권 키는 PC에만 있습니다.</p>
    </div>
  );
}
