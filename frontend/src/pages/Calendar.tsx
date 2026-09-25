import { Card, Err, Loading } from "../components/ui";
import { useApi } from "../components/useApi";
import { num, pct } from "../format";

interface Ev { event_id: string; event_type: string; event_date: string; title: string; affected: string[]; importance: number; expected_move: number | null; days_until: number; source: string }

export default function CalendarPage() {
  const c = useApi<{ available: boolean; reason: string | null; events: Ev[] }>("/calendar?days=60");
  if (c.loading && !c.data) return <Loading what="calendar" />;
  if (!c.data) return <Err error={c.error} />;
  if (!c.data.available) return <div className="warn">Calendar unavailable: {c.data.reason}</div>;
  return (
    <div className="grid">
      <h1>Catalyst Calendar</h1>
      <Card>
        <table><thead><tr><th>Date</th><th>Days</th><th>Type</th><th>Event</th><th>Affected</th><th>Importance</th><th>Expected vol.</th></tr></thead>
          <tbody>{c.data.events.map((e) => <tr key={e.event_id}><td>{e.event_date}</td><td>{e.days_until}</td><td>{e.event_type}</td><td>{e.title}</td><td>{e.affected.join(", ") || "market-wide"}</td><td>{num(e.importance)}</td><td>{e.expected_move === null ? "N/A" : `±${pct(e.expected_move, 1, false)}`}</td></tr>)}</tbody></table>
      </Card>
    </div>
  );
}
