import { num, pct, won } from "../format";
import { Card } from "./ui";

/** /api/portfolio ``krw``: the won return of each US holding split into the stock and the dollar (domain.fx_attrib). */
export interface KrwRow { ticker: string; known: boolean; reason: string | null; buy_fx: number | null; cost_krw: number | null; value_krw: number | null;
  stock_krw: number | null; fx_krw: number | null; total_krw: number | null; stock_pct: number | null; fx_pct: number | null; total_pct: number | null }
export interface KrwView { fx_now: number | null; fx_source: string | null; fx_at: string | null; loading: boolean; rows: KrwRow[]; note: string;
  totals: { count: number; cost_krw: number | null; stock_krw: number | null; fx_krw: number | null; total_krw: number | null; stock_pct: number | null; fx_pct: number | null; total_pct: number | null } }

const tone = (v: number | null | undefined) => (v == null ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "");
const signed = (v: number | null | undefined) => (v == null ? "N/A" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${won(Math.abs(v))}`);

export function KrwReturn({ k }: { k: KrwView | undefined }) {
  if (!k || !k.rows.length) return null;
  const t = k.totals;
  const s = t.stock_krw ?? 0, f = t.fx_krw ?? 0;
  const span = Math.abs(s) + Math.abs(f) || 1;
  return (
    <Card title="원화 기준 수익" testId="krw-return" explain="주가가 오른 몫과 원/달러 환율이 움직인 몫을 나눴습니다. 두 몫을 더하면 원화 손익과 정확히 같습니다.">
      {k.loading ? <div className="caption">매수 당시 환율을 불러오는 중…</div> : t.count === 0 ? (
        <div className="caption">매수일이 기록된 보유가 없어 나눌 수 없습니다. 거래 기록으로 입력하거나 토스증권을 연결하면 매수일의 환율로 계산합니다.</div>
      ) : (
        <div className="krw-head">
          <div>
            <div className="caption">원화 손익 ({t.count}종목)</div>
            <div className={`krw-total ${tone(t.total_krw)}`}>{signed(t.total_krw)} <span>{pct(t.total_pct)}</span></div>
          </div>
          <div className="krw-split" aria-label="주가 몫과 환율 몫">
            <div className="bar">
              <i className={`seg stock ${s < 0 ? "neg" : ""}`} style={{ width: `${(Math.abs(s) / span) * 100}%` }} />
              <i className={`seg fx ${f < 0 ? "neg" : ""}`} style={{ width: `${(Math.abs(f) / span) * 100}%` }} />
            </div>
            <div className="legend">
              <span><i className="sw stock" />주가로 <b className={tone(s)}>{signed(s)}</b> <em>{pct(t.stock_pct)}</em></span>
              <span><i className="sw fx" />환율로 <b className={tone(f)}>{signed(f)}</b> <em>{pct(t.fx_pct)}</em></span>
            </div>
          </div>
        </div>
      )}
      <div className="scroll" style={{ marginTop: 12 }}><table><thead><tr><th>종목</th><th className="num">매수 환율</th><th className="num">주가 효과</th><th className="num">환율 효과</th><th className="num">원화 손익</th></tr></thead>
        <tbody>{k.rows.map((r) => r.known ? (
          <tr key={r.ticker} className="nowrap-row"><td><b>{r.ticker}</b></td><td className="num">{num(r.buy_fx, 1)}원</td>
            <td className={`num ${tone(r.stock_krw)}`}>{signed(r.stock_krw)}<div className="caption">{pct(r.stock_pct)}</div></td>
            <td className={`num ${tone(r.fx_krw)}`}>{signed(r.fx_krw)}<div className="caption">{pct(r.fx_pct)}</div></td>
            <td className={`num ${tone(r.total_krw)}`}><b>{signed(r.total_krw)}</b><div className="caption">{pct(r.total_pct)}</div></td></tr>
        ) : (
          <tr key={r.ticker}><td><b>{r.ticker}</b></td><td colSpan={4} className="caption" style={{ whiteSpace: "normal" }}>나눌 수 없음 — {r.reason}</td></tr>
        ))}</tbody></table></div>
      <div className="caption" style={{ marginTop: 8 }}>지금 환율 {k.fx_now ? `${num(k.fx_now, 2)}원` : "N/A"}{k.fx_source ? ` · ${k.fx_source}` : ""}{k.fx_at ? ` · ${k.fx_at.slice(0, 16).replace("T", " ")}` : ""} · 주가는 평가 기준일 종가 · {k.note}</div>
    </Card>
  );
}
