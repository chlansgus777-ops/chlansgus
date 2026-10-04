import { Component, lazy, Suspense, type ReactElement, type ReactNode } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Loading, ModeBanner, StatePanel } from "./components/ui";
import { QuickSearch } from "./components/QuickSearch";
import { PhoneGate } from "./components/Phone";
import { StatusBar, StatusProvider, useStatus } from "./components/status";
import { BrandMark, IHelp, IHome, IMarket, IPerf, IPortfolio, ISettings, IStocks } from "./components/icons";
import type { SystemInfo } from "./types";
import Dashboard from "./pages/Dashboard";
import Glance from "./glance/Glance";
import { openGlance } from "./glance/desktop";
const Stocks = lazy(() => import("./pages/Stocks"));
const StockDetail = lazy(() => import("./pages/StockDetail"));
const Portfolio = lazy(() => import("./pages/Portfolio"));
const Market = lazy(() => import("./pages/Market"));
const Performance = lazy(() => import("./pages/Performance"));
const Settings = lazy(() => import("./pages/Settings"));
const Guide = lazy(() => import("./pages/Guide"));

/** A replaced build or a failed page must leave navigation and a recovery action visible. */
export class PageBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  override state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  override render() {
    return this.state.failed
      ? <StatePanel kind="disconnected" what="화면을 불러오지 못했습니다. 앱이 갱신되었거나 연결이 끊겼을 수 있습니다."
          actions={<button onClick={() => window.location.reload()}>화면 다시 열기</button>} />
      : this.props.children;
  }
}

/** Five destinations named after what the user wants to do (product overhaul 2026-09-28): today's view, finding and
 * judging a stock, my account, the market, and whether the recommendations worked. Settings and help sit apart. */
const NAV: [string, (p: { className?: string }) => ReactElement, string][] = [
  ["/", IHome, "홈"], ["/stocks", IStocks, "종목"], ["/portfolio", IPortfolio, "포트폴리오"], ["/market", IMarket, "시장"], ["/performance", IPerf, "성과"],
];

/** Which build is running (the commit it was built from): after installing a new version the owner can check it. */
export function versionLabel(sys: SystemInfo): string {
  const v = sys.versions ?? {};
  return `앱 버전 ${v.app_version ?? "?"} · 빌드 ${v.code_version ?? "unknown"}`;
}

export default function App() {
  const loc = useLocation();
  if (loc.pathname === "/glance") return <PhoneGate><Glance /></PhoneGate>;  // its own surface: no menu, no status bar
  return (
    <PhoneGate>
      <StatusProvider>
        <Shell />
      </StatusProvider>
    </PhoneGate>
  );
}

/** GLANCE MODE: the quiet desktop object (its own window on the desktop app). */
function GlanceButton() {
  return <button type="button" className="glance-open" onClick={() => void openGlance()} data-testid="open-glance" title="시장 상태와 관심 종목 판단만 작게 띄웁니다">Glance 모드</button>;
}

function Shell() {
  const st = useStatus()!;
  const sys = st.system;
  const loc = useLocation();
  const inStocks = loc.pathname.startsWith("/stocks");
  return (
    <div className="layout">
      <nav className="nav" aria-label="주 메뉴">
        <div className="brand"><BrandMark className="mark" /><div>MarketLens<small>미국 주식 판단 도우미</small></div></div>
        <QuickSearch />
        {NAV.map(([to, Icon, label]) => (
          <NavLink key={to} to={to} end={to === "/"} title={label} className={({ isActive }) => (isActive || (to === "/stocks" && inStocks) ? "active" : "")}>
            <Icon />{label}
          </NavLink>
        ))}
        <div className="grow" />
        <GlanceButton />
        <NavLink to="/settings" title="설정" className={({ isActive }) => `util${isActive ? " active" : ""}`}><ISettings />설정</NavLink>
        <NavLink to="/guide" title="용어·도움말" className={({ isActive }) => `util${isActive ? " active" : ""}`}><IHelp />용어·도움말</NavLink>
        <div className="foot">주문은 넣지 않습니다. 모든 매매는 직접 판단·실행하세요. 가격은 미국 달러(USD).
          {sys.data && <div data-testid="app-version" style={{ marginTop: 6 }}>{versionLabel(sys.data)}</div>}</div>
      </nav>
      <div className="main-col">
        {sys.data && sys.data.mode === "MOCK" && <ModeBanner mode="MOCK" />}
        <StatusBar />
        {sys.error && (
          <div style={{ padding: "14px 34px 0" }}>
            <StatePanel kind="disconnected" what={`백엔드 연결 실패: ${sys.error}`} actions={<button onClick={st.refresh}>다시 시도</button>} />
          </div>
        )}
        <main className="main" key={loc.pathname.split("/").slice(0, 2).join("/")}>
          <PageBoundary key={loc.pathname}><Suspense fallback={<Loading what="화면" rows={2} />}><Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/stocks" element={<Stocks />} />
            <Route path="/stocks/:ticker" element={<StockDetail />} />
            <Route path="/portfolio" element={<Portfolio />} />
            <Route path="/market" element={<Market />} />
            <Route path="/performance" element={<Performance />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/guide" element={<Guide />} />
            {/* earlier addresses (bookmarks, old links) land on the screen that now holds the same thing */}
            <Route path="/opportunities" element={<Navigate to="/stocks?tab=candidates" replace />} />
            <Route path="/watchlist" element={<Navigate to="/stocks?tab=watch" replace />} />
            <Route path="/issues" element={<Navigate to="/market?tab=issues" replace />} />
            <Route path="/calendar" element={<Navigate to="/market?tab=calendar" replace />} />
            <Route path="/macro" element={<Navigate to="/market?tab=macro" replace />} />
            <Route path="/health" element={<Navigate to="/settings?tab=status" replace />} />
            <Route path="/committee" element={<Navigate to="/stocks" replace />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes></Suspense></PageBoundary>
        </main>
      </div>
    </div>
  );
}
