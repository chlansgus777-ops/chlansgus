import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
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
  return (
    <div className="grid">
      <div className="page-head enter">
        <div>
          <h1>종목</h1>
          <div className="t-sub">종목 코드를 넣으면 지금 사도 되는지, 얼마에 사야 하는지, 무엇이 판단을 바꾸는지 분석합니다. 아래에는 마지막 스캔의 후보와 관심 종목이 있습니다.</div>
        </div>
      </div>
      <section className="today enter" style={{ padding: "20px 22px" }} aria-label="종목 분석하기">
        <form className="row" onSubmit={(e) => {
          e.preventDefault();
          const v = t.trim();
          if (!TICKER.test(v)) { setBad("종목 코드는 영문으로 시작하는 1~10자(예: NVDA, BRK.B)여야 합니다."); return; }
          setBad(null);
          nav(`/stocks/${v.toUpperCase()}`);
        }}>
          <span style={{ color: "var(--accent)", display: "inline-flex" }}><IStocks width={22} height={22} /></span>
          <input placeholder="미국 상장 종목 코드 (예: NVDA, BRK.B)" value={t} onChange={(e) => setT(e.target.value)} aria-label="종목 코드" style={{ flex: "1 1 260px", height: 44, fontSize: 16 }} />
          <button type="submit" className="primary lg">분석 <IArrow /></button>
        </form>
        <div className="caption" style={{ marginTop: 10 }}>이미 분석한 종목은 저장된 결과를 바로 보여주고, 처음이면 분석을 실행합니다(모의 데이터 수 초, 실데이터 수십 초).</div>
        <div style={{ marginTop: bad ? 10 : 0 }}><Err error={bad} /></div>
      </section>
      <div className="row spread">
        <Tabs<Tab> label="목록" value={tab} onChange={(v) => setParams(v === "candidates" ? {} : { tab: v }, { replace: true })}
              items={[["candidates", "스캔 후보", o.data?.rows.length], ["watch", "관심 종목", w.data?.length]]} />
      </div>
      {tab === "candidates" ? <Opportunities /> : <Watchlist />}
    </div>
  );
}
