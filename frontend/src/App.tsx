import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { Err, ModeBanner } from "./components/ui";
import { useApi } from "./components/useApi";
import { ModeSwitch } from "./mode";
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

const NAV: [string, string, string][] = [
  ["/", "◉", "대시보드"], ["/opportunities", "◎", "기회 찾기"], ["/stocks", "⌕", "종목 분석"], ["/portfolio", "◔", "포트폴리오"],
  ["/macro", "∿", "시장·거시"], ["/issues", "▤", "이슈·캘린더"], ["/committee", "◈", "AI 위원회"], ["/performance", "↗", "성과 분석"],
  ["/health", "●", "시스템 상태"], ["/settings", "⚙", "설정"],
];

export default function App() {
  const sys = useApi<SystemInfo>("/system");
  const loc = useLocation();
  const isStock = loc.pathname.startsWith("/stocks/");
  return (
    <div className="layout">
      <nav className="nav" aria-label="주 메뉴">
        <div className="brand">◎ MarketLens<small>미국 주식 투자 판단 도우미</small></div>
        {NAV.map(([to, icon, label]) => (
          <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => (isActive || (to === "/stocks" && isStock) ? "active" : "")}>
            <span aria-hidden>{icon}</span>{label}
          </NavLink>
        ))}
        <div className="foot">MarketLens는 주문을 넣지 않습니다. 모든 매매는 직접 판단·실행하세요. 가격은 모두 미국 달러(USD)입니다.</div>
      </nav>
      <div style={{ minWidth: 0 }}>
        {sys.data && <ModeBanner mode={sys.data.mode} />}
        <div className="topbar">
          <span className="caption">{sys.data ? `데이터: ${sys.data.mode === "MOCK" ? "모의(MOCK)" : "실데이터(LIVE)"} · AI 위원회 ${sys.data.llm.available ? "사용 가능" : "사용 불가"}` : ""}</span>
          <div className="row"><Link to="/guide">용어·판정 설명</Link><ModeSwitch /></div>
        </div>
        {sys.error && <div style={{ padding: "10px 28px" }}><Err error={`백엔드 연결 실패: ${sys.error}`} retry={sys.reload} /></div>}
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
