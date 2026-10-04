import { useEffect, useState } from "react";

/** 간략 / 자세히 (owner 2026-10-04: "보기도 어렵고 편의성이 너무 없어 … 보고 바로 판단"): the brief view shows the
 * decision and the few numbers it rests on; the detailed view is the full screen as before. Per viewer, remembered. */
export type ViewMode = "brief" | "full";
const KEY = "ml.view";
const EVENT = "ml-view-change";

function read(): ViewMode {
  try {
    return localStorage.getItem(KEY) === "full" ? "full" : "brief";
  } catch {
    return "brief";
  }
}

export function setViewMode(v: ViewMode): void {
  try {
    localStorage.setItem(KEY, v);
  } catch {
    /* private window: this visit only */
  }
  window.dispatchEvent(new CustomEvent(EVENT, { detail: v }));
}

export function useViewMode(): [ViewMode, (v: ViewMode) => void] {
  const [v, setV] = useState<ViewMode>(read);
  useEffect(() => {
    const on = (e: Event) => setV((e as CustomEvent<ViewMode>).detail ?? read());
    window.addEventListener(EVENT, on);
    return () => window.removeEventListener(EVENT, on);
  }, []);
  return [v, setViewMode];
}
