// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NotReady, SyncControl, type ReadinessInfo } from "./Readiness";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const r: ReadinessInfo = {
  mode: "LIVE", recommendation_readiness: "NOT READY", readiness_reasons: [], scanner_status: "SCANNER_NOT_READY",
  scanner_reasons: ["유니버스(종목 목록)가 아직 적재되지 않음"], progress: { price_history: 0 }, sync: { status: "NEVER_SYNCED" }, categories: [],
};

describe("sync start acknowledgement", () => {
  it("does not announce latest data until the job is complete", async () => {
    const job = { status: "RUNNING", round: 0, up_to_date: true, target_session: "2026-09-24" };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ job, target_session: "2026-09-24" }))));
    render(<SyncControl />);
    await screen.findByRole("button", { name: "데이터 받는 중…" });
    expect(screen.queryByText(/새로 받을 데이터 없이 이미 최신입니다/)).toBeNull();
  });
  const done = { status: "DONE", round: 1, finished_at: "2026-09-29T12:00:00Z" };
  it("shows a 200 refusal over the old completed job", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(
      url.endsWith("/sync/start") ? { started: false, reason: "전체 시장 스캔이 진행 중입니다", job: done } : { job: done }))));
    render(<SyncControl compact />);
    fireEvent.click(await screen.findByRole("button", { name: "새 거래일 받기" }));
    expect(await screen.findByText("전체 시장 스캔이 진행 중입니다")).toBeTruthy();
    expect(screen.getByRole("button", { name: "새 거래일 받기" })).toBeTruthy();
  });
  it("immediately disables duplicate clicks until acknowledgement", async () => {
    let finish!: (r: Response) => void;
    let posts = 0;
    vi.stubGlobal("fetch", vi.fn((url: string) => {
      if (url.endsWith("/sync/start")) { posts++; return new Promise<Response>((r) => { finish = r; }); }
      return Promise.resolve(new Response(JSON.stringify({ job: done })));
    }));
    render(<SyncControl compact />);
    const button = await screen.findByRole("button", { name: "새 거래일 받기" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(posts).toBe(1);
    finish(new Response(JSON.stringify({ started: false, reason: "이미 데이터를 준비하는 중", job: { status: "RUNNING", round: 1 } })));
    expect(await screen.findByText("이미 데이터를 준비하는 중")).toBeTruthy();
  });
  it("recovers a job after a lost acknowledgement without another POST", async () => {
    let started = false;
    let posts = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.endsWith("/sync/start")) { started = true; posts++; throw new TypeError("disconnected"); }
      return new Response(JSON.stringify({ job: started ? { status: "RUNNING", round: 1 } : done }));
    }));
    render(<SyncControl compact />);
    fireEvent.click(await screen.findByRole("button", { name: "새 거래일 받기" }));
    expect(await screen.findByText(/서버에서 시작된 작업을 찾았습니다/)).toBeTruthy();
    expect(posts).toBe(1);
    expect((screen.getByRole("button", { name: "데이터 받는 중…" }) as HTMLButtonElement).disabled).toBe(true);
  });
});

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

describe("a preparation with nothing left to fetch while the readiness is not ready", () => {
  it("says it is not ready yet, why, which failures it saw and when it retries — never 'done'", async () => {
    const job = { status: "INCOMPLETE", round: 1, bar_days_remaining: 0, errors: [], missing: [],
      reasons: ["대형주 분기 재무 수집 12% < 60% (수집 실패 800종목(재시도 대기))"],
      failures: ["재무 AAPL: unauthorized (403) — check API key / license / SEC User-Agent"], retry_at: "2026-09-27T18:00:00+00:00" };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ job }), { status: 200 })));
    render(<MemoryRouter><NotReady r={r} /></MemoryRouter>);
    expect(await screen.findByText(/아직 준비 안 됨/)).toBeTruthy();
    expect(screen.queryByText(/상태: 끝남/)).toBeNull();
    expect(screen.getByText(/대형주 분기 재무 수집 12%/)).toBeTruthy();
    expect(screen.getByText(/AAPL: unauthorized \(403\)/)).toBeTruthy();
    expect(screen.getByText(/다시 시도할 수 있는 시각/)).toBeTruthy();
  });
});

describe("progress while the data is prepared (owner request)", () => {
  it("shows the overall percentage, each step's counts and the time left", async () => {
    const job = { status: "RUNNING", round: 2, started_at: "2026-09-27T12:00:00+00:00",
      progress: { percent: 37.4, eta_seconds: 2460, current: "bars",
        steps: { bars: { done: 76, total: 206, percent: 36, detail: "2026-05-14" }, universe: { done: 1, total: 1, percent: 100, detail: "" },
                 fundamentals: { done: 35, total: 812, percent: 4, detail: "MSFT" } } } };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ job }), { status: 200 })));
    render(<MemoryRouter><NotReady r={r} /></MemoryRouter>);
    expect(await screen.findByText(/데이터 준비 37\.4%/)).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: "데이터 준비 전체" }).getAttribute("aria-valuenow")).toBe("37");
    expect(screen.getByText(/가격 이력: 76\/206 거래일 \(36%\)/)).toBeTruthy();
    expect(screen.getByText(/2026-05-14 받는 중/)).toBeTruthy();
    expect(screen.getByText(/재무: 35\/812 종목 \(4%\)/)).toBeTruthy();
    expect(screen.getByText(/남은 시간 약 41분/)).toBeTruthy();
  });
});
