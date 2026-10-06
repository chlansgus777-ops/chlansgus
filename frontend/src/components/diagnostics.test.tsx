// @vitest-environment jsdom
/** 이 PC의 실제 속도: the agreed targets with what this PC measured, the slow moments, the screens actually used,
 * and a copy of the measurements — nothing about the account. */
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { DiagnosticsCard, VisitCounter, recordHomeShown, resetHomeRecordedForTests, type Diag } from "./Diagnostics";

beforeEach(() => { localStorage.clear(); resetHomeRecordedForTests(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const D: Diag = {
  at: "2026-10-07T01:00:00Z", uptime_s: 1200, platform: "win32", cpu_recent: 0.05, note: "이 PC에서 잰 값입니다.",
  targets: [
    { id: "p95", label: "화면 응답 (95%가 이 안에)", target: 0.3, value: 0.12, unit: "s", status: "OK" },
    { id: "over_limit", label: "15초 넘은 응답 (연결 끊김으로 보임)", target: 0, value: 2, unit: "회", status: "OVER" },
    { id: "idle_cpu", label: "가만히 둘 때 CPU (코어 1개 기준)", target: 0.02, value: null, unit: "share", status: "UNKNOWN" },
    { id: "memory", label: "메모리", target: 500, value: 812, unit: "MB", status: "OVER" },
  ],
  requests: { count: 340, p95: 0.12, max: 21.5, routes: [{ route: "GET /api/strategies", count: 30, p50: 0.2, p95: 9.1, max: 21.5, slow: 4, over_limit: 2, errors: 0 }] },
  jobs: [{ job: "strategies:scan", count: 3, failed: 0, avg: 14.2, max: 16.0, last: 0.1 }],
  slow: [{ kind: "화면 요청", name: "GET /api/strategies", seconds: 21.5, at: "2026-10-07T00:58:00Z" }],
};

it("shows each target with the value measured on this PC and whether it is met", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(D))));
  recordHomeShown(4200);
  render(<MemoryRouter><DiagnosticsCard /></MemoryRouter>);
  expect((await screen.findByTestId("diag-home")).textContent).toContain("4.20초");
  expect(screen.getByTestId("diag-home").textContent).toContain("목표 밖");
  expect(screen.getByTestId("diag-p95").textContent).toContain("목표 안");
  expect(screen.getByTestId("diag-over_limit").textContent).toContain("2회");
  expect(screen.getByTestId("diag-memory").textContent).toContain("812MB");
  expect(screen.getByTestId("diag-idle_cpu").textContent).toContain("측정 전");
  expect(screen.getByTestId("diag-slow").textContent).toContain("21.5초");
  expect(screen.getByTestId("diag-jobs").textContent).toContain("strategies:scan");
});

it("counts the screens opened by their first path segment, never a ticker", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(D))));
  const at = (p: string) => render(<MemoryRouter initialEntries={[p]}><Routes><Route path="*" element={<VisitCounter />} /></Routes></MemoryRouter>);
  at("/stocks/AAPL").unmount();
  at("/stocks/MSFT").unmount();
  at("/").unmount();
  expect(JSON.parse(localStorage.getItem("ml.diag.pages")!)).toEqual({ "/stocks": 2, "/": 1 });
  render(<MemoryRouter><DiagnosticsCard /></MemoryRouter>);
  expect((await screen.findByTestId("diag-pages")).textContent).toContain("종목 2");
  expect(localStorage.getItem("ml.diag.pages")).not.toContain("AAPL");
});

it("home time is recorded once per start", () => {
  recordHomeShown(1500);
  recordHomeShown(9000);
  expect(JSON.parse(localStorage.getItem("ml.diag.home")!)).toEqual([1.5]);
});
