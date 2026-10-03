import { useApi, usePoll } from "./useApi";
import { stamp } from "../format";

interface NewsRow {
  news_id: string; title: string; published_at: string; source: string; url: string; tickers: string[];
  metadata?: { provider: string; provider_url?: string; event_type: string; category?: string; view_count?: number } | null;
}
interface NewsResponse {
  enabled: boolean; authenticated?: boolean; transport_mode?: string; rows: NewsRow[]; pending?: boolean; refreshing?: boolean; error?: string; last_success?: string | null; cache_age_s?: number;
}
const EVENTS: Record<string, string> = { earnings: "실적", guidance: "가이던스", dilution: "희석", ma: "M&A", regulation: "규제", fed: "Fed",
  macro: "경제지표", options_news: "옵션 뉴스", buyback: "자사주 매입", dividend: "배당", analyst: "애널리스트", management: "경영진", contract: "계약", product: "제품", geopolitics: "지정학" };

/** Optional news inside existing market/stock news areas. No provider URLs or payload selectors here. */
export function LatestNews({ ticker, excludeIds=[] }: { ticker?: string; excludeIds?:string[] }) {
  const r = useApi<NewsResponse>(ticker ? `/news?ticker=${encodeURIComponent(ticker)}` : "/news");
  usePoll(r.reload, r.data?.pending || r.data?.refreshing ? 3_000 : 60_000, r.data !== null);
  const data = r.data;
  if (!data?.enabled && !r.error) return null;
  if (!data?.enabled) return <div role="status" className="ribbon warn">보조 뉴스 상태 조회 실패: {r.error}<button className="sm ghost" onClick={r.reload} disabled={r.loading}>상태 다시 확인</button></div>;
  const error = r.error || data.error;
  const rows = data.rows.filter(n=>!excludeIds.includes(n.news_id));
  return (
    <section aria-label="최신 보조 뉴스" className="supplement-news" style={{ marginTop: 12 }}>
      <h3>최신 보조 뉴스</h3>
      <div className="caption">SaveTicker {data.transport_mode === "browser" ? "브라우저 수집" : data.authenticated ? "로그인 수집" : "공개 목록"} · 제목·종목 태그 기반 참고 자료 · 점수 자동 가산 없음
        {data.last_success ? ` · 마지막 수집 ${stamp(data.last_success)}` : " · 수집 성공 확인 전"}</div>
      {error ? <div role="status" className="ribbon warn">보조 뉴스 수집 실패 — 기존 뉴스·분석은 계속 표시합니다. {data.last_success ? "마지막 수집 값은 이전 자료입니다." : "수집에 성공한 자료가 없습니다."}<details><summary>수집 오류</summary><div className="caption">{error}</div></details></div>
        : data.pending ? <div role="status" className="caption">보조 뉴스를 받는 중…</div> : null}
      {!data.pending && !error && !data.rows.length ? <div className="caption">해당 범위에 수집된 뉴스가 없습니다.</div> : null}
      <ul className="list supplement-news-list">{rows.slice(0, 8).map((n) => (
        <li key={n.news_id}><div>
          {n.url || n.metadata?.provider_url ? <a href={n.url || n.metadata?.provider_url} target="_blank" rel="noopener noreferrer">{n.title}</a> : <b>{n.title}</b>}
          <div className="caption">{stamp(n.published_at)} · {n.source}
            {n.metadata ? ` · 경유 ${n.metadata.provider}` : ""}{n.metadata && EVENTS[n.metadata.event_type] ? ` · ${EVENTS[n.metadata.event_type]}` : ""}
            {n.tickers.length ? ` · ${n.tickers.join(" · ")}` : ""}</div>
        </div></li>
      ))}</ul>
    </section>
  );
}
