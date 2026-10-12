import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { setKoreaTimeOnly } from "./format";

/** Plain words vs. full detail. The 쉽게/자세히 switch is gone (owner spec 2026-10-12 "미니멀 개편"): the basic screens
 * (내 계좌) are wrapped in an easy provider, and the screens under 설정 → 고급 기능 render outside any provider, which
 * shows everything — English codes, evidence ids, research numbers and rule formulas. */
export type ViewMode = "easy" | "detail";

interface Ctx { mode: ViewMode; setMode: (m: ViewMode) => void }
const ViewModeCtx = createContext<Ctx>({ mode: "detail", setMode: () => {} });

export function ViewModeProvider({ children, initial = "easy" }: { children: ReactNode; initial?: ViewMode }) {
  const [mode, setMode] = useState<ViewMode>(initial);
  // Times read before the children render (format.stamp is a plain function); the app shell re-renders on a switch.
  setKoreaTimeOnly(mode === "easy");
  useEffect(() => () => setKoreaTimeOnly(false), []);
  return <ViewModeCtx.Provider value={{ mode, setMode }}>{children}</ViewModeCtx.Provider>;
}

export function useViewMode(): Ctx { return useContext(ViewModeCtx); }
export function useEasy(): boolean { return useContext(ViewModeCtx).mode === "easy"; }

/** Shown only in 자세히 보기. */
export function DetailOnly({ children }: { children: ReactNode }) {
  return useEasy() ? null : <>{children}</>;
}

/** "위험 선호(Risk On)" → "위험 선호", "매수(BUY)" → "매수": drops a trailing English code in parentheses. */
export function plainKo(label: string): string {
  return label.replace(/\s*\([A-Za-z][A-Za-z0-9 .,/&'-]*\)$/, "");
}
