/** Recently opened stocks. The sidebar search that used this list left with the minimal redesign (owner spec
 * 2026-10-12); the 종목 screen still offers them. */
const TICKER = /^[A-Za-z][A-Za-z0-9.-]{0,9}$/;
const RECENT_KEY = "marketlens.recent";

/** Recently opened stocks (this viewer only, best effort — storage may be unavailable). */
export function recentStocks(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]");
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && TICKER.test(x)).slice(0, 8) : [];
  } catch {
    return [];
  }
}

export function rememberStock(ticker: string) {
  try {
    const t = ticker.toUpperCase();
    localStorage.setItem(RECENT_KEY, JSON.stringify([t, ...recentStocks().filter((x) => x !== t)].slice(0, 8)));
  } catch { /* storage unavailable: nothing to remember */ }
}
