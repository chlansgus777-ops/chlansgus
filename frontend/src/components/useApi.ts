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

/** Fetch ``path`` (null = don't fetch). Keeps the last good data while reloading, exposes a retry. */
export function useApi<T>(path: string | null, deps: unknown[] = []): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(path !== null);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  useEffect(() => {
    if (path === null) return;
    let alive = true;
    setLoading(true);
    api
      .get<T>(path)
      .then((d) => { if (alive) { setData(d); setError(null); } })
      .catch((e: unknown) => { if (alive) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, tick, ...deps]);
  const state: LoadState = path === null ? "idle" : loading && data === null ? "loading" : error && data === null ? "error" : "ready";
  return { data, error, loading, state, reload };
}
