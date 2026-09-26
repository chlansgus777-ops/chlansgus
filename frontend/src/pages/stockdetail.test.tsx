// @vitest-environment jsdom
import { act, cleanup, render, renderHook, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useApi } from "../components/useApi";
import type { CommitteeResult, StockDetail as SD } from "../types";
import { committeeFor } from "./StockDetail";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const com = (ticker: string, action: string) => ({ ticker, status: "OK", final_action: action }) as unknown as CommitteeResult;
const detail = (ticker: string, id: number, committee: CommitteeResult | null, comRec: number | null = id) =>
  ({ recommendation: { id, ticker }, analysis: { ticker }, committee, committee_recommendation_id: comRec }) as unknown as SD;

describe("AI committee binding (audit P0: another stock's committee on screen)", () => {
  it("accepts only the committee of the recommendation version on screen", () => {
    expect(committeeFor(detail("AAPL", 5, com("AAPL", "WAIT")), "AAPL")?.final_action).toBe("WAIT");
    expect(committeeFor(detail("AAPL", 5, com("NVDA", "BUY")), "AAPL")).toBeNull(); // other ticker
    expect(committeeFor(detail("AAPL", 5, com("AAPL", "BUY"), 4), "AAPL")).toBeNull(); // older version's committee
    expect(committeeFor(detail("NVDA", 5, com("NVDA", "BUY")), "AAPL")).toBeNull(); // data of another page
  });
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

describe("reused AI committee is labelled with its origin (evaluation 2, item 15)", () => {
  it("says the opinion is carried over, from when, and which recommendation", async () => {
    const { CommitteeView } = await import("../components/CommitteeView");
    const c = { ticker: "NVDA", status: "REUSED", reason: "중요한 변화 없음 → 2026-09-22 위원회 결과 재사용(추가 AI 호출 없음)", deterministic_action: "WAIT", final_action: "WAIT",
      deterministic_confidence: 60, final_confidence: 55, size_class: null, action_changed_by: null, reports: {}, invalid_outputs: {}, debate: [], synthesis: null, risk_review: null,
      portfolio_advice: null, consensus_pct: 70, divergence: "LOW", guard: {}, injection_flags: [], prompt_version: "p", depth: "REUSED", reused_from: 41 } as unknown as CommitteeResult;
    render(<CommitteeView c={c} evidence={new Map()} />);
    const box = screen.getByTestId("committee-reused");
    expect(box.textContent).toContain("2026-09-22");
    expect(box.textContent).toContain("#41");
    expect(screen.getByText("이전 결과 재사용")).toBeTruthy();
  });
});
