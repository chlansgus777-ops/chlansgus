import { Fragment, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { advise, priceZone } from "../advice";
import { api } from "../api";
import { CommitteeView } from "../components/CommitteeView";
import { Action, Bar, Card, Empty, Err, EvidenceChips, FreshnessTable, Loading, Notice, PriceChart, PriceLadder, Quality, StatusBadge, Term } from "../components/ui";
import { useApi } from "../components/useApi";
import { day, krwAux, num, pct, price, stamp, usdWithKo } from "../format";
import { GLOSSARY } from "../glossary";
import { COMPONENT_KO, DATA_TYPE_KO, REGIME_KO, RISK_KO, SESSION_KO, SIZE_KO, STATUS_INFO, VETO_KO, ko } from "../i18n";
import { More } from "../mode";
import type { Analysis, CommitteeResult, Evidence, StockDetail as SD } from "../types";

const RESULT_KO: Record<string, string> = {
  BEAT_AND_RAISE: "예상 상회 + 가이던스 상향", BEAT: "예상 상회", BEAT_WEAK_GUIDE: "예상 상회했으나 가이던스 부진", GUIDE_UP: "예상 부합 + 가이던스 상향",
  INLINE: "예상 부합", GUIDE_DOWN: "예상 부합했으나 가이던스 하향", MISS_STRONG_GUIDE: "예상 하회했으나 가이던스 양호", MISS: "예상 하회", MISS_AND_LOWER: "예상 하회 + 가이던스 하향", UNKNOWN: "판단 불가",
};
const BAR_KO: Record<string, string> = { LOW: "낮음", NORMAL: "보통", HIGH: "높음(서프라이즈 부담)", UNKNOWN: "판단 불가" };
const SCEN_KO: Record<string, string> = { Bull: "강세", Base: "기본", Bear: "약세" };
const FIT_KO: Record<string, string> = { GOOD: "잘 맞음", NEUTRAL: "보통", POOR: "쏠림 주의" };

function Metric({ k, label }: { k: string; label: string }) {
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

/** One instance per ticker: every piece of local state (busy flags, notes, pending AI runs) starts fresh
 * when the ticker changes, so nothing from another stock can be shown on this page. */
export default function StockDetailPage() {
  const { ticker = "" } = useParams();
  return <StockDetail key={ticker.toUpperCase()} ticker={ticker.toUpperCase()} />;
}

/** The AI committee shown must belong to the recommendation version on screen (same ticker and id). */
export function committeeFor(data: SD | null, ticker: string): CommitteeResult | null {
  if (!data || !data.committee) return null;
  if (data.analysis.ticker !== ticker || data.recommendation.ticker !== ticker) return null;
  if (data.committee_recommendation_id != null && data.committee_recommendation_id !== data.recommendation.id) return null;
  if (data.committee.ticker && data.committee.ticker !== ticker) return null;
  return data.committee;
}

export interface LiveStatus { id: number; status: string | null; reason: string | null }

/** "현재 유효" is a statement about NOW: while the page stays open the stored recommendation is re-judged
 * every minute (the server re-checks age, session and the cached quote against the plan). A plain read
 * of the same recommendation — never a new analysis; a newer recommendation id is ignored here. */
export function useLiveStatus(ticker: string, recId: number | undefined, everyMs = 60_000): LiveStatus | null {
  const [live, setLive] = useState<LiveStatus | null>(null);
  useEffect(() => {
    if (recId === undefined) return;
    let alive = true;
    const timer = window.setInterval(() => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      api.get<SD>(`/stocks/${ticker}`).then((x) => {
        if (alive && x.recommendation.id === recId) setLive({ id: recId, status: x.recommendation.current_status ?? null, reason: x.recommendation.current_status_reason ?? null });
      }).catch(() => undefined);
    }, everyMs);
    return () => { alive = false; window.clearInterval(timer); };
  }, [ticker, recId, everyMs]);
  return live && live.id === recId ? live : null;
}

function StockDetail({ ticker }: { ticker: string }) {
  const [refreshTick, setRefreshTick] = useState(0);
  const d = useApi<SD>(`/stocks/${ticker}${refreshTick ? "?refresh=true" : ""}`, [refreshTick]);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const evIndex = useMemo(() => new Map<string, Evidence>((d.data?.analysis.evidence ?? []).map((e) => [e.evidence_id, e])), [d.data]);
  const live = useLiveStatus(ticker, d.data?.recommendation.id);
  if (d.state === "loading") return <Loading what={`${ticker} 분석`} steps={["가격·재무 데이터 확인", "업종 모델 적용", "이슈·거시 반영", "가격 계획 계산"]} />;
  if (!d.data) return <Err error={d.error} retry={d.reload} />;
  if (d.data.analysis.ticker !== ticker) return <Loading what={`${ticker} 분석`} />;  // never render another stock's data
  const { recommendation: stored, analysis: a } = d.data;
  const rec = live ? { ...stored, current_status: live.status, current_status_reason: live.reason } : stored;
  const com = committeeFor(d.data, ticker);
  const comps = a.scorecard.components;
  const positives = comps.flatMap((c) => c.reasons.filter((r) => r.sign > 0)).slice(0, 5);
  const negatives = comps.flatMap((c) => c.reasons.filter((r) => r.sign < 0)).slice(0, 5);
  const usdkrw = a.evidence.find((e) => e.metric === "macro.USDKRW" && typeof e.value === "number")?.value as number | undefined;
  const checks = a.data_quality.checks ?? [];
  const missing = checks.filter((c) => c.quality !== "FRESH" && c.quality !== "DELAYED");
  const unavailableComps = comps.filter((c) => !c.available);
  const e = a.entry;
  const finalAction = com && !["UNAVAILABLE", "SKIPPED"].includes(com.status) ? com.final_action : rec.action;
  const adv = advise({ action: finalAction, price: a.price, maxBuy: e?.max_buy, idealEntry: e?.ideal_entry, stop: e?.stop, rr: e?.rr_at_current, eventRisk: a.event_risk.level, vetoes: a.decision.vetoes, sizeLimit: a.decision.size_limit, status: rec.current_status, sectorKnown: a.sector_known });
  const zone = priceZone({ action: finalAction, price: a.price, maxBuy: e?.max_buy, stop: e?.stop });
  const cautions: string[] = [
    ...a.decision.vetoes.map((v) => `하드 거부권: ${VETO_KO[v] ?? v}`),
    ...(a.event_risk.level === "HIGH" || a.event_risk.level === "EXTREME" ? [`이벤트 위험 ${ko(RISK_KO, a.event_risk.level)}: ${a.event_risk.reasons.join("; ")}`] : []),
    ...negatives.map((r) => r.text),
    ...(a.portfolio_review?.warnings ?? []),
    ...(missing.length ? [`데이터 주의: ${missing.map((c) => DATA_TYPE_KO[c.data_type] ?? c.data_type).join(", ")}`] : []),
  ].slice(0, 6);
  const run = async (label: string, f: () => Promise<void>) => {
    setBusy(label);
    setActionErr(null);
    try { await f(); } catch (err) { setActionErr(err instanceof Error ? err.message : String(err)); } finally { setBusy(null); }
  };
  const closes = (d.data.price_history ?? []).map((p) => p.close);
  return (
    <div className="grid">
      {/* ---------------- hero: the answer first ---------------- */}
      <section className="card hero">
        <div className="row spread" style={{ alignItems: "flex-start" }}>
          <div>
            <div className="caption"><Link to="/stocks">종목 분석</Link> / {a.security.exchange} · {a.sector_known === false ? <span className="warn">업종 분류 불명확</span> : a.security.industry}{a.security.is_adr ? ` · 해외 발행사(${a.security.country_of_incorporation})` : ""}</div>
            <h1 style={{ marginTop: 4 }}>{a.ticker} <span className="t-sub" style={{ fontWeight: 500 }}>{a.security.company_name}</span></h1>
            <div className="row" style={{ marginTop: 6, alignItems: "baseline" }}>
              <span className="t-key">{price(a.price)}</span>
              {usdkrw && a.price !== null && <span className="caption" title={`원/달러 ${num(usdkrw, 1)} 기준 참고 환산`}>{krwAux(a.price, usdkrw)}</span>}
              <span className="tag neutral" title={GLOSSARY.session?.short}>{ko(SESSION_KO, a.session, "세션 정보 없음")}</span>
              <Quality q={a.price_quality} />
            </div>
            <div className="caption">가격 기준 시각 {stamp(a.price_timestamp)} · 출처 {a.price_source ?? "N/A"}</div>
          </div>
          <div className="stack" style={{ alignItems: "flex-end", minWidth: 220 }}>
            <Action a={finalAction} status={rec.current_status} quality={rec.data_quality} lg />
            <div className="row">
              <div className="stat" style={{ alignItems: "flex-end" }}><span className="label"><Term k="score">점수</Term></span><span className="value">{num(rec.score, 1)}<span className="caption">/100</span></span></div>
              <div className="stat" style={{ alignItems: "flex-end" }}><span className="label"><Term k="confidence">분석 신뢰도</Term></span><span className="value">{num(rec.confidence, 0)}<span className="caption">/100 · {confidenceLevel(rec.confidence)}</span></span></div>
            </div>
            <div className="conf-note" data-testid="confidence-note">분석 신뢰도는 주가 상승 확률이 아니라 데이터 완성도·일치도·모델 합의를 나타냅니다.</div>
            <StatusBadge s={rec.current_status} reason={rec.current_status_reason} />
            {(rec.version ?? 1) > 1 && <span className="caption" title={`추천 #${rec.supersedes_id}을 AI 위원회가 검토한 새 버전입니다. 원래 추천은 그대로 보존됩니다.`}>AI 위원회 검토본 v{rec.version} · 발행 {stamp(rec.issued_at ?? null)}</span>}
          </div>
        </div>
        <div className="subtle" style={{ marginTop: 14 }}>
          <b style={{ fontSize: 15 }}>{adv.headline}</b>
          {adv.details.map((t, i) => <div key={i} className="explain">· {t}</div>)}
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="primary" disabled={!!busy} onClick={() => setRefreshTick((t) => t + 1)}>분석 다시하기</button>
          <button disabled={!!busy} onClick={() => run("watch", async () => { await api.post(`/watchlist/${a.ticker}`); setNote("관심종목에 추가했습니다."); })}>관심종목 추가</button>
          <button className="ghost" disabled={!!busy} onClick={() => run("replay", async () => {
            const r = await api.get<{ matches: boolean; replay_score: number; replay_action: string }>(`/recommendations/${rec.id}/replay`);
            setNote(r.matches ? `당시 분석을 저장된 데이터로 다시 계산해도 같은 결과입니다(점수 ${r.replay_score}, ${r.replay_action}).` : `재계산 결과가 다릅니다: 점수 ${r.replay_score}, ${r.replay_action}`);
          })}>당시 분석 다시보기</button>
        </div>
        {note && <div className="explain">{note}</div>}
        <Err error={actionErr} />
      </section>
      {rec.current_status && rec.current_status !== "CURRENT" && <Notice tone="warn">{STATUS_INFO[rec.current_status]?.label}: {rec.current_status_reason}</Notice>}
      {a.mode === "MOCK" && <Notice tone="neg">모의 데이터(MOCK)로 만든 분석입니다. 실제 투자 판단에 사용하지 마세요.</Notice>}
      {finalAction === "DATA INSUFFICIENT" && (
        <Card title="지금은 판단하지 않습니다" icon="◌" tone="warn" testId="data-insufficient">
          <div><b>현재 이 종목은 핵심 데이터가 부족해 신뢰할 만한 매수/매도 판단을 제공하지 않습니다.</b></div>
          <div className="explain">데이터가 없다는 것은 ‘나쁘다’는 뜻이 아닙니다. 부족한 데이터가 채워지면 다시 분석합니다.</div>
          <div className="caption" style={{ marginTop: 8 }}>부족한 데이터</div>
          <div className="missing-list">{missingData(a).map((m) => <span key={m} className="tag warn">{m}</span>)}</div>
        </Card>
      )}

      {/* ---------------- key cards ---------------- */}
      <div className="g3">
        <Card title="매수 계획" icon="⌖" explain={zone.text} tone={zone.tone === "pos" ? "pos" : zone.tone === "neg" ? "neg" : zone.tone === "warn" ? "warn" : undefined}>
          {e && a.price !== null ? (
            <>
              <PriceLadder now={a.price} stop={e.stop} ideal={e.ideal_entry} maxBuy={e.max_buy} t1={e.target1} t2={e.target2} />
              <div className="kv">
                <span className="k"><Term k="ideal_entry" /></span><span>{price(e.ideal_entry)}</span>
                <span className="k"><Term k="max_buy" /></span><span className="warn">{price(e.max_buy)}</span>
                <span className="k"><Term k="add_zone" /></span><span>{price(e.add_zone_low)} – {price(e.add_zone_high)}</span>
                <span className="k"><Term k="stop">손절 기준가</Term></span><span className="neg">{price(e.stop)} ({pct(e.downside_pct)})</span>
                <span className="k"><Term k="target" /></span><span className="pos">{price(e.target1)} ({pct(e.upside_t1_pct)}) / {price(e.target2)}</span>
                <span className="k"><Term k="rr" /></span><span>{num(e.rr_at_current)} <span className="caption">(2 이상이 기준)</span></span>
              </div>
              <div className="caption" style={{ marginTop: 6 }}><Term k="close_exit">종가 기준 이탈</Term>: 종가가 손절 기준가 아래로 마감하면 매도(보유 중)·매수 중단으로 판단합니다. 장중에만 내려간 경우는 신규 매수만 멈춥니다. <Term k="paper_stop">모의투자</Term>는 장중 손절 주문을 가정합니다.</div>
            </>
          ) : <Empty hint="현재가·가격 이력이 부족하거나, 손절가 < 현재가 < 목표가 순서를 만족하는 계획을 만들 수 없을 때 표시하지 않습니다.">지금은 가격 계획을 제시하지 않습니다.</Empty>}
        </Card>
        <Card title="좋은 이유" icon="↑" tone={positives.length ? "pos" : undefined}>
          {positives.length ? <ul className="list">{positives.map((r, i) => <li key={i}><span className="dot pos">+</span><span>{r.text} <EvidenceChips ids={a.reason_evidence[r.text]} index={evIndex} /></span></li>)}</ul> : <Empty>뚜렷한 긍정 요인이 없습니다.</Empty>}
        </Card>
        <Card title="주의할 이유" icon="⚠" tone={cautions.length ? "warn" : undefined}>
          {cautions.length ? <ul className="list">{cautions.map((t, i) => <li key={i}><span className="dot warn">!</span><span>{t}</span></li>)}</ul> : <Empty>눈에 띄는 주의 요인이 없습니다.</Empty>}
        </Card>
      </div>
      <div className="g3">
        <Card title="현재 이슈 영향" icon="▤" explain="뉴스·사건이 이 종목에 주는 영향(−100~+100)">
          <div className="horizon">
            {[["IMMEDIATE", "오늘"], ["SHORT", "1~5일"], ["SWING", "2~6주"]].map(([h, l]) => {
              const v = a.horizon_view[h!];
              const none = v === null || v === undefined;
              return <div key={h} className="h"><div className="caption">{l}</div><div className={`t-key-sm ${none ? "" : v > 0 ? "pos" : v < 0 ? "neg" : ""}`}>{none ? "—" : `${v > 0 ? "▲" : v < 0 ? "▼" : "■"} ${num(v, 1)}`}</div><div className="caption">{none ? "뉴스 수집 실패" : v > 3 ? "긍정적" : v < -3 ? "부정적" : "영향 작음"}</div></div>;
            })}
          </div>
        </Card>
        <Card title="투자 논리가 깨지는 조건" icon="✕">
          <ul className="list">
            {e && <li><span className="dot neg">↓</span><span><b>종가 기준 이탈</b>: 종가가 <b className="neg">{price(e.stop)}</b> 아래로 마감하면 가격 기준으로 매수 근거가 깨집니다.</span></li>}
            {a.thesis_conditions.map((c) => <li key={c.condition_id}><span className={`dot ${a.thesis_breaches.some((b) => b.startsWith(c.description)) ? "neg" : "info"}`}>•</span><span>{c.description}</span></li>)}
            {!e && !a.thesis_conditions.length && <li className="caption">등록된 조건이 없습니다.</li>}
          </ul>
          {a.thesis_invalidated && <Notice tone="neg">이미 투자 논리가 훼손되었습니다: {a.thesis_breaches.join("; ")}</Notice>}
        </Card>
        <Card title="내 포트폴리오에 넣어도 될까?" icon="◔">
          {a.portfolio_review ? (
            <>
              <div className="row"><span className={`tag ${a.portfolio_review.fit === "GOOD" ? "pos" : a.portfolio_review.fit === "POOR" ? "neg" : "warn"}`}>{FIT_KO[a.portfolio_review.fit] ?? a.portfolio_review.fit}</span><span>허용 비중: <b>{ko(SIZE_KO, a.portfolio_review.size_cap)}</b></span></div>
              {a.portfolio_review.warnings.length ? <ul className="list" style={{ marginTop: 8 }}>{a.portfolio_review.warnings.map((w, i) => <li key={i}><span className="dot warn">!</span><span>{w}</span></li>)}</ul> : <div className="explain">업종·테마 쏠림이나 높은 상관관계 문제가 없습니다.</div>}
            </>
          ) : <Empty hint="포트폴리오 화면에서 보유 종목을 입력하면 자동으로 확인합니다.">포트폴리오 정보가 없어 확인하지 않았습니다.</Empty>}
        </Card>
      </div>

      {closes.length > 1 && e && (
        <Card title="가격 흐름과 매수 계획" icon="∿" explain="최근 약 6개월 종가 · 점선은 손절가·최대 매수가·목표가">
          <PriceChart closes={closes} levels={[["손절", e.stop, "var(--neg)"], ["최대 매수", e.max_buy, "var(--warn)"], ["1차 목표", e.target1, "var(--pos)"]]} />
        </Card>
      )}

      <Card title="왜 이런 판단이 나왔나요?" icon="?">
        <ul className="list">{a.decision.reasons.map((r, i) => <li key={i}><span className="dot info">{i + 1}</span><span>{r}</span></li>)}</ul>
        {a.decision.notes.length > 0 && <ul className="list" style={{ marginTop: 8 }}>{a.decision.notes.map((n, i) => <li key={i}><span className="dot warn">!</span><span>{n}</span></li>)}</ul>}
        <details style={{ marginTop: 8 }}><summary>왜 바뀌었나요? (지난 분석과 비교)</summary>
          {a.changes.length ? <ul className="list">{a.changes.map((c, i) => <li key={i} className={c.material ? "" : "caption"}><span className={`dot ${c.material ? "warn" : "info"}`}>{c.material ? "●" : "○"}</span><span>{c.text}</span></li>)}</ul> : <Empty>지난 분석 이후 달라진 점이 없습니다.</Empty>}
        </details>
      </Card>

      <Card title="부족하거나 오래된 데이터" icon="◌" explain="없는 데이터는 다른 값으로 채우지 않고, 판단에서 보수적으로 처리합니다.">
        {missing.length === 0 && unavailableComps.length === 0 ? <div className="explain">판단에 필요한 데이터가 모두 최신이거나 사용 가능한 상태입니다.</div> : (
          <ul className="list">
            {missing.map((c) => <li key={c.data_type}><span className="dot warn">!</span><span><Quality q={c.quality} /> {c.reason_ko}</span></li>)}
            {unavailableComps.map((c) => <li key={c.name}><span className="dot info">i</span><span>{COMPONENT_KO[c.name] ?? c.name}: 데이터가 부족해 중립보다 낮은 보수적 점수를 적용했습니다.</span></li>)}
          </ul>
        )}
      </Card>

      <Card title="AI 투자위원회" icon="◈" explain="7명의 AI 분석가 요약 — 결정론적 판단을 올릴 수는 없고 낮추기만 할 수 있습니다." right={<button disabled={!!busy} onClick={() => run("com", async () => { await api.post<CommitteeResult>(`/recommendations/${rec.id}/committee`); d.reload(); })}>{busy === "com" ? "AI 위원회 분석 중…" : com ? "결과 새로 보기" : "AI 위원회 실행"}</button>}>
        {busy === "com" && <Loading what="AI 위원회" steps={["분석가 7명 의견", "강세·약세 토론 2라운드", "리스크 검토", "근거 수치 검증"]} />}
        {com ? <CommitteeView c={com} evidence={evIndex} /> : <Empty hint="스캔 상위 후보에는 자동으로 실행됩니다. 이 종목은 버튼을 눌러 실행할 수 있습니다.">아직 실행하지 않았습니다.</Empty>}
      </Card>

      {/* ---------------- details: folded in beginner mode ---------------- */}
      <h3>세부 데이터</h3>
      <More title="데이터 종류별 신선도" hint="각 데이터가 언제 기준인지">
        <FreshnessTable checks={checks} />
      </More>
      <More title="점수 구성" hint={`${a.scorecard.model_version} · 업종 모델: ${a.sector_model_name}`}>
        <div className="explain" style={{ marginBottom: 8 }}>업종 모델 선택 이유: {a.sector_model_reason}</div>
        <table><thead><tr><th>구성요소</th><th>점수</th><th style={{ width: "25%" }}></th><th>근거</th></tr></thead>
          <tbody>{comps.map((c) => (
            <tr key={c.name}>
              <td>{COMPONENT_KO[c.name] ?? c.name}{!c.available && <span className="warn"> (데이터 부족 → 보수적)</span>}</td>
              <td>{(c.subscore * c.weight).toFixed(1)} / {c.weight}</td>
              <td><Bar value={c.subscore} /></td>
              <td style={{ whiteSpace: "normal" }}>{c.reasons.map((r) => r.text).join(" · ") || "—"}</td>
            </tr>))}</tbody></table>
      </More>
      <More title="재무 · 밸류에이션 · 실적">
        <div className="g2">
          <div>
            <h2>업종별 핵심 재무 지표</h2>
            {(a.fundamental_rules?.items ?? []).length ? <table><tbody>{(a.fundamental_rules?.items ?? []).map((i) => <tr key={i.metric}><td><Metric k={i.metric} label={i.label} /></td><td>{i.value === null ? <span className="caption">데이터 없음</span> : num(i.value, 3)}</td><td style={{ width: "30%" }}>{i.subscore !== null && <Bar value={i.subscore} />}</td></tr>)}</tbody></table> : <Empty>재무 데이터 없음</Empty>}
          </div>
          <div>
            <h2>밸류에이션 <span className="caption">(가격 기준: {a.valuation_price_basis})</span></h2>
            {(a.valuation_rules?.items ?? []).length ? <table><tbody>{(a.valuation_rules?.items ?? []).map((i) => <tr key={i.metric}><td><Metric k={i.metric} label={i.label} /></td><td>{i.value === null ? <span className="caption">계산 불가</span> : num(i.value, 2)}</td><td style={{ width: "30%" }}>{i.subscore !== null && <Bar value={i.subscore} />}</td></tr>)}</tbody></table> : <Empty>밸류에이션 계산 불가</Empty>}
            {a.relative_valuation && <div className="kv" style={{ marginTop: 8 }}>
              <span className="k">자기 과거 대비</span><span>{a.relative_valuation.history_percentile === null ? "N/A" : `백분위 ${(a.relative_valuation.history_percentile * 100).toFixed(0)}% (높을수록 비쌈)`}</span>
              <span className="k">동종업계 대비</span><span>{pct(a.relative_valuation.premium_to_peers)}</span>
              <span className="k">선행 이익수익률 − 미 10년물</span><span>{pct(a.relative_valuation.equity_risk_spread, 2)}</span>
            </div>}
            <div className="caption">시가총액 {usdWithKo(a.security.market_cap)}</div>
          </div>
          <div>
            <h2><Term k="surprise">실적 (실제 vs 예상)</Term></h2>
            {a.earnings ? <div className="kv">
              <span className="k">결과 판정</span><b>{RESULT_KO[a.earnings.result_quality] ?? a.earnings.result_quality}</b>
              <span className="k">매출 서프라이즈</span><span>{pct(a.earnings.revenue_surprise)}</span>
              <span className="k">EPS 서프라이즈</span><span>{pct(a.earnings.eps_surprise)}</span>
              <span className="k"><Term k="guidance">가이던스 vs 컨센서스</Term></span><span>{pct(a.earnings.guide_rev_vs_cons)}</span>
              <span className="k">시장 기대 수준</span><span>{BAR_KO[a.earnings.expectation_bar] ?? a.earnings.expectation_bar}</span>
            </div> : <Empty>최근 실적 데이터가 없거나 오래되었습니다.</Empty>}
          </div>
          <div>
            <h2><Term k="revision">애널리스트 추정치 변화</Term></h2>
            {a.analyst ? <>
              <div className="kv">
                <span className="k"><Term k="forward_pe">선행 EPS</Term></span><span>{a.analyst["forward_eps"] == null ? "없음" : price(a.analyst["forward_eps"] as number)} <span className="caption">{String(a.analyst["forward_eps_basis"] ?? "")}</span></span>
                {["7d", "30d", "60d", "90d"].map((w) => {
                  const st = ((a.analyst?.["revision_status"] ?? {}) as Record<string, string>)[w] ?? "UNKNOWN";
                  const v = a.analyst?.[`eps_revision_${w}`] as number | null | undefined;
                  const basis = ((a.analyst?.["revision_basis"] ?? {}) as Record<string, string>)[w];
                  return <Fragment key={w}><span className="k">EPS {w.replace("d", "일")} 변화</span><span>{st === "READY" ? pct(v ?? null) : st.startsWith("ACCUMULATING") ? <span className="warn">누적 중 {st.split(" ")[1] ?? ""}</span> : <span className="muted">알 수 없음</span>} {basis && basis !== "NONE" && <span className="caption">({basis === "PROVIDER" ? "공급자 제공" : "자체 누적"})</span>}</span></Fragment>;
                })}
                <span className="k">애널리스트 수</span><span>{String(a.analyst["analyst_count"] ?? "N/A")}</span>
                <span className="k">출처</span><span className="caption">{String(a.analyst["source"] ?? "")}</span>
              </div>
              {typeof a.analyst["cross_check"] === "string" && !(a.analyst["cross_check"] as string).startsWith("SINGLE") && <div className={`caption ${(a.analyst["cross_check"] as string).startsWith("CONSISTENT") ? "" : "warn"}`}>공급자 비교: {a.analyst["cross_check"] as string}</div>}
            </> : <Empty hint="무료 추정치는 최종 후보에만 조회합니다(하루 호출 한도). 없는 값은 점수에서 가산점 없이 보수적으로 처리됩니다.">현재 연결된 데이터 제공자에서 이 종목의 최신 추정치를 받지 못했습니다.</Empty>}
          </div>
        </div>
      </More>
      <More title="거시 · 기술적 흐름 · 이벤트">
        <div className="g3">
          <div>
            <h2>거시 영향</h2>
            <div className="caption">시장 국면: {ko(REGIME_KO, a.primary_regime)} · 순영향 {num(a.macro_impact?.net ?? null)}</div>
            <ul className="list">{(a.macro_impact?.contributions ?? []).map(([f, v, t]) => <li key={f}><span className={`dot ${v > 0 ? "pos" : "neg"}`}>{v > 0 ? "↑" : "↓"}</span><span>{t}</span></li>)}</ul>
          </div>
          <div>
            <h2>기술적 흐름 <span className="caption">(진입 타이밍 참고)</span></h2>
            {a.technicals ? <div className="kv">{[["sma50", "50일 이동평균", "sma"], ["sma200", "200일 이동평균", "sma"], ["rsi14", "RSI(14)", "rsi14"], ["atr14", "ATR(14)", "atr14"], ["rs_6m", "6개월 상대강도", "rs_6m"]].map(([k, l, g]) => <Fragment key={k}><span className="k"><Term k={g!}>{l}</Term></span><span>{num(a.technicals?.[k!] as number | null)}</span></Fragment>)}</div> : <Empty>가격 이력 부족</Empty>}
          </div>
          <div>
            <h2>이벤트 · 옵션</h2>
            <div>이벤트 위험: <b className={a.event_risk.level === "EXTREME" || a.event_risk.level === "HIGH" ? "neg" : ""}>{ko(RISK_KO, a.event_risk.level)}</b></div>
            <ul className="list">{a.upcoming_events.slice(0, 4).map((ev) => <li key={ev.event_id}><span className="dot info">▦</span><span>{day(ev.event_date)} · {ev.title}</span></li>)}</ul>
            {a.options ? <div className="caption"><Term k="iv_rank">IV 순위</Term> {num(a.options["iv_rank"] ?? null)} · <Term k="expected_move">예상 변동폭</Term> ±{pct(a.options["expected_move"] ?? null, 1, false)}</div> : <div className="caption">옵션 데이터 없음(무료 공급원 없음)</div>}
            {a.short_interest_pct != null && <div className="caption"><Term k="short_interest" /> {pct(a.short_interest_pct, 1, false)}</div>}
          </div>
        </div>
      </More>
      <More title="이슈 상세 · 선반영 정도 · 시나리오">
        {a.issue_impacts.length ? <table><thead><tr><th>이슈</th><th>전달 경로</th><th>2~6주 영향</th><th><Term k="priced_in" /></th></tr></thead><tbody>
          {a.issue_impacts.map((i) => { const sw = i.horizons.find((h) => h.horizon === "SWING"); const pi = a.priced_in[i.issue_id]; return <tr key={i.issue_id}><td>{i.issue_id}</td><td>{i.exposure_path.join(" → ")}</td><td className={(sw?.impact_score ?? 0) > 0 ? "pos" : "neg"}>{num(sw?.impact_score ?? null, 1)}</td><td>{pi?.band ? <>{pi.band} <span className="caption">({pi.model === "FULL" ? "옵션 포함" : "Priced-In Lite"} · 신뢰 {pi.confidence_level === "HIGH" ? "높음" : "보통"})</span></> : <span className="muted">반영 정도 추정 제한</span>}</td></tr>; })}
        </tbody></table> : <Empty>이 종목에 영향을 주는 이슈가 없습니다.</Empty>}
        <h2 style={{ marginTop: 14 }}>시나리오 <span className="caption">(확률은 보정 데이터가 쌓이기 전까지 표시하지 않음)</span></h2>
        <table><thead><tr><th>시나리오</th><th>촉발 요인</th><th>가격 범위</th><th>무효화 조건</th></tr></thead>
          <tbody>{a.scenarios.map((s) => <tr key={s.name}><td>{SCEN_KO[s.name] ?? s.name}</td><td style={{ whiteSpace: "normal" }}>{s.trigger}</td><td>{price(s.price_low)} – {price(s.price_high)}</td><td style={{ whiteSpace: "normal" }}>{s.invalidation}</td></tr>)}</tbody></table>
      </More>
      <More title="근거 원본 · 버전 · 추천 이력" hint="감사·재현용">
        {(a.fundamental_adjustments ?? []).length > 0 && <div className="explain" style={{ marginBottom: 8 }}>재무 보정(공시 시점 기준 재작성 반영 · 주식분할 기준 통일): {(a.fundamental_adjustments ?? []).join(" · ")}</div>}
        {a.evidence.filter((x) => x.metric === "earnings.guidance_source").map((x) => <div key={x.evidence_id} className="explain">가이던스 원문(SEC): “{String(x.value)}” {x.source.startsWith("https://www.sec.gov/") && <a href={x.source} target="_blank" rel="noreferrer">원문</a>}</div>)}
        <div className="scroll"><table><thead><tr><th>ID</th><th>항목</th><th>값</th><th>단위</th><th>출처</th><th>시점</th><th>품질</th></tr></thead>
          <tbody>{a.evidence.map((x) => <tr key={x.evidence_id}><td>{x.evidence_id}</td><td>{x.label}</td><td>{typeof x.value === "number" ? num(x.value, 4) : String(x.value)}</td><td>{x.unit ?? ""}</td><td>{x.source}</td><td>{stamp(x.source_ts)}</td><td><Quality q={x.quality} /></td></tr>)}</tbody></table></div>
        <div className="caption">{Object.entries(d.data.versions).map(([k, v]) => `${k}: ${v ?? "N/A"}`).join(" · ")}</div>
        <div className="caption">추천 이력: {d.data.history.map((h) => `${day(h.as_of)} ${h.action} ${h.score.toFixed(0)}`).join(" | ")}</div>
      </More>
    </div>
  );
}
