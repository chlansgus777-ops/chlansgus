import { Link } from "react-router-dom";
import { api } from "../api";
import { Change } from "./ui";
import { signedWon } from "./AccountLive";
import { useApi, usePoll } from "./useApi";
import { pct, price } from "../format";

/** 오늘의 브리핑 (owner 2026-09-29: "고정되어 있으면 도움이 안 돼 — 오늘의 브리핑으로, 실시간으로"; in the app only): the
 * market and my account on the live prices, what to check today. A figure without a live price says it is a close. */
export type Briefing = {
  date_kst: string; ready: boolean; built_at: string; session: string; session_expected: string; notes: string[];
  live?: boolean; live_at?: string | null;
  market: { name: string; ticker: string; close: number | null; change: number | null; live?: boolean }[];
  /** the account's today as the 토스증권 app counts it (a share bought today from its purchase price), live */
  account: { holdings: number; priced: number; pnl: number | null; change: number | null; movers: { ticker: string; change: number | null; pnl: number | null; close: number | null }[];
    pnl_krw?: number | null; total_pnl?: number | null; total_rate?: number | null; total_pnl_krw?: number | null; basis?: "TOSS" | "PRICE"; notes?: string[] };
  watch: { ticker: string; kind: "STOP" | "TARGET"; price: number; level: number; distance: number; text: string }[];
  alerts: { id: number; at: string; ticker: string; kind: string; level: string; text: string }[];
  events: { date: string; title: string; tickers: string[]; type: string }[];
  candidates: { ticker: string; action: string; action_ko: string; score: number; max_buy: number | null; as_of: string }[];
  scan_as_of: string | null; headline: string;
};

const WD = ["일", "월", "화", "수", "목", "금", "토"];

function kstTitle(d: string): string {
  const [y, m, dd] = d.split("-").map(Number);
  const w = new Date(Date.UTC(y!, m! - 1, dd!)).getUTCDay();
  return `${m}월 ${dd}일 (${WD[w]})`;
}

function md(d: string): string {
  const [, m, dd] = d.split("-").map(Number);
  return `${m}/${dd}`;
}

function kstTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("ko-KR", { timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

export function signedUsd(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "N/A";
  const r = Math.round(v);
  return `${r > 0 ? "+" : r < 0 ? "−" : ""}$${Math.abs(r).toLocaleString("en-US")}`;
}

export function MorningBriefing() {
  const b = useApi<Briefing>("/briefing");
  usePoll(b.reload, 3_000, true);  // it follows the live prices
  const refresh = async () => { try { await api.get("/briefing?refresh=true"); } finally { b.reload(); } };
  return b.data ? <BriefingView x={b.data} onRefresh={refresh} showCandidates={false} /> : null;
}

export function BriefingView({ x, onRefresh, showCandidates = true }: { x: Briefing; onRefresh?: () => void; showCandidates?: boolean }) {
  if (!x.date_kst || !x.market || !x.account) return null;  // an incomplete answer never breaks the home screen
  const acc = x.account;
  const refresh = () => onRefresh?.();
  const tone = (v: number | null | undefined) => (v == null ? "" : v > 0 ? "up" : v < 0 ? "down" : "");
  return (
    <section className={`brief enter${showCandidates ? "" : " home-brief"}`} aria-label="오늘의 브리핑" data-testid="morning-briefing">
      <div className="brief-head">
        <div className="brief-title">
          <span className="sun" aria-hidden>☀</span>
          <div>
            <h2>오늘의 브리핑</h2>
            <div className="brief-sub">{kstTitle(x.date_kst)} · {x.live && x.live_at ? <><span className="live-dot" aria-hidden />실시간 {kstTime(x.live_at)} 기준</> : `미국 ${md(x.session)} 종가 기준`}</div>
          </div>
        </div>
        <button type="button" className="ghost sm" onClick={refresh} title="지금 다시 계산">새로 고침</button>
      </div>

      <div className="brief-tiles">
        {x.market.map((m) => (
          <div key={m.ticker} className={`brief-tile ${tone(m.change)}`}>
            <div className="t">{m.name}</div>
            <div className="v">{m.change == null ? <span className="muted">자료 없음</span> : pct(m.change, 2)}</div>
            <div className="s">{m.close == null ? `${m.ticker} 가격 없음` : `${m.ticker} ${price(m.close)}${m.live ? " · 실시간" : " · 종가"}`}</div>
          </div>
        ))}
        <div className={`brief-tile acct ${tone(acc.pnl)}`}>
          <div className="t">내 계좌 오늘 (미국 주식{acc.basis === "TOSS" ? " · 토스 기준" : ""})</div>
          <div className="v">{acc.holdings === 0 ? <span className="muted">보유 종목 없음</span> : acc.pnl == null ? <span className="muted">지금 가격 대기</span> : signedUsd(acc.pnl)}</div>
          <div className="s">{acc.change != null ? `${pct(acc.change, 2)} · ` : ""}{acc.pnl_krw != null ? `${signedWon(acc.pnl_krw)} · ` : ""}{acc.holdings ? (acc.priced < acc.holdings ? `${acc.priced}/${acc.holdings}종목` : `${acc.holdings}종목`) : "포트폴리오에 종목을 넣으면 표시됩니다"}</div>
          {acc.total_pnl != null && <div className="s" data-testid="brief-total">총 손익 {signedUsd(acc.total_pnl)}{acc.total_rate != null ? ` (${pct(acc.total_rate, 2)})` : ""}{acc.total_pnl_krw != null ? ` · ${signedWon(acc.total_pnl_krw)}` : ""}</div>}
        </div>
      </div>

      {acc.movers.length > 0 && (
        <div className="brief-movers" aria-label="많이 움직인 보유 종목">
          {acc.movers.map((m) => (
            <Link key={m.ticker} to={`/stocks/${m.ticker}`} className={`mover ${tone(m.change)}`}>
              <b>{m.ticker}</b><Change v={m.change} digits={2} /><span className="pnl">{signedUsd(m.pnl)}</span>
            </Link>
          ))}
        </div>
      )}

      <div className="brief-cols">
        {x.watch.length > 0 && (
          <div className="brief-col">
            <h3>확인할 보유 종목</h3>
            {x.watch.map((w) => (
              <Link key={w.ticker + w.kind} to={`/stocks/${w.ticker}`} className={`brief-row ${w.kind === "STOP" ? "neg" : "pos"}`}>
                <span className="tag">{w.kind === "STOP" ? "손절" : "목표"}</span><span className="txt">{w.text}</span>
              </Link>
            ))}
          </div>
        )}
        {x.events.length > 0 && (
          <div className="brief-col">
            <h3>오늘·내일 일정</h3>
            {x.events.map((e, i) => (
              <div key={i} className="brief-row"><span className="tag">{md(e.date)}</span><span className="txt">{e.title}{e.tickers.length ? ` · ${e.tickers.join(", ")}` : ""}</span></div>
            ))}
          </div>
        )}
        {showCandidates && x.candidates.length > 0 && (
          <div className="brief-col">
            <h3>지난 스캔의 매수 후보</h3>
            {x.candidates.map((c) => (
              <Link key={c.ticker} to={`/stocks/${c.ticker}`} className="brief-row">
                <span className="tag">{c.action_ko}</span><span className="txt"><b>{c.ticker}</b> · 점수 {Math.round(c.score)}{c.max_buy ? ` · 최대 매수가 ${price(c.max_buy)}` : ""}</span>
              </Link>
            ))}
          </div>
        )}
        {x.alerts.length > 0 && (
          <div className="brief-col">
            <h3>최근 알림</h3>
            {x.alerts.map((a) => <div key={a.id} className={`brief-row lvl-${a.level}`}><span className="txt">{a.text}</span></div>)}
          </div>
        )}
        {/* nothing to flag: say so (what was checked) instead of leaving half the card blank */}
        {!x.watch.length && !x.events.length && !x.alerts.length && !(showCandidates && x.candidates.length) && (
          <div className="brief-col brief-check" data-testid="brief-all-clear">
            <h3>오늘 점검</h3>
            <div className="brief-row ok"><span className="tag">✓</span><span className="txt">{acc.holdings ? `보유 ${acc.holdings}종목 중 손절·목표가 근처인 종목 없음` : "보유 종목 없음"}</span></div>
            <div className="brief-row ok"><span className="tag">✓</span><span className="txt">받은 일정 중 오늘·내일 일정 없음</span></div>
            <div className="brief-row ok"><span className="tag">✓</span><span className="txt">밤사이 새 알림 없음</span></div>
          </div>
        )}
      </div>
      {!x.live && x.notes.length > 0 && <div className="brief-notes">{x.notes.join(" · ")}</div>}
      {acc.notes?.map((note, i) => <div className="brief-notes" key={i}>{note}</div>)}
    </section>
  );
}
