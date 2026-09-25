import { useState } from "react";
import { api } from "../api";
import { Card, Donut, Empty, Err, Loading, Notice, Term } from "../components/ui";
import { useApi } from "../components/useApi";
import { day, num, pct, price, usdWithKo } from "../format";
import { More } from "../mode";

interface HoldingV { ticker: string; quantity: number; cost_basis: number; price: number | null; price_day: string | null; market_value: number | null; unrealized_pnl: number | null; unrealized_pct: number | null; weight: number | null; sector: string }
interface Pf {
  valuation_day: string | null; cash: number; invested_value: number; nav: number; unrealized_pnl: number; holdings: HoldingV[];
  sector_weights: Record<string, number>; theme_weights: Record<string, number>; hhi: number; beta: number | null;
  correlations: [string, string, number][]; missing_prices: string[]; notes: string[]; currency: string; note: string;
}

/** Plain-language reading of the portfolio (only from the numbers above). */
export function interpret(x: Pf): { tone: "info" | "warn"; text: string }[] {
  const out: { tone: "info" | "warn"; text: string }[] = [];
  if (!x.holdings.length) return out;
  const cashW = x.nav > 0 ? x.cash / x.nav : 0;
  const top = [...x.holdings].filter((h) => h.weight !== null).sort((a, b) => (b.weight ?? 0) - (a.weight ?? 0))[0];
  const topSector = Object.entries(x.sector_weights).sort((a, b) => b[1] - a[1])[0];
  const ai = x.theme_weights["AI"];
  if (ai !== undefined && ai >= 0.3) out.push({ tone: "warn", text: `현재 포트폴리오는 AI/반도체 비중이 ${pct(ai, 0, false)}로 높습니다. 같은 테마 종목을 더하면 함께 오르내릴 위험이 커집니다.` });
  if (topSector && topSector[1] >= 0.3) out.push({ tone: "warn", text: `${topSector[0]} 업종에 ${pct(topSector[1], 0, false)}가 몰려 있습니다(한도 30%).` });
  if (top && (top.weight ?? 0) >= 0.1) out.push({ tone: "warn", text: `${top.ticker} 한 종목이 ${pct(top.weight, 0, false)}를 차지합니다(종목당 한도 10%). 추가매수보다 분산을 고려하세요.` });
  const hi = x.correlations.filter(([, , c]) => c >= 0.75);
  if (hi.length) out.push({ tone: "warn", text: `${hi.map(([a, b]) => `${a}·${b}`).join(", ")}는 거의 같이 움직입니다. 사실상 같은 종목을 여러 개 가진 효과입니다.` });
  if (cashW < 0.05) out.push({ tone: "warn", text: `현금이 ${pct(cashW, 1, false)}뿐이라 새 종목은 소액만 가능합니다.` });
  else out.push({ tone: "info", text: `현금 비중은 ${pct(cashW, 0, false)}입니다.` });
  if (x.beta !== null) out.push({ tone: "info", text: `시장이 1% 움직일 때 이 포트폴리오는 평균 약 ${num(x.beta, 2)}% 움직였습니다.` });
  if (out.every((o) => o.tone === "info")) out.unshift({ tone: "info", text: "업종·종목 쏠림 없이 비교적 고르게 분산되어 있습니다." });
  return out;
}

export default function Portfolio() {
  const p = useApi<Pf>("/portfolio");
  const [row, setRow] = useState({ ticker: "", quantity: "", cost: "" });
  const [cash, setCash] = useState("");
  const [err, setErr] = useState<string | null>(null);
  if (p.state === "loading") return <Loading what="포트폴리오 재계산" steps={["보유 종목 종가 확인", "평가금액·손익 계산", "쏠림·상관관계 점검"]} />;
  if (!p.data) return <Err error={p.error} retry={p.reload} />;
  const save = async (body: unknown) => { setErr(null); try { await api.put("/portfolio", body); p.reload(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } };
  const x = p.data;
  const reading = interpret(x);
  const weights: [string, number][] = [...x.holdings.filter((h) => (h.market_value ?? 0) > 0).map((h) => [h.ticker, h.market_value ?? 0] as [string, number]), ["현금", x.cash]];
  const pnlPct = x.invested_value - x.unrealized_pnl > 0 ? x.unrealized_pnl / (x.invested_value - x.unrealized_pnl) : null;
  return (
    <div className="grid">
      <div className="page-head"><div><h1>내 포트폴리오</h1><div className="t-sub">{x.note}</div></div></div>
      {x.notes.map((n, i) => <Notice key={i} tone="warn">{n}</Notice>)}
      <Err error={err} />
      <div className="g4">
        <Card title="총 평가금액"><div className="t-key">{price(x.nav)}</div><div className="caption">{usdWithKo(x.nav)} · 기준일 {day(x.valuation_day)}</div></Card>
        <Card title="평가손익"><div className={`t-key ${x.unrealized_pnl >= 0 ? "pos" : "neg"}`}>{x.unrealized_pnl >= 0 ? "▲" : "▼"} {price(x.unrealized_pnl)}</div><div className="caption">매입금액 대비 {pct(pnlPct)}</div></Card>
        <Card title="현금"><div className="t-key">{price(x.cash)}</div><div className="caption">전체의 {pct(x.nav > 0 ? x.cash / x.nav : null, 0, false)}</div></Card>
        <Card title="분산 정도"><div className="t-key">{x.hhi < 0.15 ? "좋음" : x.hhi < 0.3 ? "보통" : "쏠림"}</div><div className="caption"><Term k="hhi">집중도(HHI)</Term> {num(x.hhi, 2)} · <Term k="beta">베타</Term> {num(x.beta, 2)}</div></Card>
      </div>
      <div className="g2">
        <Card title="한 줄 해석" icon="✎">
          {reading.length ? <ul className="list">{reading.map((r, i) => <li key={i}><span className={`dot ${r.tone === "warn" ? "warn" : "info"}`}>{r.tone === "warn" ? "!" : "i"}</span><span>{r.text}</span></li>)}</ul> : <Empty hint="아래에서 종목 코드·수량·매입 단가를 입력하세요.">아직 보유 종목이 없습니다.</Empty>}
        </Card>
        <Card title="비중 한눈에 보기" icon="◔">
          {x.holdings.length ? <Donut parts={weights} /> : <Empty>보유 종목을 입력하면 비중 차트가 보입니다.</Empty>}
        </Card>
      </div>
      <div className="g2">
        <Card title="업종 쏠림">
          {Object.keys(x.sector_weights).length ? Object.entries(x.sector_weights).sort((a, b) => b[1] - a[1]).map(([s, w]) => (
            <div key={s} style={{ marginBottom: 8 }}><div className="row spread"><span>{s}</span><b className={w > 0.3 ? "warn" : ""}>{pct(w, 1, false)}</b></div><div className="bar"><div style={{ width: `${Math.min(100, w * 100)}%`, background: w > 0.3 ? "var(--warn)" : undefined }} /></div></div>
          )) : <Empty>없음</Empty>}
        </Card>
        <Card title="보유 종목 추가·수정" explain="수량 0을 입력하면 삭제됩니다. MarketLens는 실제 주문을 넣지 않습니다.">
          <form className="stack" onSubmit={(e) => {
            e.preventDefault();
            const q = Number(row.quantity), c = Number(row.cost);
            if (!row.ticker.trim() || !Number.isFinite(q) || q < 0 || !Number.isFinite(c) || c < 0) { setErr("종목 코드, 수량(0 이상), 매입 단가(0 이상)를 입력하세요."); return; }
            void save({ holdings: [{ ticker: row.ticker.trim().toUpperCase(), quantity: q, cost_basis: c }] });
          }}>
            <div className="row">
              <input placeholder="종목 코드 (예: NVDA)" value={row.ticker} onChange={(e) => setRow({ ...row, ticker: e.target.value })} />
              <input placeholder="수량" value={row.quantity} onChange={(e) => setRow({ ...row, quantity: e.target.value })} />
              <input placeholder="매입 단가(USD)" value={row.cost} onChange={(e) => setRow({ ...row, cost: e.target.value })} />
              <button className="primary">저장</button>
            </div>
          </form>
          <form className="row" style={{ marginTop: 10 }} onSubmit={(e) => { e.preventDefault(); const v = Number(cash); if (Number.isFinite(v) && v >= 0) void save({ cash: v }); else setErr("현금은 0 이상의 숫자여야 합니다."); }}>
            <input value={cash} onChange={(e) => setCash(e.target.value)} placeholder="현금(USD)" /><button>현금 저장</button>
          </form>
        </Card>
      </div>
      <More title="보유 종목 상세" hint="모든 종목을 같은 거래일 종가로 평가">
        {x.holdings.length ? (
          <table><thead><tr><th>종목</th><th>수량</th><th>매입 단가</th><th>종가(기준일)</th><th>평가액</th><th>평가손익</th><th>비중</th><th>섹터</th></tr></thead>
            <tbody>{x.holdings.map((h) => <tr key={h.ticker}><td>{h.ticker}</td><td>{num(h.quantity, 0)}</td><td>{price(h.cost_basis)}</td><td>{price(h.price)} <span className="caption">{day(h.price_day)}</span></td>
              <td>{price(h.market_value)}</td><td className={(h.unrealized_pnl ?? 0) >= 0 ? "pos" : "neg"}>{price(h.unrealized_pnl)} ({pct(h.unrealized_pct)})</td><td>{pct(h.weight, 1, false)}</td><td>{h.sector}</td></tr>)}</tbody></table>
        ) : <Empty>보유 종목이 없습니다.</Empty>}
        {x.correlations.length > 0 && <div className="caption" style={{ marginTop: 8 }}><Term k="correlation">상관계수</Term>: {x.correlations.map(([a, b, c]) => `${a}↔${b} ${num(c, 2)}`).join(" · ")}</div>}
      </More>
    </div>
  );
}
