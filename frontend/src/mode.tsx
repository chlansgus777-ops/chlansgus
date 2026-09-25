import { createContext, useContext, useState, type ReactNode } from "react";

/** Beginner (default): conclusions and explanations first, raw data folded away.
 * Advanced: every detail section open by default. Stored per viewer (best effort). */
export type Mode = "beginner" | "advanced";
const Ctx = createContext<{ mode: Mode; setMode: (m: Mode) => void }>({ mode: "beginner", setMode: () => undefined });

function load(): Mode {
  try {
    return localStorage.getItem("marketlens.mode") === "advanced" ? "advanced" : "beginner";
  } catch {
    return "beginner";
  }
}

export function ModeProvider({ children }: { children: ReactNode }) {
  const [mode, set] = useState<Mode>(load);
  const setMode = (m: Mode) => {
    set(m);
    try { localStorage.setItem("marketlens.mode", m); } catch { /* storage unavailable: keep in memory */ }
  };
  return <Ctx.Provider value={{ mode, setMode }}>{children}</Ctx.Provider>;
}

export const useMode = () => useContext(Ctx);

export function ModeSwitch() {
  const { mode, setMode } = useMode();
  return (
    <div className="seg" role="group" aria-label="정보 표시 수준">
      <button className={mode === "beginner" ? "on" : ""} onClick={() => setMode("beginner")} title="핵심 판단과 쉬운 설명 위주">쉽게 보기</button>
      <button className={mode === "advanced" ? "on" : ""} onClick={() => setMode("advanced")} title="세부 지표·원본 데이터까지 모두 펼침">자세히 보기</button>
    </div>
  );
}

/** A section that is folded in beginner mode and open in advanced mode. */
export function More({ title, children, hint }: { title: string; children: ReactNode; hint?: string }) {
  const { mode } = useMode();
  return (
    <details open={mode === "advanced"} className="card" key={mode}>
      <summary>{title}{hint && <span className="caption"> · {hint}</span>}</summary>
      {children}
    </details>
  );
}
