import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { CommitteeView } from "../components/CommitteeView";
import { Card, Err, Loading } from "../components/ui";
import { useApi } from "../components/useApi";
import type { CommitteeResult, Evidence, Opportunities, StockDetail } from "../types";

export default function Committee() {
  const [params, setParams] = useSearchParams();
  const ticker = params.get("ticker");
  const o = useApi<Opportunities>("/opportunities");
  const d = useApi<StockDetail>(ticker ? `/stocks/${ticker}` : null, [ticker]);
  const [run, setRun] = useState<CommitteeResult | null>(null);
  const [busy, setBusy] = useState(false);
  const ev = useMemo(() => new Map<string, Evidence>((d.data?.analysis.evidence ?? []).map((e) => [e.evidence_id, e])), [d.data]);
  const com = run ?? d.data?.committee ?? null;
  return (
    <div className="grid">
      <h1>AI Investment Committee</h1>
      <div className="muted">Seven specialist analysts → Bull/Bear debate (2 fixed rounds, evidence-cited) → Decision Synthesizer → Risk Manager (downgrade-only) → Portfolio Manager (size ≤ deterministic cap). The committee never changes prices or the deterministic score and cannot override hard vetoes.</div>
      <Card>
        <div className="row">
          <select value={ticker ?? ""} onChange={(e) => { setRun(null); setParams({ ticker: e.target.value }); }}>
            <option value="">Select a candidate…</option>
            {(o.data?.rows ?? []).map((r) => <option key={r.id} value={r.ticker}>{r.rank}. {r.ticker} — {r.action} ({r.committee_status})</option>)}
          </select>
          {d.data && <button disabled={busy} onClick={async () => { setBusy(true); try { setRun(await api.post<CommitteeResult>(`/recommendations/${d.data!.recommendation.id}/committee`)); } finally { setBusy(false); } }}>{busy ? "Running…" : "Run / load committee"}</button>}
        </div>
      </Card>
      {ticker && d.loading && <Loading what={ticker} />}
      <Err error={d.error} />
      {com && <Card title={`${ticker} committee`}><CommitteeView c={com} evidence={ev} /></Card>}
      {ticker && d.data && !com && <div className="muted">No committee result yet for this recommendation.</div>}
    </div>
  );
}
