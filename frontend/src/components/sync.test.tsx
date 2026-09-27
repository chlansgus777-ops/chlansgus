// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NotReady, type ReadinessInfo } from "./Readiness";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const r: ReadinessInfo = {
  mode: "LIVE", recommendation_readiness: "NOT READY", readiness_reasons: [], scanner_status: "SCANNER_NOT_READY",
  scanner_reasons: ["유니버스(종목 목록)가 아직 적재되지 않음"], progress: { price_history: 0 }, sync: { status: "NEVER_SYNCED" }, categories: [],
};

describe("data preparation from the screen", () => {
  it("LIVE: the not-ready card has a button that starts the background sync and shows its progress", async () => {
    let job: unknown = null;
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url.endsWith("/sync/start")) {
        job = { status: "RUNNING", round: 1, bar_days_remaining: 176, fundamentals_pending: 900 };
        return new Response(JSON.stringify({ started: true, job }), { status: 200 });
      }
      return new Response(JSON.stringify({ job }), { status: 200 });
    }));
    render(<MemoryRouter><NotReady r={r} /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: "데이터 준비 시작" }));
    await waitFor(() => expect(screen.getByText(/받는 중 · 1회차 · 남은 가격 거래일 176 · 재무 대기 900종목/)).toBeTruthy());
    expect(calls.some((c) => c.startsWith("POST") && c.endsWith("/sync/start"))).toBe(true);
    expect((screen.getByRole("button", { name: "데이터 받는 중…" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("MOCK: no preparation button", () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
    render(<MemoryRouter><NotReady r={{ ...r, mode: "MOCK" }} /></MemoryRouter>);
    expect(screen.queryByRole("button", { name: "데이터 준비 시작" })).toBeNull();
  });
});

describe("a preparation that needs a key first", () => {
  it("says what to set instead of 'done', with a way to the settings", async () => {
    const job = { status: "NEEDS_SETUP", round: 1, bar_days_remaining: 0, errors: [], missing: ["POLYGON_API_KEY가 없어 일봉(가격 이력)을 받을 수 없음 — 설정 화면에서 입력하고 앱을 다시 시작하세요"] };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ job }), { status: 200 })));
    render(<MemoryRouter><NotReady r={r} /></MemoryRouter>);
    expect(await screen.findByText(/설정 필요/)).toBeTruthy();
    expect(screen.queryByText(/상태: 끝남/)).toBeNull();
    expect(screen.getAllByText(/POLYGON_API_KEY가 없어/).length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: /설정 화면/ }).getAttribute("href")).toContain("settings");
  });
});
