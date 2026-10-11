import { Component, createElement, lazy, Suspense, useEffect, type ComponentType, type ReactElement, type ReactNode } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Loading, ModeBanner, StatePanel } from "./components/ui";
import { QuickSearch } from "./components/QuickSearch";
import { PhoneGate } from "./components/Phone";
import { VisitCounter } from "./components/Diagnostics";
import { StatusBar, StatusProvider, useStatus } from "./components/status";
import { BrandMark, IFlag, IHelp, IHome, IMarket, IPerf, IPortfolio, ISettings, IStocks } from "./components/icons";
import type { SystemInfo } from "./types";
import Dashboard from "./pages/Dashboard";
import Glance from "./glance/Glance";
import { openGlance } from "./glance/desktop";
/** A page loaded on demand that, once its file is here, renders like any other component. React.lazy alone suspends on
 * its first render even when the file is already loaded, and React then holds the loading screen for at least 300 ms:
 * every menu's first visit waited that long before it even asked for its data (diagnosis 2026-10-11). */
type Page = (() => ReactElement) & { preload: () => Promise<unknown> };
function lazyPage(load: () => Promise<{ default: ComponentType }>): Page {
  let loaded: ComponentType | null = null;
  const preload = () => load().then((m) => { loaded = m.default; });
  const Lazy = lazy(() => load().then((m) => { loaded = m.default; return m; }));
  return Object.assign(() => createElement(loaded ?? Lazy), { preload });
}
const Stocks = lazyPage(() => import("./pages/Stocks"));
const Strategies = lazyPage(() => import("./pages/Strategies"));
const StockDetail = lazyPage(() => import("./pages/StockDetail"));
const Portfolio = lazyPage(() => import("./pages/Portfolio"));
const Market = lazyPage(() => import("./pages/Market"));
const Performance = lazyPage(() => import("./pages/Performance"));
const Settings = lazyPage(() => import("./pages/Settings"));
const Guide = lazyPage(() => import("./pages/Guide"));
const PAGES = [Stocks, StockDetail, Portfolio, Market, Performance, Strategies, Settings, Guide];

/** A few seconds after the app opens, fetch the other pages' files in the background (one at a time, when the browser is
 * idle) so opening a menu shows it at once. A failed fetch is left to the page's own load on the visit. */
let preloading = false;
function preloadPages(): void {
  if (preloading || typeof window === "undefined") return;
  preloading = true;
  const idle = (f: () => void) => (typeof window.requestIdleCallback === "function" ? window.requestIdleCallback(() => f(), { timeout: 2000 }) : window.setTimeout(f, 200));
  const next = (i: number) => { if (i < PAGES.length) idle(() => { void PAGES[i]!.preload().catch(() => undefined).finally(() => next(i + 1)); }); };
  window.setTimeout(() => next(0), 3000); // after the first screen and its data: never competing with them
}

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
  ["/", IHome, "홈"], ["/strategies", IFlag, "전략"], ["/stocks", IStocks, "종목"], ["/portfolio", IPortfolio, "포트폴리오"], ["/market", IMarket, "시장"], ["/performance", IPerf, "성과"],
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
  useEffect(preloadPages, []);
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
        <VisitCounter />
        {sys.error && (
          <div style={{ padding: "14px 34px 0" }}>
            <StatePanel kind="disconnected" what={`백엔드 연결 실패: ${sys.error}`} actions={<button onClick={st.refresh}>다시 시도</button>} />
          </div>
        )}
        <main className="main" key={loc.pathname.split("/").slice(0, 2).join("/")}>
          <PageBoundary key={loc.pathname}><Suspense fallback={<Loading what="화면" rows={2} />}><Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/strategies" element={<Strategies />} />
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
