/** API client for the local MarketLens backend.
 * - Base URL: injected by the desktop shell (window.__MARKETLENS_API__), else VITE_API_BASE, else same origin.
 * - Every request carries X-MarketLens-Client (required by the backend for state-changing calls: CSRF guard)
 *   and the per-launch token when the desktop shell provides one.
 * - GET requests are retried on network errors and 503 (the backend may still be starting). */

export type Json = Record<string, unknown>;

declare global {
  interface Window {
    __MARKETLENS_API__?: string;
    __MARKETLENS_TOKEN__?: string;
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: string) {
    super(message);
  }
}

const STATUS_KO: Record<number, string> = {
  0: "백엔드에 연결할 수 없습니다(시작 중이거나 종료됨).",
  400: "요청 형식이 올바르지 않습니다.",
  401: "인증 토큰이 없거나 올바르지 않습니다.",
  403: "허용되지 않은 요청입니다(보안 정책).",
  404: "데이터를 찾을 수 없습니다.",
  422: "입력값이 올바르지 않습니다.",
  429: "요청이 너무 많습니다. 잠시 후 다시 시도하세요.",
  500: "서버 내부 오류가 발생했습니다.",
  503: "서비스가 아직 준비되지 않았습니다.",
};

export function describeStatus(status: number): string {
  return STATUS_KO[status] ?? `요청 실패(HTTP ${status})`;
}

function baseUrl(): string {
  if (typeof window !== "undefined" && window.__MARKETLENS_API__) return window.__MARKETLENS_API__.replace(/\/$/, "");
  return ((import.meta.env.VITE_API_BASE as string | undefined) ?? "").replace(/\/$/, "");
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export interface RequestOptions { retries?: number; backoffMs?: number }

async function req<T>(method: string, path: string, body?: unknown, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json", "X-MarketLens-Client": "marketlens-ui" };
  if (typeof window !== "undefined" && window.__MARKETLENS_TOKEN__) headers["X-MarketLens-Token"] = window.__MARKETLENS_TOKEN__;
  const init: RequestInit = { method, headers };
  if (body !== undefined) init.body = JSON.stringify(body);
  const retries = opts.retries ?? (method === "GET" ? 2 : 0);
  const backoff = opts.backoffMs ?? 500;
  let last: ApiError | null = null;
  for (let attempt = 0; attempt <= retries; attempt++) {
    if (attempt > 0) await sleep(backoff * 2 ** (attempt - 1));
    let r: Response;
    try {
      r = await fetch(`${baseUrl()}/api${path}`, init);
    } catch {
      last = new ApiError(0, describeStatus(0));
      continue; // network error → retry (backend may be starting)
    }
    if (r.ok) return (await r.json()) as T;
    let detail: string | undefined;
    try {
      const j = (await r.json()) as { detail?: unknown };
      detail = typeof j.detail === "string" ? j.detail : undefined;
    } catch {
      detail = undefined;
    }
    last = new ApiError(r.status, detail ? `${describeStatus(r.status)} — ${detail}` : describeStatus(r.status), detail);
    if (r.status !== 503 && r.status !== 429) break; // only transient statuses are retried
  }
  throw last ?? new ApiError(0, describeStatus(0));
}

export const api = {
  get: <T>(p: string, o?: RequestOptions) => req<T>("GET", p, undefined, o),
  post: <T>(p: string, b?: unknown) => req<T>("POST", p, b),
  put: <T>(p: string, b?: unknown) => req<T>("PUT", p, b),
  del: <T>(p: string) => req<T>("DELETE", p),
};
