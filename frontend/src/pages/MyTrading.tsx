import { Fragment, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { api, baseUrl } from "../api";
import { Card, Disclosure, Empty, Err, Loading, Notice, Ribbon, StatePanel, Tabs } from "../components/ui";
import { useApi } from "../components/useApi";
import { HoldingPlans, PriceLadder, money, pp, type Level } from "../components/HoldingPlans";
import { day, shares } from "../format";

type Pattern = "CANDIDATE" | "PARTIAL" | "NOT" | "UNCONFIRMED" | "INSUFFICIENT" | "NEED_BASIS";
export const PATTERN_KO: Record<Pattern, string> = {
  CANDIDATE: "본전 탈출 후보", PARTIAL: "일부 수량만 후보", NOT: "후보 아님", UNCONFIRMED: "미확정(봉 안 순서 불명)",
  INSUFFICIENT: "판정 불가(가격 자료 부족)", NEED_BASIS: "기초 매수 기록 필요",
};
const PATTERN_TONE: Record<Pattern, string> = { CANDIDATE: "neg", PARTIAL: "sell", NOT: "", UNCONFIRMED: "muted", INSUFFICIENT: "muted", NEED_BASIS: "muted" };
const STOP_KO: Record<string, string> = { NO_STOP: "사전 손절가 미설정", TOUCHED_HELD: "손절선 접촉 후 보유", EXIT_BELOW: "손절가 아래 청산", NOT_TOUCHED: "손절선 미접촉", INSUFFICIENT: "판정 불가" };
const EARLY_KO: Record<string, string> = { CANDIDATE: "이른 청산 후보", NOT: "아님", INSUFFICIENT: "판정 불가" };

interface Early { status: string; reason: string; max_rise: number | null; max_fall: number | null; days_observed: number }
interface StopR { status: string; reason: string; stop: number | null; target: number | null; pre_recorded: boolean | null; source: string | null }
interface Trade {
  id: string; symbol: string; currency: string; sell_order_id: string; buy_order_id: string | null; row_of_order: number; bought_at: string | null; sold_at: string;
  quantity: number; entry: number | null; exit: number; holding_days: number | null; gross_pnl: number | null; net_pnl: number | null; gross_ret: number | null;
  net_ret: number | null; buy_fees: number; sell_fees: number; mae: number | null; mfe: number | null; pattern: Pattern; pattern_reason: string;
  path: { status: string; reason: string; first_bar: string | null; last_bar: string | null; missing_days: string[]; interval: string; notes: string[]; uncertain_low: number | null } | null;
  stop: StopR; early: Early | null; has_note: boolean;
}
interface Order { order_id: string; symbol: string; currency: string; sold_at: string; quantity: number; price: number; rows: number; pattern: Pattern; net_pnl: number | null;
  net_ret: number | null; candidate_quantity: number; known_quantity: number; need_basis_quantity: number; early: Early; stop: string; avg_cost_view: { avg_cost: number | null; net_ret: number | null } | null }
interface Finding { id: string; title: string; evidence: string; fix: string; confidence: string }
interface Stats { win_rate: number; avg_win_pct: number | null; avg_loss_pct: number | null; expectancy_pct: number; payoff: number | null; wins: number; losses: number;
  median_hold_days_win: number | null; median_hold_days_loss: number | null }
interface Diagnosis { closed: number; headline: string | null; cause?: string; findings: Finding[]; stats: Stats | null; suggested: { rules: TradeRules; why: string[] } | null }
interface TradeRules { stop_pct: number; use_app_stop: boolean; take1_pct: number; take1_fraction: number; trail_pct: number; add_mode: string; add_trigger_pct: number; add_fraction: number; max_adds: number; saved_at: string | null }
interface PatternRules { dip_pct: number; exit_min_pct: number; exit_max_pct: number; repeat_min: number; early_days: number; early_rise_pct: number }
export interface Report {
  source: string; is_sample: boolean; generated_at: string; period: { first: string | null; last: string | null };
  meta: { connected?: boolean; label?: string; fills?: { count: number; complete: boolean; since: string | null; synced_at: string | null }; skipped?: string[]; extended?: Record<string, { at: string }> };
  rules: PatternRules; trade_rules: TradeRules; rules_saved: boolean;
  summary: { sell_orders: number; matched_rows: number; realized: Record<string, { net: number; gross: number; fees: number }>; profit_orders: number; known_orders: number; profit_ratio: number | null;
    breakeven: { candidates: number; partial: number; unconfirmed: number; evaluable: number; ratio: number | null; not_evaluable: number; repeated: boolean };
    early_exit: { candidates: number; evaluable: number }; stop_review: { candidates: number; no_stop: number }; data_short: number; count_unit: string };
  diagnosis: Diagnosis;
  by_symbol: { symbol: string; currency: string; sell_orders: number; evaluable: number; candidates: number; partial: number; insufficient: number; early: number; stop_review: number; net_pnl: number; ratio: number | null; repeated: boolean }[];
  orders: Order[]; trades: Trade[]; open_lots: { symbol: string; currency: string; quantity: number; entry: number | null; bought_at: string | null; basis: string }[]; warnings: string[];
}
interface Detail {
  trade: Trade; order: Order | null; is_sample: boolean; calc: string[]; bars: { day: string; open: number; high: number; low: number; close: number }[];
  raw: { buy: Record<string, unknown> | null; sell: Record<string, unknown> | null }; note: { buy_reason?: string; during?: string; sell_reason?: string; updated_at?: string };
  user_stop: { stop: number; target: number | null; recorded_at: string } | null; basis_note: string | null;
}

const ADD_KO: Record<string, string> = { winners_only: "수익 중일 때만", none: "안 함", any: "조건 없음(물타기 허용)" };
const md = (iso: string | null | undefined) => (iso ? iso.slice(5, 10).replace("-", ".") : "—");
const tone = (v: number | null | undefined) => (v == null ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "");

/** 내 매매 진단 (성과 › 내 매매 진단): why the account is not making money and what to change, from the owner's own
 * executions (토스증권, read-only), the rules that turn it into "지금 할 일" for each holding, and the trade log with
 * each sale's price path. The virtual sample is a separate source with its own banner — never mixed in. */
export default function MyTrading() {
  const [sample, setSample] = useState(false);
  const r = useApi<Report>(`/habits${sample ? "?sample=1" : ""}`, [sample]);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const loc = useLocation();  // the app runs under a HashRouter: the in-page anchor is the router's hash, not the window's
  useEffect(() => {
    if (r.data && loc.hash === "#rules") document.getElementById("rules")?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  }, [r.data, loc.hash]);
  const run = async (tag: string, f: () => Promise<unknown>, done?: string) => {
    if (busy) return;
    setBusy(tag); setErr(null); setMsg(null);
    try { await f(); r.reload(); if (done) setMsg(done); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(""); }
  };
  if (r.state === "loading" && !r.data) return <Loading what="내 매매 진단" steps={["토스증권 체결 기록 읽기", "매수·매도 짝 맞추기(FIFO)", "보유 기간 가격 확인", "습관 진단"]} />;
  if (!r.data) return <Err error={r.error} retry={r.reload} />;
  const x = r.data;
  const connected = x.is_sample || x.meta.connected;
  const f = x.meta.fills;
  return (
    <div className="grid habits" data-testid="my-trading">
      <div className="habits-bar">
        <Tabs<"real" | "sample"> label="자료" value={sample ? "sample" : "real"} onChange={(v) => setSample(v === "sample")}
          items={[["real", "내 토스증권 계좌"], ["sample", "가상 샘플"]]} />
        <div className="row tight">
          {!x.is_sample && f && <span className="caption">체결 {f.count.toLocaleString("ko-KR")}건 · 동기화 {f.synced_at ? new Date(f.synced_at).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—"}</span>}
          {!x.is_sample && x.meta.connected && <button className="sm" disabled={!!busy} onClick={() => void run("refresh", () => api.post("/habits/refresh"), "토스증권에서 새 체결을 읽었습니다")}>{busy === "refresh" ? <><span className="spin" />조회 중…</> : "새 체결 조회"}</button>}
          {connected && <a className="btn sm" href={`${baseUrl()}/api/habits/export.csv${x.is_sample ? "?sample=1" : ""}`} download>CSV 내보내기</a>}
        </div>
      </div>
      {x.is_sample
        ? <Ribbon tone="danger" cap="가상 샘플" testId="sample-banner">실제 거래가 아닌 가상 체결·가상 가격입니다. 화면 읽는 법을 보여주는 용도이며 내 계좌 분석과 섞이지 않습니다.</Ribbon>
        : connected && <Ribbon tone="info" cap="실제 계좌 · 읽기 전용" testId="real-banner">토스증권에서 체결된 주문(체결 수량·평균 체결가·수수료·세금)만 읽어 계산합니다. 주문을 넣거나 바꾸는 기능은 없습니다.</Ribbon>}
      <Err error={err} />
      {msg && <Notice tone="info">{msg}</Notice>}
      {!connected ? (
        <>
          <StatePanel kind="insufficient" title="토스증권을 연결하면 내 매매를 진단합니다" what="토스증권이 연결되지 않아 체결 기록을 읽지 않았습니다."
            unknown="승률·평균 손실·물타기·본전 탈출처럼 실제 사고판 기록이 있어야 나오는 진단은 아직 계산할 수 없습니다."
            todo="설정에서 토스증권을 연결하세요(조회 전용). 그 전에도 아래 '내 매매 규칙'은 정해 둘 수 있고, 이 앱에 입력한 보유 종목에 바로 적용됩니다."
            actions={<><Link className="btn primary" to="/settings">토스증권 연결</Link><button onClick={() => setSample(true)}>가상 샘플로 화면 보기</button></>} />
          <HoldingPlans />
          <RulesCard x={x} onSaved={r.reload} />
        </>
      ) : (
        <>
          <WhyLosing x={x} />
          {!x.is_sample && <HoldingPlans />}
          <RulesCard x={x} onSaved={r.reload} />
          <Patterns x={x} />
          <TradeLog x={x} sample={sample} onExtend={(sym) => void run(`ext:${sym}`, () => api.post("/habits/extend", { symbol: sym }), `${sym}: 전체 기간 체결을 다시 읽었습니다`)} busy={busy} />
          <Disclosure title="분석 범위 · 자료 기준" hint={`${day(x.period.first)} ~ ${day(x.period.last)} · 매도 주문 ${x.summary.sell_orders}건${x.warnings.length ? ` · 경고 ${x.warnings.length}` : ""}`} testId="habits-basis">
            <div className="kv">
              <span className="k">분석 기간</span><span>{day(x.period.first)} ~ {day(x.period.last)}</span>
              <span className="k">자료</span><span>{x.is_sample ? "가상 샘플" : f ? `토스 체결 ${f.count.toLocaleString("ko-KR")}건 · ${day(f.since)}부터 · ${f.complete ? "끝까지 조회" : "일부만 조회"}` : "—"}</span>
              <span className="k">세는 단위</span><span>{x.summary.count_unit} · 매도 주문 {x.summary.sell_orders}건 → FIFO 매칭 행 {x.summary.matched_rows}개</span>
              <span className="k">가격 자료</span><span>미국 종목: 일봉(시가·고가·저가·종가) 관측치 · 국내 종목: 앱에 과거 일봉이 없어 가격 경로 판정 불가</span>
              <span className="k">판단의 한계</span><span>체결 가격과 일봉만으로 계산합니다. 매도 당시의 생각이나 판단은 알 수 없어 거래 상세의 메모로 남깁니다.</span>
            </div>
            {(x.meta.skipped ?? []).map((s, i) => <Notice key={`s${i}`} tone="warn">{s}</Notice>)}
            {x.warnings.map((w, i) => <Notice key={i} tone="warn">{w}</Notice>)}
            {x.open_lots.length > 0 && (
              <div data-testid="open-lots" style={{ marginTop: 10 }}>
                <div className="t-kicker">아직 보유 중인 수량(FIFO 잔여)</div>
                <table><thead><tr><th>종목</th><th className="num">수량</th><th className="num">매수가</th><th>매수일</th><th>근거</th></tr></thead>
                  <tbody>{x.open_lots.map((o, i) => <tr key={i}><td>{o.symbol}</td><td className="num">{shares(o.quantity)}</td><td className="num">{money(o.entry, o.currency)}</td><td>{day(o.bought_at)}</td><td className="caption">{o.basis}</td></tr>)}</tbody></table>
              </div>
            )}
          </Disclosure>
        </>
      )}
    </div>
  );
}

function Kpi({ label, children, sub, tone: t }: { label: string; children: ReactNode; sub?: ReactNode; tone?: string }) {
  return <div className={`kpi ${t ?? ""}`}><div className="t-kicker">{label}</div><div className="kpi-v">{children}</div>{sub && <div className="caption">{sub}</div>}</div>;
}

/** 이긴 매도의 평균 이익과 진 매도의 평균 손실을 같은 눈금에: 손실 막대가 길면 그게 문제다. */
function PayoffBar({ win, loss }: { win: number | null; loss: number | null }) {
  const w = Math.max(0, win ?? 0), l = Math.max(0, -(loss ?? 0));
  const m = Math.max(w, l, 0.1);
  return (
    <div className="payoff" aria-label={`평균 이익 ${pp(win)}, 평균 손실 ${pp(loss)}`}>
      <div className="side neg"><span>{pp(loss)}</span><i style={{ width: `${(l / m) * 100}%` }} /></div>
      <div className="side pos"><i style={{ width: `${(w / m) * 100}%` }} /><span>{pp(win)}</span></div>
    </div>
  );
}

function WhyLosing({ x }: { x: Report }) {
  const d = x.diagnosis;
  const s = d.stats;
  const top = d.findings.slice(0, 3);
  const rest = d.findings.slice(3);
  const realized = Object.entries(x.summary.realized);
  return (
    <section className="today why" aria-label="왜 손해를 보고 있나" data-testid="why-losing">
      <div className="t-kicker">왜 수익이 안 나고 손해를 보나</div>
      {s ? (
        <div className="kpis">
          <Kpi label="실현 손익(비용 반영)" tone={tone(realized[0]?.[1].net)} sub={realized.length > 1 ? "통화별로 따로 — 환율로 합치지 않음" : `수수료·세금 ${money(realized[0]?.[1].fees, realized[0]?.[0] ?? "USD")} 포함`}>
            {realized.length ? realized.map(([cur, v]) => <div key={cur}>{money(v.net, cur)}</div>) : "—"}
          </Kpi>
          <Kpi label="승률" sub={`매도 ${d.closed}건 중 이익 ${s.wins} · 손실 ${s.losses}`}>{Math.round(s.win_rate * 100)}%</Kpi>
          <Kpi label="평균 손실 vs 평균 이익" sub={s.payoff !== null ? `손익비 ${s.payoff.toFixed(2)} — 1보다 작으면 손실이 이익보다 큼` : "이긴 매도 또는 진 매도가 없음"}><PayoffBar win={s.avg_win_pct} loss={s.avg_loss_pct} /></Kpi>
          <Kpi label="매도 1건당 기대값" tone={tone(s.expectancy_pct)} sub={s.median_hold_days_loss !== null && s.median_hold_days_win !== null ? `보유 중앙값: 이익 ${s.median_hold_days_win.toFixed(1)}일 · 손실 ${s.median_hold_days_loss.toFixed(1)}일` : undefined}>{pp(s.expectancy_pct, 2)}</Kpi>
        </div>
      ) : <p className="lead" style={{ marginTop: 8 }}>{d.headline}</p>}
      {d.cause && <p className="cause">{d.cause}</p>}
      {top.length > 0 ? (
        <div className="fixes">
          {top.map((f, i) => <FixCard key={f.id} f={f} n={i + 1} />)}
        </div>
      ) : <p className="muted" style={{ marginTop: 10 }}>{d.closed ? "반복되는 손실 습관을 찾지 못했습니다." : "매수 기록이 있는 매도가 생기면 진단합니다."}</p>}
      {rest.length > 0 && (
        <Disclosure title={`다른 발견 ${rest.length}개`} hint={rest.map((f) => f.title).join(" · ")}>
          <div className="fixes rest">{rest.map((f, i) => <FixCard key={f.id} f={f} n={i + 4} />)}</div>
        </Disclosure>
      )}
    </section>
  );
}

function FixCard({ f, n }: { f: Finding; n: number }) {
  return (
    <article className="fix" data-testid={`finding-${f.id}`}>
      <div className="fix-head"><span className="fix-n">{n}</span><b>{f.title}</b>{f.confidence === "표본 적음" && <span className="pill quiet" title="매도 5건 미만 — 참고만">표본 적음</span>}</div>
      <div className="fix-ev">{f.evidence}</div>
      <div className="fix-do"><span className="fix-arrow" aria-hidden>→</span><span><b>고칠 점</b> {f.fix}</span></div>
    </article>
  );
}

function PercentField({ label, value, onChange, hint, step = 0.5, min, max }: { label: string; value: number; onChange: (v: number) => void; hint?: string; step?: number; min?: number; max?: number }) {
  return (
    <label className="pfield">
      <span className="pf-l">{label}</span>
      <span className="pf-in"><input aria-label={label} type="number" inputMode="decimal" step={step} min={min} max={max} value={Number.isFinite(value) ? String(value) : ""}
        onChange={(e) => onChange(e.target.value === "" ? NaN : Number(e.target.value))} /><em>%</em></span>
      {hint && <span className="caption">{hint}</span>}
    </label>
  );
}

function RulesCard({ x, onSaved }: { x: Report; onSaved: () => void }) {
  const [t, setT] = useState<TradeRules>(x.trade_rules);
  const [pat, setPat] = useState<PatternRules>(x.rules);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setT(x.trade_rules); setPat(x.rules); }, [x.trade_rules, x.rules]);
  const sug = x.diagnosis.suggested;
  const dirty = JSON.stringify({ ...t, saved_at: null }) !== JSON.stringify({ ...x.trade_rules, saved_at: null });
  const save = async (trade: TradeRules | null, pattern: PatternRules | null) => {
    if (busy || x.is_sample) return;
    setBusy(true); setErr(null); setOk(null);
    try { await api.put("/habits/rules", { trade, pattern }); onSaved(); setOk("저장했습니다. 보유 종목의 '지금 할 일'과 진단이 이 규칙으로 바로 다시 계산됩니다."); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const ex = 100;  // the preview: a purchase at 100
  // the same check the server makes on save: an add at or above the first take-profit would fire with it
  const clash = t.add_mode === "winners_only" && t.max_adds > 0 && t.add_trigger_pct >= t.take1_pct;
  const preview = ([
    { key: "stop", label: "손절", value: ex * (1 + t.stop_pct / 100), tone: "neg" },
    { key: "avg", label: "매수가", value: ex, tone: "neutral" },
    { key: "take1", label: `익절 ${Math.round(t.take1_fraction * 100)}%`, value: ex * (1 + t.take1_pct / 100), tone: "pos" },
    ...(t.add_mode === "winners_only" && !clash ? [{ key: "add", label: "추가매수", value: ex * (1 + t.add_trigger_pct / 100), tone: "buy" }] : []),
  ] as Level[]).filter((l) => Number.isFinite(l.value));
  const diff = sug ? (["stop_pct", "take1_pct", "add_mode", "add_trigger_pct"] as const).filter((k) => sug.rules[k] !== t[k]) : [];
  return (
    <div id="rules">
      <Card title="내 매매 규칙 — 기계적으로 따를 기준" testId="rules-card"
        explain="여기서 정한 규칙이 보유 종목마다 손절가·익절가·추가매수 조건이 되고, 포트폴리오·종목 화면·홈에 '지금 할 일'로 나타납니다. 저장한 뒤에 산 종목은 이 손절가가 '매수 전에 정한 손절가'로 기록됩니다."
        right={<span className={`pill ${x.rules_saved ? "" : "quiet"}`}>{x.rules_saved ? `저장됨 · ${new Date(x.trade_rules.saved_at ?? "").toLocaleDateString("ko-KR")}` : "기본값 사용 중"}</span>}>
        {sug && diff.length > 0 && (
          <div className="suggest" data-testid="rules-suggested">
            <div className="suggest-head"><b>내 거래 기록으로 계산한 제안</b>
              <button className="sm primary" disabled={x.is_sample} onClick={() => setT({ ...t, ...sug.rules, saved_at: t.saved_at })}>제안값을 입력칸에 넣기</button></div>
            <div className="chips">{diff.map((k) => <span key={k} className="chip">{k === "stop_pct" ? "손절" : k === "take1_pct" ? "1차 익절" : k === "add_trigger_pct" ? "추가매수 기준" : "추가매수"} <s>{k === "add_mode" ? ADD_KO[t[k]] : `${t[k]}%`}</s> → <b>{k === "add_mode" ? ADD_KO[sug.rules[k]] : `${sug.rules[k]}%`}</b></span>)}</div>
            <ul className="caption">{sug.why.map((w, i) => <li key={i}>{w}</li>)}</ul>
          </div>
        )}
        {sug && diff.length === 0 && <div className="caption suggest-same" data-testid="rules-suggested">내 거래 기록으로 계산한 제안과 지금 규칙이 같습니다. {sug.why[0]}</div>}
        <div className="rules-body">
          <div className="rules-form">
            <fieldset><legend>손절</legend>
              <PercentField label="손절 (평단 대비 %)" value={t.stop_pct} onChange={(v) => setT({ ...t, stop_pct: v })} hint="여기에 닿으면 전량 매도" max={0} />
              <label className="check"><input type="checkbox" checked={t.use_app_stop} onChange={(e) => setT({ ...t, use_app_stop: e.target.checked })} /><span>앱 분석 손절가가 더 높으면 그것 사용</span></label>
            </fieldset>
            <fieldset><legend>익절</legend>
              <PercentField label="1차 익절 (평단 대비 %)" value={t.take1_pct} onChange={(v) => setT({ ...t, take1_pct: v })} min={0} />
              <PercentField label="1차 익절 때 파는 비율" value={Math.round(t.take1_fraction * 100)} step={10} min={10} max={100} onChange={(v) => setT({ ...t, take1_fraction: v / 100 })} />
              <PercentField label="나머지: 고점 대비 추적 손절" value={t.trail_pct} onChange={(v) => setT({ ...t, trail_pct: v })} hint="1차 익절가에 닿은 뒤부터" max={0} />
            </fieldset>
            <fieldset><legend>추가매수</legend>
              <div className="seg" role="tablist" aria-label="추가매수 방식">
                {Object.entries(ADD_KO).map(([k, v]) => <button key={k} type="button" role="tab" aria-selected={t.add_mode === k} onClick={() => setT({ ...t, add_mode: k })}>{v}</button>)}
              </div>
              {t.add_mode === "winners_only" && <PercentField label="평단보다 이만큼 올랐을 때" value={t.add_trigger_pct} onChange={(v) => setT({ ...t, add_trigger_pct: v })} min={0} />}
              {t.add_mode !== "none" && <>
                <PercentField label="첫 매수 수량 대비" value={Math.round(t.add_fraction * 100)} step={10} min={10} max={200} onChange={(v) => setT({ ...t, add_fraction: v / 100 })} />
                <label className="pfield"><span className="pf-l">추가매수 최대 횟수</span><span className="pf-in"><input aria-label="추가매수 최대 횟수" type="number" step={1} min={0} max={5} value={t.max_adds} onChange={(e) => setT({ ...t, max_adds: Number(e.target.value) })} /><em>회</em></span></label>
              </>}
              {clash && <div className="caption warn" data-testid="rules-clash">추가매수 기준(+{t.add_trigger_pct}%)이 1차 익절(+{t.take1_pct}%)과 같거나 높습니다. 같은 가격에서 '절반 매도'와 '추가매수'가 겹치니, 추가매수 기준을 1차 익절보다 낮게 정하세요(예: +{Math.max(0.5, Math.floor(Math.round(t.take1_pct) / 2 / 0.5) * 0.5)}%).</div>}
              {t.add_mode === "any" && <div className="caption warn">물타기를 허용하면 손실 중인 종목에 돈이 더 들어가 손절이 어려워집니다.</div>}
            </fieldset>
          </div>
          <div className="rules-preview">
            <div className="t-kicker">미리보기 · {ex}달러에 샀다면</div>
            <PriceLadder levels={preview} currency="USD" testId="rules-preview" />
            <ol className="preview-steps">
              <li><b className="neg">{money(ex * (1 + t.stop_pct / 100), "USD")}</b> 아래로 내려가면 전부 판다</li>
              <li><b className="pos">{money(ex * (1 + t.take1_pct / 100), "USD")}</b>에 닿으면 {Math.round(t.take1_fraction * 100)}%를 판다</li>
              <li>그 뒤 나머지는 최고가에서 {Math.abs(t.trail_pct)}% 내려오면 판다</li>
              <li>{t.add_mode === "none" ? "추가매수는 하지 않는다" : clash ? <span className="warn">추가매수 기준이 1차 익절과 겹쳐 실행되지 않는다 — 고쳐야 함</span> : t.add_mode === "winners_only" ? <>추가매수는 <b className="buy">{money(ex * (1 + t.add_trigger_pct / 100), "USD")}</b> 이상일 때만, 첫 매수의 {Math.round(t.add_fraction * 100)}%씩 {t.max_adds}번까지</> : `추가매수는 가격 조건 없이 첫 매수의 ${Math.round(t.add_fraction * 100)}%씩 ${t.max_adds}번까지`}</li>
            </ol>
          </div>
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="primary" disabled={busy || x.is_sample || clash || (!dirty && x.rules_saved)} onClick={() => void save(t, null)}>{busy ? "저장 중…" : "규칙 저장"}</button>
          {dirty && !x.is_sample && <button className="ghost" onClick={() => setT(x.trade_rules)}>되돌리기</button>}
          {x.is_sample && <span className="caption">가상 샘플에서는 규칙을 저장하지 않습니다</span>}
        </div>
        <Err error={err} />{ok && <Notice tone="info">{ok}</Notice>}
        <Disclosure title="패턴 판정 기준" hint={`본전 탈출: 하락 ${pat.dip_pct}% 이하 → 순수익률 ${pat.exit_min_pct}~${pat.exit_max_pct}% 매도 · ${pat.repeat_min}회 이상 반복 · 이른 청산: 매도 후 ${pat.early_days}거래일 +${pat.early_rise_pct}%`}>
          <div className="rules-pattern">
            <PercentField label="매수 후 하락폭 (이하)" value={pat.dip_pct} onChange={(v) => setPat({ ...pat, dip_pct: v })} max={0} />
            <PercentField label="탈출 순수익률 하한" value={pat.exit_min_pct} onChange={(v) => setPat({ ...pat, exit_min_pct: v })} />
            <PercentField label="탈출 순수익률 상한" value={pat.exit_max_pct} onChange={(v) => setPat({ ...pat, exit_max_pct: v })} />
            <PercentField label="이른 청산: 매도 후 상승" value={pat.early_rise_pct} onChange={(v) => setPat({ ...pat, early_rise_pct: v })} min={0} />
            <label className="pfield"><span className="pf-l">이른 청산: 관찰 기간</span><span className="pf-in"><input aria-label="이른 청산 관찰 기간" type="number" min={1} max={60} value={pat.early_days} onChange={(e) => setPat({ ...pat, early_days: Number(e.target.value) })} /><em>거래일</em></span></label>
            <label className="pfield"><span className="pf-l">반복 기준</span><span className="pf-in"><input aria-label="반복 기준" type="number" min={1} value={pat.repeat_min} onChange={(e) => setPat({ ...pat, repeat_min: Number(e.target.value) })} /><em>회</em></span></label>
          </div>
          <button className="sm" disabled={busy || x.is_sample} onClick={() => void save(null, pat)}>기준 저장 후 다시 분석</button>
        </Disclosure>
      </Card>
    </div>
  );
}

function Patterns({ x }: { x: Report }) {
  const s = x.summary;
  const be = s.breakeven;
  return (
    <Card title="반복되는 패턴" testId="habit-summary" explain="매도 주문 1건을 1회로 셉니다(여러 매수 lot·부분 체결로 나뉘어도 한 번). 비율의 분모는 가격 자료가 있어 판정할 수 있었던 매도 주문입니다.">
      <div className="pattern-tiles">
        <div className="ptile" data-testid="tile-breakeven">
          <div className="t-kicker">물렸다가 본전 근처 탈출</div>
          <div className="pt-v">{be.candidates}회{be.evaluable ? <small> / {be.evaluable}</small> : null}</div>
          <div className="caption">{be.ratio === null ? "판정할 수 있는 매도 없음" : `평가 가능한 매도의 ${Math.round(be.ratio * 100)}%`}{be.repeated ? " · 반복" : ""}{be.partial ? ` · 일부 수량 ${be.partial}` : ""}{be.unconfirmed ? ` · 미확정 ${be.unconfirmed}` : ""}</div>
        </div>
        <div className="ptile"><div className="t-kicker">이른 청산(사후 비교)</div><div className="pt-v">{s.early_exit.candidates}회<small> / {s.early_exit.evaluable}</small></div><div className="caption">매도 후 5거래일 안에 +5% 이상 — 당시 판단이 틀렸다는 뜻은 아님</div></div>
        <div className="ptile"><div className="t-kicker">손절 검토</div><div className="pt-v">{s.stop_review.candidates}회</div><div className="caption">사전 손절가 없음 {s.stop_review.no_stop}건</div></div>
        <div className="ptile muted"><div className="t-kicker">판정 불가</div><div className="pt-v">{s.data_short}건</div><div className="caption">가격 자료 부족 또는 매수 기록 없음 — '후보 없음'과 다름</div></div>
      </div>
      {x.by_symbol.length > 0 && (
        <div className="scroll" style={{ marginTop: 14 }}><table className="tight-table" data-testid="by-symbol"><thead><tr><th>종목</th><th className="num">매도</th><th className="num">본전 탈출</th><th className="num">이른 청산</th><th className="num">손절 검토</th><th className="num">판정 불가</th><th className="num">실현 손익</th></tr></thead>
          <tbody>{x.by_symbol.map((b) => <tr key={b.symbol}><td><b>{b.symbol}</b>{b.repeated && <span className="plan-act neg" style={{ marginLeft: 6 }}>반복</span>}</td><td className="num">{b.sell_orders}</td>
            <td className="num">{b.candidates}/{b.evaluable}{b.ratio !== null ? <span className="caption"> {Math.round(b.ratio * 100)}%</span> : null}</td><td className="num">{b.early}</td><td className="num">{b.stop_review}</td><td className="num">{b.insufficient}</td>
            <td className={`num ${tone(b.net_pnl)}`}>{money(b.net_pnl, b.currency)}</td></tr>)}</tbody></table></div>
      )}
    </Card>
  );
}

function TradeLog({ x, sample, onExtend, busy }: { x: Report; sample: boolean; onExtend: (sym: string) => void; busy: string }) {
  const [sym, setSym] = useState("");
  const [pat, setPat] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const symbols = useMemo(() => [...new Set(x.trades.map((t) => t.symbol))].sort(), [x.trades]);
  const rows = x.trades.filter((t) => (!sym || t.symbol === sym) && (!pat || t.pattern === pat || (pat === "EARLY" && t.early?.status === "CANDIDATE") || (pat === "STOP" && ["TOUCHED_HELD", "EXIT_BELOW"].includes(t.stop.status)))
    && (!from || t.sold_at.slice(0, 10) >= from) && (!to || t.sold_at.slice(0, 10) <= to));
  const filtered = !!(sym || pat || from || to);
  return (
    <Card title="거래 로그" testId="trade-log" explain="한 줄 = 매도 주문의 FIFO 매칭 행. 줄을 누르면 매수부터 매도까지의 가격 흐름, 계산 근거, 원본 체결 기록, 메모가 열립니다.">
      <div className="filters">
        <select aria-label="종목 필터" value={sym} onChange={(e) => setSym(e.target.value)}><option value="">전체 종목</option>{symbols.map((s) => <option key={s}>{s}</option>)}</select>
        <select aria-label="패턴 필터" value={pat} onChange={(e) => setPat(e.target.value)}><option value="">전체 판정</option>{Object.entries(PATTERN_KO).map(([k, v]) => <option key={k} value={k}>{v}</option>)}<option value="EARLY">이른 청산 후보</option><option value="STOP">손절 검토 후보</option></select>
        <span className="date-range"><input aria-label="시작일" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /><span>~</span><input aria-label="종료일" type="date" value={to} onChange={(e) => setTo(e.target.value)} /></span>
        <span className="caption">{rows.length}/{x.trades.length}줄</span>
        {filtered && <button className="ghost sm" onClick={() => { setSym(""); setPat(""); setFrom(""); setTo(""); }}>필터 지우기</button>}
      </div>
      {!rows.length ? <Empty>{x.trades.length ? "조건에 맞는 거래가 없습니다." : "아직 매도 체결이 없습니다."}</Empty> : (
        <div className="scroll"><table className="trade-table">
          <thead><tr><th>종목</th><th className="hide-sm">보유</th><th className="num hide-sm">수량</th><th className="num hide-sm">진입 → 청산</th><th className="num">순손익</th><th className="num hide-sm" title="보유 중 최대 하락 / 최대 상승(진입가 대비)">MAE / MFE</th><th>판정</th></tr></thead>
          <tbody>{rows.map((t) => {
            const isOpen = open === t.id;
            const flags = [t.early?.status === "CANDIDATE" ? "이른 청산(사후)" : null, ["TOUCHED_HELD", "EXIT_BELOW"].includes(t.stop.status) ? STOP_KO[t.stop.status] : null,
              (t.path?.missing_days.length ?? 0) > 0 ? `일봉 ${t.path!.missing_days.length}일 누락` : null, t.has_note ? "메모" : null].filter(Boolean);
            return (
              <Fragment key={t.id}>
                <tr className={`clickable${t.pattern === "CANDIDATE" ? " flag" : ""}${isOpen ? " open" : ""}`} onClick={() => setOpen(isOpen ? null : t.id)} data-testid={`trade-${t.id}`} aria-expanded={isOpen}>
                  <td className="nowrap"><b>{t.symbol}</b>{t.row_of_order > 0 && <div className="caption" title="한 매도 주문이 여러 매수 lot과 짝지어진 경우">같은 매도 {t.row_of_order + 1}행</div>}<div className="caption show-sm">{md(t.bought_at)} → {md(t.sold_at)}</div></td>
                  <td className="nowrap hide-sm">{md(t.bought_at)} → {md(t.sold_at)}<div className="caption">{t.holding_days !== null ? `${t.holding_days < 1 ? "1일 미만" : `${Math.round(t.holding_days)}일`}` : "매수 기록 없음"}</div></td>
                  <td className="num hide-sm">{shares(t.quantity)}</td>
                  <td className="num nowrap hide-sm">{money(t.entry, t.currency)} → {money(t.exit, t.currency)}</td>
                  <td className={`num ${tone(t.net_pnl)}`}>{money(t.net_pnl, t.currency)}<div className="caption">{pp(t.net_ret, 2)}</div></td>
                  <td className="num nowrap hide-sm"><span className="neg">{pp(t.mae)}</span> / <span className="pos">{pp(t.mfe)}</span></td>
                  <td><span className={`plan-act ${PATTERN_TONE[t.pattern]}`}>{PATTERN_KO[t.pattern]}</span>
                    {flags.length > 0 && <div className="caption">{flags.join(" · ")}</div>}
                    {t.pattern === "NEED_BASIS" && !sample && <button className="sm" style={{ marginTop: 4 }} disabled={!!busy} onClick={(e) => { e.stopPropagation(); onExtend(t.symbol); }}>{busy === `ext:${t.symbol}` ? "조회 중…" : "기간 확대 조회"}</button>}
                  </td>
                </tr>
                {isOpen && <tr className="detail-row"><td colSpan={7}><TradeDetail id={t.id} sample={sample} /></td></tr>}
              </Fragment>
            );
          })}</tbody></table></div>
      )}
    </Card>
  );
}

function TradeDetail({ id, sample }: { id: string; sample: boolean }) {
  const d = useApi<Detail>(`/habits/trades/${encodeURIComponent(id)}${sample ? "?sample=1" : ""}`, [id, sample]);
  const [note, setNote] = useState({ buy_reason: "", during: "", sell_reason: "" });
  const [stop, setStop] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (d.data) { setNote({ buy_reason: d.data.note.buy_reason ?? "", during: d.data.note.during ?? "", sell_reason: d.data.note.sell_reason ?? "" }); setStop(d.data.user_stop ? String(d.data.user_stop.stop) : ""); } }, [d.data]);
  if (!d.data) return d.error ? <Err error={d.error} retry={d.reload} /> : <div className="muted" style={{ padding: 12 }}><span className="spin" />가격 흐름을 불러오는 중…</div>;
  const x = d.data;
  const t = x.trade;
  const save = async (f: () => Promise<unknown>, ok: string) => {
    setErr(null); setMsg(null);
    try { await f(); setMsg(ok); d.reload(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <div className="trade-detail" data-testid="trade-detail">
      {x.is_sample && <div className="caption warn">가상 샘플 거래입니다</div>}
      <div className="td-grid">
        <div className="td-chart">
          <PathChart bars={x.bars} entry={t.entry} exit={t.exit} stop={t.stop.stop} target={t.stop.target} buy={t.bought_at} sell={t.sold_at} mae={t.mae} />
          <div className="caption">{t.path ? `${t.path.interval} · ${day(t.path.first_bar)}~${day(t.path.last_bar)}${t.path.missing_days.length ? ` · 누락 ${t.path.missing_days.join(", ")}` : ""}` : "가격 경로 없음"}</div>
          {t.path?.notes.map((n, i) => <div key={i} className="caption">· {n}</div>)}
          {t.path && t.path.status !== "OK" && <div className="caption warn">· {t.path.reason}</div>}
        </div>
        <div className="td-side">
          <div className={`td-verdict ${PATTERN_TONE[t.pattern]}`}><span className={`plan-act ${PATTERN_TONE[t.pattern]}`}>{PATTERN_KO[t.pattern]}</span><p>{t.pattern_reason}</p></div>
          <dl className="td-facts">
            <div><dt>진입 → 청산</dt><dd>{money(t.entry, t.currency)} → {money(t.exit, t.currency)}</dd></div>
            <div><dt>순손익</dt><dd className={tone(t.net_pnl)}>{money(t.net_pnl, t.currency)} ({pp(t.net_ret, 2)})</dd></div>
            <div><dt>비용 전</dt><dd>{money(t.gross_pnl, t.currency)} ({pp(t.gross_ret, 2)})</dd></div>
            <div><dt>수수료·세금</dt><dd>{money(t.buy_fees + t.sell_fees, t.currency)}</dd></div>
            <div><dt>보유 중 최저 / 최고</dt><dd><span className="neg">{pp(t.mae, 2)}</span> / <span className="pos">{pp(t.mfe, 2)}</span></dd></div>
          </dl>
          {t.early && <div className="td-review"><b>매도 후 검토</b> {EARLY_KO[t.early.status] ?? t.early.status} — {t.early.reason}{t.early.max_fall !== null ? ` · 같은 기간 최대 하락 ${pp(t.early.max_fall, 2)}` : ""}</div>}
          <div className="td-review"><b>손절 검토</b> {STOP_KO[t.stop.status] ?? t.stop.status}{t.stop.reason && t.stop.reason !== STOP_KO[t.stop.status] ? ` — ${t.stop.reason}` : ""}</div>
          {x.basis_note && <Notice tone="info">{x.basis_note}{x.order?.avg_cost_view?.avg_cost ? ` 평균단가 ${money(x.order.avg_cost_view.avg_cost, t.currency)} 기준 순수익률 ${pp(x.order.avg_cost_view.net_ret, 2)}.` : ""}{x.order && x.order.candidate_quantity > 0 && x.order.candidate_quantity < x.order.known_quantity ? ` 이 매도 중 ${shares(x.order.candidate_quantity)}주만 조건에 맞습니다.` : ""}</Notice>}
          <Disclosure title="계산 근거"><ul className="caption calc">{x.calc.map((c, i) => <li key={i}>{c}</li>)}</ul></Disclosure>
          <Disclosure title="원본 체결 기록"><pre className="raw">{JSON.stringify({ 매수: x.raw.buy, 매도: x.raw.sell }, null, 1)}</pre></Disclosure>
        </div>
      </div>
      {!x.is_sample && (
        <div className="td-notes">
          <div>
            <div className="t-kicker">내 메모 — 그때 무슨 생각이었나</div>
            <div className="note-grid">
              {([["buy_reason", "매수 근거"], ["during", "하락 중 생각"], ["sell_reason", "매도 이유"]] as const).map(([k, l]) => (
                <label key={k}><span className="caption">{l}</span><textarea aria-label={l} rows={2} value={note[k]} placeholder={k === "during" ? "예: 곧 회복할 거라 생각했다" : undefined} onChange={(e) => setNote({ ...note, [k]: e.target.value })} /></label>
              ))}
            </div>
            <button className="sm" onClick={() => void save(() => api.put(`/habits/notes/${encodeURIComponent(t.id)}`, note), "메모를 저장했습니다")}>메모 저장</button>
            {x.note.updated_at && <span className="caption"> 마지막 저장 {new Date(x.note.updated_at).toLocaleString("ko-KR")}</span>}
          </div>
          {t.buy_order_id && (
            <div className="stop-entry">
              <div className="t-kicker">이 매수의 손절가</div>
              <div className="row tight"><span className="pf-in"><input aria-label="손절가 입력" inputMode="decimal" value={stop} onChange={(e) => setStop(e.target.value)} placeholder="예: 95" /></span>
                <button className="sm" onClick={() => void save(() => api.put(`/habits/stops/${encodeURIComponent(t.buy_order_id!)}`, { stop: stop.trim() ? Number(stop) : null }), "손절가를 저장했습니다")}>저장</button></div>
              <div className="caption">지금 넣으면 '사후 입력'으로 표시됩니다. '내 매매 규칙'을 저장해 두면 다음 매수부터 손절가가 미리 기록됩니다.</div>
            </div>
          )}
        </div>
      )}
      <Err error={err} />{msg && <Notice tone="info">{msg}</Notice>}
    </div>
  );
}

/** Daily high–low ranges with the close, the holding window shaded, the entry/exit/stop/target levels and the
 * deepest certain dip — what happened between buying and selling. */
export function PathChart({ bars, entry, exit, stop, target, buy, sell, mae }: { bars: Detail["bars"]; entry: number | null; exit: number; stop: number | null; target: number | null; buy: string | null; sell: string; mae?: number | null }) {
  if (!bars.length) return <Empty>보유 기간 일봉이 없습니다.</Empty>;
  const W = 520, H = 210, padL = 8, padR = 64, padT = 10, padB = 18;
  const lv = [entry, exit, stop, target].filter((v): v is number => v !== null);
  const lo = Math.min(...bars.map((b) => b.low), ...lv), hi = Math.max(...bars.map((b) => b.high), ...lv);
  const y = (v: number) => padT + (H - padT - padB) * (1 - (v - lo) / (hi - lo || 1));
  const bw = (W - padL - padR) / bars.length;
  const xi = (i: number) => padL + bw * (i + 0.5);
  const idx = (iso: string | null) => (iso ? bars.findIndex((b) => b.day >= iso.slice(0, 10)) : -1);
  const bi = idx(buy), si = idx(sell);
  const levels = [
    entry !== null ? { v: entry, c: "var(--accent)", l: "진입" } : null, { v: exit, c: "var(--text-2)", l: "청산" },
    stop !== null ? { v: stop, c: "var(--down)", l: "손절" } : null, target !== null ? { v: target, c: "var(--up)", l: "목표" } : null,
  ].filter((v): v is { v: number; c: string; l: string } => v !== null).sort((a, b) => b.v - a.v);
  let last = -99;
  const labelY = levels.map((l) => { const yy = Math.max(y(l.v) + 3, last + 11); last = yy; return yy; });  // labels never overlap
  const dipPrice = entry !== null && mae != null && mae < 0 ? entry * (1 + mae / 100) : null;
  const close = bars.map((b, i) => `${i ? "L" : "M"}${xi(i).toFixed(1)},${y(b.close).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="매수부터 매도까지 가격 흐름" data-testid="path-chart" className="path-chart">
      {bi >= 0 && si >= bi && <rect x={xi(bi) - bw / 2} y={padT} width={xi(si) - xi(bi) + bw} height={H - padT - padB} fill="var(--accent-soft)" />}
      {bars.map((b, i) => <line key={b.day} x1={xi(i)} x2={xi(i)} y1={y(b.high)} y2={y(b.low)} stroke="var(--line-3)" strokeWidth={Math.max(1.5, bw * 0.45)} strokeLinecap="round" />)}
      <path d={close} fill="none" stroke="var(--sub)" strokeWidth={1.2} />
      {levels.map((l, i) => <g key={l.l}><line x1={padL} x2={W - padR} y1={y(l.v)} y2={y(l.v)} stroke={l.c} strokeDasharray="5 4" strokeWidth={1.1} />
        <text x={W - padR + 4} y={labelY[i]} fontSize="10.5" fill={l.c}>{l.l} {l.v.toFixed(2)}</text></g>)}
      {dipPrice !== null && <circle cx={xi(bars.reduce((k, b, i) => (bi <= i && i <= si && b.low < bars[k]!.low ? i : k), Math.max(0, bi)))} cy={y(dipPrice)} r={3.5} fill="var(--down)" />}
      {bi >= 0 && <text x={xi(bi)} y={H - 4} fontSize="10" textAnchor="middle" fill="var(--accent)">매수</text>}
      {si >= 0 && <text x={xi(si)} y={H - 4} fontSize="10" textAnchor="middle" fill="var(--text-2)">매도</text>}
    </svg>
  );
}
