import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { recentStocks } from "../components/QuickSearch";
import { IArrow, IStocks } from "../components/icons";
import { Err, Tabs } from "../components/ui";
import { useApi } from "../components/useApi";
import type { Opportunities as Opp } from "../types";
import Opportunities from "./Opportunities";
import Watchlist from "./Watchlist";

const TICKER = /^[A-Za-z][A-Za-z0-9.-]{0,9}$/;
type Tab = "candidates" | "watch";

/** Finding a stock: analyse any ticker, browse the last scan's candidates, keep a watchlist — one screen (the old
 * 기회 찾기 and 종목 분석 screens showed the same candidate table twice). The tab lives in the URL, so back,
 * refresh and bookmarks keep it. */
export default function Stocks() {
  const [params, setParams] = useSearchParams();
  const tab: Tab = params.get("tab") === "watch" ? "watch" : "candidates";
  const [t, setT] = useState("");
  const [bad, setBad] = useState<string | null>(null);
  const nav = useNavigate();
  const o = useApi<Opp>("/opportunities");
  const w = useApi<unknown[]>("/watchlist");
  const [recent] = useState(recentStocks);
  return (
    <div className="grid">
      <div className="page-head enter">
        <div>
          <h1>종목</h1>
          <div className="t-sub">후보를 비교하고, 가격 계획과 판단 근거를 확인하세요.</div>
        </div>
      </div>
      <section className="stock-search enter" aria-label="종목 분석하기">
        <form className="row" onSubmit={(e) => {
          e.preventDefault();
          const v = t.trim();
          if (!TICKER.test(v)) { setBad("종목 코드는 영문으로 시작하는 1~10자(예: NVDA, BRK.B)여야 합니다."); return; }
          setBad(null);
          nav(`/stocks/${v.toUpperCase()}`);
        }}>
          <span style={{ color: "var(--accent)", display: "inline-flex" }}><IStocks width={22} height={22} /></span>
          <input placeholder="종목 코드 · NVDA, BRK.B" value={t} onChange={(e) => setT(e.target.value)} aria-label="종목 코드" style={{ flex: "1 1 180px", minWidth: 0 }} />
          <button type="submit" className="primary lg">분석 <IArrow /></button>
        </form>
        {recent.length > 0 && <div className="recent-row" style={{ marginTop: 12 }} aria-label="최근 본 종목"><span className="caption">최근 본 종목</span>{recent.map((r) => <Link key={r} to={`/stocks/${r}`}>{r}</Link>)}</div>}
        <div style={{ marginTop: bad ? 10 : 0 }}><Err error={bad} /></div>
      </section>
      <div className="row spread">
        <Tabs<Tab> label="목록" value={tab} onChange={(v) => setParams(v === "candidates" ? {} : { tab: v }, { replace: true })}
              items={[["candidates", "후보", o.data?.rows.length], ["watch", "관심 종목", w.data?.length]]} />
      </div>
      {tab === "candidates" ? <Opportunities /> : <Watchlist />}
    </div>
  );
}
