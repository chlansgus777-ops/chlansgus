import { useApi, usePoll } from "./useApi";
import { num, pct, stamp } from "../format";
import { LatestNews } from "./LatestNews";

type Resource = "calendar" | "details" | "reports" | "options";
interface Row {
  title: string; source_text?: string; collected_at: string; published_at?: string; scheduled_at?: string | null; event_date?: string;
  event_id?: string; news_id?: string; report_id?: string; date_only?: boolean; voting_member?: boolean | null; stance?: string | null;
  provider_summary?: string | null; source_url?: string | null; report_type?: string; has_readable_text?: boolean; provider_url?: string;
  option_id?: string; metrics?: Record<string,number|null>; dates?: Record<string,string|null>; snapshot_prior_day?: boolean; batch_prior_day?: boolean; optionable?: boolean;
  related_companies?: {ticker:string; name:string|null}[];
  community_sentiment?: {positive_percent:number|null; total_votes:number}|null;
  earnings?: {period_label:string; fiscal_year:number|null; basis:string; eps?: Metric; revenue?: Metric}|null;
}
interface Metric { actual:number; estimate:number|null; surprise_fraction:number|null; source_text:string; basis?:string }
interface State { rows:Row[]; last_success:string|null; stale:boolean; error:string|null }
interface Response { enabled:boolean; resources:Partial<Record<Resource,State>> }
const ERRORS:Record<string,string> = {AUTH_REQUIRED:"SaveTicker 브라우저에서 로그인해주세요.",ACCESS_DENIED:"SaveTicker 접근이 거절됐습니다.",NETWORK:"수신 실패. 다음 수집에서 재시도합니다.",FORMAT:"응답 구조를 확인할 수 없어 기존 값을 유지합니다."};
const TITLES = {calendar:"SaveTicker 경제·Fed 일정",details:"SaveTicker 요약·실적 자료",reports:"SaveTicker 리포트",options:"SaveTicker 옵션 집계"};
const OPTION_LABELS:Record<string,string> = {maxPain:"맥스 페인",volume:"거래량",putCallRatioVolume:"풋/콜 거래량",putCallRatioOpenInterest:"풋/콜 미결제약정",referencePrice:"집계 기준 가격",netGammaExposure:"순 감마 노출",gammaPer1Pct:"1%당 감마",callWall:"콜 월",putWall:"풋 월",gammaFlip:"감마 플립"};
const hasDetail = (r:Row) => !!(r.source_text || r.provider_summary || r.earnings || r.community_sentiment);

export function SaveTickerNews({ticker}:{ticker?:string}) {
  const result=useApi<Response>("/saveticker/supplement");
  const detailed=(result.data?.enabled?result.data.resources?.details?.rows:[])??[];
  const shown=detailed.filter(r=>hasDetail(r)&&(!ticker||r.related_companies?.some(c=>c.ticker===ticker))).slice(0,8);
  return <><LatestNews ticker={ticker} excludeIds={shown.flatMap(r=>r.news_id?[r.news_id]:[])}/><SaveTickerSupplement resource="details" ticker={ticker}/></>;
}

/** Provider evidence only: never substitutes for fundamentals, recommendations, or MarketLens AI. */
export function SaveTickerSupplement({resource,ticker}:{resource:Resource;ticker?:string}) {
  const result=useApi<Response>("/saveticker/supplement");
  usePoll(result.reload,60_000,result.data !== null);
  if (!result.data && result.error) return <div role="status" className="ribbon warn">보조 자료 상태 조회 실패: {result.error}<button className="sm ghost" disabled={result.loading} onClick={result.reload}>상태 다시 확인</button></div>;
  if (!result.data?.enabled) return null;
  const state=result.data.resources?.[resource];
  if (!state) return null;
  const eligible=resource==="details"?state.rows.filter(hasDetail):state.rows;
  const rows=ticker?eligible.filter(r=>r.related_companies?.some(c=>c.ticker===ticker)):eligible;
  return <section className="supplement-news" aria-label={TITLES[resource]} style={{marginTop:16}}>
    <h3>{TITLES[resource]}</h3>
    <div className="caption">공급자 자료 · MarketLens 분석과 별도 · 투자 점수 자동 가산 없음{state.last_success?` · 수집 ${stamp(state.last_success)}`:" · 수집 확인 전"}</div>
    {result.error || state.error || (state.stale && state.last_success) ? <div role="status" className="ribbon warn">{result.error || (state.error?ERRORS[state.error] || "수집 오류":"수신이 중단되거나 연결이 해제됐습니다.")} 마지막 자료는 이전 값입니다.</div>:null}
    {!rows.length?<div className="caption">{state.last_success?"해당 범위에 수집된 자료가 없습니다.":`브라우저 확장 ${resource==="options"?"1.2":"1.1"} 이상에서 수집합니다. 아직 이 자료의 수신은 확인되지 않았습니다.`}</div>:null}
    <ul className="list supplement-news-list">{rows.slice(0,resource==="calendar"?40:8).map(r=><li key={r.event_id||r.news_id||r.report_id||r.option_id}><div>
      <strong>{r.title}</strong>
      <div className="caption">{resource==="calendar"?(r.scheduled_at?stamp(r.scheduled_at):`${r.event_date} · 시각 미제공`):resource==="options"?`스냅샷 ${r.dates?.snapshotDate??"미제공"}${r.snapshot_prior_day?" · 전일 자료":""} · 배치 ${r.dates?.batchDate??"미제공"}${r.batch_prior_day?" · 전일 자료":""}`:r.published_at?stamp(r.published_at):"시각 확인 전"}
        {r.related_companies?.length?` · ${r.related_companies.map(c=>c.ticker).join(" · ")}`:""}
        {r.stance?` · 공급자 성향 ${r.stance==="hawkish"?"매파":r.stance==="dovish"?"비둘기":"중립"}`:""}
        {typeof r.voting_member==="boolean"?` · 공급자 표기 투표권 ${r.voting_member?"O":"X"}`:""}</div>
      {r.earnings?<div className="caption">{r.earnings.period_label} · {r.earnings.basis==="adjusted"?"조정 기준":"조정 여부 미명시"} · 회계연도 미확인 · USD
        {(["eps","revenue"] as const).map(key=>{const m=r.earnings?.[key];return m?<div key={key} title={m.source_text}>{key==="eps"?"EPS":"매출"} 실제 {num(m.actual,key==="eps"?2:0)} / 예상 {m.estimate===null?"미제공":num(m.estimate,key==="eps"?2:0)} · 서프라이즈 {m.surprise_fraction===null?"계산 불가":pct(m.surprise_fraction)}{m.basis==="adjusted"?" · 조정 기준":""}</div>:null;})}</div>:null}
      {resource==="reports"&&!r.source_text?<div className="caption">리포트 목록 수신 · 읽을 수 있는 본문은 아직 수집되지 않았습니다.</div>:null}
      {resource==="options"?<><div className="caption">{r.optionable===false?"옵션 미지원 · ":""}공급자 집계 자료 · 실시간 체결가가 아닙니다. 개별 계약·IV·그릭스는 미제공입니다. 최근 기사에 연결된 최대 3종목 범위입니다.</div><details><summary>옵션 집계 지표 확인</summary><div className="scroll"><table><tbody>{Object.entries(OPTION_LABELS).map(([key,label])=><tr key={key}><td>{label}</td><td className="num">{r.metrics?.[key]==null?"미제공":num(r.metrics[key],key==="volume"?0:2)}</td></tr>)}</tbody></table></div></details><div className="caption">가까운 만기 {r.dates?.nearestExpiry??"미제공"}</div></>:null}
      {r.provider_summary?<details><summary>SaveTicker 제공 요약</summary><p style={{whiteSpace:"pre-wrap"}}>{r.provider_summary}</p></details>:null}
      {r.community_sentiment?<div className="caption">SaveTicker 사용자 반응 · {r.community_sentiment.total_votes}표 · 긍정 {r.community_sentiment.positive_percent===null?"미확인":`${num(r.community_sentiment.positive_percent,1)}%`} · 시장 전체 심리가 아닙니다.</div>:null}
      {r.source_text?<details><summary>{resource==="calendar"?"공급자 일정 내용":"공급자 본문 발췌 펼치기"}</summary><div className="caption">최대 12,000자 발췌입니다. 생략된 본문은 SaveTicker에서 확인하세요.</div><p style={{whiteSpace:"pre-wrap",overflowWrap:"anywhere"}}>{r.source_text}</p></details>:null}
      {r.source_url?<a href={r.source_url} target="_blank" rel="noopener noreferrer">원 출처 열기</a>:null}
      {r.provider_url?<a href={r.provider_url} target="_blank" rel="noopener noreferrer">SaveTicker 리포트 열기</a>:null}
    </div></li>)}</ul>
    {rows.length>40&&resource==="calendar"?<details><summary>나머지 일정 {rows.length-40}건</summary>{rows.slice(40).map(r=><p key={r.event_id}>{r.title} · {r.scheduled_at?stamp(r.scheduled_at):r.event_date}</p>)}</details>:null}
  </section>;
}
