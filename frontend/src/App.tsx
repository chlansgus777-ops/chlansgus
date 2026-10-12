import { Component, createElement, lazy, Suspense, useEffect, type ComponentType, type ReactElement, type ReactNode } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Loading, StatePanel } from "./components/ui";
import { PhoneGate } from "./components/Phone";
import { VisitCounter } from "./components/Diagnostics";
import { ProblemLine, StatusProvider, useStatus } from "./components/status";
import { BrandMark, IHome, IPortfolio, ISettings } from "./components/icons";
import type { SystemInfo } from "./types";
import Home from "./pages/Home";
import RoutineList, { RoutineFull } from "./pages/RoutineList";
import Glance from "./glance/Glance";
import { ViewModeProvider } from "./viewMode";
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
const Evidence = lazyPage(() => import("./pages/Evidence"));
const Dashboard = lazyPage(() => import("./pages/Dashboard"));
const PAGES = [Portfolio, Settings, Evidence, Dashboard, Stocks, StockDetail, Market, Performance, Strategies, Guide];

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

/** Three destinations (owner spec 2026-10-12 "미니멀 개편"): home, my account, settings. Everything else is reached from
 * 설정 → 고급 기능, and the 설정 tab stays lit on those screens. */
const NAV: [string, (p: { className?: string }) => ReactElement, string][] = [
  ["/", IHome, "홈"], ["/portfolio", IPortfolio, "내 계좌"], ["/settings", ISettings, "설정"],
];
const ADVANCED = ["/settings", "/today", "/routine/full", "/evidence", "/strategies", "/stocks", "/market", "/performance", "/guide"];
const navClass = (to: string, path: string) => ({ isActive }: { isActive: boolean }) =>
  isActive || (to === "/settings" && ADVANCED.some((p) => path === p || path.startsWith(`${p}/`))) ? "active" : "";

/** The one line under every screen: the app never orders, and a past record is not a promise. */
export const DISCLAIMER = "주문은 하지 않습니다 · 과거 성적은 미래를 보장하지 않습니다";

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

function Shell() {
  useEffect(preloadPages, []);
  const st = useStatus()!;
  const loc = useLocation();
  return (
    <div className="layout">
      <nav className="nav" aria-label="주 메뉴">
        <div className="brand"><BrandMark className="mark" /><div>MarketLens</div></div>
        {NAV.map(([to, Icon, label]) => (
          <NavLink key={to} to={to} end={to === "/"} title={label} className={navClass(to, loc.pathname)}><Icon />{label}</NavLink>
        ))}
      </nav>
      <div className="main-col">
        <ProblemLine />
        <VisitCounter />
        {st.system.error && (
          <div className="m-shell-pad">
            <StatePanel kind="disconnected" what={`백엔드 연결 실패: ${st.system.error}`} actions={<button onClick={st.refresh}>다시 시도</button>} />
          </div>
        )}
        <main className="main" key={loc.pathname.split("/").slice(0, 2).join("/")}>
          <PageBoundary key={loc.pathname}><Suspense fallback={<Loading what="화면" rows={2} />}><Routes>
            <Route path="/" element={<Home />} />
            <Route path="/routine" element={<RoutineList />} />
            <Route path="/portfolio" element={<ViewModeProvider><Portfolio /></ViewModeProvider>} />
            <Route path="/settings" element={<Settings />} />
            {/* 설정 → 고급 기능 */}
            <Route path="/today" element={<Dashboard />} />
            <Route path="/routine/full" element={<RoutineFull />} />
            <Route path="/evidence" element={<Evidence />} />
            <Route path="/strategies" element={<Strategies />} />
            <Route path="/stocks" element={<Stocks />} />
            <Route path="/stocks/:ticker" element={<StockDetail />} />
            <Route path="/market" element={<Market />} />
            <Route path="/performance" element={<Performance />} />
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
        <footer className="m-foot" data-testid="disclaimer">{DISCLAIMER}</footer>
        {/* phone width: the three destinations as a bottom tab bar */}
        <nav className="tabbar" aria-label="주 메뉴(하단)" data-testid="tabbar">
          {NAV.map(([to, Icon, label]) => (
            <NavLink key={to} to={to} end={to === "/"} className={navClass(to, loc.pathname)}><Icon /><span>{label}</span></NavLink>
          ))}
        </nav>
      </div>
    </div>
  );
}
