import { useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { CommitteeSummary, CommitteeView } from "../components/CommitteeView";
import { Card, Disclosure, Empty, Err, Loading, StatePanel } from "../components/ui";
import { useStatus } from "../components/status";
import { useApi } from "../components/useApi";
import { actionLabel } from "../i18n";
import type { CommitteeResult, Evidence, Opportunities, StockDetail } from "../types";
import { committeeFor } from "./StockDetail";

/** A committee result is shown only for the ticker and recommendation on screen: a run that returns after the
 * user picked another stock is dropped. */
export function acceptRun(res: CommitteeResult, ticker: string | null, current: string | null): boolean {
  return !!ticker && ticker === current && res.ticker === ticker;
}

export default function Committee() {
  const [params, setParams] = useSearchParams();
  const ticker = params.get("ticker");
  const current = useRef<string | null>(ticker);
  current.current = ticker;
  const st = useStatus();
  const o = useApi<Opportunities>("/opportunities");
  const d = useApi<StockDetail>(ticker ? `/stocks/${ticker}` : null, [ticker]);
  const [run, setRun] = useState<{ ticker: string; recId: number; c: CommitteeResult } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const ev = useMemo(() => new Map<string, Evidence>((d.data?.analysis.evidence ?? []).map((e) => [e.evidence_id, e])), [d.data]);
  const mine = ticker && d.data && d.data.analysis.ticker === ticker ? d.data : null;
  const com = (run && mine && run.ticker === ticker && run.recId === mine.recommendation.id ? run.c : null) ?? (mine ? committeeFor(mine, ticker!) : null);
  const start = async () => {
    if (!mine || !ticker) return;
    const t = ticker, id = mine.recommendation.id;
    setBusy(true);
    setErr(null);
    try {
      const res = await api.post<CommitteeResult>(`/recommendations/${id}/committee`);
      if (acceptRun(res, t, current.current)) setRun({ ticker: t, recId: id, c: res });
    } catch (e) { if (current.current === t) setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  return (
    <div className="grid">
      <div className="page-head"><div><h1>AI 투자위원회</h1>
        <div className="t-sub">전문 분석가 7명 → 강세/약세 토론(2라운드, 근거 인용 필수) → 결정 종합 → 리스크 매니저(하향만 가능) → 포트폴리오 매니저(결정론적 한도 이하 비중만).</div></div></div>
      <div className="explain">위원회는 가격·점수·결정론적 판정을 바꿀 수 없고 하드 거부권을 뒤집을 수 없습니다. 근거와 맞지 않는 수치(종목·지표·단위·부호 불일치)는 자동 삭제됩니다.</div>
      {st?.system.data && !st.system.data.llm.available && <StatePanel kind="ai_unavailable" />}
      <Card>
        <div className="row">
          <select value={ticker ?? ""} onChange={(e) => { setRun(null); setErr(null); setParams({ ticker: e.target.value }); }} aria-label="후보 선택">
            <option value="">후보 선택…</option>
            {(o.data?.rows ?? []).map((r) => <option key={r.id} value={r.ticker}>{r.rank}. {r.ticker} — {actionLabel(r.action)} ({r.committee_status})</option>)}
          </select>
          {mine && <button disabled={busy} onClick={start}>{busy ? "실행 중…" : "위원회 실행 / 불러오기"}</button>}
          {mine && <Link to={`/stocks/${ticker}`}>종목 상세 보기 →</Link>}
        </div>
      </Card>
      {ticker && d.state === "loading" && <Loading what={ticker} />}
      <Err error={d.error ?? err} retry={d.error ? d.reload : undefined} />
      {com && ticker && (
        <Card title={`${ticker} 위원회 결과`} explain={`추천 #${mine?.recommendation.id} 기준`}>
          <CommitteeSummary c={com} />
          <div style={{ marginTop: 12 }}><Disclosure title="AI 토론 전체 · 분석가별 의견" inset open><CommitteeView c={com} evidence={ev} /></Disclosure></div>
        </Card>
      )}
      {mine && !com && <Empty>이 추천에 대한 위원회 결과가 아직 없습니다.</Empty>}
    </div>
  );
}
