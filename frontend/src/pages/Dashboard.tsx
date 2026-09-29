import { Fragment } from "react";
import { Link } from "react-router-dom";
import { IArrow, IDownload, IEvent, IPerf, IPortfolio, IShield, IStar } from "../components/icons";
import { Action, Card, Change, Empty, Err, LineChart, Loading, Notice, ScoreMeter, StaleData, StatePanel, StatusBadge, Term } from "../components/ui";
import { NotReady, ReadinessBanner, SyncControl, type ReadinessInfo } from "../components/Readiness";
import { type ScanStatus, usePageTime, useStatus } from "../components/status";
import { useApi, usePoll } from "../components/useApi";
import { LivePrice } from "../components/LivePrice";
import { useJudgedRows, useQuote, useViewQuotes, type QuoteRow } from "../quotes";
import { LiveZone } from "../components/LiveZone";
import { useLiveRows } from "../components/liveBoard";
import { MorningBriefing } from "../components/Briefing";
import { ago, day, num, pct, price, stampEt, errKo } from "../format";
import { ACTION_PLAIN, BULLISH, HEALTH_KO, REGIME_KO, RISK_KO, SESSION_KO, VETO_KO, actionTone, ko } from "../i18n";
import type { OppRow, ScanInfo } from "../types";

export type { ScanStatus } from "../components/status";

interface Dash {
  scan: ScanInfo | null;
  regime: { primary: string; readings: { regime: string; score: number; confidence: number; evidence: string[] }[] };
  top_opportunities: OppRow[];
  major_risks: { ticker: string; text: string }[];
  upcoming_catalysts: { event_id: string; title: string; event_date: string; days_until: number; importance: number }[];
  portfolio: { holdings: number; cash: number; cash_entered?: boolean };
  provider_health: { name: string; kind: string; status: string }[];
  performance: { equity: number; starting_capital: number; return: number | null; max_drawdown: number | null; as_of: string; curve: number[] } | null;
  recommendation_changes: { ticker: string; text: string; action: string }[];
  watchlist_alerts: { ticker: string; level: string; text: string }[];
  readiness?: ReadinessInfo;
  /** outside sections: loaded or not, when fetched, whether a background refresh runs */
  regime_status?: SectionStatus;
  catalysts_status?: SectionStatus;
}
interface SectionStatus { available?: boolean; pending?: boolean; reason?: string | null; fetched_at?: string | null; refreshing?: boolean; refresh_error?: string | null }

const REGIME_HELP: Record<string, string> = {
  "Risk On": "투자자들이 위험을 감수하는 분위기 — 성장주·경기민감주에 우호적",
  "Risk Off": "투자자들이 위험을 피하는 분위기 — 방어주·현금 선호",
  "AI Momentum": "AI 관련 종목이 상대적으로 강한 환경",
  "Inflation Shock": "물가 상승 압력이 커 금리·밸류에이션에 부담",
  "Growth Scare": "경기 둔화 우려가 커진 환경",
  "Credit Stress": "회사채 금리차가 벌어지며 자금 조달 환경이 나빠짐",
  "Multiple Compression": "금리 상승 등으로 주가 배수가 낮아지는 환경",
  "Multiple Expansion": "금리 하락 등으로 주가 배수가 높아지는 환경",
  Neutral: "뚜렷한 방향 없이 종목별로 움직이는 환경",
  Unknown: "거시 지표를 받지 못해 시장 국면을 판단하지 않았습니다",
};
const MAX_CARDS = 5;
const BAD_QUALITY = new Set(["STALE", "MISSING", "CONFLICTING"]);

/** A candidate card may only show a recommendation that is bullish, re-judged as current and built on usable data.
 * Everything else is listed separately — an old or out-of-range BUY never looks like a fresh one. */
export function splitCandidates(rows: OppRow[]): { valid: OppRow[]; notValid: OppRow[] } {
  const bullish = rows.filter((r) => BULLISH.has(r.action));
  const ok = (r: OppRow) => r.actionable_now !== false && (r.current_status ?? "CURRENT") === "CURRENT" && !BAD_QUALITY.has(r.data_quality) && !BAD_QUALITY.has(r.price_quality);
  return { valid: bullish.filter(ok), notValid: bullish.filter((r) => !ok(r)) };
}

export function riskLine(r: OppRow): string {
  const k = r.key_risk;
  if (r.vetoes.length) return `거부권: ${r.vetoes.map((v) => VETO_KO[v] ?? v).join(", ")}`;
  if (!k) return r.key_risk === null ? "분석이 표시한 부정 요인 없음" : "자료 부족 — 상세 화면에서 확인";
  if (k.kind === "veto") return `거부권: ${VETO_KO[k.code ?? ""] ?? k.code}`;
  if (k.kind === "event") return `이벤트 위험 ${ko(RISK_KO, k.code)}${k.text ? `: ${k.text}` : ""}`;
  return k.text ?? "자료 부족";
}

/** The latest quote once the app has one; until then the analysis price (labelled below as 분석 가격). */
function CardPrice({ r }: { r: OppRow }) {
  const { row } = useQuote(r.ticker);
  return row?.price != null ? <LivePrice ticker={r.ticker} size="sm" showState={false} /> : <span style={{ whiteSpace: "nowrap" }}>{price(r.price)}</span>;
}

function CandidateCard({ r, lead }: { r: OppRow; lead?: boolean }) {
  const plain = ACTION_PLAIN[r.action] ?? "";
  const zone = r.ideal_entry != null && r.max_buy != null ? `${price(r.ideal_entry)}–${num(r.max_buy, 2)}` : "자료 부족";  // one line in the tile
  const toMax = r.price != null && r.max_buy != null ? r.max_buy / r.price - 1 : null;
  return (
    <Link to={`/stocks/${r.ticker}`} className={`cand${lead ? " lead" : ""}`} style={{ ["--rail" as string]: `var(--${actionTone(r.action)})` }} data-testid="candidate-card" aria-label={`${r.ticker} 상세 보기`}>
      <div className="top">
        <div style={{ minWidth: 0 }}><div className="tk">{r.ticker}</div><div className="co">{r.company} · {r.sector_known === false ? "업종 불명확" : r.sector}</div></div>
        <span className="stack-tight" style={{ alignItems: "flex-end" }}><Action a={r.action} status={r.current_status} quality={r.data_quality} /><LiveZone ticker={r.ticker} recId={r.id} compact /></span>
      </div>
      <div className="why">{plain}</div>
      {r.key_reason ? <div className="why"><b>핵심 이유 · </b>{r.key_reason}</div> : null}
      <div className="facts">
        <div title="최신 시세(앱 공용 스트림) — 아래 ‘분석 가격’과 다를 수 있습니다"><div className="t">현재가</div><div className="v"><CardPrice r={r} /></div></div>
        <div title="이상적 진입가 ~ 최대 매수가 (백엔드 가격 계획)"><div className="t">검토 가격대</div><div className="v" style={{ fontSize: 13, whiteSpace: "nowrap" }}>{zone}</div></div>
        <div title="종가가 이 가격 아래로 마감하면 매수 근거가 깨졌다고 봅니다"><div className="t">손절 기준</div><div className="v">{r.stop == null ? "자료 부족" : price(r.stop)}</div></div>
      </div>
      <div className="when">분석 가격 {price(r.price)} · {stampEt(r.price_timestamp)}{r.session ? ` · ${ko(SESSION_KO, r.session)}` : ""} · 손절까지 {r.downside == null ? "자료 부족" : pct(r.downside)}{toMax != null ? ` · 최대 매수가까지 ${pct(toMax)}` : ""}</div>
      <div className="risk"><span aria-hidden>⚠</span><span><b>가장 큰 위험 · </b>{riskLine(r)}</span></div>
      <div style={{ display: "grid", gridTemplateColumns: "auto minmax(0, 1fr)", gap: 10, alignItems: "center" }}>
        <span className="when" style={{ whiteSpace: "nowrap" }}>점수 <b style={{ color: "var(--text)" }}>{num(r.score, 1)}</b></span>
        <ScoreMeter score={r.score} />
      </div>
      <div className="when">분석 신뢰도 {num(r.confidence, 0)}/100 · 손익비 {num(r.rr, 2)} — 모두 상승 확률이 아닙니다</div>
    </Link>
  );
}

/** How much of the market the last scan judged, why the rest was left out, the missing-data rate and the AI
 * cost — and whether the scan finished (an interrupted scan keeps what it saved). */
/** What the live prices say right now, without a scan: names inside their buy zone with a current price, and held
 * names at their stop or target. Updates on every quote. */
export function LiveSignals() {
  const rows = useJudgedRows();
  const buy = rows.filter((r) => r.judge?.valid_now).sort((a, b) => (b.judge?.rr_now ?? 0) - (a.judge?.rr_now ?? 0));
  const held = rows.filter((r) => r.judge?.held && (r.judge.zone === "STOP_HIT" || r.judge.zone === "TARGET_HIT"));
  const near = rows.filter((r) => r.judge?.bullish && r.judge.zone === "ABOVE_MAX" && (r.judge.to_max_pct ?? -1) > -0.02);
  return (
    <Card title="실시간 신호" icon="◉" testId="live-signals" explain="체결이 올 때마다 저장된 가격 계획과 비교합니다 — 스캔을 누르지 않아도 바뀝니다.">
      {!rows.length ? <Empty hint="스캔 결과·보유·관심 종목의 시세가 들어오면 여기에 바로 나옵니다.">아직 판정할 실시간 시세가 없습니다.</Empty> : (
        <div className="stack" style={{ gap: 8 }}>
          {held.map((r) => <LiveSignalRow key={`h-${r.ticker}`} r={r} />)}
          {buy.map((r) => <LiveSignalRow key={`b-${r.ticker}`} r={r} />)}
          {near.slice(0, 3).map((r) => <LiveSignalRow key={`n-${r.ticker}`} r={r} />)}
          {!held.length && !buy.length && !near.length && <div className="caption">지금은 매수 구간에 있거나 손절·목표에 닿은 종목이 없습니다. 바뀌는 순간 여기와 알림으로 알려 드립니다.</div>}
        </div>
      )}
    </Card>
  );
}

function LiveSignalRow({ r }: { r: QuoteRow }) {
  return (
    <Link to={`/stocks/${r.ticker}`} className="row spread live-signal" data-testid={`live-signal-${r.ticker}`}>
      <span className="row tight"><b>{r.ticker}</b><span className="num">{r.price != null ? price(r.price) : "—"}</span></span>
      <LiveZone ticker={r.ticker} />
    </Link>
  );
}

export function CoverageCard({ s }: { s: ScanStatus | null }) {
  if (!s || !s.state) return <Card title="분석 범위" sub><Empty hint="‘시장 스캔 실행’을 누르면 표시됩니다.">아직 스캔 기록이 없습니다.</Empty></Card>;
  const c = s.coverage;
  const st = s.state;
  const rate = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);
  return (
    <Card title="분석 범위" sub explain="후보를 고를 때 종목 목록 중 얼마를 실제로 판단했는지와 빠진 이유" tone={st.status === "INTERRUPTED" ? "warn" : undefined}>
      {st.status === "INTERRUPTED" && <Notice tone="warn">지난 스캔이 중간에 멈췄습니다({st.saved}/{st.total}개 저장). 저장된 결과는 유지되며, 다시 스캔하면 이미 받은 AI 검토 결과를 재사용합니다.</Notice>}
      {st.status === "RUNNING" && <div className="caption">스캔 진행 중: {st.saved}/{st.total}개 저장</div>}
      {c && (
        <div className="kv" style={{ marginTop: st.status === "INTERRUPTED" ? 10 : 0 }}>
          <span className="k">종목 목록</span><span>{c.universe.toLocaleString("ko-KR")}개</span>
          <span className="k">기준 미달로 제외</span><span>{c.excluded.toLocaleString("ko-KR")}개 ({rate(c.universe ? c.excluded / c.universe : null)})</span>
          <span className="k">정밀 분석</span><span>{c.deep_analysed.toLocaleString("ko-KR")}개 → 최종 순위 {c.analysed}개</span>
          <span className="k">데이터 부족으로 판단 안 함</span><span className={c.data_insufficient ? "warn" : undefined}>{c.data_insufficient}개 ({rate(c.data_insufficient_rate)})</span>
          {Object.entries(c.missing_by_field).slice(0, 4).map(([k, v]) => <Fragment key={k}><span className="k">{FIELD_KO[k] ?? k} 없음</span><span>{v.count}개 ({rate(v.rate)})</span></Fragment>)}
          <span className="k">AI 검토 비용</span><span>{c.llm.calls}회 · 약 ${c.llm.estimated_cost_usd.toFixed(2)}{c.llm.cost_complete ? "" : " (일부 비용 미상)"}</span>
        </div>
      )}
      {c && Object.keys(c.excluded_by_reason).length > 0 && (
        <div className="caption" style={{ marginTop: 8 }}>제외 이유: {Object.entries(c.excluded_by_reason).map(([k, v]) => `${k} ${v}`).join(" · ")}</div>
      )}
    </Card>
  );
}
const FIELD_KO: Record<string, string> = { price: "현재가", price_history: "가격 이력", fundamentals: "재무", analyst: "애널리스트 추정치", earnings: "실적", macro: "거시", news: "뉴스", short_interest: "공매도 잔고" };

/** The scan's real scope in one line — never "the whole market" when only a list or part of it was judged. */
export function scopeLine(s: ScanStatus | null | undefined, mode: string | undefined): string | null {
  const c = s?.coverage;
  if (!c) return null;
  const list = mode === "MOCK" ? "모의 종목 목록" : "종목 목록";
  return `후보 선정 범위: ${list} ${c.universe.toLocaleString("ko-KR")}개 → 기준 통과 ${(c.universe - c.excluded).toLocaleString("ko-KR")}개 → 정밀 분석 ${c.deep_analysed.toLocaleString("ko-KR")}개 → 최종 ${c.analysed}개`;
}

/** When an outside section's data was fetched — shown once it is older than 30 minutes or a refresh failed, so a
 * cached answer is never taken for a new one. */
function SectionAge({ s, nowMs, what }: { s?: SectionStatus; nowMs: number; what: string }) {
  if (!s?.fetched_at) return null;
  const old = nowMs - Date.parse(s.fetched_at) > 30 * 60_000;
  if (!old && !s.refresh_error) return null;
  return <div className="s" data-testid={`age-${what}`}>{what} {ago(s.fetched_at, nowMs)} 기준{s.refresh_error ? " · 새로 받기 실패(이전 값 표시)" : s.refreshing ? " · 새로 받는 중" : ""}</div>;
}

export default function Dashboard() {
  const d = useApi<Dash>("/dashboard");
  const st = useStatus();
  // the macro regime / the calendar still loading in the background: ask again shortly (cheap — stored data only)
  const sectionsLoading = !!(d.data?.regime_status?.pending || d.data?.catalysts_status?.pending || (d.data?.readiness?.stats_pending && d.data.readiness.recommendation_readiness == null));
  usePoll(d.reload, 3_000, sectionsLoading);
  const ownScan = useApi<ScanStatus>(st ? null : "/scan/status"); // outside the app shell (tests) read it here
  const ss = st ? st.scan.data : ownScan.data;
  const x = d.data;
  const lv = useLiveRows(x?.top_opportunities);  // the live re-judgement, every second (no scan button)
  const { valid, notValid } = splitCandidates(lv.rows);
  const shown = valid.slice(0, MAX_CARDS);
  const newestPrice = shown.map((r) => r.price_timestamp).filter((t): t is string => !!t).sort().pop() ?? null;
  useViewQuotes(shown.map((r) => r.ticker));  // the few names on the home screen join the app-wide quote stream
  usePageTime(x && x.scan ? { label: "후보", priceTs: newestPrice, priceSession: shown[0]?.session ?? null, analysedAt: x.scan.as_of } : null);
  if (d.state === "loading") return <Loading what="오늘의 요약" rows={2} />;
  if (!x) return <Err error={d.error} retry={d.reload} />;
  const sysMode = st?.system.data?.mode ?? x.scan?.mode;
  const down = x.provider_health.filter((h) => h.status === "DOWN");
  const risk0 = x.major_risks[0];
  const next = x.upcoming_catalysts[0];
  const notReady = x.readiness?.scanner_status === "SCANNER_NOT_READY";
  const nowMs = st?.nowMs ?? Date.now();
  const scope = scopeLine(ss, sysMode);
  const headline = !x.scan ? "후보를 고르는 중입니다" : shown.length ? "개 종목이 지금 매수 조건을 통과했습니다" : notReady ? "데이터 준비가 끝나지 않아 후보를 계산하지 못했습니다" : "지금은 매수 조건을 통과한 종목이 없습니다";
  return (
    <div className="grid">
      <div className="page-head enter">
        <div>
          <h1>오늘</h1>
          <div className="t-sub">{x.scan ? <>{lv.at ? <><span className="live-dot" aria-hidden />실시간 판정 · 1초 · </> : null}후보 선정 {stampEt(x.scan.as_of)} ({ago(x.scan.as_of, nowMs)}){scope ? ` · ${scope}` : ""}</> : "데이터 준비가 끝나면 앱이 스스로 후보를 고르고, 실시간 가격으로 1초마다 다시 판정합니다."}</div>
        </div>
        <div className="actions">
          {sysMode === "LIVE" && <button data-testid="goto-data-prep" onClick={() => document.getElementById("data-prep")?.scrollIntoView({ behavior: "smooth", block: "start" })}><IDownload />데이터 준비</button>}
        </div>
      </div>

      {x.readiness && x.readiness.recommendation_readiness !== "PAPER ONLY" && <ReadinessBanner r={x.readiness} />}
      <StaleData error={d.error} at={d.fetchedAt} retry={d.reload} nowMs={nowMs} />
      {sysMode === "LIVE" && down.length > 0 && (
        <StatePanel kind="provider_failure" testId="provider-failure" what={<><b>{down.map((h) => `${h.kind}(${h.name})`).join(", ")}</b> 데이터를 받지 못하고 있습니다. 해당 값은 ‘없음’으로 표시하고 판단에서 보수적으로 처리합니다.</>}
                    actions={<Link to="/settings?tab=status">원인 보기 →</Link>} />
      )}

      <div className="home-grid">
        <div className="home-main">
          <MorningBriefing />
          {/* the first five seconds: how many names pass right now, the market's mood, the biggest risk, the next event */}
          <section className="today enter" aria-label="오늘의 요약">
            <div className="big">
              {x.scan && shown.length > 0 && <span className="count">{shown.length}</span>}
              <span className="count-l">{headline}</span>
            </div>
            <p className="lead">
              {x.scan && !x.regime_status?.pending ? <>시장 분위기는 <b>{ko(REGIME_KO, x.regime.primary, "판단 불가")}</b> — {REGIME_HELP[x.regime.primary] ?? "거시 지표로 판단한 현재 환경"}.</> : x.scan ? null : "종목 목록을 거래대금·시가총액으로 거른 뒤 업종별 재무·밸류에이션·실적·가격 계획을 차례로 계산해 후보를 고릅니다."}
              {notValid.length > 0 ? <> 매수 계열 추천 중 {notValid.length}개는 시간 경과·가격 조건 이탈·자료 오래됨으로 지금은 유효하지 않아 따로 표시했습니다.</> : null}
            </p>
            <div className="facts">
              <div><div className="t">시장 분위기</div><div className="v">{x.regime_status?.pending ? <span className="muted" data-testid="regime-loading">거시 지표 불러오는 중…</span> : ko(REGIME_KO, x.regime.primary, "판단 불가")}</div>
                <SectionAge s={x.regime_status} nowMs={nowMs} what="거시 지표" />
                {x.regime.readings.length > 1 && <div className="s" title={x.regime.readings.map((r) => `${ko(REGIME_KO, r.regime)}: ${r.evidence.join(", ")}`).join("\n")}>함께 나타난 국면: {x.regime.readings.filter((r) => r.regime !== x.regime.primary).slice(0, 2).map((r) => ko(REGIME_KO, r.regime)).join(", ")}</div>}</div>
              <div className={risk0 || notValid.length ? "warn" : ""}><div className="t">가장 큰 위험</div><div className="v">{risk0 ? `${risk0.ticker} — ${risk0.text.split(", ").map((v) => VETO_KO[v] ?? v).join(", ")}` : notValid.length ? `유효하지 않은 추천 ${notValid.length}개` : !x.scan ? <span className="muted">후보 선정 후 표시</span> : "상위 후보에 유동성·이벤트·투자 논리 위험 신호 없음"}</div>
                <div className="s">{risk0 ? "상위 후보 중 위험 거부권이나 큰 이벤트가 걸린 종목" : !x.scan ? "후보를 고르면 그중 가장 큰 위험을 보여줍니다" : "종목별 가장 큰 위험은 후보 카드에 있습니다"}</div></div>
              <div><div className="t">다음 핵심 일정</div><div className="v">{next ? next.title : x.catalysts_status?.pending ? <span className="muted">일정 불러오는 중…</span> : "일정 자료 없음"}</div>
                <div className="s">{next ? `${day(next.event_date)} (미국 날짜) · ${next.days_until === 0 ? "오늘" : `${next.days_until}일 후`}` : x.catalysts_status?.pending ? "일정 공급자에게 요청했습니다" : x.catalysts_status?.reason ? errKo(x.catalysts_status.reason) : "일정 공급자에게서 받은 일정이 없습니다"}</div></div>
            </div>
          </section>

          {sysMode === "LIVE" && notReady && !shown.length && <span id="data-prep" />}
          <Card title="지금 검토할 후보" right={<Link to="/stocks?tab=candidates" className="row tight">전체 후보 보기 <IArrow width={15} height={15} /></Link>}
                explain="매수 조건을 통과하고 지금 다시 확인해도 유효한 종목만, 최대 5개까지 보여줍니다.">
            {shown.length ? <div className="cands">{shown.map((r, i) => <CandidateCard key={r.id} r={r} lead={i === 0 && shown.length !== 2 && shown.length !== 4} />)}</div>
              : notReady && x.readiness ? <NotReady r={x.readiness} onChange={d.reload} />
              : !x.scan ? <StatePanel kind="not_scanned" />
              : <StatePanel kind="no_candidates" actions={<Link to="/stocks?tab=candidates">대기·관찰 종목 보기 →</Link>} />}
            {shown.length > 0 && shown.length < 3 && (
              <div className="explain" style={{ marginTop: 12 }} data-testid="few-candidates">매수 조건을 통과한 종목이 {shown.length}개뿐입니다. 빈자리를 점수 상위 종목으로 채우지 않았습니다. 대기·관찰 종목은 ‘전체 후보 보기’에서 볼 수 있습니다.</div>
            )}
            {notValid.length > 0 && (
              <div style={{ marginTop: 16 }} data-testid="not-valid">
                <div className="t-kicker" style={{ marginBottom: 8 }}>지금은 유효하지 않은 추천 — 매수 신호로 보지 마세요</div>
                <div className="invalid-list">{notValid.slice(0, 5).map((r) => (
                  <div className="it" key={r.id}><Link to={`/stocks/${r.ticker}`}>{r.ticker}</Link> <Action a={r.action} status={r.current_status} quality={r.data_quality} /> <StatusBadge s={r.current_status} reason={r.current_status_reason} action={r.action} /> <span className="caption">{r.current_status_reason ?? ""}</span></div>
                ))}</div>
              </div>
            )}
          </Card>

          {sysMode === "LIVE" && !(notReady && !shown.length) && (
            <div id="data-prep"><Card title="데이터 준비" testId="data-prep"
                  explain="가격·재무 데이터를 받아 이 PC에 저장합니다. 처음 한 번은 오래 걸리고, 그 뒤로는 하루 한 번 누르면 새 거래일만 받습니다.">
              <SyncControl compact onChange={() => { d.reload(); st?.refresh(); }} />
            </Card></div>
          )}

          {x.recommendation_changes.length > 0 && (
            <Card title="추천이 바뀐 종목" sub explain="직전 분석과 비교해 판정이 달라진 종목">
              <div className="reading">{x.recommendation_changes.map((c) => <div key={c.ticker} className="stmt"><span className="kind k-CALC">변경</span><div className="body"><Link to={`/stocks/${c.ticker}`}><b>{c.ticker}</b></Link> {c.text}</div></div>)}</div>
            </Card>
          )}
        </div>

        <aside className="home-rail" aria-label="보조 정보">
          <LiveSignals />
          <div className="rail-card">
            <div className="head"><h2><IEvent />다가오는 일정</h2><Link to="/market?tab=calendar">전체 →</Link></div>
            {x.upcoming_catalysts.length ? x.upcoming_catalysts.slice(0, 5).map((e) => (
              <div className="rail-item" key={e.event_id}><span className="t">{e.title}</span><span className={`chip-days${e.days_until <= 2 ? " soon" : ""}`}>{e.days_until === 0 ? "오늘" : `D-${e.days_until}`}</span><span className="s">{day(e.event_date)} (미국 날짜)</span></div>
            )) : <div className="caption">{x.catalysts_status?.pending ? "일정 불러오는 중…" : "일정 데이터가 없습니다."}</div>}
            <SectionAge s={x.catalysts_status} nowMs={nowMs} what="일정" />
          </div>
          <div className="rail-card">
            <div className="head"><h2><IStar />관심 종목</h2><Link to="/stocks?tab=watch">관리 →</Link></div>
            {x.watchlist_alerts.length ? x.watchlist_alerts.map((a) => (
              <div className="rail-item" key={a.ticker}><span className="t"><Link to={`/stocks/${a.ticker}`}><b>{a.ticker}</b></Link> <LivePrice ticker={a.ticker} size="sm" showState={false} /> <span className={a.level === "warning" ? "warn" : a.level === "positive" ? "ok" : "muted"}>{a.text}</span></span></div>
            )) : <div className="caption">관심 종목이 없습니다. 종목 화면에서 ‘관심 종목 추가’를 누르세요.</div>}
          </div>
          <div className="rail-card">
            <div className="head"><h2><IPortfolio />내 포트폴리오</h2><Link to="/portfolio">관리 →</Link></div>
            {x.portfolio.holdings ? (
              <div className="kv"><span className="k">보유 종목</span><span>{x.portfolio.holdings}개</span><span className="k">현금</span><span>{x.portfolio.cash_entered === false ? <span className="muted" title={`매수 수량은 가정 금액 ${price(x.portfolio.cash)} 기준으로 계산합니다`}>미입력</span> : price(x.portfolio.cash)}</span></div>
            ) : <div className="caption">아직 입력한 보유 종목이 없습니다. 입력하면 새 종목을 넣을 때 쏠림·한도를 자동으로 확인합니다.</div>}
          </div>
          <div className="rail-card">
            <div className="head"><h2><IPerf />모의투자</h2><Link to="/performance">성과 →</Link></div>
            <div className="caption" style={{ marginBottom: 8 }}>{sysMode === "MOCK" ? "모의 데이터로 만든 시뮬레이션 — 실전 성과가 아닙니다" : "실데이터 가격으로 계산한 시뮬레이션 — 실제 주문이 아닙니다"}</div>
            {x.performance ? (
              <>
                <div className="row spread"><span className="t-key-sm"><Change v={x.performance.return} /></span><span className="caption"><Term k="max_drawdown">최대 낙폭</Term> {pct(x.performance.max_drawdown)}</span></div>
                <LineChart values={x.performance.curve} height={56} />
                <div className="caption">기준일 {day(x.performance.as_of)} · 시작 자본 {price(x.performance.starting_capital)}</div>
              </>
            ) : <div className="caption">아직 결과가 확정된 모의 포지션이 없습니다. 성과 화면에서 ‘결과 갱신’을 누르면 계산합니다.</div>}
          </div>
          <CoverageCard s={ss ?? null} />
          <div className="rail-card">
            <div className="head"><h2><IShield />데이터 연결</h2><Link to="/settings?tab=status">자세히 →</Link></div>
            {down.length ? <Notice tone="warn">일부 공급자가 중단되었습니다({down.map((h) => h.kind).join(", ")}). 해당 데이터는 ‘없음’으로 표시되고 판단에서 보수적으로 처리됩니다.</Notice>
              : x.provider_health.length ? <div className="caption">공급자 {x.provider_health.length}곳 · {x.provider_health.every((h) => h.status === "HEALTHY") ? "모두 정상" : x.provider_health.map((h) => `${h.kind} ${HEALTH_KO[h.status] ?? h.status}`).filter((t) => !t.endsWith("정상")).join(", ")}</div>
              : <div className="caption">아직 데이터 호출 기록이 없습니다(첫 스캔 후 표시).</div>}
          </div>
        </aside>
      </div>
    </div>
  );
}
