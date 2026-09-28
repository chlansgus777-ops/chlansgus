import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { OppTable } from "../components/OppTable";
import { Action, Card, Change, Empty, Err, LineChart, Loading, Notice, Ribbon, StaleData, StatePanel, StatusBadge, Term } from "../components/ui";
import { NotReady, ReadinessBanner, SyncControl, type ReadinessInfo } from "../components/Readiness";
import { type ScanStatus, usePageTime, useStatus } from "../components/status";
import { useApi } from "../components/useApi";
import { ago, day, num, pct, price, stampEt } from "../format";
import { ACTION_PLAIN, BULLISH, HEALTH_KO, REGIME_KO, RISK_KO, SESSION_KO, VETO_KO, actionTone, ko } from "../i18n";
import { useMode } from "../mode";
import type { OppRow, ScanInfo } from "../types";

export type { ScanStatus } from "../components/status";

interface Dash {
  scan: ScanInfo | null;
  regime: { primary: string; readings: { regime: string; score: number; confidence: number; evidence: string[] }[] };
  top_opportunities: OppRow[];
  major_risks: { ticker: string; text: string }[];
  upcoming_catalysts: { event_id: string; title: string; event_date: string; days_until: number; importance: number }[];
  portfolio: { holdings: number; cash: number };
  provider_health: { name: string; kind: string; status: string }[];
  performance: { equity: number; starting_capital: number; return: number | null; max_drawdown: number | null; as_of: string; curve: number[] } | null;
  recommendation_changes: { ticker: string; text: string; action: string }[];
  watchlist_alerts: { ticker: string; level: string; text: string }[];
  readiness?: ReadinessInfo;
}

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
const SCAN_STEPS = ["종목 목록 1차 필터", "펀더멘털 선별", "뉴스·이슈 반영", "최종 점수·가격 계획", "상위 후보 AI 위원회"];
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

function CandidateCard({ r }: { r: OppRow }) {
  const plain = ACTION_PLAIN[r.action] ?? "";
  const zone = r.ideal_entry != null && r.max_buy != null ? `${price(r.ideal_entry)} ~ ${price(r.max_buy)}` : "자료 부족";
  return (
    <Link to={`/stocks/${r.ticker}`} className="cand" style={{ ["--rail" as string]: `var(--${actionTone(r.action)})` }} data-testid="candidate-card" aria-label={`${r.ticker} 상세 보기`}>
      <div className="top">
        <div style={{ minWidth: 0 }}><div className="tk">{r.ticker}</div><div className="co">{r.company} · {r.sector_known === false ? "업종 불명확" : r.sector}</div></div>
        <Action a={r.action} status={r.current_status} quality={r.data_quality} />
      </div>
      <div className="why">{plain}</div>
      {r.key_reason ? <div className="why"><b>핵심 이유 · </b>{r.key_reason}</div> : null}
      <div className="facts">
        <div><div className="t">현재가</div><div className="v">{price(r.price)}</div></div>
        <div title="이상적 진입가 ~ 최대 매수가 (백엔드 가격 계획)"><div className="t">검토 가격대</div><div className="v" style={{ fontSize: 13 }}>{zone}</div></div>
        <div title="종가가 이 가격 아래로 마감하면 매수 근거가 깨졌다고 봅니다"><div className="t">손절 기준</div><div className="v">{r.stop == null ? "자료 부족" : price(r.stop)}</div></div>
      </div>
      <div className="when">가격 기준 {stampEt(r.price_timestamp)}{r.session ? ` · ${ko(SESSION_KO, r.session)}` : ""} · 손절까지 {r.downside == null ? "자료 부족" : pct(r.downside)}</div>
      <div className="risk"><span aria-hidden>⚠</span><span><b>가장 큰 위험 · </b>{riskLine(r)}</span></div>
      <div className="when">점수 {num(r.score, 1)}/100 · 분석 신뢰도 {num(r.confidence, 0)}/100 · 손익비 {num(r.rr, 2)} — 모두 상승 확률이 아닙니다</div>
    </Link>
  );
}

/** How much of the market the last scan judged, why the rest was left out, the missing-data rate and the AI
 * cost — and whether the scan finished (an interrupted scan keeps what it saved). */
export function CoverageCard({ s }: { s: ScanStatus | null }) {
  if (!s || !s.state) return <Card title="분석 범위" icon="◫"><Empty hint="‘시장 스캔 실행’을 누르면 표시됩니다.">아직 스캔 기록이 없습니다.</Empty></Card>;
  const c = s.coverage;
  const st = s.state;
  const rate = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);
  return (
    <Card title="분석 범위" icon="◫" explain="마지막 스캔이 종목 목록 중 얼마를 실제로 판단했는지와 빠진 이유"
          tone={st.status === "INTERRUPTED" ? "warn" : undefined}>
      {st.status === "INTERRUPTED" && <Notice tone="warn">지난 스캔이 중간에 멈췄습니다({st.saved}/{st.total}개 저장). 저장된 결과는 유지되며, 다시 스캔하면 이미 받은 AI 검토 결과를 재사용합니다.</Notice>}
      {st.status === "RUNNING" && <div className="caption">스캔 진행 중: {st.saved}/{st.total}개 저장</div>}
      {c && (
        <div className="kv">
          <span className="k">종목 목록</span><span>{c.universe.toLocaleString("ko-KR")}개</span>
          <span className="k">기준 미달로 제외</span><span>{c.excluded.toLocaleString("ko-KR")}개 ({rate(c.universe ? c.excluded / c.universe : null)})</span>
          <span className="k">정밀 분석</span><span>{c.deep_analysed.toLocaleString("ko-KR")}개 → 최종 순위 {c.analysed}개</span>
          <span className="k">데이터 부족으로 판단 안 함</span><span className={c.data_insufficient ? "warn" : undefined}>{c.data_insufficient}개 ({rate(c.data_insufficient_rate)})</span>
          {Object.entries(c.missing_by_field).slice(0, 4).map(([k, v]) => <Fragment key={k}><span className="k">{FIELD_KO[k] ?? k} 없음</span><span>{v.count}개 ({rate(v.rate)})</span></Fragment>)}
          <span className="k">AI 검토 비용</span><span>{c.llm.calls}회 · 약 ${c.llm.estimated_cost_usd.toFixed(2)}{c.llm.cost_complete ? "" : " (일부 비용 미상)"}</span>
        </div>
      )}
      {c && Object.keys(c.excluded_by_reason).length > 0 && (
        <div className="caption" style={{ marginTop: 6 }}>제외 이유: {Object.entries(c.excluded_by_reason).map(([k, v]) => `${k} ${v}`).join(" · ")}</div>
      )}
    </Card>
  );
}
const FIELD_KO: Record<string, string> = { price: "현재가", price_history: "가격 이력", fundamentals: "재무", analyst: "애널리스트 추정치", earnings: "실적", macro: "거시", news: "뉴스", options: "옵션", ownership: "수급" };

/** The scan's real scope in one line — never "the whole market" when only a list or part of it was judged. */
export function scopeLine(s: ScanStatus | null | undefined, mode: string | undefined): string | null {
  const c = s?.coverage;
  if (!c) return null;
  const list = mode === "MOCK" ? "모의 종목 목록" : "종목 목록";
  return `이번 스캔 범위: ${list} ${c.universe.toLocaleString("ko-KR")}개 → 기준 통과 ${(c.universe - c.excluded).toLocaleString("ko-KR")}개 → 정밀 분석 ${c.deep_analysed.toLocaleString("ko-KR")}개 → 최종 ${c.analysed}개`;
}

export default function Dashboard() {
  const d = useApi<Dash>("/dashboard");
  const st = useStatus();
  const ownScan = useApi<ScanStatus>(st ? null : "/scan/status"); // outside the app shell (tests) read it here
  const ss = st ? st.scan.data : ownScan.data;
  const { mode } = useMode();
  const [busy, setBusy] = useState(false);
  const [scanErr, setScanErr] = useState<string | null>(null);
  const x = d.data;
  const { valid, notValid } = splitCandidates(x?.top_opportunities ?? []);
  const shown = valid.slice(0, MAX_CARDS);
  const newestPrice = shown.map((r) => r.price_timestamp).filter((t): t is string => !!t).sort().pop() ?? null;
  usePageTime(x && x.scan ? { label: "후보", priceTs: newestPrice, priceSession: shown[0]?.session ?? null, analysedAt: x.scan.as_of } : null);
  const scan = async () => {
    setBusy(true);
    st?.setScanning(true);
    setScanErr(null);
    try { await api.post("/scan?committee=true"); d.reload(); } catch (e) { setScanErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); st?.setScanning(false); st?.refresh(); ownScan.reload(); }
  };
  if (d.state === "loading") return <Loading what="대시보드" />;
  if (!x) return <Err error={d.error} retry={d.reload} />;
  const sysMode = st?.system.data?.mode ?? x.scan?.mode;
  const down = x.provider_health.filter((h) => h.status === "DOWN");
  const risk0 = x.major_risks[0];
  const next = x.upcoming_catalysts[0];
  const notReady = x.readiness?.scanner_status === "SCANNER_NOT_READY";
  const nowMs = st?.nowMs ?? Date.now();
  const scope = scopeLine(ss, sysMode);
  return (
    <div className="grid">
      <div className="page-head">
        <div>
          <h1>오늘의 미국 주식 한눈에 보기</h1>
          <div className="t-sub">{x.scan ? <>분석 {stampEt(x.scan.as_of)} ({ago(x.scan.as_of, nowMs)}){scope ? ` · ${scope}` : ""}</> : "아직 스캔 결과가 없습니다."}</div>
        </div>
        <div className="row">
          {sysMode === "LIVE" && <button data-testid="goto-data-prep" onClick={() => document.getElementById("data-prep")?.scrollIntoView({ behavior: "smooth", block: "start" })}>데이터 준비 ↓</button>}
          <button className="primary" disabled={busy} onClick={scan}>{busy ? "스캔 중…" : "시장 스캔 실행"}</button>
        </div>
      </div>

      {x.readiness && x.readiness.recommendation_readiness !== "PAPER ONLY" && <ReadinessBanner r={x.readiness} />}
      <StaleData error={d.error} at={d.fetchedAt} retry={d.reload} nowMs={nowMs} />
      {sysMode === "LIVE" && down.length > 0 && (
        <StatePanel kind="provider_failure" testId="provider-failure" what={<><b>{down.map((h) => `${h.kind}(${h.name})`).join(", ")}</b> 데이터를 받지 못하고 있습니다. 해당 값은 ‘없음’으로 표시하고 판단에서 보수적으로 처리합니다.</>}
                    actions={<Link to="/health">원인 보기 →</Link>} />
      )}
      {ss?.state?.status === "INTERRUPTED" && <Ribbon tone="warn" cap="분석 중단">지난 스캔이 중간에 멈췄습니다({ss.state.saved}/{ss.state.total}개 저장). 아래 후보는 저장된 결과만 보여줍니다.</Ribbon>}
      {notValid.length > 0 && <Ribbon tone="warn" cap="유효하지 않은 추천">매수 계열 추천 {notValid.length}개는 지금 유효하지 않아(시간 경과·가격 조건 이탈·자료 오래됨) 후보 카드에서 뺐습니다. 아래 ‘지금은 유효하지 않은 추천’에서 이유를 확인하세요.</Ribbon>}
      {(busy || ss?.state?.status === "RUNNING") && <StatePanel kind="analyzing" what={`시장 스캔을 실행하고 있습니다${ss?.state?.status === "RUNNING" ? `(${ss.state.saved}/${ss.state.total}개 저장)` : ""}: ${SCAN_STEPS.join(" → ")}`} />}
      <Err error={scanErr} />

      {/* ① ② ③ ④ — the first five seconds */}
      <div className="pulse" data-testid="pulse">
        <div className="p">
          <div className="n"><span className="no" aria-hidden>1</span><h2 style={{ fontSize: 13.5, color: "var(--muted)" }}>오늘 시장 분위기</h2></div>
          <div className="v">{ko(REGIME_KO, x.regime.primary, "판단 불가")}</div>
          <div className="s">{REGIME_HELP[x.regime.primary] ?? "거시 지표로 판단한 현재 환경"}</div>
          {x.regime.readings.length > 1 && <div className="caption" title={x.regime.readings.map((r) => `${ko(REGIME_KO, r.regime)}: ${r.evidence.join(", ")}`).join("\n")}>함께 나타난 국면: {x.regime.readings.filter((r) => r.regime !== x.regime.primary).slice(0, 3).map((r) => ko(REGIME_KO, r.regime)).join(", ")}</div>}
        </div>
        <div className={`p${shown.length ? "" : " tone-warn"}`}>
          <div className="n"><span className="no" aria-hidden>2</span>검토 후보</div>
          <div className="v">{shown.length ? `${shown.length}개` : "지금은 없음"}</div>
          <div className="s">{shown.length ? "매수 조건 통과 · 지금 다시 확인해도 유효" : notReady ? "데이터 준비가 끝나지 않았습니다" : x.scan ? "이번 스캔에서 조건을 통과한 종목이 없습니다" : "아직 스캔하지 않았습니다"}</div>
        </div>
        <div className={`p${risk0 || notValid.length ? " tone-warn" : ""}`}>
          <div className="n"><span className="no" aria-hidden>3</span>가장 큰 위험</div>
          <div className="v">{risk0 ? `${risk0.ticker} — ${risk0.text.split(", ").map((v) => VETO_KO[v] ?? v).join(", ")}` : notValid.length ? `유효하지 않은 추천 ${notValid.length}개` : "상위 후보에 거부권·고위험 일정 없음"}</div>
          <div className="s">{risk0 ? "상위 후보 중 거부권이나 큰 이벤트가 걸린 종목" : "종목별 가장 큰 위험은 후보 카드에 있습니다"}</div>
        </div>
        <div className="p">
          <div className="n"><span className="no" aria-hidden>4</span>다음 핵심 일정</div>
          <div className="v">{next ? next.title : "일정 자료 없음"}</div>
          <div className="s">{next ? `${day(next.event_date)} (미국 날짜) · ${next.days_until === 0 ? "오늘" : `${next.days_until}일 후`}` : "일정 공급자에게서 받은 일정이 없습니다"}</div>
        </div>
      </div>

      {sysMode === "LIVE" && notReady && !shown.length && <span id="data-prep" />}
      <Card title="지금 검토할 후보" icon="◎" right={<Link to="/opportunities">전체 후보 보기 →</Link>}
            explain="매수 조건을 통과하고 지금 다시 확인해도 유효한 종목만, 최대 5개까지 보여줍니다.">
        {shown.length ? <div className="cands">{shown.map((r) => <CandidateCard key={r.id} r={r} />)}</div>
          : notReady && x.readiness ? <NotReady r={x.readiness} onChange={d.reload} />
          : !x.scan ? <StatePanel kind="not_scanned" actions={<button className="primary" disabled={busy} onClick={scan}>시장 스캔 실행</button>} />
          : <StatePanel kind="no_candidates" actions={<Link to="/opportunities">대기·관찰 종목 보기 →</Link>} />}
        {shown.length > 0 && shown.length < 3 && (
          <div className="explain" style={{ marginTop: 10 }} data-testid="few-candidates">매수 조건을 통과한 종목이 {shown.length}개뿐입니다. 빈자리를 점수 상위 종목으로 채우지 않았습니다. 대기·관찰 종목은 ‘전체 후보 보기’에서 볼 수 있습니다.</div>
        )}
        {notValid.length > 0 && (
          <div style={{ marginTop: 14 }} data-testid="not-valid">
            <div className="caption" style={{ marginBottom: 6 }}>지금은 유효하지 않은 추천 — 매수 신호로 보지 마세요</div>
            <ul className="list">{notValid.slice(0, 5).map((r) => (
              <li key={r.id}><span className="dot warn">!</span><span><Link to={`/stocks/${r.ticker}`}>{r.ticker}</Link> <Action a={r.action} status={r.current_status} quality={r.data_quality} /> <StatusBadge s={r.current_status} reason={r.current_status_reason} /> <span className="caption">{r.current_status_reason ?? ""}</span></span></li>
            ))}</ul>
          </div>
        )}
      </Card>

      {sysMode === "LIVE" && !(notReady && !shown.length) && (
        <div id="data-prep"><Card title="데이터 준비" icon="⏳" testId="data-prep"
              explain="가격·재무 데이터를 받아 이 PC에 저장합니다. 처음 한 번은 오래 걸리고, 그 뒤로는 하루 한 번 누르면 새 거래일만 받습니다.">
          <SyncControl onChange={() => { d.reload(); st?.refresh(); }} />
        </Card></div>
      )}

      <div className="g3">
        <Card title="가장 조심할 위험" icon="⚠" tone={x.major_risks.length ? "warn" : undefined}>
          {x.major_risks.length ? <ul className="list">{x.major_risks.slice(0, 5).map((r, i) => <li key={i}><span className="dot warn">!</span><span><Link to={`/stocks/${r.ticker}`}>{r.ticker}</Link> — {r.text.split(", ").map((v) => VETO_KO[v] ?? v).join(", ")}</span></li>)}</ul> : <Empty hint="거부권(hard veto)이나 결과가 크게 갈리는 일정이 걸린 상위 후보가 있으면 여기에 나옵니다.">상위 후보에서 눈에 띄는 위험 신호가 없습니다.</Empty>}
        </Card>
        <Card title="다가오는 중요한 일정" icon="▦" right={<Link to="/calendar">일정 →</Link>}>
          {x.upcoming_catalysts.length ? <ul className="list">{x.upcoming_catalysts.slice(0, 5).map((e) => <li key={e.event_id}><span className="dot info">{e.days_until}</span><span>{e.title}<div className="caption">{day(e.event_date)} (미국 날짜) · {e.days_until === 0 ? "오늘" : `${e.days_until}일 후`}</div></span></li>)}</ul> : <Empty>일정 데이터가 없습니다.</Empty>}
        </Card>
        <Card title="내 포트폴리오" icon="◔" right={<Link to="/portfolio">관리 →</Link>}>
          {x.portfolio.holdings ? (
            <div className="kv"><span className="k">보유 종목</span><span>{x.portfolio.holdings}개</span><span className="k">현금</span><span>{price(x.portfolio.cash)}</span></div>
          ) : <Empty hint="보유 종목을 입력하면 새 종목을 넣을 때 쏠림·중복 위험을 자동으로 확인합니다.">아직 입력한 보유 종목이 없습니다.</Empty>}
        </Card>
      </div>

      <div className="g3">
        <Card title="모의투자 성과" icon="↗" right={<Link to="/performance">분석 →</Link>}
              explain={sysMode === "MOCK" ? "모의 데이터로 만든 시뮬레이션 — 실전 성과가 아닙니다" : "실데이터 가격으로 계산한 시뮬레이션(Paper) — 실제 주문이 아닙니다"}>
          {x.performance ? (
            <>
              <div className="row spread"><span className="t-key-sm"><Change v={x.performance.return} /></span><span className="caption"><Term k="max_drawdown">최대 낙폭</Term> {pct(x.performance.max_drawdown)}</span></div>
              <LineChart values={x.performance.curve} height={70} />
              <div className="caption">기준일 {day(x.performance.as_of)} · 시작 자본 {price(x.performance.starting_capital)}</div>
            </>
          ) : <Empty hint="성과 분석 화면에서 ‘결과·모의투자 갱신’을 누르세요.">아직 모의투자 기록이 없습니다.</Empty>}
        </Card>
        <Card title="추천이 바뀐 종목" icon="⇄">
          {x.recommendation_changes.length ? <ul className="list">{x.recommendation_changes.map((c) => <li key={c.ticker}><span className="dot info">↻</span><span><Link to={`/stocks/${c.ticker}`}>{c.ticker}</Link> {c.text}</span></li>)}</ul> : <Empty>최근 스캔에서 추천이 바뀐 종목이 없습니다.</Empty>}
        </Card>
        <Card title="관심 종목 알림" icon="★" right={<Link to="/stocks">관심 종목 →</Link>}>
          {x.watchlist_alerts.length ? <ul className="list">{x.watchlist_alerts.map((a) => <li key={a.ticker}><span className={`dot ${a.level === "positive" ? "info" : a.level === "warning" ? "warn" : "info"}`}>{a.level === "positive" ? "●" : a.level === "warning" ? "!" : "i"}</span><span><Link to={`/stocks/${a.ticker}`}>{a.ticker}</Link> {a.text}</span></li>)}</ul> : <Empty hint="종목 분석 화면에서 ‘관심종목 추가’를 누르세요.">관심 종목이 없습니다.</Empty>}
        </Card>
      </div>

      <div className="g2">
        <CoverageCard s={ss ?? null} />
        <Card title="시스템 상태" icon="●" right={<Link to="/health">자세히 →</Link>}>
          {down.length ? <Notice tone="warn">일부 데이터 공급자가 중단되었습니다({down.map((h) => h.kind).join(", ")}). 해당 데이터는 ‘없음’으로 표시되고 판단에서 보수적으로 처리됩니다.</Notice>
            : <div className="row">{x.provider_health.length ? x.provider_health.map((h) => <span key={h.name} className={`pill tone-${h.status === "HEALTHY" ? "ok" : h.status === "DOWN" ? "danger" : "warn"}`}>{h.status === "HEALTHY" ? "✓" : h.status === "DOWN" ? "⛔" : "!"} {h.kind} · {HEALTH_KO[h.status] ?? h.status}</span>) : <span className="caption">아직 데이터 호출 기록이 없습니다(첫 스캔 후 표시).</span>}</div>}
        </Card>
      </div>

      {mode === "advanced" && x.top_opportunities.length > 0 && <Card title="상위 후보 전체 비교"><OppTable rows={x.top_opportunities} compact /></Card>}
      {mode === "advanced" && x.scan && (
        <Card title="스캐너 단계 (저비용 → 고비용)">
          <div className="scroll"><table><thead><tr><th>단계</th><th>입력</th><th>통과</th><th>설명</th></tr></thead>
            <tbody>{x.scan.stages.map((s) => <tr key={s.stage}><td>{s.stage}</td><td>{s.input_count.toLocaleString("ko-KR")}</td><td>{s.output_count.toLocaleString("ko-KR")}</td><td className="caption" style={{ whiteSpace: "normal" }}>{s.note}</td></tr>)}</tbody></table></div>
        </Card>
      )}
    </div>
  );
}
