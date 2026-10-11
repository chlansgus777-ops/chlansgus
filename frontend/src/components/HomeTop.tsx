import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { pct, price } from "../format";
import { useAccountLive } from "./AccountLive";
import { RoutineCard, type Routine } from "./RoutineCard";
import { Term } from "./ui";
import { invalidateApi, useApi, useSettling } from "./useApi";

/** The top of home (owner spec 2026-10-11 "UI/UX 및 추천 신뢰도 전면 개선", 안 A): ① 내 현황 ② 이번 달 할 일 (one summary
 * card, at most a quarter of the first screen) ③ 왜 이 방법인가 (past record next to its yardsticks, and the worst case on
 * the same card). Every number carries its unit and what it is measured against; nothing promises a return. */

const tone = (v: number | null | undefined) => (v == null || Math.abs(v) < 1e-9 ? "" : v > 0 ? "pos" : "neg");
const usd0 = (v: number) => `$${Math.round(v).toLocaleString("en-US")}`;
const signed = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${price(Math.abs(v))}`;
/** "2021년 2월" from an ISO day. */
export const ym = (iso: string | null | undefined) => (iso ? `${iso.slice(0, 4)}년 ${Number(iso.slice(5, 7))}월` : "—");
/** Whole months between two ISO days ("3년 9개월"). */
export function span(a: string, b: string): string {
  const m = (Number(b.slice(0, 4)) - Number(a.slice(0, 4))) * 12 + Number(b.slice(5, 7)) - Number(a.slice(5, 7));
  const y = Math.floor(m / 12);
  return y ? `${y}년${m % 12 ? ` ${m % 12}개월` : ""}` : `${m}개월`;
}

// ------------------------------------------------------------------ ① 내 현황
export function MyStatus({ cash, cashEntered }: { cash: number | null; cashEntered: boolean }) {
  const a = useAccountLive();
  const g = useApi<{ amount: number | null }>("/goal");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const t = a?.totals;
  const held = (t?.count ?? 0) > 0;
  const invested = t?.value ?? 0;
  const total = held || cashEntered ? invested + (cashEntered ? cash ?? 0 : 0) : null;
  const goal = g.data?.amount ?? null;
  const progress = total != null && goal ? Math.min(1, total / goal) : null;
  const save = async (amount: number | null) => {
    await api.put("/goal", { amount });
    invalidateApi(["/goal"]);
    g.reload();
    setEditing(false);
  };
  return (
    <section className="hero-card status-card enter" data-testid="my-status" aria-labelledby="my-status-h">
      <div className="hero-kicker" id="my-status-h">내 현황</div>
      {total == null ? (
        <>
          <div className="hero-num muted" data-testid="status-total">—</div>
          <p className="hero-line">아직 내 계좌 정보가 없어요. 가진 주식과 현금을 넣으면 총 자산과 수익률이 여기에 나옵니다.</p>
          <Link to="/portfolio" className="btn primary" data-testid="status-setup">내 계좌 입력하기</Link>
        </>
      ) : (
        <>
          <div className="hero-label">총 자산 <span className="muted">(주식 {price(invested)}{cashEntered ? ` + 현금 ${price(cash)}` : ""})</span></div>
          <div className="hero-num" data-testid="status-total">{price(total)}</div>
          {held && t?.pnl != null ? (
            <div className={`hero-change ${tone(t.pnl)}`} data-testid="status-pnl">
              {t.pnl > 0 ? "▲" : t.pnl < 0 ? "▼" : ""} {signed(t.pnl)} ({pct(t.pnl_rate, 1)})
              <span className="basis">산 가격 대비 · 지금까지</span>
            </div>
          ) : <div className="hero-change"><span className="basis">보유 주식이 없어 수익률이 없습니다</span></div>}
          {held && t?.daily != null && (
            <div className={`hero-sub ${tone(t.daily)}`}>오늘 {signed(t.daily)} ({pct(t.daily_rate, 1)}) <span className="basis">어제 종가 대비</span></div>
          )}
        </>
      )}
      <div className="goal" data-testid="status-goal">
        {editing ? (
          <form className="goal-edit" onSubmit={(e) => { e.preventDefault(); const v = Number(draft.replace(/[$,\s]/g, "")); if (v > 0) void save(v); }}>
            <label><Term k="goal">목표 금액</Term> $<input inputMode="decimal" autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="150000" aria-label="목표 금액(달러)" /></label>
            <button type="submit" className="sm">저장</button>
            <button type="button" className="sm ghost" onClick={() => setEditing(false)}>취소</button>
            {goal != null && <button type="button" className="sm ghost" onClick={() => void save(null)}>목표 지우기</button>}
          </form>
        ) : goal != null ? (
          <>
            <div className="goal-row"><span><Term k="goal">목표</Term> {price(goal)}까지</span><b data-testid="goal-pct">{progress != null ? pct(progress, 0, false) : "—"}</b></div>
            <div className="goal-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress != null ? Math.round(progress * 100) : 0} aria-label="목표 대비 진척도">
              <span style={{ width: `${(progress ?? 0) * 100}%` }} />
            </div>
            <button type="button" className="link-btn" onClick={() => { setDraft(String(goal)); setEditing(true); }}>목표 바꾸기</button>
          </>
        ) : (
          <button type="button" className="link-btn" data-testid="goal-set" onClick={() => { setDraft(""); setEditing(true); }}>＋ 목표 금액 정하기 — 얼마나 왔는지 보여 드려요</button>
        )}
      </div>
    </section>
  );
}

// ------------------------------------------------------------------ ② 이번 달 할 일 (summary)
/** The one action this month in a sentence: what, how much, by when. The list unfolds below on a tap. */
export function routineAction(x: Routine): { what: string; how: string | null } {
  const buys = (x.buy ?? []).filter((b) => b.status === "OK" || b.status === "CASH" || b.status === undefined).filter((b) => b.shares);
  const sells = x.sell ?? [];
  if (x.state !== "ACT" || (!buys.length && !sells.length)) return { what: (x.headline ?? "").replace(/^이번 달 할 일:\s*/, "") || "이번 달 목록을 준비하고 있습니다", how: x.when ?? null };
  const parts = [sells.length ? `${sells.length}종목 팔기` : null, buys.length ? `${buys.length}종목 사기` : null].filter(Boolean);
  const slot = x.account?.slot;
  const how = buys.length ? `${slot != null ? `종목마다 약 ${price(slot)}씩` : "종목마다 같은 금액씩"}${x.funding ? ` · 합계 약 ${price(x.funding.planned)}` : ""}` : null;
  return { what: parts.join(" · "), how };
}

/** "접기" (owner 2026-10-11: between two changes the card has nothing to act on and only takes room): folded per month — the
 * month's list date is stored, so the next month's new list opens by itself. Per device (browser storage). */
const FOLD_KEY = "ml.todoFolded";
function readFold(): string | null { try { return localStorage.getItem(FOLD_KEY); } catch { return null; } }
function writeFold(v: string | null): void { try { if (v) localStorage.setItem(FOLD_KEY, v); else localStorage.removeItem(FOLD_KEY); } catch { /* not stored: folds for this visit only */ } }

export function RoutineSummary() {
  const [open, setOpen] = useState(false);
  const [foldedFor, setFoldedFor] = useState<string | null>(readFold);
  const r = useSettling<Routine>("/routine");
  const x = r.data;
  const act = x ? routineAction(x) : null;
  const clash = new Set((x?.conflicts ?? []).map((c) => c.ticker)).size;  // a stop or a take-profit of my own rules must not hide behind the fold
  const month = x?.rebalance_day ?? x?.next_rebalance ?? "-";
  const folded = !!x && foldedFor === month;
  const next = x?.next_rebalance ? `${Number(x.next_rebalance.slice(5, 7))}월 ${Number(x.next_rebalance.slice(8, 10))}일` : null;
  const fold = (on: boolean) => { const v = on ? month : null; writeFold(v); setFoldedFor(v); if (on) setOpen(false); };
  if (folded) return (
    <section className="todo-folded enter" data-testid="routine-folded" aria-label="이번 달 할 일 (접힘)">
      <span className="t">이번 달 할 일</span>
      <span className="muted">{next ? `다음 목록 ${next}${x?.days_to_next != null ? ` (${x.days_to_next}일 남음)` : ""}` : "접어 두었습니다"}</span>
      {clash > 0 && <span className="todo-clash" data-testid="todo-clash">⚠ {clash}종목은 내 규칙과 엇갈림</span>}
      <button type="button" className="sm ghost" data-testid="routine-unfold-card" onClick={() => fold(false)}>펼치기 ▾</button>
    </section>
  );
  return (
    <>
      <section className="hero-card todo-card enter" data-testid="routine-summary" aria-labelledby="todo-h">
        <div className="hero-kicker" id="todo-h">이번 달 할 일
          <span className="row tight">{x?.days_to_next != null ? <span className="dday">다음 교체까지 {x.days_to_next}일</span> : null}
            <button type="button" className="sm ghost todo-fold" data-testid="routine-fold" title="다음 달 목록이 나올 때까지 한 줄로 접어 둡니다" onClick={() => fold(true)}>접기 ▴</button></span>
        </div>
        <div className="todo-what" data-testid="todo-what">{act ? act.what : r.error ? "이번 달 목록을 불러오지 못했습니다" : "불러오는 중…"}</div>
        {act?.how && <div className="todo-how">{act.how}</div>}
        <div className="todo-why">
          <Term k="routine_rule">왜 이 종목들?</Term> 지난 1년 가장 많이 오른 큰 회사 20곳<span className="todo-next">{next ? ` · 다음 목록 ${next}` : ""}</span>
        </div>
        <div className="todo-foot">
          <button type="button" className="sm" data-testid="routine-toggle" aria-expanded={open} onClick={() => setOpen((o) => !o)}>{open ? "목록 접기 ▴" : "목록 펼치기 ▾"}</button>
          {clash > 0
            ? <span className="todo-clash" data-testid="todo-clash">⚠ {clash}종목은 내 규칙과 엇갈림</span>
            : <span className="muted">주문은 증권사 앱에서 직접 하세요</span>}
        </div>
      </section>
      {open && <div className="routine-unfold" data-testid="routine-unfold"><RoutineCard /></div>}
    </>
  );
}

// ------------------------------------------------------------------ ③ 왜 이 방법인가 (evidence)
export interface Curve { cagr: number; total_return: number; volatility: number; max_drawdown: number; by_year: Record<string, number>;
  worst_12m: { return: number; start: string; end: string } | null; drawdown: { max_drawdown: number; peak: string; bottom: string; recovered: string | null } | null;
  monthly?: [string, number][] }
export interface Evidence {
  available: boolean; reason?: string; note?: string; period?: { start: string; end: string };
  routine?: Curve & { trades: number }; spy?: Curve; deposit?: Curve & { series: string };
  random?: { seeds: number; cagr: { p10: number; median: number; p90: number; min: number; max: number }; max_drawdown_median: number; routine_beats_share: number; median_run: Curve };
  source?: { workflow_run: string; data_sha256: string; cost: number; commit: string };
}

export function useEvidence() {
  return useApi<Evidence>("/routine/evidence");
}

/** One row of the comparison: name, a bar for the yearly return on a shared scale, the worst fall. */
function Row({ label, term, c, best, scale, testId, note }: { label: string; term: string; c: Curve; best?: boolean; scale: number; testId: string; note?: string }) {
  return (
    <div className={`ev-row${best ? " best" : ""}`} data-testid={testId}>
      <div className="ev-name"><Term k={term}>{label}</Term>{note && <span className="muted"> {note}</span>}</div>
      <div className="ev-bar" aria-hidden><span className={c.cagr >= 0 ? "up" : "down"} style={{ width: `${Math.max(2, (Math.abs(c.cagr) / scale) * 100)}%` }} /></div>
      <div className={`ev-cagr ${tone(c.cagr)}`}>{pct(c.cagr, 1)}<small>/년</small></div>
      <div className={`ev-mdd ${c.max_drawdown < -0.005 ? "neg" : ""}`}>{c.max_drawdown < -0.005 ? pct(c.max_drawdown, 0) : "거의 0%"}</div>
    </div>
  );
}

export function EvidenceTable({ e }: { e: Evidence }) {
  const r = e.routine!, s = e.spy!, d = e.deposit!, rnd = e.random!;
  const scale = Math.max(r.cagr, s.cagr, d.cagr, rnd.median_run.cagr);
  return (
    <div className="ev-table" role="table" aria-label="방법별 과거 성적">
      <div className="ev-row head" role="row"><div>방법</div><div /><div><Term k="cagr">연평균</Term></div><div><Term k="worst_fall">최대 하락</Term></div></div>
      <Row label="이번 달 루틴" term="routine_rule" c={r} best scale={scale} testId="ev-routine" />
      <Row label="S&P 500 그냥 보유" term="sp500_hold" c={s} scale={scale} testId="ev-spy" />
      <Row label="무작위로 고르기" term="random_bench" c={rnd.median_run} scale={scale} testId="ev-random" note={`${rnd.seeds}번 중 가운데`} />
      <Row label="달러 예금" term="deposit_bench" c={d} scale={scale} testId="ev-deposit" />
    </div>
  );
}

/** The worst stretch, in words, next to the market's worst over the same years. */
export function worstLine(e: Evidence): string {
  const dd = e.routine!.drawdown!;
  const back = dd.recovered ? `, 원래 금액을 되찾기까지 ${span(dd.peak, dd.recovered)} 걸렸어요` : ", 아직 원래 금액을 되찾지 못했어요";
  return `가장 나빴을 때 ${ym(dd.peak)}부터 ${ym(dd.bottom)}까지 계좌가 ${pct(dd.max_drawdown, 0)} 줄었고${back}. S&P 500이 같은 10년 동안 가장 많이 떨어졌을 때는 ${pct(e.spy!.max_drawdown, 0)}(${ym(e.spy!.drawdown?.peak)}~${ym(e.spy!.drawdown?.bottom)})였습니다.`;
}

/** How the routine stood against the 50 coin-picked runs — said plainly whether it beat all, most or few of them. */
export function randomLine(e: Evidence): string {
  const n = e.random!.seeds;
  const k = Math.round(e.random!.routine_beats_share * n);
  const who = k === n ? `${n}번 모두` : `${n}번 중 ${k}번`;
  return `${k >= n / 2 ? "✓" : "✗"} 같은 규칙으로 종목만 제비뽑기한 ${who}보다 연평균이 높았어요 (제비뽑기 가운데 ${pct(e.random!.median_run.cagr, 1)}, 최고 ${pct(e.random!.cagr.max, 1)}).`;
}

export function EvidenceCard() {
  const q = useEvidence();
  const e = q.data;
  const ok = !!e?.available && !!e.routine && !!e.spy && !!e.deposit && !!e.random;
  const years = e?.period ? `${e.period.start.slice(0, 4)}년 ${Number(e.period.start.slice(5, 7))}월 ~ ${e.period.end.slice(0, 4)}년 ${Number(e.period.end.slice(5, 7))}월` : "";
  return (
    <section className="card ev-card enter" data-testid="evidence" aria-labelledby="ev-h">
      <div className="card-head">
        <div style={{ minWidth: 0 }}>
          <h2 className="t-card" id="ev-h">왜 이 방법인가요? 과거 성적표</h2>
          <div className="explain">이 루틴을 {years || "과거"} 동안 그대로 따랐다고 계산한 결과입니다 (<Term k="backtest">과거 시뮬레이션</Term>). 실제로 번 돈이 아닙니다.</div>
        </div>
        <Link to="/evidence" className="row tight" data-testid="ev-more">계산 방법·연도별 보기 →</Link>
      </div>
      {!e ? <div className="caption">{q.error ? "과거 성적을 불러오지 못했습니다." : "불러오는 중…"}</div>
        : !ok ? <div className="caption">{e.reason ?? "과거 성적이 아직 없습니다."}</div>
        : (
          <>
            <EvidenceTable e={e} />
            <div className="ev-points">
              <div data-testid="ev-random-note">{randomLine(e)}</div>
              <div>✓ 처음에 $1,000을 넣었다면 지금 약 {usd0(1000 * (1 + e.routine!.total_return))}입니다 (S&P 500은 약 {usd0(1000 * (1 + e.spy!.total_return))}). 거래 수수료는 뺐고, 세금과 환전 비용은 반영하지 않은 값입니다.</div>
            </div>
            <div className="ev-risk" role="note" data-testid="ev-risk">
              <b>⚠ 최악의 경우도 꼭 보세요.</b> {worstLine(e)} 1년 사이 가장 크게 잃은 때는 {pct(e.routine!.worst_12m?.return, 0)}였습니다. <Term k="swing">출렁임 크기</Term>는 S&P 500의 약 {(e.routine!.volatility / e.spy!.volatility).toFixed(1)}배입니다.
            </div>
            <div className="caption ev-disclaim">과거 결과가 앞으로도 그렇다는 보장은 없습니다. 앱 자체 검증 기준 6개 중 2개(출렁임 대비 수익, 몇몇 종목에 기댄 정도)를 통과하지 못한 규칙입니다.</div>
          </>
        )}
    </section>
  );
}
