import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { CommitteeView } from "../components/CommitteeView";
import { Card, Empty, Err, Loading } from "../components/ui";
import { useApi } from "../components/useApi";
import { actionLabel } from "../i18n";
import type { CommitteeResult, Evidence, Opportunities, StockDetail } from "../types";

export default function Committee() {
  const [params, setParams] = useSearchParams();
  const ticker = params.get("ticker");
  const o = useApi<Opportunities>("/opportunities");
  const d = useApi<StockDetail>(ticker ? `/stocks/${ticker}` : null, [ticker]);
  const [run, setRun] = useState<CommitteeResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const ev = useMemo(() => new Map<string, Evidence>((d.data?.analysis.evidence ?? []).map((e) => [e.evidence_id, e])), [d.data]);
  const com = run ?? d.data?.committee ?? null;
  const start = async () => {
    if (!d.data) return;
    setBusy(true);
    setErr(null);
    try { setRun(await api.post<CommitteeResult>(`/recommendations/${d.data.recommendation.id}/committee`)); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  return (
    <div className="grid">
      <h1>AI 투자위원회</h1>
      <div className="muted">전문 분석가 7명 → 강세/약세 토론(2라운드, 근거 인용 필수) → 결정 종합 → 리스크 매니저(하향만 가능) → 포트폴리오 매니저(결정론적 한도 이하 비중만). 위원회는 가격·점수·결정론적 판정을 바꿀 수 없고 하드 거부권을 뒤집을 수 없습니다. 근거와 맞지 않는 수치(종목·지표·단위·부호 불일치)는 자동 삭제됩니다.</div>
      <Card>
        <div className="row">
          <select value={ticker ?? ""} onChange={(e) => { setRun(null); setParams({ ticker: e.target.value }); }} aria-label="후보 선택">
            <option value="">후보 선택…</option>
            {(o.data?.rows ?? []).map((r) => <option key={r.id} value={r.ticker}>{r.rank}. {r.ticker} — {actionLabel(r.action)} ({r.committee_status})</option>)}
          </select>
          {d.data && <button disabled={busy} onClick={start}>{busy ? "실행 중…" : "위원회 실행 / 불러오기"}</button>}
        </div>
      </Card>
      {ticker && d.state === "loading" && <Loading what={ticker} />}
      <Err error={d.error ?? err} retry={d.error ? d.reload : undefined} />
      {com && <Card title={`${ticker} 위원회 결과`}><CommitteeView c={com} evidence={ev} /></Card>}
      {ticker && d.data && !com && <Empty>이 추천에 대한 위원회 결과가 아직 없습니다.</Empty>}
    </div>
  );
}
