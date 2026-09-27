// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CoverageCard } from "./Dashboard";
import { SetupCard } from "./Settings";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const coverage = {
  universe: 5200, excluded: 3900, deep_analysed: 1300, analysed: 40, data_insufficient: 6, data_insufficient_rate: 0.15,
  missing_by_field: { analyst: { count: 20, rate: 0.5 } }, excluded_by_reason: { "시가총액 기준 미달": 2500, "주가 기준 미달": 400 },
  llm: { calls: 70, estimated_cost_usd: 1.234, cost_complete: true },
};

describe("coverage card", () => {
  it("shows how much of the market was judged, the missing-data rate and the AI cost", () => {
    render(<MemoryRouter><CoverageCard s={{ state: { scan_id: 1, status: "COMPLETE", started_at: "", saved: 40, total: 40 }, coverage }} /></MemoryRouter>);
    expect(screen.getByText("5,200개")).toBeTruthy();
    expect(screen.getByText(/6개 \(15.0%\)/)).toBeTruthy();
    expect(screen.getByText(/애널리스트 추정치 없음/)).toBeTruthy();
    expect(screen.getByText(/70회 · 약 \$1.23/)).toBeTruthy();
    expect(screen.getByText(/시가총액 기준 미달 2500/)).toBeTruthy();
  });
  it("says so when the last scan was interrupted, and that its saved results are kept", () => {
    render(<MemoryRouter><CoverageCard s={{ state: { scan_id: 2, status: "INTERRUPTED", started_at: "", saved: 12, total: 40 }, coverage: null }} /></MemoryRouter>);
    expect(screen.getByText(/중간에 멈췄습니다\(12\/40개 저장\)/)).toBeTruthy();
  });
});

describe("first-run setup", () => {
  it("sends only the filled fields and never shows a stored key", async () => {
    const calls: { url: string; body: string }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, body: String(init.body) });
      return new Response(JSON.stringify({ note: "저장했습니다. 앱을 다시 시작하면 적용됩니다." }), { status: 200 });
    }));
    render(<SetupCard configured={{ FINNHUB_API_KEY: true, SEC_USER_AGENT: false }} mode="MOCK" />);
    expect((screen.getByLabelText(/Finnhub API 키/) as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText(/Finnhub API 키/) as HTMLInputElement).type).toBe("password");
    fireEvent.change(screen.getByLabelText(/SEC 요청자/), { target: { value: "Hong Gildong hong@example.com" } });
    fireEvent.change(screen.getByLabelText("데이터 모드"), { target: { value: "LIVE" } });
    fireEvent.click(screen.getByText("저장"));
    await waitFor(() => expect(screen.getByRole("status").textContent).toMatch(/다시 시작/));
    expect(calls[0]!.url).toMatch(/\/api\/settings\/setup$/);
    expect(JSON.parse(calls[0]!.body)).toEqual({ values: { SEC_USER_AGENT: "Hong Gildong hong@example.com", MARKETLENS_MODE: "LIVE" } });
  });
});
