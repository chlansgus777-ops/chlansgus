import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { OppTable } from "../components/OppTable";
import { Card, Err } from "../components/ui";
import { useApi } from "../components/useApi";
import type { Opportunities } from "../types";
import Watchlist from "./Watchlist";

const TICKER = /^[A-Za-z][A-Za-z0-9.-]{0,9}$/;

export default function Stocks() {
  const [t, setT] = useState("");
  const [bad, setBad] = useState<string | null>(null);
  const nav = useNavigate();
  const o = useApi<Opportunities>("/opportunities");
  return (
    <div className="grid">
      <div className="page-head"><div><h1>종목 분석</h1><div className="t-sub">종목 코드를 넣으면 지금 사도 되는지, 얼마에 사야 하는지, 무엇을 조심해야 하는지 알려드립니다.</div></div></div>
      <Card title="미국 상장 종목 분석하기" icon="⌕">
        <form className="row" onSubmit={(e) => {
          e.preventDefault();
          const v = t.trim();
          if (!TICKER.test(v)) { setBad("종목 코드는 영문으로 시작하는 1~10자(예: NVDA, BRK.B)여야 합니다."); return; }
          setBad(null);
          nav(`/stocks/${v.toUpperCase()}`);
        }}>
          <input placeholder="종목 코드 (예: NVDA)" value={t} onChange={(e) => setT(e.target.value)} aria-label="종목 코드" />
          <button type="submit" className="primary">분석</button>
        </form>
        <Err error={bad} />
      </Card>
      <Watchlist />
      {o.data && o.data.rows.length > 0 && <Card title="최근 스캔 후보"><OppTable rows={o.data.rows} compact /></Card>}
    </div>
  );
}
