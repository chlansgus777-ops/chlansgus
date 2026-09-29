import { Link } from "react-router-dom";
import { api } from "../api";
import { Change } from "./ui";
import { useApi, usePoll } from "./useApi";
import { pct, price } from "../format";

/** 오늘 아침 브리핑 (owner 2026-09-29: "아침 브리핑 (한국시간 오전 7시)" — in the app only): the US session that ended
 * overnight for the owner's account, from 07:00 KST. Every figure carries its session; a part without data says so. */
export type Briefing = {
  date_kst: string; ready: boolean; built_at: string; session: string; session_expected: string; notes: string[];
  market: { name: string; ticker: string; close: number | null; change: number | null }[];
  account: { holdings: number; priced: number; pnl: number | null; change: number | null; movers: { ticker: string; change: number | null; pnl: number | null; close: number | null }[] };
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

export function signedUsd(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "N/A";
  const r = Math.round(v);
  return `${r > 0 ? "+" : r < 0 ? "−" : ""}$${Math.abs(r).toLocaleString("en-US")}`;
}

export function MorningBriefing() {
  const b = useApi<Briefing>("/briefing");
  // the numbers are end-of-session: every 10 minutes is plenty — before 07:00 every minute, so the card appears on time
  usePoll(b.reload, b.data && !b.data.ready ? 60_000 : 600_000, true);
  const refresh = async () => { try { await api.get("/briefing?refresh=true"); } finally { b.reload(); } };
  return b.data ? <BriefingView x={b.data} onRefresh={refresh} /> : null;
}

export function BriefingView({ x, onRefresh }: { x: Briefing; onRefresh?: () => void }) {
  if (!x.ready) return null;  // before 07:00 KST the card is not shown (the alert announces it)
  const acc = x.account;
  const refresh = () => onRefresh?.();
  const tone = (v: number | null | undefined) => (v == null ? "" : v > 0 ? "up" : v < 0 ? "down" : "");
  return (
    <section className="brief enter" aria-label="오늘 아침 브리핑" data-testid="morning-briefing">
      <div className="brief-head">
        <div className="brief-title">
          <span className="sun" aria-hidden>☀</span>
          <div>
            <h2>오늘 아침 브리핑</h2>
            <div className="brief-sub">{kstTitle(x.date_kst)} · 미국 {md(x.session)} 장 기준</div>
          </div>
        </div>
        <button type="button" className="ghost sm" onClick={refresh} title="지금 다시 계산">새로 고침</button>
      </div>

      <div className="brief-tiles">
        {x.market.map((m) => (
          <div key={m.ticker} className={`brief-tile ${tone(m.change)}`}>
            <div className="t">{m.name}</div>
            <div className="v">{m.change == null ? <span className="muted">자료 없음</span> : pct(m.change, 2)}</div>
            <div className="s">{m.close == null ? `${m.ticker} 종가 없음` : `${m.ticker} ${price(m.close)}`}</div>
          </div>
        ))}
        <div className={`brief-tile acct ${tone(acc.pnl)}`}>
          <div className="t">내 계좌 (미국 주식)</div>
          <div className="v">{acc.holdings === 0 ? <span className="muted">보유 종목 없음</span> : acc.pnl == null ? <span className="muted">등락 계산 불가</span> : signedUsd(acc.pnl)}</div>
          <div className="s">{acc.change != null ? `${pct(acc.change, 2)} · ` : ""}{acc.holdings ? `${acc.priced}/${acc.holdings}종목 가격 확인` : "포트폴리오에 종목을 넣으면 표시됩니다"}</div>
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
        {x.candidates.length > 0 && (
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
            <h3>밤사이 알림</h3>
            {x.alerts.map((a) => <div key={a.id} className={`brief-row lvl-${a.level}`}><span className="txt">{a.text}</span></div>)}
          </div>
        )}
      </div>
      {x.notes.length > 0 && <div className="brief-notes">{x.notes.join(" · ")}</div>}
    </section>
  );
}
