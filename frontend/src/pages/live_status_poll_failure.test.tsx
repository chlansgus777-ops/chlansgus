// @vitest-environment jsdom
// 4차 평가 M5 (P3): 실행하려면 frontend/src/pages/ 로 복사한 뒤  cd frontend && npx vitest run src/pages/live_status_poll_failure.test.tsx
// 4182468: 첫 폴링 뒤 서버에 닿지 못하면(사이드카 재시작, 500 등) 오류를 버리고 마지막 상태를 유지해, 3시간 뒤에도 "분석 5분 경과 · CURRENT"가 남는다.
import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

it("a status that can no longer be re-judged is not kept as CURRENT", async () => {
  vi.useFakeTimers();
  const { useLiveStatus } = await import("./StockDetail");
  let poll = 0;
  vi.stubGlobal("fetch", () => {
    poll += 1;
    if (poll > 1) return Promise.reject(new TypeError("Failed to fetch"));  // the backend stopped answering
    return Promise.resolve(new Response(JSON.stringify({ recommendation: { id: 7, ticker: "NVDA", current_status: "CURRENT", current_status_reason: "분석 5분 경과" }, analysis: { ticker: "NVDA" } }),
      { status: 200, headers: { "content-type": "application/json" } }));
  });
  const { result } = renderHook(() => useLiveStatus("NVDA", 7, 60_000));
  for (let i = 0; i < 180; i++) { await act(async () => { await vi.advanceTimersByTimeAsync(60_000); }); }  // three hours
  expect(result.current?.status).not.toBe("CURRENT");
});
