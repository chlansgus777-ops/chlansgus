import { Fragment } from "react";
import { Card, Err, Loading, StatePanel } from "../components/ui";
import { ProgressBars, ReadinessBanner, statusKo, SyncControl, type ReadinessInfo } from "../components/Readiness";
import { useApi, usePoll } from "../components/useApi";
import { num, pct, stamp, stampEt, KIND_KO, errCauseKo } from "../format";

interface H { name: string; kind: string; mode: string; status: string; configured: boolean; requests: number; error_rate: number; latency_ms: number | null; last_success: string | null; last_failure: string | null; last_error: string | null; freshness: string | null; rate_limit_state: string; breaker_state: string }
interface Resp { providers: H[]; configured: { kind: string; provider: string; mode: string; configured: boolean; reason: string | null }[]; llm: { provider: string; available: boolean; status: string; usage: Record<string, number | boolean> } }

const BREAKER_KO: Record<string, string> = { CLOSED: "정상", OPEN: "차단됨", HALF_OPEN: "시험 재개" };
const ST_KO: Record<string, string> = { HEALTHY: "정상", DEGRADED: "불안정", DOWN: "중단", UNKNOWN: "미확인" };
const USAGE_KO: Record<string, string> = { calls: "호출 수", input_tokens: "입력 토큰", output_tokens: "출력 토큰", estimated_cost_usd: "추정 비용(USD, 단가 아는 호출만)", unknown_cost_calls: "단가 미상 호출 수", cost_complete: "비용 집계 완전성", avg_latency_ms: "평균 지연(ms)", cached_calls: "캐시 사용", errors: "오류" };

export default function Health() {
  const h = useApi<Resp>("/health");
  const r = useApi<ReadinessInfo>("/readiness");
  usePoll(r.reload, 3_000, !!r.data?.stats_pending); // a recount runs in the background: pick it up when done
  if (h.state === "loading") return <Loading what="시스템 상태" />;
  if (!h.data) return <Err error={h.error} retry={h.reload} />;
  const cls = (s: string) => (s === "HEALTHY" ? "ok" : s === "DOWN" ? "danger" : "warn");
  return (
    <div className="grid">
      <div className="row spread"><span className="caption">데이터 공급자 연결과 추천에 필요한 데이터 준비 상태</span><button onClick={() => { h.reload(); r.reload(); }}>새로고침</button></div>
      {h.data.providers.some((p) => p.status === "DOWN" && p.mode === "LIVE") && (
        <StatePanel kind="provider_failure" what={`중단된 공급자: ${h.data.providers.filter((p) => p.status === "DOWN" && p.mode === "LIVE").map((p) => `${p.name}(${KIND_KO[p.kind] ?? p.kind})${p.last_error ? ` — ${errCauseKo(p.last_error).slice(0, 80)}` : ""}`).join(" · ")}`} />
      )}
      {!h.data.llm.available && h.data.llm.provider !== "none" && <StatePanel kind="ai_unavailable" what={`AI 공급자: ${h.data.llm.provider} — ${ST_KO[h.data.llm.status] ?? h.data.llm.status}`} />}
      {r.data && r.data.recommendation_readiness == null && <StatePanel kind="analyzing" what="데이터 준비 상태를 처음 계산하고 있습니다(저장된 전체 종목을 셉니다). 끝나면 여기에 표시됩니다." />}
      {r.data && r.data.recommendation_readiness != null && (
        <>
          <ReadinessBanner r={r.data} />
          <div className="g2">
            <Card title="추천을 믿어도 되는 상태인가요?" icon="◉" explain="데이터 종류별 준비 상태(무료 공급원 기준)">
              {r.data.categories.length > 0 && <table className="matrix"><thead><tr><th>데이터</th><th>상태</th><th>공급원</th><th>설명</th></tr></thead>
                <tbody>{r.data.categories.map((c) => <tr key={c.category}><td style={{ whiteSpace: "nowrap" }}>{c.category}</td>
                  <td className={c.status.startsWith("READY") ? "pos" : c.status.startsWith("UNAVAILABLE") || c.status.startsWith("BLOCKED") ? "neg" : "warn"} style={{ whiteSpace: "nowrap" }}>{(() => { const [main, ...rest] = statusKo(c.status).split(" · "); return <>{main}{rest.length ? <div className="caption">{rest.join(" · ")}</div> : null}</>; })()}</td>
                  <td className="muted">{c.provider}</td><td className="muted" style={{ whiteSpace: "normal" }}>{c.note}</td></tr>)}</tbody></table>}
              {r.data.mode === "MOCK" && <div className="explain">모의(MOCK) 모드에서는 실데이터 공급자를 쓰지 않습니다.</div>}
              {r.data.readiness_reasons.length > 0 && <ul className="list">{r.data.readiness_reasons.map((x, i) => <li key={i}><span className="dot warn">!</span><span>{x}</span></li>)}</ul>}
            </Card>
            <Card title="스캐너 데이터 준비" icon="⏳" explain={r.data.scanner_status === "SCANNER_READY" ? "전체 시장 스캔에 필요한 데이터가 준비되었습니다." : r.data.scanner_status === "NOT_APPLICABLE" ? "모의 데이터는 준비가 필요 없습니다." : "아직 준비 중입니다. 준비 전 스캔 결과는 믿으면 안 됩니다."}>
              {r.data.mode === "LIVE" && <SyncControl onChange={r.reload} />}
              <ProgressBars p={r.data.progress} />
              {r.data.sync.status && <div className="caption">마지막 동기화: {r.data.sync.status}{r.data.sync.at ? ` · ${stamp(r.data.sync.at)}` : ""}{r.data.sync.bar_days_remaining ? ` · 남은 가격 거래일 ${r.data.sync.bar_days_remaining}` : ""}</div>}
              {r.data.scanner_reasons.length > 0 && <ul className="list">{r.data.scanner_reasons.map((x, i) => <li key={i}><span className="dot warn">!</span><span>{x}</span></li>)}</ul>}
            </Card>
          </div>
        </>
      )}
      <Card title="데이터 공급자">
        <div className="scroll"><table><thead><tr><th>공급자</th><th>종류</th><th>모드</th><th>상태</th><th>데이터 시점</th><th>지연</th><th>마지막 성공</th><th>오류율</th><th>차단기</th><th>요청 제한</th><th>마지막 오류</th></tr></thead>
          <tbody>{h.data.providers.map((p) => <tr key={p.name} className="nowrap-row"><td><b>{p.name}</b></td><td>{KIND_KO[p.kind] ?? p.kind}</td><td>{p.mode}</td><td className={cls(p.status)}>{p.status === "HEALTHY" ? "✓ " : p.status === "DOWN" ? "⛔ " : "! "}{ST_KO[p.status] ?? p.status}</td><td title={stamp(p.freshness)}>{stampEt(p.freshness)}</td><td className="num">{p.latency_ms === null ? "N/A" : `${num(p.latency_ms, 0)} ms`}</td><td title={stamp(p.last_success)}>{stampEt(p.last_success)}</td><td className="num">{pct(p.error_rate, 0, false)}</td><td>{BREAKER_KO[p.breaker_state] ?? p.breaker_state}</td><td>{p.rate_limit_state === "OK" ? "정상" : p.rate_limit_state}</td><td className="muted wrap" title={p.last_error ?? ""}>{p.last_error ? errCauseKo(p.last_error).slice(0, 60) : "—"}</td></tr>)}</tbody></table></div>
      </Card>
      <Card title="공급자 구성 (1순위 → 대체)">
        <table><tbody>{h.data.configured.map((c, i) => <tr key={i}><td>{KIND_KO[c.kind] ?? c.kind}</td><td>{c.provider}</td><td>{c.mode}</td><td className={c.configured ? "pos" : "neg"}>{c.configured ? "설정됨" : "미설정"}</td><td className="muted">{c.reason ?? ""}</td></tr>)}</tbody></table>
      </Card>
      <Card title="LLM(AI) 공급자">
        <div>{h.data.llm.provider} · <span className={cls(h.data.llm.status)}>{ST_KO[h.data.llm.status] ?? h.data.llm.status}</span>{!h.data.llm.available && " — AI 위원회 사용 불가(결정론적 분석에는 영향 없음)"}</div>
        <div className="kv">{Object.entries(h.data.llm.usage).map(([k, v]) => <Fragment key={k}><span className="k">{USAGE_KO[k] ?? k}</span><span>{typeof v === "boolean" ? (v ? "완전" : "불완전(단가 미상 호출 포함)") : num(v, k.includes("cost") ? 4 : 0)}</span></Fragment>)}</div>
      </Card>
    </div>
  );
}
