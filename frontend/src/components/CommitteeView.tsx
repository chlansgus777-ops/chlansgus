import { useState } from "react";
import type { CommitteeResult, Evidence } from "../types";
import { Action, Card, EvidenceChips, Stance } from "./ui";

const ORDER = ["fundamental", "earnings", "valuation", "macro", "technical", "news", "risk_analyst"];

export function CommitteeView({ c, evidence }: { c: CommitteeResult; evidence: Map<string, Evidence> }) {
  const [full, setFull] = useState(false);
  if (c.status === "UNAVAILABLE") return <div className="warn">{c.reason ?? "AI COMMITTEE UNAVAILABLE"} — deterministic analysis remains valid.</div>;
  const bull = c.debate.filter((d) => d.side === "bull");
  const bear = c.debate.filter((d) => d.side === "bear");
  return (
    <div className="grid">
      <div className="row">
        <span>Status <b>{c.status}</b></span>
        <span>Consensus <b>{c.consensus_pct ?? "N/A"}%</b></span>
        <span>Divergence <b className={c.divergence === "HIGH" ? "neg" : ""}>{c.divergence ?? "N/A"}{c.divergence === "HIGH" ? " — HIGH DIVERGENCE" : ""}</b></span>
        <span>Deterministic <Action a={c.deterministic_action} /> → Final <Action a={c.final_action} /></span>
        {c.action_changed_by && <span className="warn">downgraded by {c.action_changed_by}</span>}
        <span className="muted">prompt {c.prompt_version}</span>
      </div>
      {c.injection_flags.length > 0 && <div className="warn">⚠ Prompt-injection text detected in external news ({c.injection_flags.join(", ")}); it was treated as untrusted data.</div>}
      <table>
        <thead><tr><th>Agent</th><th>Stance</th><th>Conf.</th><th>Key points</th><th>Evidence</th></tr></thead>
        <tbody>
          {ORDER.map((a) => {
            const r = c.reports[a];
            if (!r) return <tr key={a}><td>{a}</td><td colSpan={4} className="neg">{c.invalid_outputs[a] ?? "no output"}</td></tr>;
            return (
              <tr key={a}>
                <td>{a}</td><td><Stance s={r.stance} /></td><td>{r.confidence}</td>
                <td style={{ whiteSpace: "normal" }}>{[...r.key_strengths, ...r.key_weaknesses].slice(0, 3).join(" · ") || r.summary}</td>
                <td><EvidenceChips ids={r.evidence_ids.slice(0, 4)} index={evidence} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="grid g2">
        <Card title="Bull case">
          {bull[0] ? <><div>{bull[0].thesis}</div><ul className="list">{bull[0].points.map((p, i) => <li key={i}>{p.claim} <EvidenceChips ids={p.evidence_ids} index={evidence} /></li>)}</ul></> : <div className="muted">N/A</div>}
        </Card>
        <Card title="Bear case">
          {bear[0] ? <><div>{bear[0].thesis}</div><ul className="list">{bear[0].points.map((p, i) => <li key={i}>{p.claim} <EvidenceChips ids={p.evidence_ids} index={evidence} /></li>)}</ul></> : <div className="muted">N/A</div>}
        </Card>
      </div>
      <div className="grid g3">
        <Card title="Decision synthesizer">
          {c.synthesis ? <div className="kv"><span className="k">Agreement</span><span>{c.synthesis.committee_agreement}</span><span className="k">Advisory</span><Stance s={c.synthesis.advisory_stance} /><span className="k">Bull</span><span>{c.synthesis.strongest_bull_argument}</span><span className="k">Bear</span><span>{c.synthesis.strongest_bear_argument}</span><span className="k">Unresolved</span><span>{c.synthesis.unresolved_uncertainty.join("; ")}</span></div> : "N/A"}
        </Card>
        <Card title="Risk manager">
          {c.risk_review ? <><div><b>{c.risk_review.risk_level}</b> risk{c.risk_review.recommended_action ? <> · suggests <Action a={c.risk_review.recommended_action} /></> : null}</div><div className="muted">{c.risk_review.summary}</div><ul className="list">{c.risk_review.concerns.map((x, i) => <li key={i}>{x}</li>)}</ul></> : "N/A"}
        </Card>
        <Card title="Portfolio manager">
          {c.portfolio_advice ? <><div>Fit <b>{c.portfolio_advice.portfolio_fit}</b> · size <b>{c.size_class}</b></div><div className="muted">{c.portfolio_advice.summary}</div>{c.portfolio_advice.concentration_warning && <div className="warn">{c.portfolio_advice.concentration_warning}</div>}</> : "N/A"}
        </Card>
      </div>
      {Object.keys(c.guard).length > 0 && (
        <Card title="Guard: rejected AI claims">
          <ul className="list">{Object.entries(c.guard).flatMap(([role, g]) => [...g.rejected_claims.map((x) => `${role}: ${x}`), ...g.invalid_evidence_ids.map((x) => `${role}: invalid evidence id ${x}`)]).map((x, i) => <li key={i} className="warn">{x}</li>)}</ul>
        </Card>
      )}
      <button onClick={() => setFull(!full)}>{full ? "Hide full debate" : "View Full Debate"}</button>
      {full && (
        <div className="grid">
          {c.debate.map((d, i) => (
            <Card key={i} title={`Round ${d.round} — ${d.side.toUpperCase()}`}>
              <div>{d.thesis}</div>
              <ul className="list">{d.points.map((p, j) => <li key={j}><b>{p.claim}</b> — {p.interpretation}{p.rebuts ? <span className="muted"> (rebuts: {p.rebuts})</span> : null} <EvidenceChips ids={p.evidence_ids} index={evidence} /></li>)}</ul>
            </Card>
          ))}
          {ORDER.map((a) => c.reports[a] && <Card key={a} title={`${a} — full report`}><div>{c.reports[a]!.summary}</div><div className="muted">Risks: {c.reports[a]!.risks.join("; ") || "—"} · Missing: {c.reports[a]!.missing_data.join("; ") || "—"}</div></Card>)}
        </div>
      )}
    </div>
  );
}
