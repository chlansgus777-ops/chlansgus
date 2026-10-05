import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { IBell } from "./icons";
import { useNavigate } from "react-router-dom";
import { pct, price } from "../format";
import { markAlertsSeen, toastQueue, useAlerts, useQuote, type Judge, type LiveAlert } from "../quotes";
import "./live.css";

/** Where the price stands against the stored plan, right now (re-judged on every quote by the backend). */
export function zoneView(j: Judge): { tone: "buy" | "warn" | "danger" | "info" | "muted"; label: string; sub: string } | null {
  const rr = j.rr_now !== null ? `손익비 ${j.rr_now.toFixed(2)}` : "";
  switch (j.zone) {
    case "BUY_ZONE":
      return j.valid_now
        ? { tone: "buy", label: "지금 매수 구간", sub: [j.to_max_pct !== null ? `최대 매수가까지 ${pct(j.to_max_pct, 1, false)}` : "", rr].filter(Boolean).join(" · ") }
        : { tone: "info", label: "재확인 필요", sub: j.quote_current ? (j.problems[0] ?? "분석을 다시 확인하세요") : "현재가 확인 필요" };
    case "ABOVE_MAX":
      return { tone: "warn", label: "최대 매수가 초과", sub: j.to_max_pct !== null ? `${pct(-j.to_max_pct, 1, false)} 위 · 기다리기` : "기다리기" };
    case "RR_LOW":
      return { tone: "warn", label: "손익비 부족", sub: `${rr} < ${j.min_rr}` };
    case "STOP_HIT":
      return { tone: "danger", label: "손절 기준 도달", sub: j.held ? "보유 중 — 매도 검토" : `손절 ${price(j.stop)}` };
    case "TARGET_HIT":
      return { tone: "info", label: "1차 목표 도달", sub: j.held ? "보유 중 — 일부 차익 검토" : `목표 ${price(j.target)}` };
    case "HOLD_RANGE":
      return { tone: "muted", label: "보유 구간", sub: [j.to_stop_pct !== null ? `손절까지 ${pct(j.to_stop_pct, 1)}` : "", j.to_target_pct !== null ? `목표까지 ${pct(j.to_target_pct, 1)}` : ""].filter(Boolean).join(" · ") };
    default:
      return null;
  }
}

/** The live zone chip of one ticker. ``recId``: only when the live plan is this very recommendation (a list row). */
export function LiveZone({ ticker, recId, compact = false }: { ticker: string; recId?: number; compact?: boolean }) {
  const { row } = useQuote(ticker);
  const j = row?.judge;
  if (!j || (recId !== undefined && j.rec_id !== recId)) return null;
  const v = zoneView(j);
  if (!v) return null;
  return (
    <span className={`lz lz-${v.tone}`} data-testid={`zone-${ticker}`} data-zone={j.zone} title={[v.label, v.sub, ...j.problems].filter(Boolean).join("\n")}>
      <i aria-hidden />{v.label}{!compact && v.sub ? <em>{v.sub}</em> : null}
    </span>
  );
}

const ALERT_ICON: Record<LiveAlert["level"], string> = { positive: "▲", warning: "!", danger: "⛔", info: "i" };
const toastLife = (a: LiveAlert) => (a.level === "danger" ? 20_000 : 9_000);

/** "AMZN 매수 구간 진입 — $251.60 (최대 매수가 …)" → a headline and its detail line (one line when there is no dash). */
export function splitAlert(text: string): [string, string] {
  const i = text.indexOf(" — ");
  return i > 0 ? [text.slice(0, i), text.slice(i + 3)] : [text, ""];
}

/** Toasts for alerts that arrive live, and the bell with the recent list (mounted once, in the app frame). */
export function AlertCenter() {
  const { alerts, unread } = useAlerts();
  const [toasts, setToasts] = useState<LiveAlert[]>([]);
  const [open, setOpen] = useState(false);
  const nav = useNavigate();
  const box = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!toastQueue.length) return;
    const fresh = toastQueue.splice(0, toastQueue.length);
    setToasts((t) => [...t, ...fresh].slice(-4));
    for (const a of fresh) {
      window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== a.id)), toastLife(a));
      notifyDesktop(a);
    }
  }, [alerts]);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  // an alert about one name opens that name; the morning briefing (no name) opens the home screen where its card is
  const go = (a: LiveAlert) => { setOpen(false); setToasts((t) => t.filter((x) => x.id !== a.id)); nav(a.ticker ? `/stocks/${a.ticker}` : "/"); };
  return (
    <>
      <div className="alert-bell" ref={box}>
        <button type="button" className="ghost sm" aria-label={`실시간 알림 ${unread}개`} data-testid="alert-bell"
                onClick={() => { setOpen(!open); if (!open) markAlertsSeen(); }}>
          <IBell width={17} height={17} />{unread > 0 && <b className="badge-count">{unread > 9 ? "9+" : unread}</b>}
        </button>
        {open && (
          <div className="alert-panel" role="dialog" aria-label="실시간 알림">
            <div className="t-kicker">실시간 알림 <span className="caption">— 가격이 계획의 경계를 넘을 때</span></div>
            {alerts.length ? [...alerts].reverse().map((a) => (
              <button type="button" key={a.id} className={`alert-item lvl-${a.level}`} onClick={() => go(a)}>
                <span className="ico" aria-hidden>{ALERT_ICON[a.level]}</span><span className="txt">{a.text}</span>
                <span className="caption">{new Date(a.at).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })}</span>
              </button>
            )) : <div className="caption" style={{ padding: "10px 4px" }}>아직 알림이 없습니다. 보유·관심·상위 후보 종목이 매수 구간에 들어오거나 손절·목표가에 닿으면 여기와 화면 오른쪽 아래에 바로 알려 드립니다.</div>}
          </div>
        )}
      </div>
      {createPortal(<div className="toasts" aria-live="polite">
        {toasts.map((a) => (
          <div key={a.id} className={`toast lvl-${a.level}`} role="status" style={{ ["--life" as string]: `${toastLife(a)}ms` }}>
            <button type="button" className="toast-body" onClick={() => go(a)}>
              <span className="ico" aria-hidden>{ALERT_ICON[a.level]}</span>
              <span className="toast-txt"><b>{splitAlert(a.text)[0]}</b>{splitAlert(a.text)[1] && <small>{splitAlert(a.text)[1]}</small>}</span>
            </button>
            <button type="button" className="toast-x" aria-label="닫기" onClick={() => setToasts((t) => t.filter((x) => x.id !== a.id))}>×</button>
          </div>
        ))}
      </div>, document.body)}
    </>
  );
}

/** A desktop notification when the window is not in front (only after the owner allowed it in Settings). */
function notifyDesktop(a: LiveAlert) {
  try {
    if (typeof Notification === "undefined" || Notification.permission !== "granted" || document.visibilityState === "visible") return;
    new Notification("MarketLens", { body: a.text, tag: `ml-${a.id}` });
  } catch { /* not supported in this window */ }
}

/** ``children`` (a stored status) only while no live verdict of this very recommendation is on screen — the two must
 * never disagree side by side ("손절 기준 도달" next to "현재 유효"). */
export function UnlessLive({ ticker, recId, children }: { ticker: string; recId: number; children: React.ReactNode }) {
  const { row } = useQuote(ticker);
  const j = row?.judge;
  if (j && j.rec_id === recId && j.zone !== "NO_PLAN") return null;
  return <>{children}</>;
}

/** A plan tile's line at the LIVE price when there is one (else ``fallback``, worded on the analysis price). */
export function LivePlanLine({ ticker, recId, level, fallback }: { ticker: string; recId: number; level: "max" | "stop"; fallback: React.ReactNode }) {
  const { row } = useQuote(ticker);
  const j = row?.judge;
  if (!j || j.rec_id !== recId || row?.price == null) return <>{fallback}</>;
  if (level === "stop") {
    return <span className={j.zone === "STOP_HIT" ? "neg" : undefined}>{j.to_stop_pct === null ? "—" : j.zone === "STOP_HIT" ? "현재가가 손절 기준 아래" : `현재가 대비 ${pct(j.to_stop_pct)}`} · 논리 철회 조건은 ⑤</span>;
  }
  const v = zoneView(j);
  if (!v) {
    // not a buy call (대기·관찰…): say where the price is against the max buy instead of "판단 없음"
    if (j.to_max_pct === null) return <>{fallback}</>;
    const above = j.to_max_pct < 0;
    return <span className={above ? "warn" : undefined}>{above ? `현재가가 최대 매수가보다 ${pct(-j.to_max_pct, 1, false)} 위` : `현재가가 최대 매수가 아래 (여유 ${pct(j.to_max_pct, 1, false)})`} · 판정 {j.action_ko}</span>;
  }
  return <span className={j.valid_now ? "pos" : j.zone === "STOP_HIT" ? "neg" : j.zone === "BUY_ZONE" ? undefined : "warn"}>
    현재가 기준 {v.label}{j.to_max_pct !== null && j.zone !== "STOP_HIT" ? ` · 최대 매수가까지 ${pct(j.to_max_pct)}` : ""}
  </span>;
}
