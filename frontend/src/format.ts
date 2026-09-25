export const dash = "N/A";

export function num(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return dash;
  return v.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function pct(v: number | null | undefined, digits = 1, signed = true): string {
  if (v === null || v === undefined || Number.isNaN(v)) return dash;
  const s = (v * 100).toFixed(digits);
  return `${signed && v > 0 ? "+" : ""}${s}%`;
}

export function price(v: number | null | undefined): string {
  if (v === null || v === undefined) return dash;
  return `$${num(v, 2)}`;
}

export function big(v: number | null | undefined): string {
  if (v === null || v === undefined) return dash;
  const a = Math.abs(v);
  if (a >= 1e12) return `${(v / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  return num(v, 0);
}

/** UTC ISO timestamp → user local time plus the New York market time. */
export function stamp(iso: string | null | undefined): string {
  if (!iso) return dash;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return dash;
  const local = d.toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
  const et = d.toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit" });
  return `${local} (${et} ET)`;
}

export function actionClass(a: string | null | undefined): string {
  return `badge a-${(a ?? "").replace(/ /g, "-")}`;
}
