import { useState } from "react";
import { Card, Empty, Err, Loading } from "../components/ui";
import { useSettling } from "../components/useApi";
import { day, num, pct, stamp, errKo } from "../format";
import { SaveTickerSupplement } from "../components/SaveTickerSupplement";

interface Ev { event_id: string; event_type: string; event_date: string; title: string; affected: string[]; importance: number; expected_move: number | null; days_until: number; source: string }

const TYPE_KO: Record<string, string> = {
  Earnings: "실적 발표", Fed: "FOMC(연준)", CPI: "CPI(소비자물가)", PCE: "PCE(개인소비지출 물가)", Jobs: "고용 지표", GDP: "GDP", "Investor Day": "투자자 설명회",
  "Product Launch": "제품 출시", Conference: "컨퍼런스", "Regulatory Decision": "규제 결정", "Antitrust Decision": "반독점 결정", "Government Policy": "정부 정책", "FDA Decision": "FDA 승인 결정",
};

export default function CalendarPage() {
  return <><PrimaryCalendar /><SaveTickerSupplement resource="calendar" /></>;
}

function PrimaryCalendar() {
  const c = useSettling<{ available: boolean; pending?: boolean; refreshing?: boolean; fetched_at?: string | null; refresh_error?: string | null; reason: string | null; events: Ev[] }>("/calendar?days=60");
  if (c.state === "loading" || c.data?.pending) return <Loading what="일정" rows={2} />;
  if (!c.data) return <Err error={c.error} retry={c.reload} />;
  const all = c.data?.events ?? [];
  const moves = all.some((e) => e.expected_move !== null);  // only a source that gives a move gets the column
  if (!c.data.available) return <div className="ribbon warn" role="alert"><span className="cap">⚠ 일정 없음</span><div className="msg" title={c.data.reason ?? ""}>{errKo(c.data.reason)} — ‘일정 없음’과 다릅니다.</div></div>;
  return (
    <div className="grid">
      <div className="explain">앞으로 60일 안의 촉매 일정입니다(미국 동부시간 기준 날짜).{c.data.fetched_at ? ` 일정 받은 시각 ${stamp(c.data.fetched_at)}` : ""}{c.data.refreshing ? " · 새로 받는 중" : c.data.refresh_error ? " · 새로 받기 실패(이전 값)" : ""}</div>
      <Card className="flush">
        <CalendarTable events={all} moves={moves} />
      </Card>
    </div>
  );
}

const MACRO = new Set(["Fed", "CPI", "PCE", "Jobs", "GDP"]);
type Kind = "all" | "macro" | "earnings" | "other";
const kindOf = (t: string): Kind => (MACRO.has(t) ? "macro" : t === "Earnings" ? "earnings" : "other");
const PAGE = 40;

/** 300 earnings dates in one table hide the four macro releases that move everything: the macro and other events
 * are one click away, a ticker search finds one company's date, and the list grows 40 rows at a time. */
function CalendarTable({ events, moves }: { events: Ev[]; moves: boolean }) {
  const [kind, setKind] = useState<Kind>("all");
  const [q, setQ] = useState("");
  const [n, setN] = useState(PAGE);
  const counts = { all: events.length, macro: 0, earnings: 0, other: 0 } as Record<Kind, number>;
  for (const e of events) counts[kindOf(e.event_type)] += 1;
  const term = q.trim().toUpperCase();
  const rows = events.filter((e) => (kind === "all" || kindOf(e.event_type) === kind)
    && (!term || e.affected.some((t) => t.toUpperCase().startsWith(term)) || e.title.toUpperCase().includes(term)));
  return (
    <>
      <div className="filters" style={{ padding: "12px 14px 0" }}>
        <div className="seg" role="tablist" aria-label="일정 종류">
          {([["all", "전체"], ["macro", "거시 지표"], ["earnings", "실적 발표"], ["other", "기타"]] as [Kind, string][]).map(([k, l]) => (
            <button key={k} type="button" role="tab" aria-selected={kind === k} disabled={!counts[k]} onClick={() => { setKind(k); setN(PAGE); }}>{l}<span className="count">{counts[k]}</span></button>
          ))}
        </div>
        <input aria-label="종목 찾기" placeholder="종목 코드·이름" value={q} onChange={(e) => { setQ(e.target.value); setN(PAGE); }} style={{ width: 180 }} />
        <span className="caption">{rows.length}건</span>
      </div>
      {rows.length ? (
          <div className="scroll" style={{ padding: "4px 8px 8px" }}><table><thead><tr><th>날짜</th><th>남은 일수</th><th>종류</th><th>이벤트</th><th>영향 종목</th><th className="num">중요도</th>{moves ? <th className="num">예상 변동폭</th> : null}</tr></thead>
            <tbody>{rows.slice(0, n).map((e) => <tr key={e.event_id}><td>{day(e.event_date)}</td><td><span className={`chip-days${e.days_until <= 2 ? " soon" : ""}`}>{e.days_until === 0 ? "오늘" : `D-${e.days_until}`}</span></td><td>{TYPE_KO[e.event_type] ?? e.event_type}</td><td style={{ whiteSpace: "normal" }}>{e.title}</td><td className="caption">{e.affected.join(", ") || "시장 전체"}</td><td className="num">{num(e.importance)}</td>{moves ? <td className="num">{e.expected_move === null ? "—" : `±${pct(e.expected_move, 1, false)}`}</td> : null}</tr>)}</tbody></table>
            {rows.length > n && <div style={{ padding: "10px 4px 4px" }}><button type="button" className="sm" onClick={() => setN(n + PAGE * 2)}>더 보기 ({rows.length - n}건 남음)</button></div>}
          </div>
      ) : <div style={{ padding: 16 }}><Empty>{events.length ? "조건에 맞는 일정이 없습니다." : "예정된 이벤트 없음"}</Empty></div>}
    </>
  );
}
