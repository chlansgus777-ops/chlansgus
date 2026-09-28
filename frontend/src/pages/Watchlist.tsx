import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { IPlus, ITrash } from "../components/icons";
import { LivePrice } from "../components/LivePrice";
import { refreshQuoteSubscriptions } from "../quotes";
import { Action, Empty, Err, Loading, StatusBadge } from "../components/ui";
import { useApi } from "../components/useApi";
import { num, price } from "../format";
import type { OppRow } from "../types";

const TICKER = /^[A-Za-z][A-Za-z0-9.-]{0,9}$/;

/** Saved names with their latest stored judgement; a name never analysed can be analysed from its row. */
export default function Watchlist() {
  const w = useApi<{ ticker: string; note: string; latest: OppRow | null }[]>("/watchlist");
  const [t, setT] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const nav = useNavigate();
  if (w.state === "loading") return <Loading what="관심 종목" rows={2} />;
  if (!w.data) return <Err error={w.error} retry={w.reload} />;
  const act = async (key: string, f: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(key); setErr(null);
    try { await f(); w.reload(); refreshQuoteSubscriptions(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(null); }
  };
  const add = () => {
    const v = t.trim().toUpperCase();
    if (!TICKER.test(v)) { setErr("종목 코드는 영문으로 시작하는 1~10자(예: NVDA, BRK.B)여야 합니다."); return; }
    void act("add", async () => { await api.post(`/watchlist/${encodeURIComponent(v)}`); setT(""); });
  };
  return (
    <section className="card flush" data-testid="watchlist">
      <form className="row" style={{ padding: "16px 18px 12px", borderBottom: "1px solid var(--line)" }} onSubmit={(e) => { e.preventDefault(); add(); }}>
        <input value={t} onChange={(e) => setT(e.target.value)} placeholder="종목 코드 (예: NVDA)" aria-label="관심 종목 코드" style={{ width: 200 }} />
        <button type="submit" disabled={busy === "add"}><IPlus />관심 종목 추가</button>
        <span className="caption">추가한 종목은 홈의 ‘관심 종목’에 판단 변화가 표시됩니다.</span>
      </form>
      {err && <div style={{ padding: "10px 18px 0" }}><Err error={err} /></div>}
      <div style={{ padding: "4px 8px 8px" }}>
        {w.data.length ? (
          <div className="scroll">
            <table>
              <thead><tr><th>종목</th><th>판단</th><th className="num">점수</th><th className="num" title="위: 최신 시세 · 아래: 분석 시점 가격">현재가</th><th className="num">최대 매수가</th><th>현재 유효성</th><th /></tr></thead>
              <tbody>{w.data.map((x) => (
                <tr key={x.ticker}>
                  <td><div className="tk-cell"><Link to={`/stocks/${x.ticker}`}>{x.ticker}</Link>{x.latest ? <span className="co">{x.latest.company}</span> : null}</div></td>
                  <td>{x.latest ? <Action a={x.latest.action} status={x.latest.current_status} quality={x.latest.data_quality} /> : <span className="muted">아직 분석 안 함</span>}</td>
                  <td className="num">{num(x.latest?.score ?? null, 1)}</td>
                  <td className="num">
                    <span style={{ display: "inline-flex", flexDirection: "column", alignItems: "flex-end", gap: 1 }}>
                      <LivePrice ticker={x.ticker} size="sm" />
                      {x.latest ? <span className="caption">분석 {price(x.latest.price)}</span> : null}
                    </span>
                  </td>
                  <td className="num">{price(x.latest?.max_buy)}</td>
                  <td><StatusBadge s={x.latest?.current_status} reason={x.latest?.current_status_reason} /></td>
                  <td className="num">
                    <div className="row tight" style={{ justifyContent: "flex-end" }}>
                      {!x.latest && <button className="sm" onClick={() => nav(`/stocks/${x.ticker}`)}>분석하기</button>}
                      <button className="sm ghost" aria-label={`${x.ticker} 관심 종목에서 삭제`} disabled={busy === x.ticker} onClick={() => void act(x.ticker, () => api.del(`/watchlist/${x.ticker}`))}><ITrash />삭제</button>
                    </div>
                  </td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        ) : <div style={{ padding: 12 }}><Empty hint="종목 상세 화면의 ‘관심 종목 추가’나 위 입력칸으로 추가하세요.">관심 종목이 없습니다.</Empty></div>}
      </div>
    </section>
  );
}
