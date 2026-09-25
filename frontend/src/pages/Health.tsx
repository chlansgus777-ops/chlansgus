import { Fragment } from "react";
import { Card, Err, Loading } from "../components/ui";
import { useApi } from "../components/useApi";
import { num, pct, stamp } from "../format";

interface H { name: string; kind: string; mode: string; status: string; configured: boolean; requests: number; error_rate: number; latency_ms: number | null; last_success: string | null; last_failure: string | null; last_error: string | null; freshness: string | null; rate_limit_state: string; breaker_state: string }
interface Resp { providers: H[]; configured: { kind: string; provider: string; mode: string; configured: boolean; reason: string | null }[]; llm: { provider: string; available: boolean; status: string; usage: Record<string, number> } }

export default function Health() {
  const h = useApi<Resp>("/health");
  if (h.loading && !h.data) return <Loading what="health" />;
  if (!h.data) return <Err error={h.error} />;
  const cls = (s: string) => (s === "HEALTHY" ? "pos" : s === "DOWN" ? "neg" : "warn");
  return (
    <div className="grid">
      <div className="row spread"><h1>System Health</h1><button onClick={h.reload}>Refresh</button></div>
      <Card title="Data providers">
        <table><thead><tr><th>Provider</th><th>Kind</th><th>Mode</th><th>Status</th><th>Freshness</th><th>Latency</th><th>Last success</th><th>Error rate</th><th>Breaker</th><th>Rate limit</th><th>Last error</th></tr></thead>
          <tbody>{h.data.providers.map((p) => <tr key={p.name}><td>{p.name}</td><td>{p.kind}</td><td>{p.mode}</td><td className={cls(p.status)}>{p.status}</td><td>{stamp(p.freshness)}</td><td>{p.latency_ms === null ? "N/A" : `${num(p.latency_ms, 0)} ms`}</td><td>{stamp(p.last_success)}</td><td>{pct(p.error_rate, 0, false)}</td><td>{p.breaker_state}</td><td>{p.rate_limit_state}</td><td className="muted" title={p.last_error ?? ""}>{(p.last_error ?? "").slice(0, 40)}</td></tr>)}</tbody></table>
      </Card>
      <Card title="Configured provider chains (primary → failover)">
        <table><tbody>{h.data.configured.map((c, i) => <tr key={i}><td>{c.kind}</td><td>{c.provider}</td><td>{c.mode}</td><td className={c.configured ? "pos" : "neg"}>{c.configured ? "configured" : "NOT CONFIGURED"}</td><td className="muted">{c.reason ?? ""}</td></tr>)}</tbody></table>
      </Card>
      <Card title="LLM provider">
        <div>{h.data.llm.provider} · <span className={cls(h.data.llm.status)}>{h.data.llm.status}</span>{!h.data.llm.available && " — AI COMMITTEE UNAVAILABLE (deterministic analysis unaffected)"}</div>
        <div className="kv">{Object.entries(h.data.llm.usage).map(([k, v]) => <Fragment key={k}><span className="k">{k}</span><span>{num(v, k.includes("cost") ? 4 : 0)}</span></Fragment>)}</div>
      </Card>
    </div>
  );
}
