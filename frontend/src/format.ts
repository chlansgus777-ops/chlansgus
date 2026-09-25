/** Display formatting. Missing values are always shown as "N/A" (never 0, never an invented value).
 * Amounts are in USD; KRW conversions are auxiliary only (and only when a USD/KRW rate is available). */

export const dash = "N/A";

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** Thousand separators (ko-KR), fixed decimals. */
export function num(v: number | null | undefined, digits = 2): string {
  if (!isNum(v)) return dash;
  return v.toLocaleString("ko-KR", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function pct(v: number | null | undefined, digits = 1, signed = true): string {
  if (!isNum(v)) return dash;
  const s = (v * 100).toFixed(digits);
  return `${signed && v > 0 ? "+" : ""}${s}%`;
}

/** USD price or amount with two decimals: "$1,234.56". */
export function price(v: number | null | undefined): string {
  if (!isNum(v)) return dash;
  return `${v < 0 ? "-" : ""}$${num(Math.abs(v), 2)}`;
}

/** Large USD amounts: "$2.55T" / "$812.30B" / "$45.10M". */
export function big(v: number | null | undefined): string {
  if (!isNum(v)) return dash;
  const a = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (a >= 1e12) return `${sign}$${(a / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(2)}M`;
  return `${sign}$${num(a, 0)}`;
}

/** Korean units for large numbers: 2.55e12 → "2조 5,500억", 123_400_000 → "1억 2,340만". */
export function koUnits(v: number | null | undefined): string {
  if (!isNum(v)) return dash;
  const sign = v < 0 ? "-" : "";
  let a = Math.round(Math.abs(v));
  if (a < 10_000) return `${sign}${a.toLocaleString("ko-KR")}`;
  const jo = Math.floor(a / 1e12);
  a -= jo * 1e12;
  const eok = Math.floor(a / 1e8);
  a -= eok * 1e8;
  const man = Math.floor(a / 1e4);
  const parts: string[] = [];
  if (jo) parts.push(`${jo.toLocaleString("ko-KR")}조`);
  if (eok) parts.push(`${eok.toLocaleString("ko-KR")}억`);
  if (!jo && man) parts.push(`${man.toLocaleString("ko-KR")}만`);
  return sign + (parts.join(" ") || "0");
}

/** USD amount with a Korean-unit reading: "$2.55T (약 2조 5,500억 달러)". */
export function usdWithKo(v: number | null | undefined): string {
  if (!isNum(v)) return dash;
  return Math.abs(v) >= 1e8 ? `${big(v)} (약 ${koUnits(v)} 달러)` : big(v);
}

/** Auxiliary KRW conversion — only when the USD/KRW rate is known; otherwise nothing is shown. */
export function krwAux(usd: number | null | undefined, usdkrw: number | null | undefined): string {
  if (!isNum(usd) || !isNum(usdkrw) || usdkrw <= 0) return "";
  const krw = usd * usdkrw;
  return Math.abs(krw) >= 1e4 ? `≈ ₩${koUnits(krw)}` : `≈ ₩${num(krw, 0)}`;
}

function parts(d: Date, timeZone: string): string {
  const f = new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
  const p = Object.fromEntries(f.formatToParts(d).map((x) => [x.type, x.value]));
  const hour = p.hour === "24" ? "00" : p.hour;
  return `${p.year}-${p.month}-${p.day} ${hour}:${p.minute}`;
}

/** "2026-09-25 11:00 ET (2026-09-26 00:00 KST)" — US market time first, Korea time alongside. */
export function stamp(iso: string | null | undefined): string {
  if (!iso) return dash;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return dash;
  return `${parts(d, "America/New_York")} ET (${parts(d, "Asia/Seoul")} KST)`;
}

/** Date only ("YYYY-MM-DD"); ISO dates pass through unchanged. */
export function day(iso: string | null | undefined): string {
  if (!iso) return dash;
  return /^\d{4}-\d{2}-\d{2}$/.test(iso) ? iso : stamp(iso).slice(0, 10);
}

export function actionClass(a: string | null | undefined): string {
  return `badge a-${(a ?? "").replace(/ /g, "-")}`;
}
