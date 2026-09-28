// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { cacheKeyOf, invalidateApi, useApi } from "./useApi";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function View({ path }: { path: string }) {
  const r = useApi<{ v: string }>(path);
  return <div data-testid="v">{r.data ? `${r.data.v}|${r.loading ? "refreshing" : "idle"}|${r.fetchedAt ? "t" : "-"}` : r.state}</div>;
}

/** fetch that answers each path from ``answers`` after the returned release function is called */
function gatedFetch(honorAbort = true) {
  const pending: { url: string; resolve: (v: string) => void; signal?: AbortSignal | null }[] = [];
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
    calls.push(url);
    return new Promise<Response>((res, rej) => {
      if (honorAbort) init?.signal?.addEventListener("abort", () => rej(new DOMException("aborted", "AbortError")));
      pending.push({ url, signal: init?.signal, resolve: (v) => res(new Response(JSON.stringify({ v }), { status: 200 })) });
    });
  }));
  return { pending, calls };
}

describe("screen cache", () => {
  it("a revisit shows the last answer at once, with its receive time, and refreshes it in the background", async () => {
    const f = gatedFetch();
    const first = render(<View path="/portfolio" />);
    expect(screen.getByTestId("v").textContent).toBe("loading");
    await act(async () => f.pending[0]!.resolve("A"));
    expect(screen.getByTestId("v").textContent).toBe("A|idle|t");
    first.unmount();
    render(<View path="/portfolio" />);
    expect(screen.getByTestId("v").textContent).toBe("A|refreshing|t"); // shown before any answer arrives
    await act(async () => f.pending[1]!.resolve("B"));
    expect(screen.getByTestId("v").textContent).toBe("B|idle|t");
  });

  it("never shows another ticker's or period's answer", async () => {
    const f = gatedFetch();
    const a = render(<View path="/stocks/NVDA" />);
    await act(async () => f.pending[0]!.resolve("nvda"));
    a.unmount();
    render(<View path="/stocks/AMD" />);
    expect(screen.getByTestId("v").textContent).toBe("loading");
    expect(cacheKeyOf("/performance?period=30d")).not.toBe(cacheKeyOf("/performance?period=all"));
    expect(cacheKeyOf("/stocks/NVDA?refresh=true")).toBe("/stocks/NVDA");
  });

  it("identical screens share one request", async () => {
    const f = gatedFetch();
    render(<><View path="/dashboard" /><View path="/dashboard" /></>);
    expect(f.calls.length).toBe(1);
    await act(async () => f.pending[0]!.resolve("D"));
    expect(screen.getAllByTestId("v").map((x) => x.textContent)).toEqual(["D|idle|t", "D|idle|t"]);
  });

  it("a request nobody shows any more is aborted", async () => {
    const f = gatedFetch();
    const v = render(<View path="/issues" />);
    v.unmount();
    await waitFor(() => expect(f.pending[0]!.signal?.aborted).toBe(true));
  });

  it("an answer that started before a change never overwrites the newer one", async () => {
    const f = gatedFetch(false); // a server that answers even an aborted request
    render(<View path="/portfolio" />);
    await act(async () => f.pending[0]!.resolve("A"));
    const refresh = () => screen.getByTestId("v");
    act(() => { invalidateApi(["/portfolio"]); }); // e.g. a refresh already running…
    const old = f.pending[1]!;
    act(() => { invalidateApi(["/portfolio"]); }); // …when a trade is recorded
    const fresh = f.pending[2]!;
    await act(async () => fresh.resolve("after-trade"));
    await act(async () => old.resolve("before-trade")); // arrives last
    expect(refresh().textContent).toBe("after-trade|idle|t");
  });

  it("a successful change outdates what it affects and the screen showing it asks again", async () => {
    const f = gatedFetch();
    render(<View path="/watchlist" />);
    await act(async () => f.pending[0]!.resolve("W1"));
    const post = api.post("/watchlist/NVDA");
    await act(async () => f.pending[1]!.resolve("ok"));
    await post;
    await waitFor(() => expect(f.calls.filter((u) => u.endsWith("/api/watchlist")).length).toBe(2));
    expect(screen.getByTestId("v").textContent).toBe("W1|refreshing|t"); // the last answer stays until the new one arrives
  });
});
