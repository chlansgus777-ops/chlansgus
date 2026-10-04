import { price as usd } from "../format";

/** 이동평균선 위치 (owner 2026-10-05): where the price sits against the 20 / 50 / 200-day lines of the analysis, and
 * which one it is touching. The same touch rule as the backend alert (application/ma_watch.py): within a quarter of the
 * daily ATR of the line, never tighter than 0.5 % of it. A display only — nothing here changes a score or a decision. */
export const MA_LINES: [number, string][] = [[20, "20일선"], [50, "50일선"], [200, "200일선"]];

export function maBand(line: number, atr: number | null | undefined): number {
  return Math.max(atr && atr > 0 ? 0.25 * atr : 0, 0.005 * line);
}

export function maState(price: number, line: number, atr: number | null | undefined): "AT" | "ABOVE" | "BELOW" {
  if (Math.abs(price - line) <= maBand(line, atr)) return "AT";
  return price > line ? "ABOVE" : "BELOW";
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) && v > 0 ? v : null);

/** The backend alerts on names whose newest analysis scores this or more (application/ma_watch.py). */
export const MA_ALERT_MIN_SCORE = 60;

export function MaTouch({ technicals, price, split = 1, score }: {
  technicals: Record<string, unknown> | null | undefined; price: number | null; split?: number; score: number | null | undefined;
}) {
  const alerts = typeof score === "number" && score >= MA_ALERT_MIN_SCORE;
  const f = split > 0 ? split : 1;
  const atr = num(technicals?.atr14) !== null ? num(technicals?.atr14)! / f : null;
  const rows = MA_LINES.map(([n, label]) => {
    const v = num(technicals?.[`sma${n}`]);
    const line = v === null ? null : v / f;
    return { n, label, line, st: line !== null && price !== null ? maState(price, line, atr) : null, d: line !== null && price !== null ? price / line - 1 : null };
  }).filter((r) => r.line !== null);
  if (!rows.length || price === null) return null;
  const touching = rows.filter((r) => r.st === "AT");
  const headline = touching.length
    ? `지금 ${touching.map((r) => r.label).join("·")}에 닿아 있음`
    : rows.every((r) => r.st === "ABOVE") ? "모든 이동평균선 위 (상승 추세)"
    : rows.every((r) => r.st === "BELOW") ? "모든 이동평균선 아래 (하락 추세)"
    : `${rows.filter((r) => r.st === "ABOVE").map((r) => r.label).join("·")} 위 · ${rows.filter((r) => r.st === "BELOW").map((r) => r.label).join("·")} 아래`;
  return (
    <div className="ma-touch" data-testid="ma-touch">
      <div className="ma-head">
        <span className={`ma-headline${touching.length ? " at" : ""}`}>{headline}</span>
        <span className="caption">{alerts ? `점수 ${Math.round(score!)}점 — 선에 닿으면 알림이 옵니다` : `알림은 점수 ${MA_ALERT_MIN_SCORE}점 이상 종목만${typeof score === "number" ? ` (지금 ${Math.round(score)}점)` : ""}`}</span>
      </div>
      <div className="ma-rows">
        {rows.map((r) => (
          <div key={r.n} className={`ma-row ${r.st?.toLowerCase() ?? ""}`}>
            <span className="ma-l">{r.label}</span>
            <span className="ma-v">{usd(r.line)}</span>
            <span className="ma-d">{r.d !== null ? `${r.d > 0 ? "+" : r.d < 0 ? "−" : ""}${Math.abs(r.d * 100).toFixed(1)}%` : ""}</span>
            <span className={`ma-chip ${r.st?.toLowerCase() ?? ""}`}>{r.st === "AT" ? "닿음" : r.st === "ABOVE" ? "위" : "아래"}</span>
          </div>
        ))}
      </div>
      <div className="caption">분석 시점의 일봉 이동평균 · 현재가가 선에서 ATR의 1/4(최소 0.5%) 안이면 ‘닿음’</div>
    </div>
  );
}
