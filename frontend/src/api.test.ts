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
  vi.unstubAllGlobals();
  delete (globalThis as { window?: Window }).window?.__MARKETLENS_TOKEN__;
});

describe("api client", () => {
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
