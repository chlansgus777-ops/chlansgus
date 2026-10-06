import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { Card, Err } from "./ui";
import { useApi, usePoll } from "./useApi";

/** 이 PC의 실제 속도 (owner 2026-10-07: "더 적게, 실제 환경에서 확실하게, 가볍게"). The backend's own measurements
 * (screen requests, background jobs, CPU, memory) next to what this window measured (how long home took to show,
 * which screens are used), against the agreed targets. Kept on this PC; the copy button is the only way out. */
type Target = { id: string; label: string; target: number; value: number | null; unit: string; status: "OK" | "OVER" | "UNKNOWN" };
type RouteRow = { route: string; count: number; p50: number | null; p95: number | null; max: number | null; slow: number; over_limit: number; errors: number };
type JobRow = { job: string; count: number; failed: number; avg: number | null; max: number | null; last: number | null };
export type Diag = {
  at: string; uptime_s: number; platform: string; targets: Target[];
  requests: { count: number; p95: number | null; max: number | null; routes: RouteRow[] };
  jobs: JobRow[]; slow: { kind: string; name: string; seconds: number; at: string }[]; cpu_recent: number | null; note: string;
};

const HOME_KEY = "ml.diag.home";
const PAGES_KEY = "ml.diag.pages";
export const HOME_TARGET_S = 3;

function read<T>(k: string, d: T): T {
  try {
    const v = localStorage.getItem(k);
    return v ? (JSON.parse(v) as T) : d;
  } catch {
    return d;
  }
}
function write(k: string, v: unknown): void {
  try {
    localStorage.setItem(k, JSON.stringify(v));
  } catch {
    /* private window or blocked storage: the measurement is just not kept */
  }
}

let homeRecorded = false;
/** Home's first data on screen, in seconds since this window started (once per start; the last 10 are kept). */
export function recordHomeShown(nowMs: number = performance.now()): void {
  if (homeRecorded) return;
  homeRecorded = true;
  const list = read<number[]>(HOME_KEY, []);
  write(HOME_KEY, [...list, Math.round(nowMs) / 1000].slice(-10));
}
export function resetHomeRecordedForTests(): void {
  homeRecorded = false;
}

/** Counts which screens are opened (the first path segment only — never a ticker). */
export function VisitCounter() {
  const loc = useLocation();
  useEffect(() => {
    const page = "/" + (loc.pathname.split("/")[1] ?? "");
    const c = read<Record<string, number>>(PAGES_KEY, {});
    c[page] = (c[page] ?? 0) + 1;
    write(PAGES_KEY, c);
  }, [loc.pathname]);
  return null;
}

const PAGE_KO: Record<string, string> = { "/": "홈", "/strategies": "전략", "/stocks": "종목", "/portfolio": "포트폴리오", "/market": "시장",
  "/performance": "성과", "/settings": "설정", "/guide": "도움말" };

function fmt(t: Target): string {
  if (t.value == null) return "아직 측정 전";
  if (t.unit === "s") return `${t.value.toFixed(2)}초`;
  if (t.unit === "share") return `${(t.value * 100).toFixed(1)}%`;
  if (t.unit === "MB") return `${Math.round(t.value)}MB`;
  return `${t.value}${t.unit}`;
}
function goal(t: Target): string {
  if (t.unit === "s") return `${t.target}초 이하`;
  if (t.unit === "share") return `${t.target * 100}% 이하`;
  if (t.unit === "MB") return `${t.target}MB 이하`;
  return `${t.target}${t.unit}`;
}
const sec = (v: number | null) => (v == null ? "—" : v < 1 ? `${Math.round(v * 1000)}ms` : `${v.toFixed(1)}초`);
const MARK = { OK: "✓ 목표 안", OVER: "✗ 목표 밖", UNKNOWN: "· 측정 전" } as const;

export function DiagnosticsCard() {
  const r = useApi<Diag>("/diagnostics");
  usePoll(r.reload, 10_000);
  const [copied, setCopied] = useState(false);
  const home = read<number[]>(HOME_KEY, []);
  const pages = Object.entries(read<Record<string, number>>(PAGES_KEY, {})).sort((a, b) => b[1] - a[1]);
  const homeLast = home.length ? home[home.length - 1]! : null;
  const homeTarget: Target = { id: "home", label: "켠 뒤 홈 표시", target: HOME_TARGET_S, value: homeLast, unit: "s",
    status: homeLast == null ? "UNKNOWN" : homeLast <= HOME_TARGET_S ? "OK" : "OVER" };
  const copy = async () => {
    const text = JSON.stringify({ backend: r.data, home_seconds: home, pages: Object.fromEntries(pages) }, null, 1);
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  if (!r.data) return <Card title="이 PC의 실제 속도">{r.error ? <Err error={r.error} retry={r.reload} /> : <div className="caption">측정값을 불러오는 중…</div>}</Card>;
  const d = r.data;
  const targets = [homeTarget, ...d.targets];
  return (
    <div className="grid" data-testid="diagnostics">
      <Card title="이 PC의 실제 속도" testId="diag-targets"
            right={<button className="sm" onClick={copy} data-testid="diag-copy">{copied ? "복사됨" : "측정값 복사"}</button>}
            explain="앱을 켠 뒤 이 PC에서 잰 값입니다. 목표: 홈 3초 안, 화면 응답 대부분 0.3초 안, 15초 넘는 응답 0회, 가만히 둘 때 CPU 2% 이하, 메모리 500MB 이하.">
        <table className="diag-table">
          <thead><tr><th>항목</th><th className="r">지금</th><th className="r">목표</th><th>판정</th></tr></thead>
          <tbody>
            {targets.map((t) => (
              <tr key={t.id} className={`s-${t.status.toLowerCase()}`} data-testid={`diag-${t.id}`}>
                <td>{t.label}</td><td className="num r">{fmt(t)}</td><td className="num r caption">{goal(t)}</td><td>{MARK[t.status]}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="caption">켠 지 {Math.round(d.uptime_s / 60)}분 · 화면 요청 {d.requests.count}회 · 가장 느린 응답 {sec(d.requests.max)}. {d.note}</div>
      </Card>
      {d.slow.length > 0 && (
        <Card title="느렸던 순간" testId="diag-slow">
          <ul className="diag-slow">{d.slow.slice(0, 10).map((s, i) => <li key={i}><span>{s.kind} · {s.name}</span><b>{sec(s.seconds)}</b><span className="caption">{new Date(s.at).toLocaleTimeString("ko-KR")}</span></li>)}</ul>
        </Card>
      )}
      <div className="grid g2">
        <Card title="화면 요청 (느린 순)" testId="diag-routes">
          <table className="diag-table">
            <thead><tr><th>요청</th><th className="r">횟수</th><th className="r">95%</th><th className="r">최대</th></tr></thead>
            <tbody>{d.requests.routes.slice(0, 12).map((x) => <tr key={x.route}><td className="mono">{x.route}</td><td className="num r">{x.count}</td><td className="num r">{sec(x.p95)}</td><td className="num r">{sec(x.max)}</td></tr>)}</tbody>
          </table>
        </Card>
        <Card title="백그라운드 계산" testId="diag-jobs">
          <table className="diag-table">
            <thead><tr><th>계산</th><th className="r">횟수</th><th className="r">평균</th><th className="r">최대</th></tr></thead>
            <tbody>{d.jobs.slice(0, 12).map((x) => <tr key={x.job}><td className="mono">{x.job}{x.failed ? ` (실패 ${x.failed})` : ""}</td><td className="num r">{x.count}</td><td className="num r">{sec(x.avg)}</td><td className="num r">{sec(x.max)}</td></tr>)}</tbody>
          </table>
        </Card>
      </div>
      <Card title="실제로 여는 화면" testId="diag-pages" explain="이 PC에서 각 화면을 연 횟수입니다. 거의 안 쓰는 화면은 줄일 후보입니다.">
        {pages.length ? <div className="diag-pages">{pages.map(([p, n]) => <span key={p} className="chip">{PAGE_KO[p] ?? p} {n}</span>)}</div> : <div className="caption">아직 기록이 없습니다.</div>}
      </Card>
    </div>
  );
}
