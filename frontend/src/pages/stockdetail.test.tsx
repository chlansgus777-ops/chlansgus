// @vitest-environment jsdom
import { act, cleanup, render, renderHook, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useApi } from "../components/useApi";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("useApi never shows another resource's data", () => {
  it("drops the previous path's data and a late response for it", async () => {
    const resolvers: Record<string, (r: Response) => void> = {};
    vi.stubGlobal("fetch", (url: string) => new Promise<Response>((res) => { resolvers[String(url).split("/api")[1] ?? String(url)] = res; }));
    const { result, rerender } = renderHook(({ p }) => useApi<{ t: string }>(p), { initialProps: { p: "/stocks/NVDA" } });
    await act(async () => { resolvers["/stocks/NVDA"]?.(new Response(JSON.stringify({ t: "NVDA" }), { status: 200 })); });
    await waitFor(() => expect(result.current.data?.t).toBe("NVDA"));
    rerender({ p: "/stocks/AAPL" });
    expect(result.current.data).toBeNull(); // not NVDA while AAPL loads
    expect(result.current.state).toBe("loading");
    await act(async () => { resolvers["/stocks/AAPL"]?.(new Response(JSON.stringify({ t: "AAPL" }), { status: 200 })); });
    await waitFor(() => expect(result.current.data?.t).toBe("AAPL"));
  });
});

describe("Stock page ticker switch", () => {
  it("remounts per ticker so no state of the previous stock survives", async () => {
    const mod = await import("./StockDetail");
    const Page = mod.default;
    vi.stubGlobal("fetch", () => new Promise(() => undefined)); // keep both pages loading
    let go: (p: string) => void = () => undefined;
    function Nav() { go = useNavigate(); return null; }
    render(<MemoryRouter initialEntries={["/stocks/NVDA"]}><Nav /><Routes><Route path="/stocks/:ticker" element={<Page />} /></Routes></MemoryRouter>);
    expect(await screen.findByText(/NVDA 분석/)).toBeTruthy();
    act(() => go("/stocks/AAPL"));
    expect(await screen.findByText(/AAPL 분석/)).toBeTruthy();
    expect(screen.queryByText(/NVDA/)).toBeNull();
  });
});

describe("recommendation status is re-judged while the page is open (evaluation 2, item 15)", () => {
  it("polls the stored recommendation and picks up PLAN_INVALIDATED without a new analysis", async () => {
    vi.useFakeTimers();
    const { useLiveStatus } = await import("./StockDetail");
    const urls: string[] = [];
    vi.stubGlobal("fetch", (url: string) => {
      urls.push(String(url));
      const body = { recommendation: { id: 7, ticker: "NVDA", current_status: "PLAN_INVALIDATED", current_status_reason: "현재가 $90.00 ≤ 손절 기준 $95.00" }, analysis: { ticker: "NVDA" } };
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
    });
    const { result } = renderHook(() => useLiveStatus("NVDA", 7, 1000));
    expect(result.current).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    vi.useRealTimers();
    await waitFor(() => expect(result.current?.status).toBe("PLAN_INVALIDATED"));
    expect(urls.every((u) => !u.includes("refresh=true"))).toBe(true); // a read, never a re-analysis
  });
});
