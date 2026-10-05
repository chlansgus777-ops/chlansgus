import { useAllQuotes, useViewQuotes } from "../quotes";

/** 업종 강세·약세: each sector's representative ETF, its move today (from the live quotes, against the previous session's
 * close), strongest first — the same list Glance shows, for the market screen. */
export const SECTOR_ETFS: [string, string][] = [
  ["SOXX", "반도체"], ["XLK", "기술"], ["XLC", "커뮤니케이션"], ["XLY", "경기소비재"], ["XLF", "금융"], ["XLV", "헬스케어"],
  ["XLI", "산업재"], ["XLE", "에너지"], ["XLB", "소재"], ["XLP", "필수소비재"], ["XLU", "유틸리티"], ["XLRE", "부동산"],
];

const signed = (c: number) => `${c > 0 ? "+" : c < 0 ? "−" : ""}${Math.abs(c * 100).toFixed(2)}%`;
const tone = (c: number) => (c > 0.0005 ? "pos" : c < -0.0005 ? "neg" : "muted");

export function SectorStrength() {
  useViewQuotes(SECTOR_ETFS.map(([t]) => t));
  const all = useAllQuotes();
  const rows = SECTOR_ETFS.map(([t, name]) => ({ t, name, c: all.get(t)?.change_pct ?? null }))
    .sort((a, b) => (a.c === null ? 1 : 0) - (b.c === null ? 1 : 0) || (b.c ?? 0) - (a.c ?? 0));
  const known = rows.filter((r) => r.c !== null);
  const max = Math.max(0.005, ...known.map((r) => Math.abs(r.c!)));
  return (
    <section className="card sector-strength" data-testid="sector-strength">
      <div className="ss-head">
        <h3>업종 강세·약세 <span className="caption">오늘 · 업종 대표 ETF 기준</span></h3>
        <span className="caption">{known.length ? `강세 ${known.filter((r) => r.c! > 0).length} · 약세 ${known.filter((r) => r.c! < 0).length}` : "시세 받는 중"}</span>
      </div>
      <ul className="ss-list">
        {rows.map((r) => (
          <li key={r.t} title={`${r.name} 대표 ETF ${r.t}의 오늘 등락(전일 종가 대비)`}>
            <span className="ss-name">{r.name}<small>{r.t}</small></span>
            <span className="ss-bar" aria-hidden>
              {r.c !== null && <i className={tone(r.c)} style={{ width: `${(Math.abs(r.c) / max) * 50}%`, [r.c >= 0 ? "left" : "right"]: "50%" }} />}
            </span>
            <b className={r.c !== null ? tone(r.c) : "muted"}>{r.c !== null ? signed(r.c) : "—"}</b>
          </li>
        ))}
      </ul>
    </section>
  );
}
