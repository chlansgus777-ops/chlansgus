/** Display formatting. Missing values are always shown as "N/A" (never 0, never an invented value).
 * Amounts are in USD; KRW conversions are auxiliary only (and only when a USD/KRW rate is available). */

export const dash = "N/A";

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** Thousand separators (ko-KR), fixed decimals. */
export function num(v: number | null | undefined, digits = 2): string {
  if (!isNum(v)) return dash;
  return v.toLocaleString("ko-KR", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

/** A share count: whole shares without decimals, fractional shares with up to 4. */
export function shares(v: number | null | undefined): string {
  if (!isNum(v)) return dash;
  return v.toLocaleString("ko-KR", { maximumFractionDigits: 4 });
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

const WD = ["일", "월", "화", "수", "목", "금", "토"];
function short(d: Date, timeZone: string): string {
  const f = new Intl.DateTimeFormat("en-CA", { timeZone, month: "2-digit", day: "2-digit", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false });
  const p = Object.fromEntries(f.formatToParts(d).map((x) => [x.type, x.value]));
  const wd = WD[["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(p.weekday ?? "")] ?? "";
  return `${p.month}-${p.day}(${wd}) ${p.hour === "24" ? "00" : p.hour}:${p.minute}`;
}

/** Compact clock pair for the status bar: New York time and Korea time, labelled. */
export function clockPair(iso: string | null | undefined): { et: string; kst: string } | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return { et: short(d, "America/New_York"), kst: short(d, "Asia/Seoul") };
}

/** "09-25(금) 16:00 ET" — a short US-market timestamp. */
export function stampEt(iso: string | null | undefined): string {
  const c = clockPair(iso);
  return c ? `${c.et} ET` : dash;
}

/** How long ago, from the server's clock: "방금" · "12분 전" · "3시간 전" · "2일 전". */
export function ago(iso: string | null | undefined, nowMs: number): string {
  if (!iso) return dash;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return dash;
  const m = Math.floor((nowMs - t) / 60_000);
  if (m < 1) return "방금";
  if (m < 60) return `${m}분 전`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}시간 전`;
  return `${Math.floor(h / 24)}일 전`;
}

const ERR_CAUSE_KO: [RegExp, string][] = [
  [/\b401\b|\b403\b|Unauthorized|Forbidden|invalid api key|api key/i, "API 키가 거부되었습니다 — 설정에서 키를 확인하세요"],
  [/\b429\b|rate.?limit|too many requests/i, "무료 요청 한도에 걸렸습니다 — 잠시 뒤 자동으로 다시 받습니다"],
  [/ConnectError|Connection refused|getaddrinfo|Name or service|NetworkError|network unreachable/i, "네트워크에 연결하지 못했습니다 — 인터넷 연결을 확인하세요"],
  [/Timeout/i, "응답이 너무 늦었습니다(시간 초과) — 잠시 뒤 다시 받습니다"],
  [/breaker|circuit/i, "연속으로 실패해 잠시 요청을 멈췄습니다 — 몇 분 뒤 다시 시도합니다"],
  [/\b5\d\d\b|server error/i, "공급자 서버 오류 — 잠시 뒤 다시 받습니다"],
];
/** Provider kinds as the screens name them. */
export const KIND_KO: Record<string, string> = {
  macro: "거시 지표", calendar: "일정", news: "뉴스", price: "시세·가격", fundamental: "재무", fundamentals: "재무", analyst: "애널리스트 추정치",
  estimates: "애널리스트 추정치", events: "일정", short_interest: "공매도 잔고", universe: "종목 목록", insider: "내부자 거래", splits: "주식분할",
};
const ERR_KIND_KO = KIND_KO;

/** Only the cause of a provider failure, in words (for a table that already names the provider). */
export function errCauseKo(raw: string | null | undefined): string {
  if (!raw) return "";
  return ERR_CAUSE_KO.find(([re]) => re.test(raw))?.[1] ?? raw;
}

/** A provider failure in words: which data, which provider, what to do — the raw exception text (English class
 * names, tuples) is for the tooltip / logs only. Text that is already a sentence is returned unchanged. */
export function errKo(raw: string | null | undefined): string {
  if (!raw) return "";
  const kind = raw.match(/all (\w+) providers failed/i)?.[1];
  const cause = ERR_CAUSE_KO.find(([re]) => re.test(raw))?.[1];
  if (!kind && !cause) return raw;
  const names = [...new Set([...raw.matchAll(/\('([\w.-]+)'/g)].map((m) => m[1]))];
  return `${kind ? (ERR_KIND_KO[kind] ?? kind) : "데이터"} 공급자${names.length ? `(${names.join(", ")})` : ""}에서 받지 못했습니다 — ${cause ?? "원인은 설정 → 연결 상태에서 확인하세요"}`;
}
