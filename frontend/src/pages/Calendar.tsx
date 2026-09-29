import { Card, Empty, Err, Loading } from "../components/ui";
import { useSettling } from "../components/useApi";
import { day, num, pct, stamp, errKo } from "../format";

interface Ev { event_id: string; event_type: string; event_date: string; title: string; affected: string[]; importance: number; expected_move: number | null; days_until: number; source: string }

const TYPE_KO: Record<string, string> = {
  Earnings: "실적 발표", Fed: "FOMC(연준)", CPI: "CPI(소비자물가)", PCE: "PCE(개인소비지출 물가)", Jobs: "고용 지표", GDP: "GDP", "Investor Day": "투자자 설명회",
  "Product Launch": "제품 출시", Conference: "컨퍼런스", "Regulatory Decision": "규제 결정", "Antitrust Decision": "반독점 결정", "Government Policy": "정부 정책", "FDA Decision": "FDA 승인 결정",
};

export default function CalendarPage() {
  const c = useSettling<{ available: boolean; pending?: boolean; refreshing?: boolean; fetched_at?: string | null; refresh_error?: string | null; reason: string | null; events: Ev[] }>("/calendar?days=60");
  if (c.state === "loading" || c.data?.pending) return <Loading what="일정" rows={2} />;
  if (!c.data) return <Err error={c.error} retry={c.reload} />;
  const moves = c.data?.events.some((e) => e.expected_move !== null) ?? false;  // only a source that gives a move gets the column
  if (!c.data.available) return <div className="ribbon warn" role="alert"><span className="cap">⚠ 일정 없음</span><div className="msg" title={c.data.reason ?? ""}>{errKo(c.data.reason)} — ‘일정 없음’과 다릅니다.</div></div>;
  return (
    <div className="grid">
      <div className="explain">앞으로 60일 안의 촉매 일정입니다(미국 동부시간 기준 날짜).{c.data.fetched_at ? ` 일정 받은 시각 ${stamp(c.data.fetched_at)}` : ""}{c.data.refreshing ? " · 새로 받는 중" : c.data.refresh_error ? " · 새로 받기 실패(이전 값)" : ""}</div>
      <Card className="flush">
        {c.data.events.length ? (
          <div className="scroll" style={{ padding: "4px 8px 8px" }}><table><thead><tr><th>날짜</th><th>남은 일수</th><th>종류</th><th>이벤트</th><th>영향 종목</th><th className="num">중요도</th>{moves ? <th className="num">예상 변동폭</th> : null}</tr></thead>
            <tbody>{c.data.events.map((e) => <tr key={e.event_id}><td>{day(e.event_date)}</td><td><span className={`chip-days${e.days_until <= 2 ? " soon" : ""}`}>{e.days_until === 0 ? "오늘" : `D-${e.days_until}`}</span></td><td>{TYPE_KO[e.event_type] ?? e.event_type}</td><td style={{ whiteSpace: "normal" }}>{e.title}</td><td className="caption">{e.affected.join(", ") || "시장 전체"}</td><td className="num">{num(e.importance)}</td>{moves ? <td className="num">{e.expected_move === null ? "—" : `±${pct(e.expected_move, 1, false)}`}</td> : null}</tr>)}</tbody></table></div>
        ) : <div style={{ padding: 16 }}><Empty>예정된 이벤트 없음</Empty></div>}
      </Card>
    </div>
  );
}
