import { Fragment, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, baseUrl } from "../api";
import { Card, Disclosure, Empty, Err, Loading, Notice, Ribbon, StatePanel, Tabs } from "../components/ui";
import { useApi } from "../components/useApi";
import { HoldingPlans, money, pp } from "../components/HoldingPlans";
import { day, shares } from "../format";

type Pattern = "CANDIDATE" | "PARTIAL" | "NOT" | "UNCONFIRMED" | "INSUFFICIENT" | "NEED_BASIS";
export const PATTERN_KO: Record<Pattern, string> = {
  CANDIDATE: "본전 탈출 후보", PARTIAL: "일부 수량만 후보", NOT: "후보 아님", UNCONFIRMED: "미확정(봉 안 순서 불명)",
  INSUFFICIENT: "판정 불가(가격 자료 부족)", NEED_BASIS: "기초 매수 기록 필요",
};
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
interface Diagnosis { closed: number; headline: string | null; cause?: string; findings: Finding[]; stats: Record<string, unknown> | null; suggested: { rules: TradeRules; why: string[] } | null }
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

const ADD_KO: Record<string, string> = { winners_only: "수익 중일 때만(불타기)", none: "추가매수 안 함", any: "조건 없음(물타기 허용)" };

/** 내 매매 진단 (성과 › 내 매매 진단): why the account is not making money and what to change, from the owner's own
 * executions (토스증권, read-only), the rules that turn it into "지금 할 일" for each holding, and the trade log with
 * each sale's price path. The virtual sample is a separate source with its own banner — never mixed in. */
export default function MyTrading() {
  const [sample, setSample] = useState(false);
  const r = useApi<Report>(`/habits${sample ? "?sample=1" : ""}`, [sample]);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    if (r.data && window.location.hash === "#rules") document.getElementById("rules")?.scrollIntoView?.({ block: "start" });
  }, [r.data]);
  const run = async (tag: string, f: () => Promise<unknown>, done?: string) => {
    if (busy) return;
    setBusy(tag); setErr(null); setMsg(null);
    try { await f(); r.reload(); if (done) setMsg(done); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(""); }
  };
  if (r.state === "loading" && !r.data) return <Loading what="내 매매 진단" steps={["토스증권 체결 기록 읽기", "매수·매도 짝 맞추기(FIFO)", "보유 기간 가격 확인", "습관 진단"]} />;
  if (!r.data) return <Err error={r.error} retry={r.reload} />;
  const x = r.data;
  const connected = x.is_sample || x.meta.connected;
  return (
    <div className="grid" data-testid="my-trading">
      <div className="row spread">
        <Tabs<"real" | "sample"> label="자료" value={sample ? "sample" : "real"} onChange={(v) => setSample(v === "sample")}
          items={[["real", "내 토스증권 계좌"], ["sample", "가상 샘플"]]} />
        <div className="row">
          {!x.is_sample && x.meta.connected && <button className="sm" disabled={!!busy} onClick={() => void run("refresh", () => api.post("/habits/refresh"), "새 체결을 읽었습니다")}>{busy === "refresh" ? "조회 중…" : "새 체결 조회"}</button>}
          {connected && <a className="btn sm" href={`${baseUrl()}/api/habits/export.csv${x.is_sample ? "?sample=1" : ""}`} download>CSV 내보내기</a>}
        </div>
      </div>
      {x.is_sample
        ? <Ribbon tone="danger" cap="가상 샘플" testId="sample-banner">실제 거래가 아닌 가상 체결·가상 가격입니다. 화면을 읽는 법을 보여주는 용도이며 내 계좌 분석과 섞이지 않습니다.</Ribbon>
        : <Ribbon tone="info" cap="실제 계좌 · 읽기 전용" testId="real-banner">토스증권에서 체결된 주문(체결 수량·평균 체결가·수수료·세금)만 읽어 분석합니다. 주문을 넣거나 바꾸는 기능은 없습니다.</Ribbon>}
      <Err error={err} />
      {msg && <Notice tone="info">{msg}</Notice>}
      {!connected ? (
        <StatePanel kind="insufficient" title="토스증권을 연결하면 내 매매를 진단합니다" what="체결 기록이 있어야 왜 손해가 나는지, 어떤 규칙이 필요한지 계산할 수 있습니다."
          actions={<><Link className="btn primary" to="/settings">토스증권 연결</Link><button onClick={() => setSample(true)}>가상 샘플로 화면 보기</button></>} />
      ) : (
        <>
          <Basis x={x} />
          <WhyLosing x={x} />
          {!x.is_sample && <HoldingPlans />}
          <RulesCard x={x} onSaved={r.reload} />
          <SummaryTiles x={x} />
          <BySymbol x={x} />
          <TradeLog x={x} sample={sample} onExtend={(sym) => void run(`ext:${sym}`, () => api.post("/habits/extend", { symbol: sym }), `${sym}: 전체 기간 체결을 다시 읽었습니다`)} busy={busy} />
          {x.open_lots.length > 0 && (
            <Card title="아직 보유 중인 수량(FIFO 잔여)" testId="open-lots">
              <table><thead><tr><th>종목</th><th className="num">수량</th><th className="num">매수가</th><th>매수 시각</th><th>근거</th></tr></thead>
                <tbody>{x.open_lots.map((o, i) => <tr key={i}><td>{o.symbol}</td><td className="num">{shares(o.quantity)}</td><td className="num">{money(o.entry, o.currency)}</td><td>{day(o.bought_at)}</td><td className="caption">{o.basis}</td></tr>)}</tbody></table>
            </Card>
          )}
          {x.warnings.length > 0 && <Disclosure title="자료 경고" hint={`${x.warnings.length}건`}>{x.warnings.map((w, i) => <Notice key={i} tone="warn">{w}</Notice>)}</Disclosure>}
        </>
      )}
    </div>
  );
}

function Basis({ x }: { x: Report }) {
  const f = x.meta.fills;
  return (
    <Card title="분석 범위" icon="ℹ" testId="habits-basis">
      <div className="kv">
        <span className="k">분석 기간</span><span>{day(x.period.first)} ~ {day(x.period.last)}</span>
        <span className="k">자료 갱신</span><span>{x.is_sample ? "가상 샘플" : f ? `토스 체결 ${f.count.toLocaleString("ko-KR")}건 · ${day(f.since)}부터 · ${f.complete ? "끝까지 조회" : "일부만 조회"} · 동기화 ${f.synced_at ? new Date(f.synced_at).toLocaleString("ko-KR") : "—"}` : "—"}</span>
        <span className="k">세는 단위</span><span>{x.summary.count_unit} · 매도 주문 {x.summary.sell_orders}건 → 매칭 행 {x.summary.matched_rows}개</span>
        <span className="k">가격 자료</span><span>미국 종목: 일봉(시가·고가·저가·종가) 관측치 · 국내 종목: 앱에 과거 일봉이 없어 가격 경로 판정 불가</span>
      </div>
      {(x.meta.skipped ?? []).map((s, i) => <div key={i} className="caption warn">{s}</div>)}
    </Card>
  );
}

function WhyLosing({ x }: { x: Report }) {
  const d = x.diagnosis;
  return (
    <section className="today" aria-label="왜 손해를 보고 있나" data-testid="why-losing">
      <div className="t-kicker">왜 수익이 안 나고 손해를 보나</div>
      <div className="big" style={{ marginTop: 6, fontSize: 18 }}>{d.headline}</div>
      {d.cause && <p className="lead" style={{ marginTop: 8 }}>{d.cause}</p>}
      {d.findings.length ? (
        <ol className="findings" style={{ marginTop: 10 }}>
          {d.findings.map((f) => (
            <li key={f.id} data-testid={`finding-${f.id}`}>
              <b>{f.title}</b>{f.confidence === "표본 적음" && <span className="pill" style={{ marginLeft: 6 }}>표본 적음</span>}
              <div className="caption">{f.evidence}</div>
              <div style={{ marginTop: 4 }}>→ <b>고칠 점:</b> {f.fix}</div>
            </li>
          ))}
        </ol>
      ) : <p className="muted">{d.closed ? "반복되는 손실 습관을 찾지 못했습니다." : "매수 기록이 있는 매도가 생기면 진단합니다."}</p>}
      <div className="caption" style={{ marginTop: 8 }}>체결 가격과 일봉만으로 계산한 결과입니다. 매도 당시의 생각이나 판단은 알 수 없으니, 거래 상세의 메모로 직접 남겨 비교하세요.</div>
    </section>
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
  const save = async (trade: TradeRules | null, pattern: PatternRules | null) => {
    if (busy || x.is_sample) return;
    setBusy(true); setErr(null); setOk(null);
    try { await api.put("/habits/rules", { trade, pattern }); onSaved(); setOk("저장했습니다. 보유 종목의 '지금 할 일'과 진단이 이 규칙으로 바로 다시 계산됩니다."); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const n = (k: keyof TradeRules, label: string, step = "0.5") => (
    <label><span>{label}</span><input aria-label={label} type="number" step={step} value={String(t[k] ?? "")} onChange={(e) => setT({ ...t, [k]: Number(e.target.value) })} /></label>
  );
  return (
    <div id="rules">
      <Card title="내 매매 규칙 — 기계적으로 따를 기준" testId="rules-card"
        explain="매수한 종목마다 이 규칙으로 손절가·1차 익절가·추적 손절·추가매수 조건이 정해지고, 포트폴리오·종목 화면·홈에 '지금 할 일'로 표시됩니다. 저장한 시각 이후의 매수에는 이 손절가가 '매수 전에 정한 손절가'로 기록됩니다.">
        <div className="caption" style={{ marginBottom: 8 }}>{x.rules_saved ? `저장한 규칙 · ${new Date(x.trade_rules.saved_at ?? "").toLocaleString("ko-KR")}` : "아직 저장하지 않은 기본값입니다"}</div>
        <div className="form-grid rules">
          {n("stop_pct", "손절: 평단 대비 %")}
          {n("take1_pct", "1차 익절: 평단 대비 %")}
          <label><span>1차 익절 때 매도 비율</span><input aria-label="1차 익절 매도 비율" type="number" step="0.1" min="0.1" max="1" value={t.take1_fraction} onChange={(e) => setT({ ...t, take1_fraction: Number(e.target.value) })} /></label>
          {n("trail_pct", "추적 손절: 고점 대비 %")}
          <label><span>추가매수</span><select aria-label="추가매수 방식" value={t.add_mode} onChange={(e) => setT({ ...t, add_mode: e.target.value })}>{Object.entries(ADD_KO).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          {n("add_trigger_pct", "추가매수: 평단 대비 % 이상")}
          <label><span>추가매수 수량(첫 매수 대비)</span><input aria-label="추가매수 수량 비율" type="number" step="0.1" value={t.add_fraction} onChange={(e) => setT({ ...t, add_fraction: Number(e.target.value) })} /></label>
          <label><span>추가매수 최대 횟수</span><input aria-label="추가매수 최대 횟수" type="number" step="1" min="0" max="5" value={t.max_adds} onChange={(e) => setT({ ...t, max_adds: Number(e.target.value) })} /></label>
          <label className="check"><input type="checkbox" checked={t.use_app_stop} onChange={(e) => setT({ ...t, use_app_stop: e.target.checked })} /><span>앱 분석 손절가가 더 높으면 그것을 사용</span></label>
        </div>
        {sug && (
          <div className="suggest" data-testid="rules-suggested" style={{ marginTop: 10 }}>
            <b>내 거래 기록으로 계산한 제안</b>: 손절 {sug.rules.stop_pct}% · 1차 익절 +{sug.rules.take1_pct}% · 추가매수 {ADD_KO[sug.rules.add_mode] ?? sug.rules.add_mode}
            <ul className="caption">{sug.why.map((w, i) => <li key={i}>{w}</li>)}</ul>
            <button className="sm" disabled={x.is_sample} onClick={() => setT({ ...t, ...sug.rules, saved_at: t.saved_at })}>제안값을 입력칸에 넣기</button>
          </div>
        )}
        <div className="row" style={{ marginTop: 10 }}>
          <button className="primary" disabled={busy || x.is_sample} onClick={() => void save(t, null)}>{busy ? "저장 중…" : "규칙 저장"}</button>
          {x.is_sample && <span className="caption">가상 샘플에서는 규칙을 저장하지 않습니다</span>}
        </div>
        <Err error={err} />{ok && <Notice tone="info">{ok}</Notice>}
        <Disclosure title="패턴 판정 기준(본전 탈출·이른 청산)" hint={`하락 ${pat.dip_pct}% · 탈출 ${pat.exit_min_pct}~${pat.exit_max_pct}% · ${pat.repeat_min}회 이상 반복 · 매도 후 ${pat.early_days}거래일 +${pat.early_rise_pct}%`}>
          <div className="form-grid rules">
            {([["dip_pct", "하락폭(이하, %)"], ["exit_min_pct", "탈출 순수익률 하한(%)"], ["exit_max_pct", "탈출 순수익률 상한(%)"], ["repeat_min", "반복 기준(회)"], ["early_days", "매도 후 관찰(거래일)"], ["early_rise_pct", "이른 청산 기준 상승(%)"]] as [keyof PatternRules, string][]).map(([k, l]) => (
              <label key={k}><span>{l}</span><input aria-label={l} type="number" step="0.5" value={pat[k]} onChange={(e) => setPat({ ...pat, [k]: Number(e.target.value) })} /></label>
            ))}
          </div>
          <button className="sm" disabled={busy || x.is_sample} onClick={() => void save(null, pat)}>기준 저장 후 다시 분석</button>
        </Disclosure>
      </Card>
    </div>
  );
}

function SummaryTiles({ x }: { x: Report }) {
  const s = x.summary;
  const be = s.breakeven;
  return (
    <div className="g4" data-testid="habit-summary">
      <Card title="실현 손익(비용 반영)">{Object.keys(s.realized).length ? Object.entries(s.realized).map(([cur, v]) => <div key={cur} className={`t-key ${v.net > 0 ? "pos" : v.net < 0 ? "neg" : ""}`}>{money(v.net, cur)}<div className="caption">수수료·세금 {money(v.fees, cur)}</div></div>) : <div className="t-key muted">—</div>}
        <div className="caption">통화별로 따로 표시(환율로 합치지 않음)</div></Card>
      <Card title="이익 매도 비율"><div className="t-key">{s.profit_ratio === null ? "—" : `${Math.round(s.profit_ratio * 100)}%`}</div><div className="caption">매수 기록이 있는 매도 주문 {s.known_orders}건 중 {s.profit_orders}건</div></Card>
      <Card title="본전 탈출 후보" testId="tile-breakeven"><div className="t-key">{be.candidates}회{be.evaluable ? ` / ${be.evaluable}` : ""}</div>
        <div className="caption">평가 가능한 매도 주문 대비 {be.ratio === null ? "—" : `${Math.round(be.ratio * 100)}%`}{be.repeated ? " · 반복 후보" : ""}{be.partial ? ` · 일부 수량 ${be.partial}` : ""}{be.unconfirmed ? ` · 미확정 ${be.unconfirmed}` : ""}</div></Card>
      <Card title="검토 후보 · 자료 부족"><div className="t-key">{s.stop_review.candidates + s.early_exit.candidates}건</div>
        <div className="caption">손절 검토 {s.stop_review.candidates} · 이른 청산(사후 비교) {s.early_exit.candidates}/{s.early_exit.evaluable} · 판정 불가 {s.data_short}</div></Card>
    </div>
  );
}

function BySymbol({ x }: { x: Report }) {
  if (!x.by_symbol.length) return null;
  return (
    <Card title="종목별 반복 패턴" testId="by-symbol">
      <div className="scroll"><table><thead><tr><th>종목</th><th className="num">매도 주문</th><th className="num">본전 탈출 후보 / 평가 가능</th><th className="num">판정 불가</th><th className="num">이른 청산</th><th className="num">손절 검토</th><th className="num">실현 손익</th></tr></thead>
        <tbody>{x.by_symbol.map((b) => <tr key={b.symbol}><td><b>{b.symbol}</b>{b.repeated && <span className="plan-act neg" style={{ marginLeft: 6 }}>반복</span>}</td><td className="num">{b.sell_orders}</td>
          <td className="num">{b.candidates} / {b.evaluable}{b.ratio !== null ? ` (${Math.round(b.ratio * 100)}%)` : ""}{b.partial ? ` · 일부 ${b.partial}` : ""}</td><td className="num">{b.insufficient}</td><td className="num">{b.early}</td><td className="num">{b.stop_review}</td>
          <td className={`num ${b.net_pnl > 0 ? "pos" : b.net_pnl < 0 ? "neg" : ""}`}>{money(b.net_pnl, b.currency)}</td></tr>)}</tbody></table></div>
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
  return (
    <Card title="거래 로그" testId="trade-log" explain="한 줄 = 매도 주문의 FIFO 매칭 행(한 매도가 여러 매수 lot에 나뉘면 여러 줄, 횟수는 매도 주문 단위로 셈). MAE·MFE는 매수 뒤·매도 주문 전의 시점이 확실한 일봉 관측치 기준입니다.">
      <div className="row" style={{ marginBottom: 8 }}>
        <select aria-label="종목 필터" value={sym} onChange={(e) => setSym(e.target.value)}><option value="">전체 종목</option>{symbols.map((s) => <option key={s}>{s}</option>)}</select>
        <select aria-label="패턴 필터" value={pat} onChange={(e) => setPat(e.target.value)}><option value="">전체 패턴</option>{Object.entries(PATTERN_KO).map(([k, v]) => <option key={k} value={k}>{v}</option>)}<option value="EARLY">이른 청산 후보</option><option value="STOP">손절 검토 후보</option></select>
        <label className="caption">매도일 <input aria-label="시작일" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /> ~ <input aria-label="종료일" type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
        <span className="caption">{rows.length}줄</span>
      </div>
      {!rows.length ? <Empty>조건에 맞는 거래가 없습니다.</Empty> : (
        <div className="scroll"><table>
          <thead><tr><th>종목</th><th>매수 → 매도</th><th className="num">수량</th><th className="num">진입 → 청산</th><th className="num">순손익(수익률)</th><th className="num">MAE / MFE</th><th>판정</th><th>경고</th></tr></thead>
          <tbody>{rows.map((t) => (
            <Fragment key={t.id}>
              <tr className="clickable" onClick={() => setOpen(open === t.id ? null : t.id)} data-testid={`trade-${t.id}`}>
                <td><b>{t.symbol}</b>{t.row_of_order > 0 && <div className="caption">같은 매도의 {t.row_of_order + 1}번째 행</div>}</td>
                <td className="caption">{day(t.bought_at)} → {day(t.sold_at)}{t.holding_days !== null ? <div>{t.holding_days.toFixed(1)}일</div> : null}</td>
                <td className="num">{shares(t.quantity)}</td>
                <td className="num">{money(t.entry, t.currency)} → {money(t.exit, t.currency)}</td>
                <td className={`num ${(t.net_pnl ?? 0) > 0 ? "pos" : (t.net_pnl ?? 0) < 0 ? "neg" : ""}`}>{money(t.net_pnl, t.currency)}<div className="caption">{pp(t.net_ret, 2)} (비용 전 {pp(t.gross_ret, 2)})</div></td>
                <td className="num">{pp(t.mae)} / {pp(t.mfe)}</td>
                <td><span className={`plan-act ${t.pattern === "CANDIDATE" ? "neg" : t.pattern === "NOT" ? "" : "muted"}`}>{PATTERN_KO[t.pattern]}</span></td>
                <td className="caption wrap" style={{ minWidth: 140 }}>
                  {t.early?.status === "CANDIDATE" && <div>이른 청산 후보(사후)</div>}
                  {["TOUCHED_HELD", "EXIT_BELOW"].includes(t.stop.status) && <div>{STOP_KO[t.stop.status]}</div>}
                  {(t.path?.missing_days.length ?? 0) > 0 && <div>일봉 {t.path!.missing_days.length}일 누락</div>}
                  {t.pattern === "NEED_BASIS" && !sample && <button className="sm" disabled={!!busy} onClick={(e) => { e.stopPropagation(); onExtend(t.symbol); }}>{busy === `ext:${t.symbol}` ? "조회 중…" : "기간 확대 조회"}</button>}
                  {t.has_note && <div>메모 있음</div>}
                </td>
              </tr>
              {open === t.id && <tr><td colSpan={8}><TradeDetail id={t.id} sample={sample} /></td></tr>}
            </Fragment>
          ))}</tbody></table></div>
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
  if (!d.data) return d.error ? <Err error={d.error} retry={d.reload} /> : <div className="muted">불러오는 중…</div>;
  const x = d.data;
  const t = x.trade;
  const save = async (f: () => Promise<unknown>, ok: string) => {
    setErr(null); setMsg(null);
    try { await f(); setMsg(ok); d.reload(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <div className="trade-detail" data-testid="trade-detail">
      {x.is_sample && <div className="caption warn">가상 샘플 거래입니다</div>}
      <div className="grid g2">
        <div>
          <PathChart bars={x.bars} entry={t.entry} exit={t.exit} stop={t.stop.stop} target={t.stop.target} buy={t.bought_at} sell={t.sold_at} />
          <div className="caption">{t.path ? `${t.path.interval} · ${day(t.path.first_bar)}~${day(t.path.last_bar)}${t.path.missing_days.length ? ` · 누락 ${t.path.missing_days.join(", ")}` : ""}` : "가격 경로 없음"}</div>
          {t.path?.notes.map((n, i) => <div key={i} className="caption">· {n}</div>)}
          {t.path && t.path.status !== "OK" && <div className="caption warn">· {t.path.reason}</div>}
        </div>
        <div>
          <div><b>{PATTERN_KO[t.pattern]}</b> — {t.pattern_reason}</div>
          {t.early && <div className="caption" style={{ marginTop: 4 }}>매도 후 검토: {EARLY_KO[t.early.status] ?? t.early.status} — {t.early.reason}{t.early.max_fall !== null ? ` · 같은 기간 최대 하락 ${pp(t.early.max_fall, 2)}` : ""}</div>}
          <div className="caption" style={{ marginTop: 4 }}>손절 검토: {STOP_KO[t.stop.status] ?? t.stop.status} — {t.stop.reason}</div>
          {x.basis_note && <Notice tone="info">{x.basis_note}{x.order?.avg_cost_view?.avg_cost ? ` 평균단가 ${money(x.order.avg_cost_view.avg_cost, t.currency)} 기준 순수익률 ${pp(x.order.avg_cost_view.net_ret, 2)}.` : ""}{x.order && x.order.candidate_quantity > 0 && x.order.candidate_quantity < x.order.known_quantity ? ` 이 매도 중 ${shares(x.order.candidate_quantity)}주만 조건에 맞습니다.` : ""}</Notice>}
          <Disclosure title="계산 근거" open><ul className="caption">{x.calc.map((c, i) => <li key={i}>{c}</li>)}</ul></Disclosure>
          <Disclosure title="원본 체결 기록"><pre className="raw">{JSON.stringify({ 매수: x.raw.buy, 매도: x.raw.sell }, null, 1)}</pre></Disclosure>
        </div>
      </div>
      {!x.is_sample && (
        <div className="grid g2" style={{ marginTop: 10 }}>
          <div>
            <div className="t-kicker">메모</div>
            {([["buy_reason", "매수 근거"], ["during", "하락 중 생각"], ["sell_reason", "매도 이유"]] as const).map(([k, l]) => (
              <label key={k} className="block"><span className="caption">{l}</span><textarea aria-label={l} rows={2} value={note[k]} onChange={(e) => setNote({ ...note, [k]: e.target.value })} /></label>
            ))}
            <button className="sm" onClick={() => void save(() => api.put(`/habits/notes/${encodeURIComponent(t.id)}`, note), "메모를 저장했습니다")}>메모 저장</button>
          </div>
          {t.buy_order_id && (
            <div>
              <div className="t-kicker">이 매수의 손절가</div>
              <div className="caption">지금 입력하면 '사후 입력'으로 표시됩니다(매수 전에 정한 값과 구분). 앞으로는 '내 매매 규칙'을 저장해 두면 매수 전 손절가가 자동으로 남습니다.</div>
              <input aria-label="손절가 입력" inputMode="decimal" value={stop} onChange={(e) => setStop(e.target.value)} placeholder="예: 95" />
              <button className="sm" onClick={() => void save(() => api.put(`/habits/stops/${encodeURIComponent(t.buy_order_id!)}`, { stop: stop.trim() ? Number(stop) : null }), "손절가를 저장했습니다")}>저장</button>
            </div>
          )}
        </div>
      )}
      <Err error={err} />{msg && <Notice tone="info">{msg}</Notice>}
    </div>
  );
}

/** Daily high–low bars with the entry, exit, stop and target levels and the purchase/sale days. */
export function PathChart({ bars, entry, exit, stop, target, buy, sell }: { bars: Detail["bars"]; entry: number | null; exit: number; stop: number | null; target: number | null; buy: string | null; sell: string }) {
  if (!bars.length) return <Empty>보유 기간 일봉이 없습니다.</Empty>;
  const W = 420, H = 160, pad = 6;
  const lv = [entry, exit, stop, target].filter((v): v is number => v !== null);
  const lo = Math.min(...bars.map((b) => b.low), ...lv), hi = Math.max(...bars.map((b) => b.high), ...lv);
  const y = (v: number) => pad + (H - 2 * pad) * (1 - (v - lo) / (hi - lo || 1));
  const bw = (W - 2 * pad) / bars.length;
  const xi = (i: number) => pad + bw * (i + 0.5);
  const idx = (iso: string | null) => (iso ? bars.findIndex((b) => b.day >= iso.slice(0, 10)) : -1);
  const line = (v: number | null, color: string, label: string) => v === null ? null : <g key={label}><line x1={pad} x2={W - pad} y1={y(v)} y2={y(v)} stroke={color} strokeDasharray="4 3" /><text x={W - pad} y={y(v) - 2} textAnchor="end" fontSize="9" fill={color}>{label} {v}</text></g>;
  const bi = idx(buy), si = idx(sell);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="매수부터 매도까지 가격 흐름" data-testid="path-chart">
      {bars.map((b, i) => <line key={b.day} x1={xi(i)} x2={xi(i)} y1={y(b.high)} y2={y(b.low)} stroke="var(--ink-3, #999)" strokeWidth={Math.max(1, bw * 0.5)} />)}
      {bi >= 0 && <line x1={xi(bi)} x2={xi(bi)} y1={pad} y2={H - pad} stroke="var(--accent)" />}
      {si >= 0 && <line x1={xi(si)} x2={xi(si)} y1={pad} y2={H - pad} stroke="var(--ink-2, #555)" />}
      {line(entry, "var(--accent)", "진입")}{line(exit, "var(--ink-2, #555)", "청산")}{line(stop, "var(--neg, #d33)", "손절")}{line(target, "var(--pos, #2a2)", "목표")}
    </svg>
  );
}
