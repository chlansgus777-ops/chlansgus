// @vitest-environment jsdom
// 3차 평가 N12 (P2): 실행하려면 frontend/src/pages/ 로 복사한 뒤  cd frontend && npx vitest run src/pages/live_status_freeze.test.tsx
// 445221f: 같은 종목의 더 새 추천(#8)이 생기면 폴링이 #7을 다시 판정하지 않아, 3시간 뒤에도 "분석 5분 경과 · CURRENT"가 그대로 남는다.
import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

it("the page does not keep showing a status that nobody re-judged after a newer recommendation appeared", async () => {
  vi.useFakeTimers();
  const { useLiveStatus } = await import("./StockDetail");
  let poll = 0;
  vi.stubGlobal("fetch", () => {
    poll += 1;
    const rec = poll === 1 ? { id: 7, current_status: "CURRENT", current_status_reason: "분석 5분 경과" }
                           : { id: 8, current_status: "CURRENT", current_status_reason: "새 분석" };  // a scan stored #8
    return Promise.resolve(new Response(JSON.stringify({ recommendation: { ticker: "NVDA", ...rec }, analysis: { ticker: "NVDA" } }),
      { status: 200, headers: { "content-type": "application/json" } }));
  });
  const { result } = renderHook(() => useLiveStatus("NVDA", 7, 60_000));
  for (let i = 0; i < 180; i++) { await act(async () => { await vi.advanceTimersByTimeAsync(60_000); }); }  // three hours
  expect(result.current?.status).not.toBe("CURRENT");
});
