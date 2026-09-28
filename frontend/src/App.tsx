import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { ModeBanner, StatePanel } from "./components/ui";
import { StatusBar, StatusProvider, useStatus } from "./components/status";
import type { SystemInfo } from "./types";
import Dashboard from "./pages/Dashboard";
import Opportunities from "./pages/Opportunities";
import Stocks from "./pages/Stocks";
import StockDetail from "./pages/StockDetail";
import Portfolio from "./pages/Portfolio";
import MarketMacro from "./pages/MarketMacro";
import IssuesCalendar from "./pages/IssuesCalendar";
import Committee from "./pages/Committee";
import Performance from "./pages/Performance";
import Health from "./pages/Health";
import Settings from "./pages/Settings";
import Guide from "./pages/Guide";

/** Beginners see five destinations; research tools sit under "고급 기능". */
const NAV: [string, string, string][] = [
  ["/", "◉", "홈"], ["/opportunities", "◎", "기회 찾기"], ["/stocks", "⌕", "종목 분석"], ["/portfolio", "◔", "내 포트폴리오"], ["/issues", "▤", "시장 이슈"],
];
const ADVANCED: [string, string, string][] = [
  ["/committee", "◈", "AI 위원회"], ["/performance", "↗", "성과 분석"], ["/macro", "∿", "시장·거시 지표"], ["/health", "●", "시스템 상태"], ["/settings", "⚙", "설정"],
];

/** Which build is running (the commit it was built from): after installing a new version the owner can check it. */
export function versionLabel(sys: SystemInfo): string {
  const v = sys.versions ?? {};
  return `앱 버전 ${v.app_version ?? "?"} · 빌드 ${v.code_version ?? "unknown"}`;
}

export default function App() {
  return (
    <StatusProvider>
      <Shell />
    </StatusProvider>
  );
}

function Shell() {
  const st = useStatus()!;
  const sys = st.system;
  const loc = useLocation();
  const isStock = loc.pathname.startsWith("/stocks/");
  const inAdvanced = ADVANCED.some(([to]) => loc.pathname.startsWith(to));
  return (
    <div className="layout">
      <nav className="nav" aria-label="주 메뉴">
        <div className="brand"><span className="logo" aria-hidden>◎</span>MarketLens<small>미국 주식 투자 판단 도우미</small></div>
        {NAV.map(([to, icon, label]) => (
          <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => (isActive || (to === "/stocks" && isStock) ? "active" : "")}>
            <span className="ico" aria-hidden>{icon}</span>{label}
          </NavLink>
        ))}
        <details className="nav-more" open={inAdvanced || undefined}>
          <summary>고급 기능</summary>
          {ADVANCED.map(([to, icon, label]) => (
            <NavLink key={to} to={to} className={({ isActive }) => (isActive ? "active" : "")}><span className="ico" aria-hidden>{icon}</span>{label}</NavLink>
          ))}
        </details>
        <div className="foot">MarketLens는 주문을 넣지 않습니다. 모든 매매는 직접 판단·실행하세요. 가격은 모두 미국 달러(USD)입니다.
          {sys.data && <div data-testid="app-version">{versionLabel(sys.data)}</div>}</div>
      </nav>
      <div style={{ minWidth: 0 }}>
        {sys.data && sys.data.mode === "MOCK" && <ModeBanner mode="MOCK" />}
        <StatusBar />
        {sys.data && sys.data.mode === "LIVE" && <ModeBanner mode="LIVE" />}
        {sys.error && (
          <div style={{ padding: "12px 28px 0" }}>
            <StatePanel kind="disconnected" what={`백엔드 연결 실패: ${sys.error}`} actions={<button onClick={st.refresh}>다시 시도</button>} />
          </div>
        )}
        <main className="main">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/opportunities" element={<Opportunities />} />
            <Route path="/stocks" element={<Stocks />} />
            <Route path="/stocks/:ticker" element={<StockDetail />} />
            <Route path="/watchlist" element={<Stocks />} />
            <Route path="/portfolio" element={<Portfolio />} />
            <Route path="/macro" element={<MarketMacro />} />
            <Route path="/market" element={<MarketMacro />} />
            <Route path="/issues" element={<IssuesCalendar />} />
            <Route path="/calendar" element={<IssuesCalendar initial="calendar" />} />
            <Route path="/committee" element={<Committee />} />
            <Route path="/performance" element={<Performance />} />
            <Route path="/health" element={<Health />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/guide" element={<Guide />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
