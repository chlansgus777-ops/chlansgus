// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { OppRow } from "../types";
import Opportunities from "./Opportunities";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function row(over: Partial<OppRow> = {}): OppRow {
  return {
    id: 1, rank: 1, ticker: "NVDA", company: "NVIDIA", sector: "Technology", sector_model: "semiconductor", price: 180.5, session: "REGULAR",
    price_timestamp: "2026-09-25T15:00:00Z", price_source: "finnhub", price_quality: "DELAYED", score: 82.4, confidence: 71, action: "BUY",
    deterministic_action: "BUY", committee_status: "NOT_RUN", ideal_entry: 175, max_buy: 183, target: 205, stop: 168, downside: -0.069, rr: 2.1,
    catalyst: "NVDA 실적발표", catalyst_date: "2026-10-30", risk: "MEDIUM", data_quality: "FRESH", mode: "LIVE", vetoes: [], as_of: "2026-09-25T15:00:00Z",
    current_status: "CURRENT", current_status_reason: "추천 당시 데이터 최신; 이후 새로 마감된 거래일 없음", sessions_since: 0, actionable_now: true,
    action_ko: "매수", valuation_price_basis: "현재가", sector_known: true, ...over,
  };
}

function serve(sequence: (() => Response)[]) {
  let i = 0;
  vi.stubGlobal("fetch", () => {
    const f = sequence[Math.min(i, sequence.length - 1)]!;
    i += 1;
    return Promise.resolve(f());
  });
}
const ok = (body: unknown) => () => new Response(JSON.stringify(body), { status: 200 });
const fail = (status: number) => () => new Response(JSON.stringify({ detail: "down" }), { status });
const scan = { id: 7, as_of: "2026-09-25T15:00:00Z", mode: "LIVE", stages: [], excluded: 0, scoring_model_version: "scoring-2.0.0" };

const renderPage = () => render(<MemoryRouter><Opportunities /></MemoryRouter>);

describe("Opportunities page data states", () => {
  it("shows a loading state first", () => {
    vi.stubGlobal("fetch", () => new Promise(() => undefined));
    renderPage();
    expect(screen.getByRole("status").textContent).toContain("불러오는 중");
  });

  it("shows an error with retry, then recovers", async () => {
    serve([fail(500), ok({ scan, rows: [row()] })]); // 500 is not retried automatically; the user retries
    const { container } = renderPage();
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("서버 내부 오류"), { timeout: 4000 });
    fireEvent.click(screen.getByText("다시 시도"));
    await waitFor(() => expect(screen.getByText("NVDA")).toBeTruthy());
    expect(container.querySelector("td .a-BUY")?.textContent).toBe("매수(BUY)");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows an empty state", async () => {
    serve([ok({ scan, rows: [] })]);
    renderPage();
    await waitFor(() => expect(screen.getByTestId("empty").textContent).toContain("후보가 없습니다"));
  });

  it("marks stale (expired) recommendations so they never look actionable", async () => {
    serve([ok({ scan, rows: [row({ id: 2, ticker: "OLD", current_status: "EXPIRED", sessions_since: 5, actionable_now: false, current_status_reason: "추천 당시 데이터 최신; 이후 5거래일 경과" })] })]);
    const { container } = renderPage();
    await waitFor(() => expect(screen.getByTestId("action-expired")).toBeTruthy());
    expect(screen.getByText("만료")).toBeTruthy();
    expect(container.querySelector("tr.row-expired")).not.toBeNull();
  });

  it("shows partial / delayed data quality explicitly", async () => {
    serve([ok({ scan, rows: [row({ data_quality: "PARTIAL", price_quality: "DELAYED" })] })]);
    renderPage();
    await waitFor(() => expect(screen.getByText("일부 누락(PARTIAL)")).toBeTruthy());
    expect(screen.getByText("지연(DELAYED)")).toBeTruthy();
  });

  it("filters to currently valid recommendations only", async () => {
    serve([ok({ scan, rows: [row(), row({ id: 3, ticker: "OLD", current_status: "EXPIRED", actionable_now: false })] })]);
    renderPage();
    await waitFor(() => expect(screen.getByText("OLD")).toBeTruthy());
    fireEvent.click(screen.getByLabelText("현재 유효한 추천만"));
    expect(screen.queryByText("OLD")).toBeNull();
    expect(screen.getByText("NVDA")).toBeTruthy();
  });
});
