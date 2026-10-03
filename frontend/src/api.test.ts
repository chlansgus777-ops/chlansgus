import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "./api";

type Call = { url: string; init: RequestInit };

function mockFetch(responses: (() => Response | Promise<Response>)[]): Call[] {
  const calls: Call[] = [];
  let i = 0;
  vi.stubGlobal("fetch", (url: string, init: RequestInit) => {
    calls.push({ url, init });
    const r = responses[Math.min(i, responses.length - 1)]!;
    i += 1;
    return Promise.resolve().then(r);
  });
  return calls;
}

const json = (body: unknown, status = 200) => () => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const netErr = () => { throw new TypeError("Failed to fetch"); };

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  delete (globalThis as { window?: Window }).window?.__MARKETLENS_TOKEN__;
});

describe("api client", () => {
  it("bounds a stalled response body and never repeats a start command", async () => {
    vi.useFakeTimers();
    const fetch = vi.fn((_url: string, init: RequestInit) => Promise.resolve({
      ok: true,
      json: () => new Promise((_resolve, reject) => init.signal?.addEventListener("abort", () => reject(new Error("aborted")), { once: true })),
    }));
    vi.stubGlobal("fetch", fetch);
    const pending = api.post("/sync/start").catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(15_001);
    const error = await pending;
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).toContain("시간이 초과");
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });
  it("sends the CSRF client header on every request", async () => {
    const calls = mockFetch([json({ ok: 1 })]);
    await api.post("/watchlist/NVDA");
    expect((calls[0]!.init.headers as Record<string, string>)["X-MarketLens-Client"]).toBe("marketlens-ui");
    expect(calls[0]!.url).toBe("/api/watchlist/NVDA");
  });

  it("retries GET on network errors while the backend starts, but never retries POST", async () => {
    const calls = mockFetch([netErr, netErr, json({ ready: true })]);
    await expect(api.get<{ ready: boolean }>("/system", { backoffMs: 1 })).resolves.toEqual({ ready: true });
    expect(calls).toHaveLength(3);
    const posts = mockFetch([netErr, json({})]);
    await expect(api.post("/scan")).rejects.toBeInstanceOf(ApiError);
    expect(posts).toHaveLength(1);
  });

  it("explains errors in Korean and keeps the server detail", async () => {
    mockFetch([json({ detail: "NVDA: 분석 결과 없음" }, 404)]);
    const err = await api.get("/stocks/NVDA", { retries: 0 }).catch((e: unknown) => e as ApiError);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(404);
    expect((err as ApiError).message).toContain("데이터를 찾을 수 없습니다");
    expect((err as ApiError).message).toContain("NVDA: 분석 결과 없음");
  });

  it("does not retry client errors", async () => {
    const calls = mockFetch([json({ detail: "x" }, 422)]);
    await expect(api.get("/stocks/bad", { backoffMs: 1 })).rejects.toBeInstanceOf(ApiError);
    expect(calls).toHaveLength(1);
  });
});

describe("the API address on a phone (owner 2026-09-30: the phone showed '백엔드가 시작되지 않았습니다')", () => {
  it("a page served by a MarketLens backend asks that backend, not the build's 127.0.0.1", async () => {
    const { servedByBackend } = await import("./api");
    expect(servedByBackend({ protocol: "http:", hostname: "192.168.0.10" }, false)).toBe(true);  // the phone
    expect(servedByBackend({ protocol: "http:", hostname: "127.0.0.1" }, false)).toBe(true);  // the PC's browser on the backend
    expect(servedByBackend({ protocol: "tauri:", hostname: "localhost" }, false)).toBe(false);  // the desktop shell
    expect(servedByBackend({ protocol: "http:", hostname: "tauri.localhost" }, false)).toBe(false);
    expect(servedByBackend({ protocol: "http:", hostname: "localhost" }, true)).toBe(false);  // the Vite dev server
    expect(servedByBackend(undefined, false)).toBe(false);
  });
});
