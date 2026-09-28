import { Fragment, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { advise, planNow, priceZone, quantityShown } from "../advice";
import { api } from "../api";
import { CommitteeSummary, CommitteeView } from "../components/CommitteeView";
import { IRefresh, IStar } from "../components/icons";
import { LivePrice } from "../components/LivePrice";
import { refreshQuoteSubscriptions, useViewQuotes } from "../quotes";
import { PlanChart } from "../components/PlanChart";
import { Action, Bar, Card, Disclosure, Empty, Err, EvidenceChips, FreshnessTable, Loading, Metric, Notice, Quality, Ribbon, ScoreMeter, Section, StaleData, StatePanel, StatusBadge, Stmt, Term, isExpired } from "../components/ui";
import { usePageTime, useStatus } from "../components/status";
import { useApi } from "../components/useApi";
import { ago, day, krwAux, num, pct, price, stamp, stampEt, usdWithKo } from "../format";
import { GLOSSARY } from "../glossary";
import { COMPONENT_KO, DATA_TYPE_KO, REGIME_KO, RISK_KO, SESSION_KO, SIZE_KO, STATUS_INFO, VERDICT_KO, VETO_KO, actionTone, ko } from "../i18n";
import type { Analysis, Brief, BriefItem, CommitteeResult, Evidence, PositionPlan, StockDetail as SD } from "../types";

const RESULT_KO: Record<string, string> = {
  BEAT_AND_RAISE: "예상 상회 + 가이던스 상향", BEAT: "예상 상회", BEAT_WEAK_GUIDE: "예상 상회했으나 가이던스 부진", GUIDE_UP: "예상 부합 + 가이던스 상향",
  INLINE: "예상 부합", GUIDE_DOWN: "예상 부합했으나 가이던스 하향", MISS_STRONG_GUIDE: "예상 하회했으나 가이던스 양호", MISS: "예상 하회", MISS_AND_LOWER: "예상 하회 + 가이던스 하향", UNKNOWN: "판단 불가",
};
const BAR_KO: Record<string, string> = { LOW: "낮음", NORMAL: "보통", HIGH: "높음(서프라이즈 부담)", UNKNOWN: "판단 불가" };
const SCEN_KO: Record<string, string> = { Bull: "강세", Base: "기본", Bear: "약세" };
const FIT_KO: Record<string, string> = { GOOD: "잘 맞음", NEUTRAL: "보통", POOR: "쏠림 주의" };

function Metricish({ k, label }: { k: string; label: string }) {
  return GLOSSARY[k] ? <Term k={k}>{label}</Term> : <>{label}</>;
}

export function confidenceLevel(c: number | null | undefined): string {
  if (c === null || c === undefined) return "알 수 없음";
  return c >= 70 ? "높음" : c >= 50 ? "보통" : "낮음";
}

/** What exactly is missing when no decision is made (vetoes, sector-model gaps, stale/missing inputs). */
export function missingData(a: Analysis): string[] {
  const out: string[] = [];
  for (const rs of [a.fundamental_rules, a.valuation_rules]) {
    if (!rs) continue;
    for (const k of rs.critical_missing ?? []) out.push(`${rs.items.find((i) => i.metric === k)?.label ?? k} (핵심)`);
    if (rs.subscore === null) for (const i of rs.items) if (i.value === null && !(rs.critical_missing ?? []).includes(i.metric)) out.push(i.label);
  }
  for (const c of a.data_quality.checks ?? []) if (c.quality === "MISSING" || c.quality === "STALE" || c.quality === "CONFLICTING") out.push(`${DATA_TYPE_KO[c.data_type] ?? c.data_type} (${c.quality === "STALE" ? "오래됨" : c.quality === "CONFLICTING" ? "충돌" : "없음"})`);
  for (const v of a.decision.vetoes) if (v !== "INSUFFICIENT_MODEL_COVERAGE") out.push(VETO_KO[v] ?? v);
  return [...new Set(out)].slice(0, 16);
}

const usd = (v?: number | null) => (v == null ? "—" : `$${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}`);

/** Dollars and whole shares instead of "소량" — from the entered portfolio value and the configured position sizes. */
function PositionPlanView({ p, shown, why }: { p?: PositionPlan; shown: boolean; why?: string | null }) {
  if (!p) return null;
  if (!p.available) return <div className="subtle caption" style={{ marginTop: 14 }}><b style={{ color: "var(--sub)" }}>매수 금액·수량</b> · {p.reason}</div>;
  // the page's own re-check may have expired the plan after the server sized it: never leave an order-sized quantity up
  if (!shown) return <div className="subtle caption" style={{ marginTop: 14 }} data-testid="position-plan-withheld"><b style={{ color: "var(--sub)" }}>매수 금액·수량</b> · 지금 실행할 수 있는 추천이 아니어서 표시하지 않습니다{why ? ` — ${why}` : ""}</div>;
  const sizeKo: Record<string, string> = { FULL: "기본 비중", HALF: "절반 비중", SMALL: "소량(4분의 1) 비중" };
  return (
    <div className="well" style={{ marginTop: 14 }} aria-label="매수 금액과 수량">
      <div className="kv">
        <span className="k">권장 매수</span><span><b style={{ fontSize: 17 }}>{p.shares}주</b> · 약 {usd(p.amount)} <span className="caption">(포트폴리오 {usd(p.nav)}의 {((p.weight ?? 0) * 100).toFixed(2)}%, {sizeKo[p.size_class ?? ""] ?? p.size_class})</span></span>
        <span className="k">손절 시 예상 손실</span><span className="neg">{p.risk_amount != null ? `${usd(p.risk_amount)} (포트폴리오의 ${((p.risk_pct ?? 0) * 100).toFixed(2)}%)` : "계산 불가"}</span>
      </div>
      {(p.notes ?? []).map((n, i) => <div key={i} className="caption" style={{ marginTop: 6 }}>{n}</div>)}
    </div>
  );
}

export default function StockDetailPage() {
  const { ticker = "" } = useParams();
  return <StockDetail key={ticker.toUpperCase()} ticker={ticker.toUpperCase()} />;
}

/** The AI review shown must belong to the recommendation version on screen (same ticker and id). */
export function committeeFor(data: SD | null, ticker: string): CommitteeResult | null {
  if (!data || !data.committee) return null;
  if (data.analysis.ticker !== ticker || data.recommendation.ticker !== ticker) return null;
  if (data.committee_recommendation_id != null && data.committee_recommendation_id !== data.recommendation.id) return null;
  if (data.committee.ticker && data.committee.ticker !== ticker) return null;
  return data.committee;
}

export interface LiveStatus { id: number; status: string | null; reason: string | null; newer?: number }

/** "현재 유효" is a statement about NOW: while the page stays open the stored recommendation is re-judged every
 * minute (a plain read of the same recommendation — never a new analysis; a newer recommendation id is ignored). */
const LIVE_STATUS_MAX_FAILURES = 3;

export function useLiveStatus(ticker: string, recId: number | undefined, everyMs = 60_000): LiveStatus | null {
  const [live, setLive] = useState<LiveStatus | null>(null);
  useEffect(() => {
    if (recId === undefined) return;
    let alive = true;
    let failures = 0;
    const timer = window.setInterval(() => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      api.get<SD>(`/stocks/${ticker}`).then((x) => {
        if (!alive) return;
        failures = 0;
        if (x.recommendation.id === recId) setLive({ id: recId, status: x.recommendation.current_status ?? null, reason: x.recommendation.current_status_reason ?? null });
        else setLive({ id: recId, status: "SUPERSEDED", reason: `더 최근 분석(#${x.recommendation.id})이 있습니다 — 새로고침하면 최신 분석을 봅니다`, newer: x.recommendation.id });
      }).catch(() => {
        failures += 1;
        if (alive && failures >= LIVE_STATUS_MAX_FAILURES) setLive({ id: recId, status: "UNVERIFIED", reason: `서버에서 상태를 ${failures}번 연속 확인하지 못했습니다 — 실행 전에 새로고침하세요` });
      });
    }, everyMs);
    return () => { alive = false; window.clearInterval(timer); };
  }, [ticker, recId, everyMs]);
  return live && live.id === recId ? live : null;
}

const NO_DATA = "자료 부족";
const lv = (v: number | null | undefined) => (v === null || v === undefined ? NO_DATA : price(v));

/** A brief list with a heading, or the fallback when the backend sent no brief (older API). */
function StmtList({ items, index, empty }: { items: BriefItem[]; index: Map<string, Evidence>; empty: string }) {
  return items.length ? <div className="reading">{items.map((it, i) => <Stmt key={i} it={it} index={index} />)}</div> : <div className="caption" style={{ padding: "12px 2px" }}>{empty}</div>;
}

/** Page order (owner's brief): 1 conclusion · 2 reasons · 3 price plan · 4 risks · 5 invalidation · 6 news, issues
 * and events · 7 my portfolio · 8 detailed data. Every number is the backend's; a missing one reads "자료 부족". */
function StockDetail({ ticker }: { ticker: string }) {
  const [refreshTick, setRefreshTick] = useState(0);
  const d = useApi<SD>(`/stocks/${ticker}${refreshTick ? "?refresh=true" : ""}`, [refreshTick]);
  const st = useStatus();
  const [busy, setBusy] = useState<string | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const evIndex = useMemo(() => new Map<string, Evidence>((d.data?.analysis.evidence ?? []).map((e) => [e.evidence_id, e])), [d.data]);
  const live = useLiveStatus(ticker, d.data?.recommendation.id);
  useViewQuotes([ticker]);  // the viewed stock joins the app-wide quote subscription while this page is open
  const mine = d.data && d.data.analysis.ticker === ticker ? d.data : null;
  usePageTime(mine ? { label: ticker, priceTs: mine.analysis.price_timestamp, priceSession: mine.analysis.session, quality: mine.analysis.price_quality, analysedAt: mine.recommendation.as_of } : null);
  if (d.state === "loading") return <Loading what={`${ticker} 분석`} steps={["가격·재무 데이터 확인", "업종 모델 적용", "이슈·거시 반영", "가격 계획 계산"]} />;
  if (!d.data) return <Err error={d.error} retry={d.reload} />;
  if (d.data.analysis.ticker !== ticker) return <Loading what={`${ticker} 분석`} />;  // never render another stock's data
  const { recommendation: stored, analysis: a } = d.data;
  const brief: Brief | undefined = d.data.brief;
  const rec = live ? { ...stored, current_status: live.status, current_status_reason: live.reason } : stored;
  const com = committeeFor(d.data, ticker);
  const comps = a.scorecard.components;
  const positives = comps.flatMap((c) => c.reasons.filter((r) => r.sign > 0)).slice(0, 5);
  const negatives = comps.flatMap((c) => c.reasons.filter((r) => r.sign < 0)).slice(0, 5);
  const usdkrw = a.evidence.find((e) => e.metric === "macro.USDKRW" && typeof e.value === "number")?.value as number | undefined;
  const checks = a.data_quality.checks ?? [];
  const missing = checks.filter((c) => c.quality !== "FRESH" && c.quality !== "DELAYED");
  const unavailableComps = comps.filter((c) => !c.available);
  // one price basis for the whole page: today's shares (review 2026-09-28 F03); the snapshot's own prices are the record
  const split = typeof stored.split_factor_since === "number" && stored.split_factor_since > 0 ? stored.split_factor_since : 1;
  const priceNow = typeof stored.price === "number" ? stored.price : a.price === null ? null : a.price / split;
  // a stale quote is not a decision input, but it is still the last known price — never blanked (real-time quotes)
  const rawEv = a.evidence.find((x) => x.metric === "price.current" && typeof x.value === "number");
  const rawQuote = rawEv ? (rawEv.value as number) : null;
  const e = planNow(a.entry, stored, a.price);
  const finalAction = com && !["UNAVAILABLE", "SKIPPED"].includes(com.status) ? com.final_action : rec.action;
  const adv = advise({ action: finalAction, price: priceNow, maxBuy: e?.max_buy, idealEntry: e?.ideal_entry, stop: e?.stop, rr: e?.rr_at_current, eventRisk: a.event_risk.level, vetoes: a.decision.vetoes, sizeLimit: a.decision.size_limit, status: rec.current_status, sectorKnown: a.sector_known });
  const zone0 = priceZone({ action: finalAction, price: priceNow, maxBuy: e?.max_buy, stop: e?.stop });
  // the zone is computed from the analysis-time price: once the stored plan is no longer current, say so
  const zone = rec.current_status && rec.current_status !== "CURRENT" && zone0.tone !== "neutral"
    ? { text: `분석 당시 가격 기준 ${zone0.text} — 지금은 ${STATUS_INFO[rec.current_status]?.label ?? "재확인 필요"}`, tone: "warn" as const } : zone0;
  const cautions: string[] = [
    ...a.decision.vetoes.map((v) => `거부권(hard veto): ${VETO_KO[v] ?? v}`),
    ...(a.event_risk.level === "HIGH" || a.event_risk.level === "EXTREME" ? [`이벤트 위험 ${ko(RISK_KO, a.event_risk.level)}: ${a.event_risk.reasons.join("; ")}`] : []),
    ...(brief ? brief.against.map((x) => x.text) : negatives.map((r) => r.text)),
    ...(a.portfolio_review?.warnings ?? []),
    ...(missing.length ? [`데이터 주의: ${missing.map((c) => DATA_TYPE_KO[c.data_type] ?? c.data_type).join(", ")}`] : []),
  ].filter((v, i, arr) => arr.indexOf(v) === i).slice(0, 6);
  const expired = isExpired(finalAction, rec.current_status, rec.data_quality);
  const tone = actionTone(finalAction, expired);
  const run = async (label: string, f: () => Promise<void>) => {
    if (busy) return;  // one action at a time: a second click never starts the same work twice
    setBusy(label);
    setActionErr(null);
    try { await f(); } catch (err) { setActionErr(err instanceof Error ? err.message : String(err)); } finally { setBusy(null); }
  };
  const hist = d.data.price_history ?? [];
  const nowMs = st?.nowMs ?? Date.now();
  const zoneTone = zone.tone === "pos" ? "ok" : zone.tone === "neg" ? "danger" : zone.tone === "warn" ? "warn" : undefined;
  const distStop = e && priceNow ? e.stop / priceNow - 1 : null;
  const distMax = e && priceNow ? e.max_buy / priceNow - 1 : null;
  const aiOff = !!(st?.system.data && !st.system.data.llm.available);
  const triggers = brief?.triggers ?? [];
  return (
    <div className="grid">
      {/* ① 결론 — the answer first, with the numbers that decide it and the single biggest risk */}
      <section className={`hero rail-${tone} enter`} aria-label="결론">
        <div className="eyebrow">
          <span><Link to="/stocks">종목</Link> / {a.security.exchange} · {a.sector_known === false ? <span className="warn">업종 분류 불명확</span> : a.security.industry}{a.security.is_adr ? ` · 해외 발행사(${a.security.country_of_incorporation})` : ""}</span>
          <span className="right">
            {a.mode === "MOCK" && <span className="pill tone-danger">모의 데이터</span>}
            <StatusBadge s={rec.current_status} reason={rec.current_status_reason} />
          </span>
        </div>
        <div className="ident">
          <h1>{a.ticker}</h1><span className="co">{a.security.company_name}</span>
        </div>
        <div className="verdict-row">
          <span className="verdict" data-testid="verdict">{expired ? "지금은 유효하지 않은 매수 신호" : VERDICT_KO[finalAction] ?? finalAction}</span>
          <Action a={finalAction} status={rec.current_status} quality={rec.data_quality} lg />
        </div>
        <p className="headline">{adv.headline}</p>
        {adv.details.length > 0 && <div className="details">{adv.details.map((t, i) => <div key={i}>· {t}</div>)}</div>}
        <div className="metrics">
          {/* the latest quote (app-wide store) and the analysis basis are two different prices, shown apart */}
          <Metric title="현재가" testId="tile-price"
                  value={<LivePrice ticker={a.ticker} size="lg" showTime />}
                  sub={<span className="lp-basis" data-testid="analysis-basis">
                    분석 기준가 {priceNow !== null ? price(priceNow) : rawQuote !== null ? <>{price(rawQuote / split)} <span className="warn">(오래된 시세 — 판단에 미사용)</span></> : NO_DATA}
                    {usdkrw && priceNow !== null ? <span title={`원/달러 ${num(usdkrw, 1)} 기준 참고 환산`}> ({krwAux(priceNow, usdkrw)})</span> : null}
                    {" · "}{stampEt(a.price_timestamp)} · {ko(SESSION_KO, a.session, "세션 정보 없음")} · <Quality q={a.price_quality} />
                    {split !== 1 ? <> · 분할 조정(분석 당시 {price(a.price)})</> : null}
                    {rec.revalidated_price != null && rec.revalidated_price !== priceNow ? <> · 재확인 가격 {price(rec.revalidated_price)}</> : null}
                  </span>} />
          <Metric title={<Term k="max_buy">최대 매수가</Term>} value={e ? price(e.max_buy) : NO_DATA} testId="tile-maxbuy" tone={zoneTone}
                  sub={<>{zone.text}{distMax !== null && zone.tone !== "neutral" ? ` · 현재가 대비 ${pct(distMax)}` : ""}</>} />
          <Metric title={<Term k="stop">손절 기준(종가)</Term>} value={e ? price(e.stop) : NO_DATA} testId="tile-stop"
                  sub={e ? `현재가 대비 ${pct(distStop)} · 논리 철회 조건은 ⑤` : "가격 계획 없음"} />
          <Metric title="가장 큰 위험" text value={cautions[0] ?? "분석이 표시한 부정 요인 없음"} tone={cautions.length ? "warn" : undefined} testId="tile-risk" />
        </div>
        <div className="foot">
          <span style={{ display: "inline-grid", gridTemplateColumns: "auto 150px", gap: 10, alignItems: "center" }}>
            <span><Term k="score">점수</Term> <b style={{ color: "var(--text)" }}>{num(rec.score, 1)}</b>/100</span>
            <ScoreMeter score={rec.score} />
          </span>
          <span><Term k="confidence">분석 신뢰도</Term> <b style={{ color: "var(--text)" }}>{num(rec.confidence, 0)}</b>/100 · {confidenceLevel(rec.confidence)}</span>
          <span className="conf-note" data-testid="confidence-note">점수·신뢰도는 주가 상승 확률이 아니라 규칙 점수와 데이터 완성도·일치도입니다.</span>
        </div>
        <div className="foot" style={{ marginTop: 6 }}>
          <span>분석 {stampEt(rec.as_of)} ({ago(rec.as_of, nowMs)}) · 추천 #{rec.id}</span>
          {(rec.version ?? 1) > 1 && <span title={`추천 #${rec.supersedes_id}을 AI가 검토한 새 버전입니다. 원래 추천은 그대로 보존됩니다.`}>AI 검토본 v{rec.version} · 발행 {stamp(rec.issued_at ?? null)}</span>}
        </div>
        <div className="actions">
          <button className="primary" disabled={!!busy} onClick={() => setRefreshTick((t) => t + 1)}><IRefresh />분석 다시하기</button>
          <button disabled={!!busy} onClick={() => run("watch", async () => { await api.post(`/watchlist/${a.ticker}`); refreshQuoteSubscriptions(); setNote("관심 종목에 추가했습니다. 종목 → 관심 탭에서 볼 수 있습니다."); })}><IStar />관심 종목 추가</button>
          <button className="ghost" disabled={!!busy} onClick={() => run("replay", async () => {
            const r = await api.get<{ matches: boolean; replay_score: number; replay_action: string }>(`/recommendations/${rec.id}/replay`);
            setNote(r.matches ? `당시 분석을 저장된 데이터로 다시 계산해도 같은 결과입니다(점수 ${r.replay_score}, ${r.replay_action}).` : `재계산 결과가 다릅니다: 점수 ${r.replay_score}, ${r.replay_action}`);
          })}>당시 분석 재현</button>
        </div>
        {note && <div className="explain" role="status" style={{ marginTop: 10 }}>{note}</div>}
        <div style={{ marginTop: actionErr ? 10 : 0 }}><Err error={actionErr} /></div>
      </section>

      <StaleData error={d.error} at={d.fetchedAt} retry={d.reload} nowMs={nowMs} />
      {rec.current_status && rec.current_status !== "CURRENT" && (
        <StatePanel kind={rec.current_status === "PLAN_INVALIDATED" ? "out_of_range" : "stale"} title={`${STATUS_INFO[rec.current_status]?.label ?? rec.current_status} — 지금은 이 계획대로 실행하지 마세요`}
                    what={<>{rec.current_status_reason ?? ""} {STATUS_INFO[rec.current_status]?.help ?? ""}</>}
                    actions={<>{live?.newer !== undefined && <button onClick={d.reload}>최신 분석 보기</button>}<button className="primary" disabled={!!busy} onClick={() => setRefreshTick((t) => t + 1)}>분석 다시하기</button></>} />
      )}
      {split !== 1 && a.entry && (
        <Ribbon tone="info" cap="분할 조정" testId="split-adjusted">
          분석 이후 {split > 1 ? `주식분할(1주 → ${num(split, 2)}주)` : `주식병합(${num(1 / split, 2)}주 → 1주)`}이 있어 가격 계획 전체를 현재 주식 수 기준으로 환산해 보여줍니다.
          분석 당시 기록: 최대 매수가 {price(a.entry.max_buy)} · 손절 {price(a.entry.stop)} · 1차 목표 {price(a.entry.target1)}
        </Ribbon>
      )}
      {a.thesis_invalidated && <Ribbon tone="danger" cap="논리 훼손">투자 논리가 이미 깨졌습니다: {a.thesis_breaches.join("; ")}</Ribbon>}
      {finalAction === "DATA INSUFFICIENT" && (
        <Card title="지금은 판단하지 않습니다" tone="warn" testId="data-insufficient">
          <StatePanel kind="insufficient" what={<b>현재 이 종목은 핵심 데이터가 부족해 신뢰할 만한 매수/매도 판단을 제공하지 않습니다.</b>}>
            <div style={{ gridColumn: "2" }}>
              <div className="caption" style={{ marginTop: 8 }}>부족한 데이터</div>
              <div className="row tight" style={{ marginTop: 6 }}>{missingData(a).map((m) => <span key={m} className="pill tone-warn">{m}</span>)}</div>
            </div>
          </StatePanel>
        </Card>
      )}

      {/* ② 판단 이유: what changed and why it matters, then what supports / argues against the call */}
      <Section no={2} title="판단 이유" sub="무엇이 달라졌고, 무엇이 판단을 지지하거나 반대하나 — 문장마다 사실·계산·해석을 표시" />
      <div className="card">
        <div className="card-head"><h3 className="t-card">최근 무엇이 달라졌나</h3><span className="caption">분석 {stampEt(rec.as_of)} 기준</span></div>
        {brief ? <StmtList items={brief.changed} index={evIndex} empty="지난 분석 이후 달라진 점이 없습니다." />
          : a.changes.length ? <ul className="list">{a.changes.map((c, i) => <li key={i} className={c.material ? "" : "caption"}><span className={`dot ${c.material ? "warn" : "info"}`}>{c.material ? "●" : "○"}</span><span>{c.text}</span></li>)}</ul>
          : <Empty>지난 분석 이후 달라진 점이 없습니다.</Empty>}
      </div>
      <div className="split">
        <div className="card">
          <div className="col-head pos"><span className="mark">▲</span>지지하는 근거<span className="n">{(brief?.support ?? positives).length}</span></div>
          {brief ? <StmtList items={brief.support} index={evIndex} empty="판단을 지지하는 근거가 따로 없습니다." />
            : positives.length ? <div className="reading">{positives.map((r, i) => <div key={i} className="stmt tone-pos"><span className="kind k-CALC">계산</span><div className="body">{r.text}<EvidenceChips ids={a.reason_evidence[r.text]} index={evIndex} /></div></div>)}</div>
            : <Empty>뚜렷한 긍정 요인이 없습니다.</Empty>}
        </div>
        <div className="card">
          <div className="col-head neg"><span className="mark">▼</span>반대하는 근거<span className="n">{(brief?.against ?? negatives).length}</span></div>
          {brief ? <StmtList items={brief.against} index={evIndex} empty="점수 모델이 찾은 뚜렷한 반대 근거가 없습니다 — 모델이 다루지 않는 위험(소송·경영진·규제)은 뉴스와 공시로 확인하세요." />
            : negatives.length ? <div className="reading">{negatives.map((r, i) => <div key={i} className="stmt tone-neg"><span className="kind k-CALC">계산</span><div className="body">{r.text}</div></div>)}</div>
            : <Empty>뚜렷한 부정 요인이 없습니다.</Empty>}
        </div>
      </div>
      <Disclosure title="규칙이 이 판단을 내린 과정" hint="점수 구간·가격 조건·거부권을 차례로 확인한 기록">
        <ul className="list">{a.decision.reasons.map((r, i) => <li key={i}><span className="dot info">{i + 1}</span><span>{r}</span></li>)}</ul>
        {a.decision.notes.length > 0 && <ul className="list" style={{ marginTop: 10 }}>{a.decision.notes.map((n, i) => <li key={i}><span className="dot warn">!</span><span>{n}</span></li>)}</ul>}
      </Disclosure>
      <Card title="AI 검토" sub explain="뉴스 원문을 읽은 AI의 추가 의견입니다. 규칙 판단을 올릴 수는 없고, 매수를 낮추거나 규모를 줄이는 것만 할 수 있습니다."
            right={<button className="sm" disabled={!!busy} onClick={() => run("com", async () => { await api.post<CommitteeResult>(`/recommendations/${rec.id}/committee`); d.reload(); })}>{busy === "com" ? <><span className="spin" />AI 검토 중…</> : com ? "다시 검토" : "AI 검토 실행"}</button>}>
        {busy === "com" && <Loading what="AI 검토" steps={["뉴스 원문 읽기", "위험 검토(하향만 가능)", "비중 조언", "근거 수치 대조"]} rows={0} />}
        {com ? (
          <>
            <CommitteeSummary c={com} />
            <div style={{ marginTop: 12 }}><Disclosure title="역할별 의견 전체" hint="근거 ID와 거절된 주장까지" inset><CommitteeView c={com} evidence={evIndex} /></Disclosure></div>
          </>
        ) : aiOff ? <StatePanel kind="ai_unavailable" />
          : <Empty hint="스캔 상위 후보에는 자동으로 실행됩니다. 이 종목은 버튼을 눌러 실행할 수 있습니다.">아직 실행하지 않았습니다.</Empty>}
      </Card>

      {/* ③ 가격 계획: the chart is the centre; below it the levels, how they were computed and the buy amount */}
      <Section no={3} title="가격 계획" sub={zone.text} />
      <div className="card" data-testid="price-plan">
        {hist.length > 1 && e ? (
          <PlanChart data={hist} levels={{ stop: e.stop, maxBuy: e.max_buy, zoneLow: e.acceptable_low, ideal: e.ideal_entry, t1: e.target1, t2: e.target2 }} height={320} />
        ) : hist.length > 1 ? <PlanChart data={hist} levels={{}} height={260} /> : <Empty>가격 이력이 부족해 차트를 그리지 않습니다.</Empty>}
        <div className="divider" />
        <div className="two">
          <div>
            <h3 className="t-card" style={{ marginBottom: 10 }}>계획 수준 <span className="caption" style={{ fontWeight: 500 }}>현재 주식 수 기준 · USD</span></h3>
            <div className="kv">
              <span className="k"><Term k="ideal_entry" /></span><span>{lv(e?.ideal_entry)}</span>
              <span className="k">허용 매수 구간</span><span>{e ? `${price(e.acceptable_low)} ~ ${price(e.acceptable_high)}` : NO_DATA}</span>
              <span className="k"><Term k="max_buy" /></span><span className="warn">{lv(e?.max_buy)}</span>
              <span className="k"><Term k="add_zone">추가 매수 조건</Term></span><span>{e ? `보유 중일 때 ${price(e.add_zone_low)} ~ ${price(e.add_zone_high)}` : NO_DATA}</span>
              <span className="k"><Term k="stop">손절 기준가(종가)</Term></span><span className="danger">{e ? `${price(e.stop)} (${pct(e.downside_pct)})` : NO_DATA}</span>
              <span className="k"><Term k="target">1차 목표</Term></span><span>{e ? `${price(e.target1)} (${pct(e.upside_t1_pct)})` : NO_DATA}</span>
              <span className="k">2차 목표</span><span>{lv(e?.target2)}</span>
              <span className="k"><Term k="rr">손익비(R/R)</Term></span><span>{e ? <>{num(e.rr_at_current)} <span className="caption">현재가 기준 · 이상적 진입가 기준 {num(e.rr_at_ideal)}</span></> : NO_DATA}</span>
            </div>
            {!e && <div className="explain" style={{ marginTop: 10 }}>현재가·가격 이력이 부족하거나, 손절가 &lt; 현재가 &lt; 목표가 순서를 만족하는 계획을 만들 수 없어 가격 계획을 제시하지 않습니다.</div>}
            {brief && (brief.valuation.plan.length > 0 || brief.valuation.assumptions.length > 0) && (
              <div className="stack" style={{ gap: 8, marginTop: 14 }}>
                <div className="t-kicker">이 수준을 정한 방법</div>
                {brief.valuation.plan.map((it, i) => <div key={i} className="formula">{it.text}{it.why ? <div className="caption">{it.why}</div> : null}</div>)}
                {brief.valuation.assumptions.map((t, i) => <div key={i} className="assume"><span className="kind k-ASSUME">가정</span><span>{t}</span></div>)}
              </div>
            )}
            <PositionPlanView p={d.data.position_plan} shown={quantityShown(rec.current_status, stored.actionable_now)} why={rec.current_status_reason} />
          </div>
          <div>
            <h3 className="t-card" style={{ marginBottom: 10 }}>가격은 어떻게 평가했나</h3>
            {brief ? (
              <div className="stack">
                {brief.valuation.items.length ? <div className="reading">{brief.valuation.items.map((it, i) => <Stmt key={i} it={it} index={evIndex} />)}</div> : <div className="caption">밸류에이션 배수를 계산할 자료가 없습니다.</div>}
                {brief.valuation.limits.map((t, i) => <Notice key={i} tone="warn">{t}</Notice>)}
              </div>
            ) : <div className="caption">{e?.rationale.join(" · ") ?? "가격 계획 없음"}</div>}
          </div>
        </div>
        <div className="caption" style={{ marginTop: 14 }}><Term k="close_exit">종가 기준 이탈</Term>: 종가가 손절 기준가 아래로 마감하면 매도(보유 중)·매수 중단으로 판단합니다. 장중에만 내려간 경우는 신규 매수만 멈춥니다. <Term k="paper_stop">모의투자</Term>도 같은 규칙으로 계산합니다. 손익비는 목표 도달 확률이 아닙니다.</div>
      </div>

      {/* ④ 위험 */}
      <Section no={4} title="위험" sub="반대 근거와, 모르는 채로 판단한 것" />
      <div className="split">
        <Card title="주의할 이유" sub tone={cautions.length ? "warn" : undefined}>
          {cautions.length ? <ul className="list">{cautions.map((t, i) => <li key={i}><span className="dot warn">!</span><span>{t}</span></li>)}</ul> : <Empty>눈에 띄는 주의 요인이 없습니다.</Empty>}
        </Card>
        <Card title="모르는 것" sub explain="없는 데이터는 다른 값으로 채우지 않고, 판단에서 보수적으로 처리합니다.">
          {brief && brief.unknowns.length ? (
            <div className="reading">{brief.unknowns.map((u, i) => (
              <div key={i} className="stmt tone-warn"><span className="kind k-VIEW">{u.label}</span><div className="body">{u.text}</div><div className="why">{u.impact}</div>{u.fix ? <div className="meta"><span>해결: {u.fix}</span></div> : null}</div>
            ))}</div>
          ) : missing.length === 0 && unavailableComps.length === 0 ? <div className="explain">판단에 필요한 데이터가 모두 최신이거나 사용 가능한 상태입니다.</div> : (
            <ul className="list">
              {missing.map((c) => <li key={c.data_type}><span className="dot warn">!</span><span><Quality q={c.quality} /> {c.reason_ko}</span></li>)}
              {unavailableComps.map((c) => <li key={c.name}><span className="dot info">i</span><span>{COMPONENT_KO[c.name] ?? c.name}: 데이터가 부족해 중립보다 낮은 보수적 점수를 적용했습니다.</span></li>)}
            </ul>
          )}
        </Card>
      </div>

      {/* ⑤ 판단 철회 조건 — the price stop and the thesis are two different things */}
      <Section no={5} title="판단 철회 조건" sub="이 중 하나가 일어나면 판단을 바꿉니다" />
      <div className="card">
        <div className="split">
          <div>
            <div className="t-kicker" style={{ marginBottom: 8 }}>가격 기준 — 손절</div>
            {e ? <div className="explain" style={{ color: "var(--text-2)" }}><b>종가 기준 이탈</b>: 종가가 <b className="danger">{price(e.stop)}</b> 아래로 마감하면 가격 기준으로 매수 근거가 깨집니다.</div> : <div className="muted">가격 계획이 없어 손절 기준도 없습니다({NO_DATA}).</div>}
            {triggers.filter((t) => t.label !== "투자 논리").length > 0 && <div className="reading" style={{ marginTop: 8 }}>{triggers.filter((t) => t.label !== "투자 논리" && !(t.label === "가격" && t.text.includes("아래로 마감"))).map((it, i) => <Stmt key={i} it={it} />)}</div>}
          </div>
          <div>
            <div className="t-kicker" style={{ marginBottom: 8 }}><Term k="thesis">투자 논리 기준 — 가격과 별개</Term></div>
            {a.thesis_conditions.length ? <ul className="list">{a.thesis_conditions.map((c) => {
              const hit = a.thesis_breaches.some((b) => b.startsWith(c.description));
              const now = triggers.find((t) => t.label === "투자 논리" && t.text.startsWith(c.description));
              return <li key={c.condition_id}><span className={`dot ${hit ? "neg" : "info"}`}>{hit ? "✕" : "•"}</span><span>{now ? now.text : c.description}{hit ? <b className="danger"> — 발생</b> : null}</span></li>;
            })}</ul> : <div className="muted">등록된 조건이 없습니다.</div>}
          </div>
        </div>
        {a.thesis_invalidated && <div style={{ marginTop: 12 }}><Notice tone="neg">이미 투자 논리가 훼손되었습니다: {a.thesis_breaches.join("; ")}</Notice></div>}
      </div>

      {/* ⑥ 뉴스 · 이슈 · 일정 */}
      <Section no={6} title="뉴스 · 이슈 · 일정" />
      <div className="split">
        <Card title="현재 이슈 영향" sub explain="MarketLens가 뉴스·사건을 이 종목에 연결해 계산한 해석(−100~+100)입니다. 기사에 적힌 사실이 아닙니다.">
          <div className="tiles" style={{ gridTemplateColumns: "repeat(3, minmax(0, 1fr))" }}>
            {[["IMMEDIATE", "오늘"], ["SHORT", "1~5일"], ["SWING", "2~6주"]].map(([h, l]) => {
              const v = a.horizon_view[h!];
              const none = v === null || v === undefined;
              return <div key={h} className="tile"><div className="t">{l}</div><div className="v" style={{ fontSize: 18 }}>{none ? "—" : `${v > 0 ? "▲" : v < 0 ? "▼" : "■"} ${num(v, 1)}`}</div><div className="s">{none ? "뉴스 수집 실패" : v > 3 ? "긍정적 해석" : v < -3 ? "부정적 해석" : "영향 작음"}</div></div>;
            })}
          </div>
          {a.issue_impacts.length ? <ul className="list" style={{ marginTop: 12 }}>{a.issue_impacts.slice(0, 4).map((i) => <li key={i.issue_id}><span className="dot info">i</span><span>{(brief?.changed.find((c) => c.label === "이슈" && c.why?.includes(i.exposure_path.join(" → ")))?.text) ?? i.issue_id} <span className="caption">— 연결 경로(시스템 해석): {i.exposure_path.join(" → ")}</span></span></li>)}</ul> : <div className="explain" style={{ marginTop: 10 }}>이 종목에 연결된 이슈가 없습니다.</div>}
          <div className="caption" style={{ marginTop: 10 }}>기사 원문과 사실은 <Link to="/market?tab=issues">시장 → 이슈</Link>에서 볼 수 있습니다.</div>
        </Card>
        <Card title="다가오는 일정" sub explain={`이벤트 위험 ${ko(RISK_KO, a.event_risk.level)}${a.event_risk.days_until != null ? ` · 가장 가까운 일정 ${a.event_risk.days_until}일 후` : ""}`}>
          {a.upcoming_events.length ? <ul className="list">{a.upcoming_events.slice(0, 6).map((ev) => <li key={ev.event_id}><span className="dot info">▦</span><span>{ev.title}<div className="caption">{day(ev.event_date)} (미국 날짜)</div></span></li>)}</ul> : <Empty>등록된 일정이 없습니다.</Empty>}
          {a.event_risk.reasons.length > 0 && <div className="explain" style={{ marginTop: 8 }}>{a.event_risk.reasons.join(" · ")}</div>}
        </Card>
      </div>

      {/* ⑦ 내 포트폴리오와의 관계 */}
      <Section no={7} title="내 포트폴리오와의 관계" />
      <Card title="내 포트폴리오에 넣어도 될까?" sub>
        {a.portfolio_review ? (
          <>
            <div className="row"><span className={`pill tone-${a.portfolio_review.fit === "GOOD" ? "ok" : a.portfolio_review.fit === "POOR" ? "danger" : "warn"}`}>{a.portfolio_review.fit === "GOOD" ? "✓" : "!"} {FIT_KO[a.portfolio_review.fit] ?? a.portfolio_review.fit}</span><span>허용 비중: <b>{ko(SIZE_KO, a.portfolio_review.size_cap)}</b></span>
              <span className="caption">편입 후 이 업종 비중 {pct(a.portfolio_review.candidate_sector_weight_after, 0, false)}{a.portfolio_review.max_correlation ? ` · 가장 비슷하게 움직이는 보유 종목 ${a.portfolio_review.max_correlation[0]} (상관 ${num(a.portfolio_review.max_correlation[1], 2)})` : ""}</span></div>
            {a.portfolio_review.warnings.length ? <ul className="list" style={{ marginTop: 10 }}>{a.portfolio_review.warnings.map((w, i) => <li key={i}><span className="dot warn">!</span><span>{w}</span></li>)}</ul> : <div className="explain" style={{ marginTop: 8 }}>업종·테마 쏠림이나 높은 상관관계 문제가 없습니다.</div>}
            <div className="caption" style={{ marginTop: 8 }}>분석 당시 입력된 포트폴리오 기준입니다. 매수 금액은 ③ 가격 계획 아래에 있습니다.</div>
          </>
        ) : <Empty hint="포트폴리오 화면에서 보유 종목을 입력하면 자동으로 확인합니다.">포트폴리오 정보가 없어 확인하지 않았습니다.</Empty>}
      </Card>

      {/* ⑧ 상세 자료 — folded; open when needed */}
      <Section no={8} title="상세 자료" sub="재무·차트 지표·근거 원본 — 필요할 때 펼치세요" />
      <div>
        <Disclosure title="데이터 종류별 신선도" hint="각 데이터가 언제 기준인지"><FreshnessTable checks={checks} /></Disclosure>
        <Disclosure title="점수 구성" hint={`${a.scorecard.model_version} · 업종 모델: ${a.sector_model_name}`}>
          <div className="explain" style={{ marginBottom: 8 }}>업종 모델 선택 이유: {a.sector_model_reason}</div>
          <div className="scroll"><table><thead><tr><th>구성요소</th><th className="num">점수</th><th style={{ width: "25%" }}></th><th>근거</th></tr></thead>
            <tbody>{comps.map((c) => (
              <tr key={c.name}>
                <td>{COMPONENT_KO[c.name] ?? c.name}{!c.available && <span className="warn"> (데이터 부족 → 보수적)</span>}</td>
                <td className="num">{(c.subscore * c.weight).toFixed(1)} / {c.weight}</td>
                <td><Bar value={c.subscore} /></td>
                <td style={{ whiteSpace: "normal" }}>{c.reasons.map((r) => r.text).join(" · ") || "—"}</td>
              </tr>))}</tbody></table></div>
        </Disclosure>
        <Disclosure title="재무 · 밸류에이션 · 실적">
          <div className="g2">
            <div>
              <h3 className="t-card">업종별 핵심 재무 지표</h3>
              {(a.fundamental_rules?.items ?? []).length ? <table><tbody>{(a.fundamental_rules?.items ?? []).map((i) => <tr key={i.metric}><td><Metricish k={i.metric} label={i.label} /></td><td className="num">{i.value === null ? <span className="caption">데이터 없음</span> : num(i.value, 3)}</td><td style={{ width: "30%" }}>{i.subscore !== null && <Bar value={i.subscore} />}</td></tr>)}</tbody></table> : <Empty>재무 데이터 없음</Empty>}
            </div>
            <div>
              <h3 className="t-card">밸류에이션 <span className="caption">(가격 기준: {a.valuation_price_basis})</span></h3>
              {(a.valuation_rules?.items ?? []).length ? <table><tbody>{(a.valuation_rules?.items ?? []).map((i) => <tr key={i.metric}><td><Metricish k={i.metric} label={i.label} /></td><td className="num">{i.value === null ? <span className="caption">계산 불가</span> : num(i.value, 2)}</td><td style={{ width: "30%" }}>{i.subscore !== null && <Bar value={i.subscore} />}</td></tr>)}</tbody></table> : <Empty>밸류에이션 계산 불가</Empty>}
              {a.relative_valuation && <div className="kv" style={{ marginTop: 10 }}>
                <span className="k">자기 과거 대비</span><span>{a.relative_valuation.history_percentile === null ? "N/A" : `백분위 ${(a.relative_valuation.history_percentile * 100).toFixed(0)}% (높을수록 비쌈)`}</span>
                <span className="k">동종업계 대비</span><span>{pct(a.relative_valuation.premium_to_peers)}</span>
                <span className="k">선행 이익수익률 − 미 10년물</span><span>{pct(a.relative_valuation.equity_risk_spread, 2)}</span>
              </div>}
              <div className="caption" style={{ marginTop: 6 }}>시가총액 {usdWithKo(a.security.market_cap)}</div>
            </div>
            <div>
              <h3 className="t-card"><Term k="surprise">실적 (실제 vs 예상)</Term></h3>
              {a.earnings ? <div className="kv">
                <span className="k">결과 판정</span><b>{RESULT_KO[a.earnings.result_quality] ?? a.earnings.result_quality}</b>
                <span className="k">매출 서프라이즈</span><span>{pct(a.earnings.revenue_surprise)}</span>
                <span className="k">EPS 서프라이즈</span><span>{pct(a.earnings.eps_surprise)}</span>
                <span className="k"><Term k="guidance">가이던스 vs 컨센서스</Term></span><span>{pct(a.earnings.guide_rev_vs_cons)}</span>
                <span className="k">시장 기대 수준</span><span>{BAR_KO[a.earnings.expectation_bar] ?? a.earnings.expectation_bar}</span>
              </div> : <Empty>최근 실적 데이터가 없거나 오래되었습니다.</Empty>}
            </div>
            <div>
              <h3 className="t-card"><Term k="eps_revision">애널리스트 추정치 변화</Term></h3>
              {a.analyst ? <>
                <div className="kv">
                  <span className="k"><Term k="forward_pe">선행 EPS</Term></span><span>{a.analyst["forward_eps"] == null ? "없음" : price(a.analyst["forward_eps"] as number)} <span className="caption">{String(a.analyst["forward_eps_basis"] ?? "")}</span></span>
                  {["7d", "30d", "60d", "90d"].map((w) => {
                    const rs = ((a.analyst?.["revision_status"] ?? {}) as Record<string, string>)[w] ?? "UNKNOWN";
                    const v = a.analyst?.[`eps_revision_${w}`] as number | null | undefined;
                    const basis = ((a.analyst?.["revision_basis"] ?? {}) as Record<string, string>)[w];
                    return <Fragment key={w}><span className="k">EPS {w.replace("d", "일")} 변화</span><span>{rs === "READY" || (rs === "UNKNOWN" && v != null) ? pct(v ?? null) : rs.startsWith("ACCUMULATING") ? <span className="warn">누적 중 {rs.split(" ")[1] ?? ""}</span> : <span className="muted">알 수 없음</span>} {basis && basis !== "NONE" && <span className="caption">({basis === "PROVIDER" ? "공급자 제공" : "자체 누적"})</span>}</span></Fragment>;
                  })}
                  <span className="k">애널리스트 수</span><span>{String(a.analyst["analyst_count"] ?? "N/A")}</span>
                  <span className="k">출처</span><span className="caption">{String(a.analyst["source"] ?? "")}</span>
                </div>
                {typeof a.analyst["cross_check"] === "string" && !(a.analyst["cross_check"] as string).startsWith("SINGLE") && <div className={`caption ${(a.analyst["cross_check"] as string).startsWith("CONSISTENT") ? "" : "warn"}`}>공급자 비교: {a.analyst["cross_check"] as string}</div>}
              </> : <Empty hint="무료 추정치는 최종 후보에만 조회합니다(하루 호출 한도). 없는 값은 점수에서 가산점 없이 보수적으로 처리됩니다.">현재 연결된 데이터 제공자에서 이 종목의 최신 추정치를 받지 못했습니다.</Empty>}
            </div>
          </div>
        </Disclosure>
        <Disclosure title="거시 · 기술적 흐름 · 옵션">
          <div className="g3">
            <div>
              <h3 className="t-card">거시 영향</h3>
              <div className="caption">시장 국면: {ko(REGIME_KO, a.primary_regime)} · 순영향 {num(a.macro_impact?.net ?? null)}</div>
              <ul className="list" style={{ marginTop: 8 }}>{(a.macro_impact?.contributions ?? []).map(([f, v, t]) => <li key={f}><span className={`dot ${v > 0 ? "pos" : "neg"}`}>{v > 0 ? "↑" : "↓"}</span><span>{t}</span></li>)}</ul>
            </div>
            <div>
              <h3 className="t-card">기술적 흐름 <span className="caption">(진입 타이밍 참고)</span></h3>
              {a.technicals ? <div className="kv">{[["sma50", "50일 이동평균", "sma"], ["sma200", "200일 이동평균", "sma"], ["rsi14", "RSI(14)", "rsi14"], ["atr14", "ATR(14)", "atr14"], ["rs_6m", "6개월 상대강도", "rs_6m"]].map(([k, l, g]) => <Fragment key={k}><span className="k"><Term k={g!}>{l}</Term></span><span>{num(a.technicals?.[k!] as number | null)}</span></Fragment>)}</div> : <Empty>가격 이력 부족</Empty>}
            </div>
            <div>
              <h3 className="t-card">옵션 · 공매도</h3>
              {a.options ? <div className="caption"><Term k="iv_rank">IV 순위</Term> {num(a.options["iv_rank"] ?? null)} · <Term k="expected_move">예상 변동폭</Term> ±{pct(a.options["expected_move"] ?? null, 1, false)}</div> : <div className="caption">옵션 데이터 없음(무료 공급원 없음)</div>}
              {a.short_interest_pct != null && <div className="caption"><Term k="short_interest" /> {pct(a.short_interest_pct, 1, false)}</div>}
            </div>
          </div>
        </Disclosure>
        <Disclosure title="이슈 상세 · 선반영 정도 · 시나리오">
          {a.issue_impacts.length ? <div className="scroll"><table><thead><tr><th>이슈</th><th>전달 경로(시스템 해석)</th><th className="num">2~6주 영향</th><th><Term k="priced_in" /></th></tr></thead><tbody>
            {a.issue_impacts.map((i) => { const sw = i.horizons.find((h) => h.horizon === "SWING"); const pi = a.priced_in[i.issue_id]; return <tr key={i.issue_id}><td>{i.issue_id}</td><td>{i.exposure_path.join(" → ")}</td><td className="num">{num(sw?.impact_score ?? null, 1)}</td><td>{pi?.band ? <>{pi.band} <span className="caption">({pi.model === "FULL" ? "옵션 포함" : "Priced-In Lite"} · 신뢰 {pi.confidence_level === "HIGH" ? "높음" : "보통"})</span></> : <span className="muted">반영 정도 추정 제한</span>}</td></tr>; })}
          </tbody></table></div> : <Empty>이 종목에 영향을 주는 이슈가 없습니다.</Empty>}
          <h3 className="t-card" style={{ marginTop: 14 }}>시나리오 <span className="caption" style={{ fontWeight: 500 }}>(확률은 보정 데이터가 쌓이기 전까지 표시하지 않음)</span></h3>
          <div className="scroll"><table><thead><tr><th>시나리오</th><th>촉발 요인</th><th className="num">가격 범위</th><th>무효화 조건</th></tr></thead>
            <tbody>{a.scenarios.map((s) => <tr key={s.name}><td>{SCEN_KO[s.name] ?? s.name}</td><td style={{ whiteSpace: "normal" }}>{s.trigger}</td><td className="num">{price(s.price_low / split)} – {price(s.price_high / split)}</td><td style={{ whiteSpace: "normal" }}>{s.invalidation}</td></tr>)}</tbody></table></div>
        </Disclosure>
        <Disclosure title="근거 원본 · 버전 · 추천 이력" hint="감사·재현용">
          {(a.fundamental_adjustments ?? []).length > 0 && <div className="explain" style={{ marginBottom: 8 }}>재무 보정(공시 시점 기준 재작성 반영 · 주식분할 기준 통일): {(a.fundamental_adjustments ?? []).join(" · ")}</div>}
          {a.evidence.filter((x) => x.metric === "earnings.guidance_source").map((x) => <div key={x.evidence_id} className="explain">가이던스 원문(SEC): “{String(x.value)}” {x.source.startsWith("https://www.sec.gov/") && <a href={x.source} target="_blank" rel="noreferrer">원문</a>}</div>)}
          <div className="scroll" style={{ maxHeight: 420 }}><table><thead><tr><th>ID</th><th>항목</th><th className="num">값</th><th>단위</th><th>출처</th><th>시점</th><th>품질</th></tr></thead>
            <tbody>{a.evidence.map((x) => <tr key={x.evidence_id}><td className="caption">{x.evidence_id}</td><td>{x.label}</td><td className="num">{typeof x.value === "number" ? num(x.value, 4) : String(x.value)}</td><td>{x.unit ?? ""}</td><td>{x.source}</td><td>{stamp(x.source_ts)}</td><td><Quality q={x.quality} /></td></tr>)}</tbody></table></div>
          <div className="caption" style={{ marginTop: 8 }}>{Object.entries(d.data.versions).map(([k, v]) => `${k}: ${v ?? "N/A"}`).join(" · ")}</div>
          <div className="caption">추천 이력: {d.data.history.map((h) => `${day(h.as_of)} ${h.action} ${h.score.toFixed(0)}`).join(" | ")}</div>
        </Disclosure>
      </div>
    </div>
  );
}
