import { price as usd } from "../format";

/** 지지선·저항선 (owner 2026-10-05): the price levels of the analysis — swing lows / highs clustered into levels
 * (domain/indicators.py), on today's share basis — split by where the price is NOW: the nearest three below are
 * supports, the nearest three above resistances, with the distance from the price. The first resistance above is
 * the plan's first target (domain/entry.py). A display only.
 *
 * Drawn small (owner 2026-10-05: "자리를 너무 많이 차지"): one ladder with the price in the middle and the levels as
 * dots, then one line of chips — the nearest level on each side stands out, the farther ones are quiet. */
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

const HELP = "최근 고점·저점이 모인 가격대입니다. 현재가에서 가까운 순으로 위 3개는 저항, 아래 3개는 지지.\n저항 1은 1차 목표가, 지지 1 아래가 손절 기준의 근거입니다.";

export function SupportResistance({ technicals, price, split = 1 }: { technicals: Record<string, unknown> | null | undefined; price: number | null; split?: number }) {
  if (price === null) return null;
  const { supports, resistances } = srLevels(technicals, price, split);
  if (!supports.length && !resistances.length) return null;
  const f = split > 0 ? split : 1;
  const hi = num(technicals?.high_52w), lo = num(technicals?.low_52w);
  // the ladder spans the levels shown, with room on both ends so the price bubble never sits on an edge
  const low = Math.min(price, ...supports), high = Math.max(price, ...resistances);
  const span = Math.max(high - low, price * 0.02);
  const a = low - span * 0.12, b = high + span * 0.12;
  const at = (v: number) => ((v - a) / (b - a)) * 100;
  const dist = (v: number) => `${v >= price ? "+" : ""}${((v / price - 1) * 100).toFixed(1)}%`;
  const s1 = supports[0], r1 = resistances[0];
  const chip = (v: number, kind: "s" | "r", i: number) => (
    <span key={`${kind}${v}`} className={`sr-chip ${kind}${i === 0 ? " near" : ""}`} title={i === 0 ? (kind === "r" ? "가장 가까운 저항 — 1차 목표가의 근거" : "가장 가까운 지지 — 이 아래가 손절 기준의 근거") : undefined}>
      <span className="sr-k">{kind === "r" ? `저항 ${i + 1}` : `지지 ${i + 1}`}</span><b>{usd(v)}</b><span className="sr-d">{dist(v)}</span>
    </span>
  );
  const me = at(price);
  return (
    <div className="sr-box" data-testid="support-resistance">
      <div className="sr-top">
        <span className="sr-title">지지 · 저항 <span className="sr-info" title={HELP} aria-label={HELP}>?</span></span>
        {(hi || lo) && <span className="caption sr-52">52주 {lo ? `최저 ${usd(lo / f)}` : ""}{hi && lo ? " · " : ""}{hi ? `최고 ${usd(hi / f)}` : ""}</span>}
      </div>
      <div className="sr-track" aria-hidden>
        <i className="sr-rail" />
        {s1 !== undefined && r1 !== undefined && <i className="sr-range" style={{ left: `${at(s1)}%`, width: `${at(r1) - at(s1)}%` }} />}
        {supports.map((v, i) => <i key={`s${v}`} className={`sr-dot s${i === 0 ? " near" : ""}`} style={{ left: `${at(v)}%` }} title={`지지 ${i + 1} ${usd(v)}`} />)}
        {resistances.map((v, i) => <i key={`r${v}`} className={`sr-dot r${i === 0 ? " near" : ""}`} style={{ left: `${at(v)}%` }} title={`저항 ${i + 1} ${usd(v)}`} />)}
        <i className="sr-pin" style={{ left: `${me}%` }} />
        <span className="sr-me" style={{ left: `${Math.min(90, Math.max(10, me))}%` }}>지금 <b>{usd(price)}</b></span>
      </div>
      <div className="sr-chips">
        <div className="sr-side s" aria-label="지지선">{supports.length ? supports.map((v, i) => chip(v, "s", i)) : <span className="sr-none">아래쪽 지지 없음</span>}</div>
        <div className="sr-side r" aria-label="저항선">{resistances.length ? resistances.map((v, i) => chip(v, "r", i)) : <span className="sr-none">위쪽 저항 없음 (신고가 구간)</span>}</div>
      </div>
    </div>
  );
}
