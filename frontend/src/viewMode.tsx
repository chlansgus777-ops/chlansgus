import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

/** 쉽게 보기 / 자세히 보기 (docs/UI_UX_GUIDELINES.md §4). Easy is the app's default: plain Korean words, the
 * answer and its plan. Detail adds the English codes, evidence ids, research numbers and rule formulas.
 * The choice lives in this browser only; storage failing keeps the default. Outside the provider (a page
 * rendered on its own, e.g. in a test) everything is shown, as before this switch existed. */
export type ViewMode = "easy" | "detail";

const KEY = "marketlens.viewMode";

function load(): ViewMode {
  try {
    return window.localStorage.getItem(KEY) === "detail" ? "detail" : "easy";
  } catch {
    return "easy";
  }
}

interface Ctx { mode: ViewMode; setMode: (m: ViewMode) => void }
const ViewModeCtx = createContext<Ctx>({ mode: "detail", setMode: () => {} });

export function ViewModeProvider({ children, initial }: { children: ReactNode; initial?: ViewMode }) {
  const [mode, set] = useState<ViewMode>(() => initial ?? load());
  const setMode = useCallback((m: ViewMode) => {
    set(m);
    try { window.localStorage.setItem(KEY, m); } catch { /* the choice just isn't remembered */ }
  }, []);
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

/** The segmented switch in the menu. */
export function ViewModeSwitch() {
  const { mode, setMode } = useViewMode();
  return (
    <div className="view-switch" role="group" aria-label="보기 방식" data-testid="view-switch">
      <button type="button" aria-pressed={mode === "easy"} className={mode === "easy" ? "on" : ""} onClick={() => setMode("easy")}
              title="결론과 가격 계획만 쉬운 말로 보여 줍니다">쉽게 보기</button>
      <button type="button" aria-pressed={mode === "detail"} className={mode === "detail" ? "on" : ""} onClick={() => setMode("detail")}
              title="영문 코드·근거 번호·계산식·연구용 수치까지 모두 보여 줍니다">자세히 보기</button>
    </div>
  );
}
