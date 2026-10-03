// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LatestNews } from "./LatestNews";
import { resetApiCache } from "./useApi";
afterEach(() => { cleanup(); resetApiCache(); vi.unstubAllGlobals(); });

it("stays hidden when the optional provider is disabled", async () => {
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ enabled: false, rows: [] })));
  vi.stubGlobal("fetch", fetcher);
  render(<LatestNews />);
  await waitFor(() => expect(fetcher).toHaveBeenCalled());
  expect(screen.queryByRole("region", { name: "최신 보조 뉴스" })).toBeNull();
});

it("shows an unavailable supplement without claiming market sentiment or successful collection", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ enabled: true, rows: [], error: "403 public access denied", last_success: null }))));
  render(<LatestNews ticker="NVDA" />);
  await waitFor(() => expect(screen.getByRole("status").textContent).toContain("기존 뉴스·분석은 계속"));
  expect(screen.getByText(/수집 성공 확인 전/)).toBeTruthy();
  expect(screen.getByText("403 public access denied")).toBeTruthy();
  expect(screen.getByRole("status").textContent).toContain("수집에 성공한 자료가 없습니다");
  expect(screen.queryByText(/시장 전체 센티먼트/)).toBeNull();
});

it("reports a failed status request on first entry", async () => {
  const fetcher = vi.fn(async () => new Response("", {status: 503}));
  vi.stubGlobal("fetch", fetcher);
  render(<LatestNews />);
  // The shared GET client retries 503 twice, with 500 ms and 1 s backoff.
  await waitFor(() => expect(screen.getByRole("status").textContent).toContain("상태 조회 실패"), {timeout: 3000});
  expect(fetcher).toHaveBeenCalledTimes(3);
});
