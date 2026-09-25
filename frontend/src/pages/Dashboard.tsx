import { useState } from "react";
import { Link } from "react-router-dom";
import { advise } from "../advice";
import { api } from "../api";
import { OppTable } from "../components/OppTable";
import { Action, Card, Empty, Err, LineChart, Loading, Notice, Term } from "../components/ui";
import { useApi } from "../components/useApi";
import { day, pct, price, stamp } from "../format";
import { REGIME_KO, VETO_KO, ko } from "../i18n";
import { useMode } from "../mode";
import type { OppRow, ScanInfo } from "../types";

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
};
const HEALTH_KO: Record<string, string> = { HEALTHY: "정상", DEGRADED: "불안정", DOWN: "중단", UNKNOWN: "미확인" };
const SCAN_STEPS = ["전체 종목 1차 필터", "펀더멘털 선별", "뉴스·이슈 반영", "최종 점수·가격 계획", "상위 후보 AI 위원회"];

function OppCard({ r }: { r: OppRow }) {
  const a = advise({ action: r.action, price: r.price, maxBuy: r.max_buy, idealEntry: r.ideal_entry, stop: r.stop, rr: r.rr, eventRisk: r.risk, vetoes: r.vetoes, status: r.current_status, sectorKnown: r.sector_known });
  return (
    <Link to={`/stocks/${r.ticker}`} className="subtle" style={{ display: "block", color: "inherit", textDecoration: "none" }}>
      <div className="row spread"><div><b style={{ fontSize: 16 }}>{r.ticker}</b> <span className="caption">{r.company}</span></div><Action a={r.action} status={r.current_status} quality={r.data_quality} /></div>
      <div className="row spread" style={{ marginTop: 6 }}>
        <span>{price(r.price)} <span className="caption">최대 매수가 {price(r.max_buy)}</span></span>
        <span className="caption">점수 <b style={{ color: "var(--text)" }}>{r.score.toFixed(1)}</b></span>
      </div>
      <div className="explain" style={{ color: "var(--text-2)" }}>{a.headline}</div>
    </Link>
  );
}

export default function Dashboard() {
  const d = useApi<Dash>("/dashboard");
  const { mode } = useMode();
  const [busy, setBusy] = useState(false);
  const [scanErr, setScanErr] = useState<string | null>(null);
  const scan = async () => {
    setBusy(true);
    setScanErr(null);
    try { await api.post("/scan?committee=true"); d.reload(); } catch (e) { setScanErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  if (d.state === "loading") return <Loading what="대시보드" />;
  if (!d.data) return <Err error={d.error} retry={d.reload} />;
  const x = d.data;
  const bullish = x.top_opportunities.filter((r) => ["BUY", "BUY SMALL", "ADD"].includes(r.action));
  const shown = (bullish.length ? bullish : x.top_opportunities).slice(0, 4);
  const expired = x.top_opportunities.filter((r) => r.actionable_now === false).length;
  const down = x.provider_health.filter((h) => h.status === "DOWN");
  return (
    <div className="grid">
      <div className="page-head">
        <div>
          <h1>오늘의 미국 주식 한눈에 보기</h1>
          <div className="t-sub">지금 가장 유망한 종목, 조심할 위험, 다가오는 일정을 한 화면에 모았습니다. · 마지막 스캔 {x.scan ? stamp(x.scan.as_of) : "없음"}</div>
        </div>
        <button className="primary" disabled={busy} onClick={scan}>{busy ? "스캔 중…" : "전체 시장 스캔"}</button>
      </div>
      {busy && <Loading what="전체 시장 스캔" steps={SCAN_STEPS} />}
      <Err error={scanErr} />
      {expired > 0 && <Notice tone="warn">상위 후보 중 {expired}개는 추천 이후 거래일이 지나 지금은 유효하지 않습니다. ‘전체 시장 스캔’으로 새로 분석하세요.</Notice>}

      <div className="g3">
        <Card title="오늘 시장 분위기" icon="∿" explain="시장 전체가 어떤 환경인지(국면)">
          <div className="t-key-sm">{ko(REGIME_KO, x.regime.primary, "판단 불가")}</div>
          <div className="explain">{REGIME_HELP[x.regime.primary] ?? "거시 지표로 판단한 현재 환경"}</div>
          {x.regime.readings.length > 1 && <div className="row" style={{ marginTop: 10 }}>{x.regime.readings.slice(0, 4).map((r) => <span key={r.regime} className="tag info" title={r.evidence.join("\n")}>{ko(REGIME_KO, r.regime)}</span>)}</div>}
        </Card>
        <Card title="지금 가장 유망한 종목" icon="◎" right={<Link to="/opportunities">전체 보기 →</Link>} className="span2">
          {shown.length ? <div className="g2">{shown.map((r) => <OppCard key={r.id} r={r} />)}</div> : <Empty hint="오른쪽 위 ‘전체 시장 스캔’을 누르면 약 1~3분 안에 후보가 만들어집니다.">아직 스캔 결과가 없습니다.</Empty>}
          {shown.length > 0 && !bullish.length && <div className="explain">지금은 매수 조건을 충족한 종목이 없어 점수 상위 종목을 보여줍니다.</div>}
        </Card>
      </div>

      <div className="g3">
        <Card title="가장 조심할 위험" icon="⚠" tone={x.major_risks.length ? "warn" : undefined}>
          {x.major_risks.length ? <ul className="list">{x.major_risks.slice(0, 5).map((r, i) => <li key={i}><span className="dot warn">!</span><span><Link to={`/stocks/${r.ticker}`}>{r.ticker}</Link> — {r.text.split(", ").map((v) => VETO_KO[v] ?? v).join(", ")}</span></li>)}</ul> : <Empty>상위 후보에서 눈에 띄는 위험 신호가 없습니다.</Empty>}
        </Card>
        <Card title="다가오는 중요한 일정" icon="▦" right={<Link to="/calendar">일정 →</Link>}>
          {x.upcoming_catalysts.length ? <ul className="list">{x.upcoming_catalysts.slice(0, 5).map((e) => <li key={e.event_id}><span className="dot info">{e.days_until}</span><span>{e.title}<div className="caption">{day(e.event_date)} · {e.days_until}일 후</div></span></li>)}</ul> : <Empty>일정 데이터가 없습니다.</Empty>}
        </Card>
        <Card title="내 포트폴리오" icon="◔" right={<Link to="/portfolio">관리 →</Link>}>
          {x.portfolio.holdings ? (
            <div className="kv"><span className="k">보유 종목</span><span>{x.portfolio.holdings}개</span><span className="k">현금</span><span>{price(x.portfolio.cash)}</span></div>
          ) : <Empty hint="보유 종목을 입력하면 새 종목을 넣을 때 쏠림·중복 위험을 자동으로 확인합니다.">아직 입력한 보유 종목이 없습니다.</Empty>}
        </Card>
      </div>

      <div className="g3">
        <Card title="모의투자 성과" icon="↗" right={<Link to="/performance">분석 →</Link>} explain="실제 주문이 아닌 시뮬레이션">
          {x.performance ? (
            <>
              <div className="row spread"><span className={`t-key-sm ${(x.performance.return ?? 0) >= 0 ? "pos" : "neg"}`}>{pct(x.performance.return)}</span><span className="caption"><Term k="max_drawdown">최대 낙폭</Term> {pct(x.performance.max_drawdown)}</span></div>
              <LineChart values={x.performance.curve} height={70} />
            </>
          ) : <Empty hint="성과 분석 화면에서 ‘결과·모의투자 갱신’을 누르세요.">아직 모의투자 기록이 없습니다.</Empty>}
        </Card>
        <Card title="추천이 바뀐 종목" icon="⇄">
          {x.recommendation_changes.length ? <ul className="list">{x.recommendation_changes.map((c) => <li key={c.ticker}><span className="dot info">↻</span><span><Link to={`/stocks/${c.ticker}`}>{c.ticker}</Link> {c.text}</span></li>)}</ul> : <Empty>최근 스캔에서 추천이 바뀐 종목이 없습니다.</Empty>}
        </Card>
        <Card title="관심 종목 알림" icon="★" right={<Link to="/stocks">관심 종목 →</Link>}>
          {x.watchlist_alerts.length ? <ul className="list">{x.watchlist_alerts.map((a) => <li key={a.ticker}><span className={`dot ${a.level === "positive" ? "pos" : a.level === "warning" ? "warn" : "info"}`}>{a.level === "positive" ? "↑" : a.level === "warning" ? "!" : "i"}</span><span><Link to={`/stocks/${a.ticker}`}>{a.ticker}</Link> {a.text}</span></li>)}</ul> : <Empty hint="종목 분석 화면에서 ‘관심종목 추가’를 누르세요.">관심 종목이 없습니다.</Empty>}
        </Card>
      </div>

      <Card title="시스템 상태" icon="●" right={<Link to="/health">자세히 →</Link>}>
        {down.length ? <Notice tone="warn">일부 데이터 공급자가 중단되었습니다({down.map((h) => h.kind).join(", ")}). 해당 데이터는 ‘없음’으로 표시되고 판단에서 보수적으로 처리됩니다.</Notice>
          : <div className="row">{x.provider_health.length ? x.provider_health.map((h) => <span key={h.name} className={`tag ${h.status === "HEALTHY" ? "pos" : h.status === "DOWN" ? "neg" : "warn"}`}>{h.kind} · {HEALTH_KO[h.status] ?? h.status}</span>) : <span className="caption">아직 데이터 호출 기록이 없습니다(첫 스캔 후 표시).</span>}</div>}
      </Card>

      {mode === "advanced" && x.top_opportunities.length > 0 && <Card title="상위 후보 전체 비교"><OppTable rows={x.top_opportunities} compact /></Card>}
      {mode === "advanced" && x.scan && (
        <Card title="스캐너 단계 (저비용 → 고비용)">
          <table><thead><tr><th>단계</th><th>입력</th><th>통과</th><th>설명</th></tr></thead>
            <tbody>{x.scan.stages.map((s) => <tr key={s.stage}><td>{s.stage}</td><td>{s.input_count.toLocaleString("ko-KR")}</td><td>{s.output_count.toLocaleString("ko-KR")}</td><td className="caption" style={{ whiteSpace: "normal" }}>{s.note}</td></tr>)}</tbody></table>
        </Card>
      )}
    </div>
  );
}
