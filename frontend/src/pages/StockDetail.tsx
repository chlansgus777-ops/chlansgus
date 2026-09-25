import { Fragment } from "react";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { CommitteeView } from "../components/CommitteeView";
import { Action, Bar, Card, Err, EvidenceChips, Loading, Quality } from "../components/ui";
import { useApi } from "../components/useApi";
import { big, num, pct, price, stamp } from "../format";
import type { CommitteeResult, Evidence, StockDetail as SD } from "../types";

const COMP_LABEL: Record<string, string> = { fundamental: "Fundamental", valuation: "Valuation", earnings_revision: "Earnings & Revision", catalyst: "Catalyst", macro: "Macro", technical: "Technical", risk: "Risk", entry_rr: "Entry R/R" };
const HORIZON: [string, string][] = [["IMMEDIATE", "Today"], ["SHORT", "1–5D"], ["SWING", "2–6W"], ["FUNDAMENTAL", "1–4Q"]];

export default function StockDetail() {
  const { ticker = "" } = useParams();
  const [refreshTick, setRefreshTick] = useState(0);
  const d = useApi<SD>(`/stocks/${ticker}${refreshTick ? "?refresh=true" : ""}`, [refreshTick]);
  const [committee, setCommittee] = useState<CommitteeResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [replay, setReplay] = useState<string | null>(null);
  const evIndex = useMemo(() => new Map<string, Evidence>((d.data?.analysis.evidence ?? []).map((e) => [e.evidence_id, e])), [d.data]);
  if (d.loading && !d.data) return <Loading what={ticker} />;
  if (!d.data) return <Err error={d.error} />;
  const { recommendation: rec, analysis: a } = d.data;
  const com = committee ?? d.data.committee;
  const comps = a.scorecard.components;
  const positives = comps.flatMap((c) => c.reasons.filter((r) => r.sign > 0)).slice(0, 5);
  const negatives = comps.flatMap((c) => c.reasons.filter((r) => r.sign < 0)).slice(0, 5);
  const runCommittee = async () => {
    setBusy(true);
    try { setCommittee(await api.post<CommitteeResult>(`/recommendations/${rec.id}/committee`)); } finally { setBusy(false); }
  };
  const doReplay = async () => {
    const r = await api.get<{ matches: boolean; replay_score: number; replay_action: string }>(`/recommendations/${rec.id}/replay`);
    setReplay(r.matches ? `Replay matches: ${r.replay_score} / ${r.replay_action}` : `Replay MISMATCH: ${r.replay_score} / ${r.replay_action}`);
  };
  const e = a.entry;
  return (
    <div className="grid">
      <div className="row spread">
        <div>
          <h1 style={{ marginBottom: 2 }}>{a.ticker} · {a.security.company_name}</h1>
          <div className="muted">{a.security.exchange} · {a.security.sector} / {a.security.industry}{a.security.is_adr ? ` · ADR (${a.security.country_of_incorporation})` : ""} · mkt cap {big(a.security.market_cap)}</div>
        </div>
        <div className="row">
          <button onClick={() => setRefreshTick((t) => t + 1)}>Re-analyze now</button>
          <button onClick={doReplay}>Replay snapshot</button>
          <Link to={`/committee?ticker=${a.ticker}`}>Committee tab →</Link>
        </div>
      </div>
      {replay && <div className={replay.includes("MISMATCH") ? "neg" : "pos"}>{replay}</div>}

      <div className="grid g4">
        <Card title="Action">
          <div className="big-action"><Action a={com && com.status !== "UNAVAILABLE" ? com.final_action : rec.action} /></div>
          <div className="muted">deterministic: {a.decision.action} {a.decision.suppressed_change ? "(held — no material change)" : ""}</div>
          {a.decision.vetoes.length > 0 && <div className="neg">Hard veto: {a.decision.vetoes.join(", ")}</div>}
          {a.decision.size_limit && <div className="warn">Size limit: {a.decision.size_limit}</div>}
        </Card>
        <Card title="Score / Confidence">
          <div className="big-action">{num(rec.score, 1)}<span className="muted">/100</span></div>
          <div>Confidence {num(rec.confidence, 0)}%</div>
        </Card>
        <Card title="Current price">
          <div className="big-action">{price(a.price)}</div>
          <div>{a.session} · <Quality q={a.price_quality} /></div>
          <div className="muted">{stamp(a.price_timestamp)} · source: {a.price_source}</div>
        </Card>
        <Card title="Data quality">
          <div><Quality q={rec.data_quality} /> · mode {a.mode}</div>
          <div className="muted">{a.data_quality.fields.filter(([, q]) => q !== "FRESH").map(([f, q]) => `${f}: ${q}`).join(" · ") || "all core fields fresh"}</div>
        </Card>
      </div>

      <div className="grid g2">
        <Card title="Price plan">
          {e ? (
            <div className="kv">
              <span className="k">Ideal entry</span><span>{price(e.ideal_entry)}</span>
              <span className="k">Acceptable range</span><span>{price(e.acceptable_low)} – {price(e.acceptable_high)}</span>
              <span className="k">Max buy</span><b>{price(e.max_buy)}</b>
              <span className="k">Add zone</span><span>{price(e.add_zone_low)} – {price(e.add_zone_high)}</span>
              <span className="k">Price stop</span><span className="neg">{price(e.stop)} ({pct(e.downside_pct)})</span>
              <span className="k">Target 1 / 2</span><span className="pos">{price(e.target1)} ({pct(e.upside_t1_pct)}) / {price(e.target2)}</span>
              <span className="k">R/R now / at ideal</span><span>{num(e.rr_at_current)} / {num(e.rr_at_ideal)}</span>
            </div>
          ) : <div className="muted">No valid price plan (price or ATR missing).</div>}
          {e && <details><summary>How the plan was derived</summary><ul className="list">{e.rationale.map((r, i) => <li key={i}>{r}</li>)}</ul></details>}
        </Card>
        <Card title="Why">
          <div className="grid g2">
            <div><b className="pos">Top positive factors</b><ul className="list">{positives.map((r, i) => <li key={i}>{r.text} <EvidenceChips ids={a.reason_evidence[r.text]} index={evIndex} /></li>)}</ul></div>
            <div><b className="neg">Top negative factors</b><ul className="list">{negatives.map((r, i) => <li key={i}>{r.text} <EvidenceChips ids={a.reason_evidence[r.text]} index={evIndex} /></li>)}</ul></div>
          </div>
        </Card>
      </div>

      <Card title="Issue impact timeline">
        <table><thead><tr>{HORIZON.map(([, l]) => <th key={l}>{l}</th>)}</tr></thead>
          <tbody><tr>{HORIZON.map(([h]) => { const v = a.horizon_view[h] ?? 0; return <td key={h} className={v > 0 ? "pos" : v < 0 ? "neg" : "muted"}>{num(v, 1)}</td>; })}</tr></tbody></table>
      </Card>

      <div className="grid g2">
        <Card title="Thesis invalidation (separate from the price stop)">
          <ul className="list">{a.thesis_conditions.map((c) => <li key={c.condition_id} className={a.thesis_breaches.some((b) => b.startsWith(c.description)) ? "neg" : ""}>{c.description}</li>)}</ul>
          {a.thesis_invalidated && <div className="neg">THESIS INVALIDATED: {a.thesis_breaches.join("; ")}</div>}
        </Card>
        <Card title="What changed">
          {a.changes.length ? <ul className="list">{a.changes.map((c, i) => <li key={i} className={c.material ? "" : "muted"}>{c.material ? "● " : "○ "}{c.text}</li>)}</ul> : <div className="muted">No change since the previous analysis.</div>}
        </Card>
      </div>

      <Card title="Score breakdown" right={<span className="muted">{a.scorecard.model_version} · sector model: {a.sector_model_name}</span>}>
        <div className="muted" style={{ marginBottom: 8 }}>Sector model reason: {a.sector_model_reason}</div>
        <table><thead><tr><th>Component</th><th>Points</th><th style={{ width: "25%" }}></th><th>Reasons</th></tr></thead>
          <tbody>{comps.map((c) => (
            <tr key={c.name}>
              <td>{COMP_LABEL[c.name] ?? c.name}{!c.available && <span className="warn"> (missing → conservative)</span>}</td>
              <td>{(c.subscore * c.weight).toFixed(1)} / {c.weight}</td>
              <td><Bar value={c.subscore} /></td>
              <td style={{ whiteSpace: "normal" }}>{c.reasons.map((r) => r.text).join(" · ") || "—"}{c.missing.length ? <span className="muted"> · missing: {c.missing.join(", ")}</span> : null}</td>
            </tr>))}</tbody></table>
      </Card>

      <div className="grid g2">
        <Card title="Fundamentals (sector model metrics)">
          <table><tbody>{(a.fundamental_rules?.items ?? []).map((i) => <tr key={i.metric}><td>{i.label}</td><td>{i.value === null ? <span className="muted">MISSING</span> : num(i.value, 3)}</td><td style={{ width: "30%" }}>{i.subscore !== null && <Bar value={i.subscore} />}</td></tr>)}</tbody></table>
        </Card>
        <Card title="Valuation">
          <table><tbody>{(a.valuation_rules?.items ?? []).map((i) => <tr key={i.metric}><td>{i.label}</td><td>{i.value === null ? <span className="muted">N/A</span> : num(i.value, 2)}</td><td style={{ width: "30%" }}>{i.subscore !== null && <Bar value={i.subscore} />}</td></tr>)}</tbody></table>
          {a.relative_valuation && <div className="kv" style={{ marginTop: 8 }}>
            <span className="k">vs own history</span><span>{a.relative_valuation.history_percentile === null ? "N/A" : `${(a.relative_valuation.history_percentile * 100).toFixed(0)}th pct`}</span>
            <span className="k">vs peers</span><span>{pct(a.relative_valuation.premium_to_peers)}</span>
            <span className="k">Fwd EY − 10Y</span><span>{pct(a.relative_valuation.equity_risk_spread, 2)}</span>
          </div>}
        </Card>
        <Card title="Earnings (actual vs expected)">
          {a.earnings ? <div className="kv">
            <span className="k">Result quality</span><b>{a.earnings.result_quality}</b>
            <span className="k">Revenue surprise</span><span>{pct(a.earnings.revenue_surprise)}</span>
            <span className="k">EPS surprise</span><span>{pct(a.earnings.eps_surprise)}</span>
            <span className="k">Guide vs consensus</span><span>{pct(a.earnings.guide_rev_vs_cons)}</span>
            <span className="k">Expectation bar</span><span>{a.earnings.expectation_bar}</span>
            <span className="k">Beat streak</span><span>{a.earnings.beat_streak}</span>
          </div> : <div className="muted">MISSING</div>}
        </Card>
        <Card title="Analyst revisions">
          {a.analyst ? <div className="kv">
            {["eps_revision_7d", "eps_revision_30d", "eps_revision_90d", "revenue_revision_30d", "revenue_revision_90d"].map((k) => <Fragment key={k}><span className="k">{k}</span><span>{pct(a.analyst?.[k] as number | null)}</span></Fragment>)}
            <span className="k">Analysts</span><span>{String(a.analyst["analyst_count"] ?? "N/A")}</span>
            <span className="k">Dispersion</span><span>{num(a.analyst["estimate_dispersion"] as number | null, 3)}</span>
            <span className="k">Target (secondary)</span><span>{price(a.analyst["target_price_consensus"] as number | null)}</span>
          </div> : <div className="muted">MISSING — no licensed estimates provider</div>}
        </Card>
        <Card title="Macro transmission">
          <div>Regime: <b>{a.primary_regime}</b> · net {num(a.macro_impact?.net ?? null)}</div>
          <ul className="list">{(a.macro_impact?.contributions ?? []).map(([f, v, t]) => <li key={f} className={v > 0 ? "pos" : "neg"}>{t}</li>)}</ul>
        </Card>
        <Card title="Technical (timing only)">
          {a.technicals ? <div className="kv">{["sma20", "sma50", "sma200", "rsi14", "atr14", "anchored_vwap", "high_52w", "low_52w", "volume_ratio", "rs_6m"].map((k) => <Fragment key={k}><span className="k">{k}</span><span>{num(a.technicals?.[k] as number | null)}</span></Fragment>)}</div> : <div className="muted">MISSING</div>}
        </Card>
        <Card title="Issues & priced-in">
          {a.issue_impacts.length ? <table><thead><tr><th>Issue</th><th>Path</th><th>2–6W</th><th>Priced-in (est.)</th></tr></thead><tbody>
            {a.issue_impacts.map((i) => { const sw = i.horizons.find((h) => h.horizon === "SWING"); const pi = a.priced_in[i.issue_id]; return <tr key={i.issue_id}><td>{i.issue_id}</td><td>{i.exposure_path.join(" → ")}</td><td className={(sw?.impact_score ?? 0) > 0 ? "pos" : "neg"}>{num(sw?.impact_score ?? null, 1)}</td><td>{pi?.value ?? "N/A"} <span className="muted">(conf {num(pi?.confidence ?? null)})</span></td></tr>; })}
          </tbody></table> : <div className="muted">No issues reach this company.</div>}
          {a.issue_impacts.map((i) => <details key={i.issue_id}><summary>Causal chain: {i.issue_id}</summary><ul className="list">{(i.horizons[3]?.mechanism ?? []).map((m, j) => <li key={j}>{m}</li>)}</ul></details>)}
        </Card>
        <Card title="Catalysts & options & risk">
          <div>Event risk: <b className={a.event_risk.level === "EXTREME" || a.event_risk.level === "HIGH" ? "neg" : ""}>{a.event_risk.level}</b> {a.event_risk.reasons.join("; ")}</div>
          <ul className="list">{a.upcoming_events.slice(0, 5).map((ev) => <li key={ev.event_id}>{ev.event_date} · {ev.title}</li>)}</ul>
          {a.options ? <div className="muted">IV {pct(a.options["atm_iv"] ?? null, 1, false)} · IV rank {num(a.options["iv_rank"] ?? null)} · expected move ±{pct(a.options["expected_move"] ?? null, 1, false)}</div> : <div className="muted">Options: MISSING</div>}
          {a.portfolio_review && <div>Portfolio cap: <b>{a.portfolio_review.size_cap}</b> {a.portfolio_review.warnings.join("; ")}</div>}
        </Card>
      </div>

      <Card title="Scenarios (probability shown only once calibrated)">
        <table><thead><tr><th>Scenario</th><th>Trigger</th><th>Mechanism</th><th>Range</th><th>Invalidation</th><th>Probability</th></tr></thead>
          <tbody>{a.scenarios.map((s) => <tr key={s.name}><td>{s.name}</td><td>{s.trigger}</td><td>{s.mechanism}</td><td>{price(s.price_low)} – {price(s.price_high)}</td><td>{s.invalidation}</td><td>{s.probability ?? "N/A"}</td></tr>)}</tbody></table>
      </Card>

      <Card title="AI Investment Committee" right={<button disabled={busy} onClick={runCommittee}>{busy ? "Running…" : com ? "Show / re-run committee" : "Run committee"}</button>}>
        {com ? <CommitteeView c={com} evidence={evIndex} /> : <div className="muted">Not run for this recommendation (runs automatically only for the top scan candidates).</div>}
      </Card>

      <Card title="Sources & versions">
        <details><summary>{a.evidence.length} evidence items</summary>
          <div className="scroll"><table><thead><tr><th>ID</th><th>Label</th><th>Value</th><th>Source</th><th>Quality</th></tr></thead>
            <tbody>{a.evidence.map((x) => <tr key={x.evidence_id}><td>{x.evidence_id}</td><td>{x.label}</td><td>{typeof x.value === "number" ? num(x.value, 4) : String(x.value)}</td><td>{x.source}</td><td><Quality q={x.quality} /></td></tr>)}</tbody></table></div>
        </details>
        <div className="muted">{Object.entries(d.data.versions).map(([k, v]) => `${k}: ${v}`).join(" · ")}</div>
        <div className="muted">History: {d.data.history.map((h) => `${h.as_of.slice(0, 10)} ${h.action} ${h.score.toFixed(0)}`).join(" | ")}</div>
      </Card>
    </div>
  );
}
