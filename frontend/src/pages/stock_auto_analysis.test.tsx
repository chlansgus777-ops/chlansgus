// @vitest-environment jsdom
/** A stock with no stored analysis analyses itself when it is opened (owner 2026-10-03: "2,3,4번은 고쳐줘" — #4, the
 * page asked for a button press). Reading still never writes: the page sends its own command, once; a phone, which
 * may only look, never sends it. */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { OnPhone } from "../components/Phone";
import { resetApiCache } from "../components/useApi";
import StockDetailPage from "./StockDetail";

afterEach(() => { cleanup(); resetApiCache(); vi.unstubAllGlobals(); });

function serveNoAnalysis() {
  const posts: string[] = [];
  let running = false;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const path = String(url).split("/api")[1] ?? String(url);
    if (init?.method === "POST") {
      posts.push(path);
      running = true;
      return new Response(JSON.stringify({ status: "RUNNING", phase: "가격·재무 자료 확인 및 분석" }), { status: 202 });
    }
    if (path.startsWith("/stocks/NVDA/analysis")) return new Response(JSON.stringify(running ? { status: "RUNNING", phase: "가격·재무 자료 확인 및 분석" } : { status: "IDLE" }));
    if (path === "/stocks/NVDA") return new Response(JSON.stringify({ detail: "NVDA: 저장된 분석이 없습니다 — 분석 시작을 누르세요" }), { status: 404 });
    return new Response(JSON.stringify(path.startsWith("/watchlist") ? [] : {}));
  }));
  return posts;
}

const page = (phone: boolean) => (
  <OnPhone.Provider value={phone}>
    <MemoryRouter initialEntries={["/stocks/NVDA"]}><Routes><Route path="/stocks/:ticker" element={<StockDetailPage />} /></Routes></MemoryRouter>
  </OnPhone.Provider>
);

it("opening a stock without an analysis starts one by itself, exactly once", async () => {
  const posts = serveNoAnalysis();
  render(page(false));
  await waitFor(() => expect(posts).toEqual(["/stocks/NVDA/analysis"]));
  await waitFor(() => expect(screen.getByTestId("no-analysis").textContent).toContain("지금 분석하고 있습니다"));
  expect(screen.getByRole("button", { name: "분석 진행 중…" })).toBeTruthy();
  await new Promise((r) => setTimeout(r, 50));
  expect(posts).toHaveLength(1); // the job's status reads never start it again
});

it("a phone does not start it: it says the PC will", async () => {
  const posts = serveNoAnalysis();
  render(page(true));
  await waitFor(() => expect(screen.getByTestId("no-analysis").textContent).toContain("PC에서 분석하면"));
  await new Promise((r) => setTimeout(r, 50));
  expect(posts).toEqual([]);
  expect(screen.queryByRole("button", { name: "분석 시작" })).toBeNull();
});
