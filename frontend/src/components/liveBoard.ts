import { useMemo } from "react";
import { useApi, usePoll } from "./useApi";
import { ACTION_INFO } from "../i18n";
import type { OppRow } from "../types";

/** The list's live re-judgements (GET /opportunities/live, from memory): each pooled analysis again with the live price,
 * every second (owner 2026-09-29: "시장 스캔 없이, 가만히 둬도 1초마다 실시간으로"). */
export type LiveJudgement = {
  current_status?: string; current_status_reason?: string; actionable_now?: boolean; review_reason?: string | null; checked_at?: string;
  at: string; quote_ts: string; price: number | null; source: string; session: string; price_quality?: string; action: string; score: number; data_quality: string;
  vetoes: string[]; max_buy: number | null; ideal_entry: number | null; stop: number | null; target1: number | null; target2: number | null;
  rr: number | null; downside: number | null; buy_zone_low: number | null; buy_zone_high: number | null;
};
export type LiveBoard = { at: string; every_s: number; rows: Record<string, LiveJudgement> };

const BULLISH = new Set(["BUY", "BUY SMALL", "ADD"]);
const INSUFFICIENT = "DATA INSUFFICIENT";

/** One stored row with its live re-judgement over it (the same fields the server's /opportunities overlay sets). */
export function applyLive(r: OppRow, j: LiveJudgement | undefined): OppRow {
  if (!j || (r.live_at && r.live_at > j.at)) return r;
  const bullish = BULLISH.has(j.action);
  return {
    ...r, action: j.action, action_ko: ACTION_INFO[j.action]?.label ?? j.action, score: j.score, price: j.price, price_timestamp: j.quote_ts,
    price_source: j.source, session: j.session, price_quality: j.price_quality ?? r.price_quality, data_quality: j.data_quality, vetoes: j.vetoes, max_buy: j.max_buy, ideal_entry: j.ideal_entry,
    stop: j.stop, target: j.target1, target2: j.target2, rr: j.rr, downside: j.downside, buy_zone_low: j.buy_zone_low, buy_zone_high: j.buy_zone_high,
    actionable_now: bullish ? j.actionable_now === true : null,
    current_status: j.current_status ?? "NEEDS_REVALIDATION", current_status_reason: j.review_reason || j.current_status_reason || "현재가 재확인 필요", live_at: j.at,
  };
}

/** Judged rows by the live score, data-insufficient last; the rank is the live order. */
export function liveOrder(rows: OppRow[]): OppRow[] {
  const out = [...rows].sort((a, b) => Number(a.action === INSUFFICIENT) - Number(b.action === INSUFFICIENT) || (b.score ?? 0) - (a.score ?? 0) || (a.rank ?? 0) - (b.rank ?? 0));
  return out.map((r, i) => (r.rank === i + 1 ? r : { ...r, rank: i + 1 }));
}

/** ``rows`` with the live re-judgements applied every second, in the live order. */
export function useLiveRows(rows: OppRow[] | undefined): { rows: OppRow[]; at: string | null } {
  const b = useApi<LiveBoard>(rows && rows.length ? "/opportunities/live" : null);
  usePoll(b.reload, 1_000, !!rows?.length);
  const board = b.data;
  const merged = useMemo(() => liveOrder((rows ?? []).map((r) => {
    const merged = applyLive(r, board?.rows && !Array.isArray(board.rows) ? board.rows[String(r.id)] : undefined);
    return b.error ? { ...merged, actionable_now: false, current_status: "UNVERIFIED", current_status_reason: "서버 연결을 확인할 수 없습니다. 마지막 결과를 표시합니다." } : merged;
  })), [rows, board, b.error]);
  // "live" only when the server really re-judged something (a live price arrived), never just because it answered
  return { rows: merged, at: board && Object.keys(board.rows ?? {}).length ? board.at : null };
}
