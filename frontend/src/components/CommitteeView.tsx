import { useState } from "react";
import { SIZE_KO, STANCE_KO, ko } from "../i18n";
import type { CommitteeResult, Evidence } from "../types";
import { Action, Card, Empty, EvidenceChips, Stance } from "./ui";

const ORDER = ["fundamental", "earnings", "valuation", "macro", "technical", "news", "risk_analyst"];
const AGENT_KO: Record<string, string> = { fundamental: "펀더멘털 분석가", earnings: "실적 분석가", valuation: "밸류에이션 분석가", macro: "거시 분석가", technical: "기술적 분석가", news: "뉴스·이슈 분석가", risk_analyst: "리스크 분석가" };
const STATUS_KO: Record<string, string> = { COMPLETED: "완료", PARTIAL: "일부 완료(무효 출력 제외)", UNAVAILABLE: "사용 불가", SKIPPED: "생략" };
const LEVEL_KO: Record<string, string> = { LOW: "낮음", MEDIUM: "보통", HIGH: "높음", EXTREME: "극단적" };
const FIT_KO: Record<string, string> = { GOOD: "적합", NEUTRAL: "보통", POOR: "부적합" };

export function CommitteeView({ c, evidence }: { c: CommitteeResult; evidence: Map<string, Evidence> }) {
  const [full, setFull] = useState(false);
  if (c.status === "UNAVAILABLE") return <div className="warn">{c.reason ?? "AI 위원회 사용 불가"} — 결정론적 분석 결과는 그대로 유효합니다.</div>;
  if (c.status === "SKIPPED") return <div className="muted">{c.reason ?? "위원회 생략"}</div>;
  const bull = c.debate.filter((d) => d.side === "bull");
  const bear = c.debate.filter((d) => d.side === "bear");
  return (
    <div className="grid">
      <div className="row">
        <span>상태 <b>{STATUS_KO[c.status] ?? c.status}</b></span>
        <span>합의도 <b>{c.consensus_pct ?? "N/A"}%</b></span>
        <span>의견 분산 <b className={c.divergence === "HIGH" ? "neg" : ""}>{c.divergence ?? "N/A"}{c.divergence === "HIGH" ? " — 의견 크게 엇갈림" : ""}</b></span>
        <span>결정론적 <Action a={c.deterministic_action} /> → 최종 <Action a={c.final_action} /></span>
        {c.action_changed_by && <span className="warn">{c.action_changed_by}에 의해 하향</span>}
        <span className="muted">프롬프트 {c.prompt_version}</span>
      </div>
      {c.injection_flags.length > 0 && <div className="warn">⚠ 외부 뉴스에서 프롬프트 주입 문구 감지({c.injection_flags.join(", ")}) — 신뢰할 수 없는 데이터로만 취급했습니다.</div>}
      <div className="g4" style={{ gap: 10 }}>
        {ORDER.map((a) => {
          const r = c.reports[a];
          const tone = !r ? "neutral" : r.stance === "positive" ? "pos" : r.stance === "negative" ? "neg" : "neutral";
          return (
            <div key={a} className="subtle" title={r ? r.summary : (c.invalid_outputs[a] ?? "출력 없음")}>
              <div className="caption">{AGENT_KO[a]}</div>
              <div className={`tag ${tone}`} style={{ marginTop: 4 }}>{r ? `${tone === "pos" ? "▲" : tone === "neg" ? "▼" : "■"} ${STANCE_KO[r.stance] ?? r.stance}` : "무효/없음"}</div>
              {r && <div className="caption" style={{ marginTop: 4 }}>확신도 {r.confidence}</div>}
            </div>
          );
        })}
        {c.risk_review && <div className="subtle"><div className="caption">리스크 매니저</div><div className={`tag ${c.risk_review.risk_level === "LOW" ? "pos" : c.risk_review.risk_level === "MEDIUM" ? "warn" : "neg"}`} style={{ marginTop: 4 }}>위험 {LEVEL_KO[c.risk_review.risk_level] ?? c.risk_review.risk_level}</div></div>}
      </div>
      <details>
        <summary>분석가별 핵심 내용 · 강세/약세 논리 보기</summary>
      <table>
        <thead><tr><th>분석가</th><th>입장</th><th>확신도</th><th>핵심 내용</th><th>근거</th></tr></thead>
        <tbody>
          {ORDER.map((a) => {
            const r = c.reports[a];
            if (!r) return <tr key={a}><td>{AGENT_KO[a]}</td><td colSpan={4} className="neg">{c.invalid_outputs[a] ?? "출력 없음"}</td></tr>;
            return (
              <tr key={a}>
                <td>{AGENT_KO[a]}</td><td><Stance s={r.stance} /></td><td>{r.confidence}</td>
                <td style={{ whiteSpace: "normal" }}>{[...r.key_strengths, ...r.key_weaknesses].slice(0, 3).join(" · ") || r.summary}</td>
                <td><EvidenceChips ids={r.evidence_ids.slice(0, 4)} index={evidence} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="grid g2">
        <Card title="강세 논리">
          {bull[0] ? <><div>{bull[0].thesis}</div><ul className="list">{bull[0].points.map((p, i) => <li key={i}>{p.claim} <EvidenceChips ids={p.evidence_ids} index={evidence} /></li>)}</ul></> : <Empty>없음(무효 처리되었거나 미실행)</Empty>}
        </Card>
        <Card title="약세 논리">
          {bear[0] ? <><div>{bear[0].thesis}</div><ul className="list">{bear[0].points.map((p, i) => <li key={i}>{p.claim} <EvidenceChips ids={p.evidence_ids} index={evidence} /></li>)}</ul></> : <Empty>없음(무효 처리되었거나 미실행)</Empty>}
        </Card>
      </div>
      </details>
      <div className="grid g3">
        <Card title="결정 종합">
          {c.synthesis ? <div className="kv"><span className="k">합의 수준</span><span>{c.synthesis.committee_agreement}</span><span className="k">권고 입장</span><span>{STANCE_KO[c.synthesis.advisory_stance] ?? c.synthesis.advisory_stance}</span><span className="k">가장 강한 강세 논거</span><span>{c.synthesis.strongest_bull_argument}</span><span className="k">가장 강한 약세 논거</span><span>{c.synthesis.strongest_bear_argument}</span><span className="k">미해결 불확실성</span><span>{c.synthesis.unresolved_uncertainty.join("; ")}</span></div> : <Empty>N/A</Empty>}
        </Card>
        <Card title="리스크 매니저">
          {c.risk_review ? <><div>위험 <b>{LEVEL_KO[c.risk_review.risk_level] ?? c.risk_review.risk_level}</b>{c.risk_review.recommended_action ? <> · 제안 <Action a={c.risk_review.recommended_action} /></> : null}</div><div className="muted">{c.risk_review.summary}</div><ul className="list">{c.risk_review.concerns.map((x, i) => <li key={i}>{x}</li>)}</ul></> : <Empty>N/A</Empty>}
        </Card>
        <Card title="포트폴리오 매니저">
          {c.portfolio_advice ? <><div>적합도 <b>{FIT_KO[c.portfolio_advice.portfolio_fit] ?? c.portfolio_advice.portfolio_fit}</b> · 비중 <b>{ko(SIZE_KO, c.size_class)}</b></div><div className="muted">{c.portfolio_advice.summary}</div>{c.portfolio_advice.concentration_warning && <div className="warn">{c.portfolio_advice.concentration_warning}</div>}</> : <Empty>N/A</Empty>}
        </Card>
      </div>
      {Object.keys(c.guard).length > 0 && (
        <Card title="검증기: 삭제된 AI 주장">
          <ul className="list">{Object.entries(c.guard).flatMap(([role, g]) => [...g.rejected_claims.map((x) => `${role}: ${x}`), ...g.invalid_evidence_ids.map((x) => `${role}: 존재하지 않는 근거 ID ${x}`)]).map((x, i) => <li key={i} className="warn">{x}</li>)}</ul>
        </Card>
      )}
      <button className="ghost" onClick={() => setFull(!full)}>{full ? "AI 토론 접기" : "AI 토론 자세히 보기"}</button>
      {full && (
        <div className="grid">
          {c.debate.map((d, i) => (
            <Card key={i} title={`${d.round}라운드 — ${d.side === "bull" ? "강세" : "약세"}`}>
              <div>{d.thesis}</div>
              <ul className="list">{d.points.map((p, j) => <li key={j}><b>{p.claim}</b> — {p.interpretation}{p.rebuts ? <span className="muted"> (반박: {p.rebuts})</span> : null} <EvidenceChips ids={p.evidence_ids} index={evidence} /></li>)}</ul>
            </Card>
          ))}
          {ORDER.map((a) => c.reports[a] && <Card key={a} title={`${AGENT_KO[a]} — 전체 보고서`}><div>{c.reports[a]!.summary}</div><div className="muted">위험: {c.reports[a]!.risks.join("; ") || "—"} · 부족한 데이터: {c.reports[a]!.missing_data.join("; ") || "—"}</div></Card>)}
        </div>
      )}
    </div>
  );
}
