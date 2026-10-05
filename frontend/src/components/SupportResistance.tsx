import { price as usd } from "../format";

/** 지지선·저항선 (owner 2026-10-05): the price levels of the analysis — swing lows / highs clustered into levels
 * (domain/indicators.py), on today's share basis — split by where the price is NOW: the nearest three below are
 * supports, the nearest three above resistances, with the distance from the price. The first resistance above is
 * the plan's first target (domain/entry.py). A display only. */
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) && v > 0 ? v : null);
const list = (v: unknown): number[] => (Array.isArray(v) ? v.map(num).filter((x): x is number => x !== null) : []);

export function srLevels(technicals: Record<string, unknown> | null | undefined, price: number, split = 1): { supports: number[]; resistances: number[] } {
  const f = split > 0 ? split : 1;
  const all = [...new Set([...list(technicals?.supports), ...list(technicals?.resistances)].map((v) => Math.round((v / f) * 100) / 100))];
  return {
    supports: all.filter((v) => v < price).sort((a, b) => b - a).slice(0, 3),
    resistances: all.filter((v) => v > price).sort((a, b) => a - b).slice(0, 3),
  };
}

export function SupportResistance({ technicals, price, split = 1 }: { technicals: Record<string, unknown> | null | undefined; price: number | null; split?: number }) {
  if (price === null) return null;
  const { supports, resistances } = srLevels(technicals, price, split);
  if (!supports.length && !resistances.length) return null;
  const f = split > 0 ? split : 1;
  const hi = num(technicals?.high_52w), lo = num(technicals?.low_52w);
  const row = (v: number, kind: "s" | "r", i: number) => (
    <li key={`${kind}${v}`} className={`sr-row ${kind}`}>
      <span className="sr-k">{kind === "r" ? `저항 ${i + 1}` : `지지 ${i + 1}`}</span>
      <b>{usd(v)}</b>
      <span className="sr-d">{`${v >= price ? "+" : ""}${((v / price - 1) * 100).toFixed(1)}%`}</span>
    </li>
  );
  return (
    <div className="sr-box" data-testid="support-resistance">
      <div className="ma-head">
        <span className="ma-headline">지지선 · 저항선</span>
        <span className="caption">최근 고점·저점이 모인 가격대 · 현재가 기준 가까운 순</span>
      </div>
      <div className="sr-cols">
        <ul className="sr-list" aria-label="저항선">{resistances.length ? [...resistances].reverse().map((v, k) => row(v, "r", resistances.length - 1 - k)) : <li className="caption">위쪽 저항 없음 (신고가 구간)</li>}</ul>
        <div className="sr-now"><span>현재가</span><b>{usd(price)}</b></div>
        <ul className="sr-list" aria-label="지지선">{supports.length ? supports.map((v, i) => row(v, "s", i)) : <li className="caption">아래쪽 지지 없음</li>}</ul>
      </div>
      {(hi || lo) && <div className="caption">52주 {lo ? `최저 ${usd(lo / f)}` : ""}{hi && lo ? " · " : ""}{hi ? `최고 ${usd(hi / f)}` : ""} · 저항 1이 1차 목표가, 지지 1 아래가 손절 기준의 근거입니다</div>}
    </div>
  );
}
