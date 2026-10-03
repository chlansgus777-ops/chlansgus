import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { invalidateApi, useApi, usePoll } from "./useApi";

type Job = { status: "IDLE" | "RUNNING" | "DONE" | "FAILED"; error?: string; finished_at?: string; phase?: string };

/** Reading a stock never writes an analysis. One explicit command starts a recoverable background job. */
export function useAnalysisJob(ticker: string, completed: () => void) {
  const job = useApi<Job>(`/stocks/${ticker}/analysis`);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [accepted, setAccepted] = useState<Job | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const guard = useRef(false);
  const seen = useRef<string | null>(null);
  useEffect(() => { setAccepted(null); }, [job.data]);
  const running = accepted?.status === "RUNNING" || job.data?.status === "RUNNING";
  const verifyOnly = uncertain || (!!job.error && running);
  usePoll(job.reload, 1000, running || sending);
  useEffect(() => {
    if (job.data?.status === "DONE" && job.data.finished_at && seen.current !== job.data.finished_at) {
      seen.current = job.data.finished_at;
      invalidateApi(["/dashboard", "/opportunities", "/watchlist", `/stocks/${ticker}?`]);
      completed();
    }
  }, [job.data, completed, ticker]);
  const start = async () => {
    if (guard.current || (running && !job.error)) return;
    guard.current = true;
    setSending(true);
    setError(null);
    try {
      if (verifyOnly) {
        setAccepted(await api.get<Job>(`/stocks/${ticker}/analysis`));
        setUncertain(false);
      } else {
        setAccepted(await api.post<Job>(`/stocks/${ticker}/analysis`));
      }
    }
    catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setUncertain(true);
      try {
        setAccepted(await api.get<Job>(`/stocks/${ticker}/analysis`));
        setUncertain(false);
      } catch { /* the next explicit click reads status only */ }
    }
    finally { job.reload(); setSending(false); guard.current = false; }
  };
  return { start, verifyOnly, busy: sending || (running && !job.error),
    error: error || job.error || job.data?.error,
    phase: uncertain ? "작업 시작 여부를 확인하지 못했습니다. 다시 누르면 상태만 확인합니다." : job.data?.phase };
}
