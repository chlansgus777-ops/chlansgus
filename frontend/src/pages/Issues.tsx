import { useState } from "react";
import { Link } from "react-router-dom";
import { Card, Empty, Err, Loading } from "../components/ui";
import { useApi } from "../components/useApi";
import { num, stamp } from "../format";
import { HORIZON_KO } from "../i18n";
import type { CompanyIssueImpact } from "../types";

interface IssueJ { issue_id: string; title: string; category: string; summary: string; publish_time: string; sources: string[]; confirmed_status: string; importance: number; confidence: number; market_awareness: number; primary_effects: { node_id: string; direction: number; mechanism: string[] }[] }
interface Resp { available: boolean; reason?: string; issues: { issue: IssueJ; affected_stocks: { ticker: string; hops: number; swing: number }[]; affected_sectors: string[] }[]; injection_flags: Record<string, string[]> }
interface Detail { issue: IssueJ; impacts: CompanyIssueImpact[]; direct: string[]; indirect: string[] }

const STATUS_KO: Record<string, string> = { CONFIRMED: "공식 확인", REPORTED: "보도", RUMOR: "루머" };

export default function Issues() {
  const r = useApi<Resp>("/issues");
  const [sel, setSel] = useState<string | null>(null);
  const d = useApi<Detail>(sel ? `/issues/${sel}` : null);
  if (r.state === "loading") return <Loading what="이슈" />;
  if (!r.data) return <Err error={r.error} retry={r.reload} />;
  if (!r.data.available) return <div className="warn" role="alert">뉴스/이슈 데이터를 받지 못했습니다: {r.data.reason} — ‘이슈 없음’과 다릅니다. 이슈 점수는 계산하지 않습니다.</div>;
  return (
    <div className="grid">
      <h3>이슈 (헤드라인이 아니라 구조화된 사건)</h3>
      {Object.keys(r.data.injection_flags).length > 0 && <div className="warn">⚠ 기사 {Object.keys(r.data.injection_flags).length}건에서 프롬프트 주입 패턴이 발견되어 신뢰할 수 없는 외부 텍스트로만 취급했습니다.</div>}
      <Card>
        {r.data.issues.length ? (
          <table><thead><tr><th>이슈</th><th>분류</th><th>중요도</th><th>신뢰도</th><th>시장 인지도</th><th>확인 상태</th><th>섹터</th><th>종목(2~6주 영향)</th></tr></thead>
            <tbody>{r.data.issues.map(({ issue: i, affected_stocks, affected_sectors }) => (
              <tr key={i.issue_id} onClick={() => setSel(i.issue_id)} style={{ cursor: "pointer" }}>
                <td style={{ whiteSpace: "normal", minWidth: 280 }}><b>{i.title}</b><div className="muted">{stamp(i.publish_time)} · {i.sources.join(", ")}</div></td><td>{i.category}</td><td>{num(i.importance)}</td><td>{num(i.confidence)}</td><td>{num(i.market_awareness)}</td><td>{STATUS_KO[i.confirmed_status] ?? i.confirmed_status}</td>
                <td style={{ whiteSpace: "normal" }}>{affected_sectors.join(", ")}</td>
                <td style={{ whiteSpace: "normal" }}>{affected_stocks.slice(0, 8).map((s) => <span key={s.ticker} className={s.swing > 0 ? "pos" : "neg"}>{s.ticker}{s.hops ? `(${s.hops}단계)` : ""} {num(s.swing, 0)} </span>)}</td>
              </tr>))}</tbody></table>
        ) : <Empty>현재 구조화된 이슈가 없습니다(뉴스 수집은 정상).</Empty>}
      </Card>
      {sel && d.data && (
        <Card title={`영향 상세 — ${d.data.issue.title}`}>
          <div>직접 영향: {d.data.direct.join(", ") || "—"} · 간접 영향(노출 그래프 경유): {d.data.indirect.join(", ") || "—"}</div>
          <table><thead><tr><th>종목</th><th>전달 경로</th>{HORIZON_KO.map(([, l]) => <th key={l}>{l}</th>)}<th>작동 방식</th></tr></thead>
            <tbody>{d.data.impacts.map((x) => <tr key={x.ticker}><td><Link to={`/stocks/${x.ticker}`}>{x.ticker}</Link></td><td>{x.exposure_path.join(" → ")}</td>
              {x.horizons.map((h) => <td key={h.horizon} className={h.impact_score > 0 ? "pos" : h.impact_score < 0 ? "neg" : ""}>{num(h.impact_score, 1)}</td>)}
              <td style={{ whiteSpace: "normal" }} className="muted">{x.horizons[3]?.mechanism.join(" → ")}</td></tr>)}</tbody></table>
        </Card>
      )}
      {sel && d.error && <Err error={d.error} retry={d.reload} />}
    </div>
  );
}
