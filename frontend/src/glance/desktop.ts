/** Glance window ↔ desktop shell (Tauri). Outside the desktop app (a browser, the tests) the same calls fall back to
 * plain navigation, so the Glance view still works as a page. */

type Invoke = (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;

function invoker(): Invoke | null {
  const t = (window as unknown as { __TAURI_INTERNALS__?: { invoke?: Invoke } }).__TAURI_INTERNALS__;
  return t && typeof t.invoke === "function" ? t.invoke : null;
}

export const isDesktop = (): boolean => invoker() !== null;

async function call(cmd: string, args?: Record<string, unknown>): Promise<boolean> {
  const inv = invoker();
  if (!inv) return false;
  try {
    await inv(cmd, args);
    return true;
  } catch {
    return false;
  }
}

/** Open (or focus) the Glance window; in a browser, show the Glance page. */
export async function openGlance(): Promise<void> {
  if (!(await call("glance_open"))) window.location.hash = "#/glance";
}

/** Close the Glance window; in a browser, go back to the full app. */
export async function closeGlance(): Promise<void> {
  if (!(await call("glance_close"))) window.location.hash = "#/";
}

/** Show the full app (Analyze Mode) at ``path`` — the main window on the desktop, this page in a browser. */
export async function openAnalyze(path = "/"): Promise<void> {
  if (!(await call("main_show", { path }))) window.location.hash = `#${path}`;
}

export async function setAlwaysOnTop(on: boolean): Promise<boolean> {
  return call("glance_set_on_top", { on });
}

/** The symbol Glance follows: the last stock page opened in Analyze Mode (shared storage of the app's windows). */
const FOCUS_KEY = "ml.focus";
export function setFocusSymbol(t: string): void {
  try {
    localStorage.setItem(FOCUS_KEY, t.toUpperCase());
  } catch {
    /* storage unavailable: Glance falls back to its own pick */
  }
}
export function clearFocusSymbol(): void {
  try {
    localStorage.removeItem(FOCUS_KEY);
  } catch {
    /* storage unavailable */
  }
}
export function focusSymbol(): string | null {
  try {
    const v = localStorage.getItem(FOCUS_KEY);
    return v && /^[A-Z0-9.\-]{1,12}$/.test(v) ? v : null;
  } catch {
    return null;
  }
}
export const FOCUS_STORAGE_KEY = FOCUS_KEY;

const TOP_KEY = "ml.glance.ontop";
export function savedOnTop(): boolean {
  try {
    return localStorage.getItem(TOP_KEY) !== "0";
  } catch {
    return true;
  }
}
export function saveOnTop(on: boolean): void {
  try {
    localStorage.setItem(TOP_KEY, on ? "1" : "0");
  } catch {
    /* ignore */
  }
}
