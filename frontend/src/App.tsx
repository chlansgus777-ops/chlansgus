import { NavLink, Route, Routes } from "react-router-dom";
import { useApi } from "./components/useApi";
import type { SystemInfo } from "./types";
import Dashboard from "./pages/Dashboard";
import Opportunities from "./pages/Opportunities";
import Market from "./pages/Market";
import Stocks from "./pages/Stocks";
import StockDetail from "./pages/StockDetail";
import Watchlist from "./pages/Watchlist";
import Portfolio from "./pages/Portfolio";
import Issues from "./pages/Issues";
import Macro from "./pages/Macro";
import CalendarPage from "./pages/Calendar";
import Committee from "./pages/Committee";
import Performance from "./pages/Performance";
import Health from "./pages/Health";
import Settings from "./pages/Settings";

const NAV: [string, string][] = [
  ["/", "Dashboard"], ["/opportunities", "Opportunities"], ["/market", "Market"], ["/stocks", "Stocks"], ["/watchlist", "Watchlist"],
  ["/portfolio", "Portfolio"], ["/issues", "Issues"], ["/macro", "Macro"], ["/calendar", "Calendar"], ["/committee", "AI Committee"],
  ["/performance", "Model Performance"], ["/health", "System Health"], ["/settings", "Settings"],
];

export default function App() {
  const sys = useApi<SystemInfo>("/system");
  return (
    <div className="layout">
      <nav className="nav">
        <div className="brand">◎ MarketLens</div>
        {NAV.map(([to, label]) => (
          <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => (isActive ? "active" : "")}>
            {label}
          </NavLink>
        ))}
        <div className="muted" style={{ padding: "18px", fontSize: 11 }}>Decision support only. MarketLens never places orders.</div>
      </nav>
      <div>
        {sys.data && (
          <div className={`banner ${sys.data.mock_banner ? "mock" : "live"}`}>
            {sys.data.mock_banner ? "MOCK DATA — synthetic prices and fundamentals, not real market data" : "LIVE MODE — missing data is shown as MISSING, never substituted"}
          </div>
        )}
        {sys.error && <div className="banner mock">Backend unreachable: {sys.error}</div>}
        <main className="main">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/opportunities" element={<Opportunities />} />
            <Route path="/market" element={<Market />} />
            <Route path="/stocks" element={<Stocks />} />
            <Route path="/stocks/:ticker" element={<StockDetail />} />
            <Route path="/watchlist" element={<Watchlist />} />
            <Route path="/portfolio" element={<Portfolio />} />
            <Route path="/issues" element={<Issues />} />
            <Route path="/macro" element={<Macro />} />
            <Route path="/calendar" element={<CalendarPage />} />
            <Route path="/committee" element={<Committee />} />
            <Route path="/performance" element={<Performance />} />
            <Route path="/health" element={<Health />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
