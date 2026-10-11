import { sectorKo } from "../i18n";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Card, Err, Loading, Term } from "../components/ui";
import { useEasy } from "../viewMode";
import { useApi, useSettling } from "../components/useApi";
import { num, stamp, errKo } from "../format";
import { HORIZON_KO } from "../i18n";
import type { CompanyIssueImpact } from "../types";

const CAT_KO: Record<string, string> = {
  Earnings: "실적", Guidance: "가이던스", AI: "AI", Regulation: "규제", "Export Control": "수출 통제", Tariff: "관세", Geopolitics: "지정학",
  "M&A": "인수합병", Product: "제품", Competition: "경쟁", "Supply Chain": "공급망", Rates: "금리", Inflation: "물가", Oil: "유가", Legal: "소송·법률",
  Antitrust: "반독점", Financing: "자금 조달", Dilution: "지분 희석", Buyback: "자사주 매입", Management: "경영진", Cybersecurity: "사이버 보안",
};

interface IssueJ { issue_id: string; title: string; category: string; summary: string; publish_time: string; sources: string[]; confirmed_status: string; importance: number; confidence: number; market_awareness: number; primary_effects: { node_id: string; direction: number; mechanism: string[] }[] }
interface Resp { available: boolean; pending?: boolean; refreshing?: boolean; as_of?: string; reason?: string; issues: { issue: IssueJ; affected_stocks: { ticker: string; hops: number; swing: number }[]; affected_sectors: string[] }[]; injection_flags: Record<string, string[]> }
interface Detail { issue: IssueJ; impacts: CompanyIssueImpact[]; direct: string[]; indirect: string[] }

const STATUS_KO: Record<string, string> = { CONFIRMED: "공식 확인", REPORTED: "보도", RUMOR: "루머" };

/** How big a deal the issue is, in a word (importance is MarketLens's 0~1 grade). */
const sizeWord = (v: number) => (v >= 0.75 ? "큰 이슈" : v >= 0.5 ? "보통 이슈" : "작은 이슈");

/** 쉽게 보기: one card per issue — what happened, who it helps, who it hurts. The grades stay in 자세히 보기. */
function EasyIssues({ issues, sel, onSel }: { issues: Resp["issues"]; sel: string | null; onSel: (id: string) => void }) {
  return (
    <div className="issue-cards" data-testid="issue-cards">
      {issues.map(({ issue: i, affected_stocks, affected_sectors }) => {
        const up = affected_stocks.filter((s) => s.swing > 3).slice(0, 5);
        const down = affected_stocks.filter((s) => s.swing < -3).slice(0, 5);
        return (
          <button type="button" key={i.issue_id} className={`issue-card${sel === i.issue_id ? " on" : ""}`} onClick={() => onSel(i.issue_id)} aria-pressed={sel === i.issue_id}>
            <span className="meta">{CAT_KO[i.category] ?? i.category} · {STATUS_KO[i.confirmed_status] ?? i.confirmed_status} · {sizeWord(i.importance)}</span>
            <b className="title">{i.title}</b>
            {affected_sectors.length > 0 && <span className="caption">관련 업종: {affected_sectors.map((x) => sectorKo(x)).slice(0, 3).join(", ")}</span>}
            <span className="who">
              {up.length > 0 && <span><span className="pos">▲ 유리</span> {up.map((x) => x.ticker).join(", ")}</span>}
              {down.length > 0 && <span><span className="neg">▼ 불리</span> {down.map((x) => x.ticker).join(", ")}</span>}
              {!up.length && !down.length && <span className="muted">눈에 띄게 영향받는 종목 없음</span>}
            </span>
          </button>
        );
      })}
    </div>
  );
}

export default function Issues() {
  const easy = useEasy();
  const r = useSettling<Resp>("/issues");
  const [sel, setSel] = useState<string | null>(null);
  const d = useApi<Detail>(sel ? `/issues/${sel}` : null);
  if (r.state === "loading" || r.data?.pending) return <Loading what="이슈(뉴스 수집·분류)" />;
  if (!r.data) return <Err error={r.error} retry={r.reload} />;
  if (!r.data.available) return <div className="ribbon warn" role="alert"><span className="cap">⚠ 뉴스 없음</span><div className="msg" title={r.data.reason ?? ""}>{errKo(r.data.reason)} — ‘이슈 없음’과 다릅니다. 이슈 점수는 계산하지 않습니다.</div></div>;
  return (
    <div className="grid">
      {r.data.as_of ? <div className="caption" data-testid="issues-as-of">이슈 기준 시각 {stamp(r.data.as_of)}{r.data.refreshing ? " · 최신 뉴스로 다시 묶는 중(끝나면 바뀝니다)" : ""}</div> : null}
      {r.data.issues.length ? <details className="explain"><summary>시장 이슈와 영향 점수 읽는 법</summary>{easy ? "▲ 유리·▼ 불리는 MarketLens가 회사의 사업·공급망 연결로 추정한 방향입니다(기사에 적힌 인과관계가 아님). 이슈를 누르면 종목별 영향 경로를 봅니다. 숫자는 ‘자세히 보기’에서 볼 수 있습니다. " : null}왼쪽은 기사·출처의 사실, 오른쪽은 MarketLens가 계산한 해석입니다. 해석은 인과관계를 확인한 것이 아니라 노출 경로에 따른 추정입니다. 행을 누르면 종목별 영향 경로를 봅니다.</details> : null}
      {Object.keys(r.data.injection_flags).length > 0 && <div className="ribbon warn" role="note"><span className="cap">⚠ 외부 텍스트</span><div className="msg">기사 {Object.keys(r.data.injection_flags).length}건에서 프롬프트 주입 패턴이 발견되어 신뢰할 수 없는 외부 텍스트로만 취급했습니다.</div></div>}
      {r.data.issues.length && easy ? <EasyIssues issues={r.data.issues} sel={sel} onSel={setSel} /> : r.data.issues.length ? <Card className="flush">
        {r.data.issues.length ? (
          <div className="scroll" style={{ padding: "4px 8px 8px" }}><table><thead>
            <tr><th colSpan={3} style={{ color: "var(--text-2)" }}>기사·출처에 나온 사실</th><th colSpan={5} style={{ color: "var(--lilac)", borderLeft: "1px solid var(--line)" }}>MarketLens 해석(계산값 — 기사 내용이 아님)</th></tr>
            <tr><th>이슈</th><th>분류</th><th>확인 상태</th><th className="num" style={{ borderLeft: "1px solid var(--line)" }}><Term k="importance">중요도</Term></th><th className="num">신뢰도</th><th className="num"><Term k="market_awareness">시장 인지도</Term></th><th>섹터</th><th>종목(<Term k="issue_impact">2~6주 영향 점수</Term>)</th></tr></thead>
            <tbody>{r.data.issues.map(({ issue: i, affected_stocks, affected_sectors }) => (
              <tr key={i.issue_id} onClick={() => setSel(i.issue_id)} onKeyDown={(e) => { if (e.key === "Enter") setSel(i.issue_id); }} tabIndex={0} style={{ cursor: "pointer" }} aria-selected={sel === i.issue_id}>
                <td style={{ whiteSpace: "normal", minWidth: 280 }}><b>{i.title}</b><div className="caption">{stamp(i.publish_time)} · {i.sources.join(", ")}</div></td><td style={{ whiteSpace: "nowrap" }} title={i.category}>{CAT_KO[i.category] ?? i.category}</td><td style={{ whiteSpace: "nowrap" }}>{STATUS_KO[i.confirmed_status] ?? i.confirmed_status}</td><td className="num" style={{ borderLeft: "1px solid var(--line)" }}>{num(i.importance)}</td><td className="num">{num(i.confidence)}</td><td className="num">{num(i.market_awareness)}</td>
                <td style={{ whiteSpace: "normal", minWidth: 120 }} title={affected_sectors.join(", ")}>{affected_sectors.map((x) => sectorKo(x)).slice(0, 2).join(", ")}{affected_sectors.length > 2 ? <span className="caption"> 외 {affected_sectors.length - 2}</span> : null}</td>
                <td style={{ whiteSpace: "normal" }}><div className="row tight">{affected_stocks.slice(0, 8).map((s) => <span key={s.ticker} className={`pill ${s.swing > 3 ? "tone-ok" : s.swing < -3 ? "tone-danger" : ""}`}>{s.ticker}{s.hops ? `(${s.hops}단계)` : ""} {s.swing > 0 ? "▲" : s.swing < 0 ? "▼" : "■"}{num(s.swing, 0)}</span>)}</div></td>
              </tr>))}</tbody></table></div>
        ) : null}
      </Card> : <div className="caption">현재 분류된 시장 이슈가 없습니다. 보조 뉴스는 아래에서 확인하세요.</div>}
      {sel && d.data && (
        <Card title={`영향 상세 — ${d.data.issue.title}`} sub>
          <div className="explain" style={{ marginBottom: 10 }}>직접 영향: {d.data.direct.join(", ") || "—"} · 간접 영향(노출 그래프 경유): {d.data.indirect.join(", ") || "—"}</div>
          <div className="scroll"><table><thead><tr><th>종목</th><th>전달 경로</th>{HORIZON_KO.map(([, l]) => <th key={l} className="num">{l}</th>)}<th>작동 방식</th></tr></thead>
            <tbody>{d.data.impacts.map((x) => <tr key={x.ticker}><td><Link to={`/stocks/${x.ticker}`}><b>{x.ticker}</b></Link></td><td>{x.exposure_path.join(" → ")}</td>
              {x.horizons.map((h) => <td key={h.horizon} className={`num ${h.impact_score > 0 ? "pos" : h.impact_score < 0 ? "neg" : ""}`}>{num(h.impact_score, 1)}</td>)}
              <td style={{ whiteSpace: "normal" }} className="caption">{x.horizons[3]?.mechanism.join(" → ")}</td></tr>)}</tbody></table></div>
          <div className="caption" style={{ marginTop: 6 }}>전달 경로·영향 점수·작동 방식은 MarketLens의 해석입니다(기사에 적힌 인과관계가 아님).</div>
        </Card>
      )}
      {sel && d.error && <Err error={d.error} retry={d.reload} />}
    </div>
  );
}
