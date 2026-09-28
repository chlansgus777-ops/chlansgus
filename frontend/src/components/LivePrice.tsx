import { useEffect, useRef, useState } from "react";
import { price as fmtPrice, stamp } from "../format";
import { effectiveState, stateLabel, useQuote, useQuoteStatus, type QuoteRow, type Link } from "../quotes";
import "./live.css";

const TONE: Record<string, string> = {
  LIVE: "live", QUIET: "quiet", DELAYED: "delayed", CLOSED_LAST: "closed", EXTENDED_NO_TRADE: "closed",
  RECONNECTING: "down", NO_DATA: "none", OVER_LIMIT: "none", UNAVAILABLE: "none",
};

function hms(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

export function quoteTitle(row: QuoteRow | undefined, l: Link): string {
  if (!row) return "아직 받은 시세가 없습니다.";
  const parts = [
    stateLabel(row, l),
    row.trade_time ? `체결 시각 ${stamp(row.trade_time)}` : null,
    row.received_time ? `수신 ${hms(row.received_time)}` : null,
    row.source ? `출처 ${row.source}${row.feed === "stream" ? " 체결 스트림" : row.feed === "snapshot" ? " 스냅샷(REST)" : ""}` : null,
    "표시용 참고 가격 — 매수 판단·수량 계산은 분석의 신선도 규칙을 따릅니다",
  ];
  return parts.filter(Boolean).join(" · ");
}

/** The app-wide feed in one line (status bar): what feeds the prices, whether it is connected, how many names. */
export function QuoteFeedStatus() {
  const { link, status } = useQuoteStatus();
  if (!status) return <span className="quote-feed" title="시세 스트림에 연결하는 중"><i />시세 연결 중</span>;
  const down = link === "retrying" || (status.streaming && !status.connected);
  const on = !down && status.streaming && status.connected;
  const word = !status.streaming ? (status.source === "mock" ? "모의 시세" : "실시간 스트림 없음 · 스냅샷") : down ? "시세 연결 끊김 · 재연결 중" : "실시간 체결 수신";
  const lat = status.provider_latency_ms;
  const title = [
    status.coverage,
    `구독 ${status.subscribed.length}/${status.max_symbols}${status.over_limit.length ? ` · 한도 초과 ${status.over_limit.length}개(${status.over_limit.slice(0, 5).join(", ")})` : ""}`,
    lat.n ? `제공자 체결→수신 지연 p50 ${lat.p50}ms · p95 ${lat.p95}ms` : null,
    status.last_error ? `최근 오류: ${status.last_error}` : null,
  ].filter(Boolean).join("\n");
  return <span className={`quote-feed${on ? " on" : down ? " down" : ""}`} title={title} data-testid="quote-feed"><i />{word}{status.streaming ? ` · ${status.subscribed.length}/${status.max_symbols}` : ""}</span>;
}

/** The latest price of one ticker from the app-wide store: fixed-width digits, a short up/down flash on a new price
 * (none with reduced motion — CSS), and a small state word. Only this element re-renders on a tick. */
export function LivePrice({ ticker, size = "md", showState = true, showTime = false }: { ticker: string; size?: "sm" | "md" | "lg"; showState?: boolean; showTime?: boolean }) {
  const { row, link } = useQuote(ticker);
  const [flash, setFlash] = useState<"" | "up" | "down">("");
  const prev = useRef<number | null>(null);
  useEffect(() => {
    const p = row?.price ?? null;
    if (p !== null && prev.current !== null && p !== prev.current) {
      setFlash(p > prev.current ? "up" : "down");
      const t = setTimeout(() => setFlash(""), 700);
      prev.current = p;
      return () => clearTimeout(t);
    }
    prev.current = p;
    return undefined;
  }, [row?.price, row?.trade_time]);
  const st = effectiveState(row, link);
  const ch = row?.change_pct ?? null;
  return (
    <span className={`lp lp-${size} tone-${TONE[st]}`} title={quoteTitle(row, link)} data-testid={`live-${ticker}`} data-state={st}>
      <span className={`lp-v num${flash ? ` flash-${flash}` : ""}`}>{row?.price != null ? fmtPrice(row.price) : "—"}</span>
      {ch !== null && size !== "sm" ? <span className={`lp-ch num ${ch >= 0 ? "pos" : "neg"}`}>{ch >= 0 ? "+" : ""}{(ch * 100).toFixed(2)}%</span> : null}
      {showState ? <span className="lp-s"><i aria-hidden />{stateLabel(row, link)}{showTime && row?.trade_time ? ` · ${hms(row.trade_time)}` : ""}</span> : null}
    </span>
  );
}
