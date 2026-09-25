import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Action, Card, Empty, Err, Loading, StatusBadge } from "../components/ui";
import { useApi } from "../components/useApi";
import { num, price } from "../format";
import type { OppRow } from "../types";

export default function Watchlist() {
  const w = useApi<{ ticker: string; note: string; latest: OppRow | null }[]>("/watchlist");
  const [t, setT] = useState("");
  const [err, setErr] = useState<string | null>(null);
  if (w.state === "loading") return <Loading what="관심 종목" />;
  if (!w.data) return <Err error={w.error} retry={w.reload} />;
  const act = async (f: () => Promise<unknown>) => { setErr(null); try { await f(); w.reload(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } };
  return (
    <div className="grid">
      <h3>관심 종목</h3>
      <Card>
        <form className="row" onSubmit={(e) => { e.preventDefault(); if (t.trim()) void act(async () => { await api.post(`/watchlist/${encodeURIComponent(t.trim().toUpperCase())}`); setT(""); }); }}>
          <input value={t} onChange={(e) => setT(e.target.value)} placeholder="종목 코드 추가" aria-label="종목 코드" /><button type="submit">관심종목 추가</button>
        </form>
        <Err error={err} />
        {w.data.length ? (
          <table><thead><tr><th>종목</th><th>현재가(USD)</th><th>점수</th><th>추천</th><th>현재 유효성</th><th>최대 매수가</th><th /></tr></thead>
            <tbody>{w.data.map((x) => <tr key={x.ticker}><td><Link to={`/stocks/${x.ticker}`}>{x.ticker}</Link></td><td>{price(x.latest?.price)}</td><td>{num(x.latest?.score ?? null, 1)}</td>
              <td>{x.latest ? <Action a={x.latest.action} status={x.latest.current_status} quality={x.latest.data_quality} /> : <span className="muted">분석 전</span>}</td>
              <td><StatusBadge s={x.latest?.current_status} reason={x.latest?.current_status_reason} /></td><td>{price(x.latest?.max_buy)}</td>
              <td><button onClick={() => void act(() => api.del(`/watchlist/${x.ticker}`))}>삭제</button></td></tr>)}</tbody></table>
        ) : <Empty>관심 종목이 없습니다.</Empty>}
      </Card>
    </div>
  );
}
