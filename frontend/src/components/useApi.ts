import { useCallback, useEffect, useState } from "react";
import { api } from "../api";

export type LoadState = "idle" | "loading" | "ready" | "error";

export interface ApiState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  state: LoadState;
  reload: () => void;
}

/** The resource a path points at, ignoring query flags such as ``?refresh=true``. */
export const resourceOf = (path: string | null): string | null => (path === null ? null : (path.split("?")[0] ?? path));

/** Fetch ``path`` (null = don't fetch). Keeps the last good data while *the same resource* reloads, but
 * never shows data of a different resource (e.g. the previous ticker) — a late response for an old path
 * is dropped. Exposes a retry. */
export function useApi<T>(path: string | null, deps: unknown[] = []): ApiState<T> {
  const [held, setHeld] = useState<{ key: string | null; data: T } | null>(null);
  const [error, setError] = useState<{ key: string | null; msg: string } | null>(null);
  const [loading, setLoading] = useState<boolean>(path !== null);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  const key = resourceOf(path);
  useEffect(() => {
    if (path === null) return;
    let alive = true;
    const k = resourceOf(path);
    setLoading(true);
    api
      .get<T>(path)
      .then((d) => { if (alive) { setHeld({ key: k, data: d }); setError(null); } })
      .catch((e: unknown) => { if (alive) setError({ key: k, msg: e instanceof Error ? e.message : String(e) }); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, tick, ...deps]);
  const data = held && held.key === key ? held.data : null;
  const err = error && error.key === key ? error.msg : null;
  const state: LoadState = path === null ? "idle" : data === null && !err ? "loading" : err && data === null ? "error" : "ready";
  return { data, error: err, loading, state, reload };
}
