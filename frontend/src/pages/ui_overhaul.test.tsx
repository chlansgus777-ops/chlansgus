// @vitest-environment jsdom
/** MarketHUD-style overhaul (2026-09-28): the first screen, the stock page order, the named states and the status
 * bar — checked against the owner's brief. Every value on screen must come from the (mocked) backend response. */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import stockFixture from "../__fixtures__/stock_mock.json";
import { StatusBar, StatusProvider, usePageTime } from "../components/status";
import { StaleData, StatePanel, Term, type StateKind } from "../components/ui";
import type { CommitteeResult, OppRow, StockDetail as SD } from "../types";
import Dashboard, { riskLine, scopeLine, splitCandidates } from "./Dashboard";
import { acceptRun } from "./Committee";
import Performance from "./Performance";
import Portfolio from "./Portfolio";
import StockDetailPage from "./StockDetail";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

function row(over: Partial<OppRow> = {}): OppRow {
  return {
    id: 1, rank: 1, ticker: "AAA", company: "Alpha Inc", sector: "Technology", sector_model: "software", price: 100, session: "CLOSED",
    price_timestamp: "2026-09-25T20:00:00Z", price_source: "polygon", price_quality: "FRESH", score: 84, confidence: 70, action: "BUY",
    deterministic_action: "BUY", committee_status: "COMPLETED", ideal_entry: 98, max_buy: 101, target: 115, stop: 94, downside: -0.06, rr: 2.5,
    catalyst: null, catalyst_date: null, risk: "LOW", data_quality: "FRESH", mode: "LIVE", vetoes: [], as_of: "2026-09-26T01:00:00Z",
    current_status: "CURRENT", current_status_reason: "최신", sessions_since: 0, actionable_now: true, action_ko: "매수", valuation_price_basis: "현재가",
    sector_known: true, key_reason: "매출 성장률 30% (업종 기준 우수)", key_risk: { kind: "negative", code: null, text: "애널리스트 커버리지 적음" }, ...over,
  };
}

type Route_ = [RegExp, unknown | ((url: string, init?: RequestInit) => unknown)];
/** fetch mock that answers by URL; records every call. A route may return a Promise to delay the answer. */
function serve(routes: Route_[]) {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${String(url).split("/api")[1]}`);
    for (const [rx, body] of routes) {
      if (rx.test(String(url))) {
        const b = typeof body === "function" ? await (body as (u: string, i?: RequestInit) => unknown)(String(url), init) : body;
        if (b instanceof Response) return b;
        return new Response(JSON.stringify(b), { status: 200 });
      }
    }
    return new Response(JSON.stringify({ detail: "not mocked" }), { status: 404 });
  }));
  return calls;
}

const system = (mode: "MOCK" | "LIVE", session = "REGULAR") => ({
  mode, mock_banner: mode === "MOCK", now: "2026-09-28T15:00:00Z", versions: {}, market: { session, ny_time: "2026-09-28T11:00:00-04:00", last_completed_session: "2026-09-25" },
  llm: { provider: "none", available: false, fast_model: "", deep_model: "" }, providers: [],
});
const scanInfo = { id: 3, as_of: "2026-09-26T01:00:00Z", mode: "LIVE", stages: [], excluded: 0, scoring_model_version: "scoring-2" };
const dash = (rows: OppRow[], over: Record<string, unknown> = {}) => ({
  scan: scanInfo, regime: { primary: "Risk On", readings: [] }, top_opportunities: rows, major_risks: [], upcoming_catalysts: [{ event_id: "e1", title: "FOMC 결정", event_date: "2026-09-30", days_until: 2, importance: 0.9 }],
  portfolio: { holdings: 0, cash: 0 }, provider_health: [], performance: null, recommendation_changes: [], watchlist_alerts: [], ...over,
});

describe("first screen: candidates", () => {
  it("cards only for bullish, current, usable recommendations — at most five, never padded", () => {
    const rows = [
      row({ id: 1, ticker: "A1" }), row({ id: 2, ticker: "A2" }),
      row({ id: 3, ticker: "OLD", current_status: "EXPIRED", actionable_now: false }),
      row({ id: 4, ticker: "INTRA", current_status: "NEEDS_REVALIDATION", actionable_now: false }),
      row({ id: 5, ticker: "OUT", current_status: "PLAN_INVALIDATED", actionable_now: false }),
      row({ id: 6, ticker: "STALEPX", price_quality: "STALE" }),
      row({ id: 7, ticker: "WAITER", action: "WAIT" }),
    ];
    const { valid, notValid } = splitCandidates(rows);
    expect(valid.map((r) => r.ticker)).toEqual(["A1", "A2"]);
    expect(notValid.map((r) => r.ticker)).toEqual(["OLD", "INTRA", "OUT", "STALEPX"]);
  });

  it("shows two cards, says why there are only two, and lists the invalid BUYs apart from the cards", async () => {
    serve([[/\/dashboard/, dash([row({ id: 1, ticker: "A1" }), row({ id: 2, ticker: "A2" }), row({ id: 3, ticker: "OLD", current_status: "EXPIRED", actionable_now: false }), row({ id: 4, ticker: "WAITER", action: "WAIT" })])], [/\/scan\/status/, { state: null, coverage: null }]]);
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    await waitFor(() => expect(screen.getAllByTestId("candidate-card")).toHaveLength(2));
    expect(screen.getByTestId("few-candidates").textContent).toContain("채우지 않았습니다");
    const cards = screen.getAllByTestId("candidate-card");
    expect(cards.some((c) => c.textContent?.includes("OLD") || c.textContent?.includes("WAITER"))).toBe(false);
    const nv = screen.getByTestId("not-valid");
    expect(within(nv).getByText("OLD")).toBeTruthy();
    expect(within(nv).getByTestId("action-expired").textContent).toContain("만료");
    // the card's reason and risk are the backend's sentences; score/confidence are said not to be probabilities
    expect(cards[0]!.textContent).toContain("매출 성장률 30% (업종 기준 우수)");
    expect(cards[0]!.textContent).toContain("애널리스트 커버리지 적음");
    expect(cards[0]!.textContent).toContain("상승 확률이 아닙니다");
    expect(cards[0]!.textContent).toContain("$98.00 ~ $101.00");
  });

  it("no bullish candidate: a no-candidates state, not the top scores dressed up as buys", async () => {
    serve([[/\/dashboard/, dash([row({ id: 7, ticker: "WAITER", action: "WAIT" })])], [/\/scan\/status/, { state: null, coverage: null }]]);
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    const st = await screen.findByTestId("state-no_candidates");
    expect(st.textContent).toContain("할 수 있는 일");
    expect(screen.queryAllByTestId("candidate-card")).toHaveLength(0);
  });

  it("never scanned: says so and offers the scan", async () => {
    serve([[/\/dashboard/, dash([], { scan: null })], [/\/scan\/status/, { state: null, coverage: null }]]);
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    expect((await screen.findByTestId("state-not_scanned")).textContent).toContain("아직 시장 스캔을 하지 않았습니다");
  });

  it("LIVE provider failure is shown, never covered by other data", async () => {
    serve([[/\/dashboard/, dash([row()], { provider_health: [{ name: "polygon", kind: "price_history", status: "DOWN" }] })], [/\/scan\/status/, { state: null, coverage: null }], [/\/system/, system("LIVE")], [/./, {}]]);
    render(<MemoryRouter><StatusProvider><Dashboard /></StatusProvider></MemoryRouter>);
    const r = await screen.findByTestId("provider-failure");
    expect(r.textContent).toContain("price_history");
    expect(r.textContent).toContain("모의 데이터로 대신 채우지 않습니다");
  });

  it("the scan scope is the real list size, never 'the whole market'", () => {
    const s = { state: null, coverage: { universe: 594, excluded: 206, deep_analysed: 60, analysed: 40, data_insufficient: 0, data_insufficient_rate: 0, missing_by_field: {}, excluded_by_reason: {}, llm: { calls: 0, estimated_cost_usd: 0, cost_complete: true } } };
    expect(scopeLine(s, "MOCK")).toBe("이번 스캔 범위: 모의 종목 목록 594개 → 기준 통과 388개 → 정밀 분석 60개 → 최종 40개");
    expect(scopeLine(s, "LIVE")).not.toContain("전체");
  });

  it("risk line: veto, then event, then the stored negative reason", () => {
    expect(riskLine(row({ vetoes: ["STALE_PRICE"] }))).toContain("현재가 오래됨");
    expect(riskLine(row({ key_risk: { kind: "event", code: "HIGH", text: "실적 발표 1일 전" } }))).toBe("이벤트 위험 높음: 실적 발표 1일 전");
    expect(riskLine(row({ key_risk: null }))).toBe("분석이 표시한 부정 요인 없음");
    expect(riskLine(row({ key_risk: undefined }))).toContain("자료 부족");
  });
});

function StatusHarness({ page }: { page?: Parameters<typeof usePageTime>[0] }) {
  usePageTime(page ?? null);
  return null;
}

describe("status bar", () => {
  const base = (mode: "MOCK" | "LIVE", extra: Route_[] = []) => serve([...extra, [/\/system/, system(mode, "PREMARKET")], [/\/readiness/, { mode, recommendation_readiness: mode === "MOCK" ? "PAPER ONLY" : "FULL", readiness_reasons: [], scanner_status: "SCANNER_READY", scanner_reasons: [], progress: {}, sync: {}, categories: [] }],
    [/\/scan\/status/, { state: { scan_id: 3, status: "COMPLETE", started_at: "2026-09-28T14:00:00Z", saved: 40, total: 40 }, coverage: null }], [/\/sync\/status/, { job: null }], [/\/health/, { providers: [], llm: { provider: "none", available: false, status: "DOWN" } }]]);

  it("tells MOCK from LIVE and shows the backend's session with New York and Korea time", async () => {
    base("MOCK");
    const { unmount } = render(<MemoryRouter><StatusProvider><StatusBar /></StatusProvider></MemoryRouter>);
    expect((await screen.findByTestId("sb-mode")).textContent).toContain("MOCK");
    const ses = screen.getByTestId("sb-session").textContent ?? "";
    expect(ses).toContain("프리마켓");
    expect(ses).toContain("뉴욕");
    expect(ses).toContain("한국");
    unmount();
    base("LIVE");
    render(<MemoryRouter><StatusProvider><StatusBar /></StatusProvider></MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId("sb-mode").textContent).toContain("LIVE"));
  });

  it("shows the price time of the page (not the time the screen fetched it) and how old it is", async () => {
    base("LIVE");
    render(<MemoryRouter><StatusProvider><StatusBar /><StatusHarness page={{ label: "NVDA", priceTs: "2026-09-25T20:00:00Z", priceSession: "CLOSED", quality: "STALE", analysedAt: "2026-09-26T01:00:00Z" }} /></StatusProvider></MemoryRouter>);
    const p = await screen.findByTestId("sb-price-time");
    await waitFor(() => expect(p.textContent).toContain("09-25(금) 16:00 ET"));
    expect(p.textContent).toContain("일 전");
    expect(p.textContent).toContain("오래됨");
    expect(p.className).toContain("tone-warn");
  });

  it("names providers that are down (LIVE)", async () => {
    base("LIVE", [[/\/health/, { providers: [{ name: "polygon", kind: "price_history", mode: "LIVE", status: "DOWN", last_error: "HTTP 429", last_failure: null }], llm: { provider: "none", available: false, status: "DOWN" } }]]);
    render(<MemoryRouter><StatusProvider><StatusBar /></StatusProvider></MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId("sb-providers").textContent).toContain("1곳 중단"));
  });

  it("says the server is unreachable instead of showing a normal state", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("network"); }));
    render(<MemoryRouter><StatusProvider><StatusBar /></StatusProvider></MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId("sb-disconnected").textContent).toContain("연결 끊김"), { timeout: 6000 });
  });
});

describe("named states and glossary", () => {
  it("every state says what happened, what cannot be known and what to do", () => {
    const kinds: StateKind[] = ["collecting", "analyzing", "not_scanned", "no_candidates", "insufficient", "stale", "out_of_range", "provider_failure", "ai_unavailable", "disconnected"];
    for (const k of kinds) {
      const { unmount } = render(<StatePanel kind={k} />);
      const el = screen.getByTestId(`state-${k}`);
      for (const label of ["무슨 상황", "알 수 없는 것", "할 수 있는 일"]) expect(el.textContent).toContain(label);
      unmount();
    }
  });

  it("a kept result after a failed refresh shows when it was received", () => {
    render(<StaleData error="백엔드에 연결할 수 없습니다" at={Date.parse("2026-09-28T01:00:00Z")} />);
    const r = screen.getByTestId("stale-data");
    expect(r.textContent).toContain("새로고침 실패");
    expect(r.textContent).toContain("2026-09-27 21:00 ET");
  });

  it("term explanations open by click / keyboard and close with Escape", () => {
    render(<p>손익비 <Term k="rr" /></p>);
    const btn = screen.getByRole("button", { name: /손익비/ });
    expect(btn.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(btn);
    expect(btn.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("note").textContent).toContain("목표 달성 확률을 의미하지 않습니다");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("note")).toBeNull();
  });
});

const stock = stockFixture as unknown as SD;
const clone = (): SD => JSON.parse(JSON.stringify(stock)) as SD;

function renderStock(ticker = stock.analysis.ticker) {
  let go: (p: string) => void = () => undefined;
  function Nav() { go = useNavigate(); return null; }
  const r = render(<MemoryRouter initialEntries={[`/stocks/${ticker}`]}><Nav /><Routes><Route path="/stocks/:ticker" element={<StockDetailPage />} /></Routes></MemoryRouter>);
  return { ...r, go: (p: string) => act(() => go(p)) };
}

describe("stock page", () => {
  it("reads in the brief's order: conclusion → reasons → price plan → risks → invalidation → news → portfolio → details", async () => {
    serve([[/\/stocks\//, clone()]]);
    const { container } = renderStock();
    await screen.findByText("가격 계획");
    const order = ["결론", "판단 이유", "가격 계획", "위험", "판단 철회 조건", "뉴스 · 이슈 · 일정", "내 포트폴리오와의 관계", "상세 자료"];
    const pos = order.map((t) => {
      const el = t === "결론" ? container.querySelector("section.hero") : screen.getByRole("heading", { name: t, level: 2 });
      expect(el, t).not.toBeNull();
      return Array.from(container.querySelectorAll("*")).indexOf(el!);
    });
    expect([...pos].sort((a, b) => a - b)).toEqual(pos);
    // the price stop and the thesis conditions are separate
    expect(screen.getByText("가격 기준 — 손절")).toBeTruthy();
    expect(screen.getByText(/투자 논리 기준 — 가격과 별개/)).toBeTruthy();
  });

  it("a missing price plan reads 자료 부족 — nothing is filled in", async () => {
    const d = clone();
    d.analysis.entry = null;
    serve([[/\/stocks\//, d]]);
    renderStock();
    const plan = await screen.findByTestId("price-plan");
    expect(within(plan).getAllByText("자료 부족").length).toBeGreaterThanOrEqual(6);
    expect(screen.getByTestId("tile-maxbuy").textContent).toContain("자료 부족");
  });

  it("an out-of-range BUY is not presented as a fresh buy", async () => {
    const d = clone();
    d.recommendation.current_status = "PLAN_INVALIDATED";
    d.recommendation.current_status_reason = "현재가 $260 > 최대 매수가";
    d.committee = null;
    serve([[/\/stocks\//, d]]);
    const { container } = renderStock();
    await screen.findByTestId("verdict");
    expect(screen.getByTestId("verdict").textContent).toBe("지금은 유효하지 않은 매수 신호");
    expect(container.querySelector("section.hero")?.className).toContain("rail-expired");
    expect(container.querySelector("section.hero")?.textContent).toContain("사지 마세요");
  });

  it("shows the AI summary only for this recommendation; another version's committee is dropped", async () => {
    const d = clone();
    serve([[/\/stocks\//, d]]);
    const first = renderStock();
    expect(await screen.findByTestId("committee-summary")).toBeTruthy();
    first.unmount();
    const other = clone();
    other.committee_recommendation_id = (other.recommendation.id ?? 0) + 99;
    serve([[/\/stocks\//, other]]);
    renderStock();
    await screen.findByText("가격 계획");
    expect(screen.queryByTestId("committee-summary")).toBeNull();
    expect(screen.getByText("아직 실행하지 않았습니다.")).toBeTruthy();
  });

  it("fast ticker switch: a late answer for the previous stock never appears", async () => {
    const a = clone();
    const b = clone();
    b.analysis.ticker = "BBBB"; b.recommendation.ticker = "BBBB"; b.analysis.security.company_name = "Beta Holdings";
    if (b.committee) b.committee.ticker = "BBBB";
    let releaseA: () => void = () => undefined;
    serve([[/\/stocks\/BBBB/, b], [/\/stocks\//, () => new Promise((res) => { releaseA = () => res(new Response(JSON.stringify(a), { status: 200 })); })]]);
    const { go, container } = renderStock(a.analysis.ticker);
    go("/stocks/BBBB");
    await screen.findByText("Beta Holdings");
    await act(async () => { releaseA(); });
    await new Promise((r) => setTimeout(r, 30));
    expect(container.textContent).not.toContain(a.analysis.security.company_name);
    expect(container.querySelector("h1")?.textContent?.startsWith("BBBB")).toBe(true);
  });

  it("opening and leaving pages never starts an analysis or an AI call", async () => {
    const calls = serve([[/\/stocks\//, clone()], [/\/dashboard/, dash([row()])], [/\/scan\/status/, { state: null, coverage: null }], [/\/system/, system("MOCK")], [/./, {}]]);
    let go: (p: string) => void = () => undefined;
    function Nav() { go = useNavigate(); return null; }
    render(<MemoryRouter initialEntries={["/"]}><StatusProvider><Nav /><Routes><Route path="/" element={<Dashboard />} /><Route path="/stocks/:ticker" element={<StockDetailPage />} /></Routes></StatusProvider></MemoryRouter>);
    await screen.findAllByTestId("candidate-card");
    for (const p of [`/stocks/${stock.analysis.ticker}`, "/", `/stocks/${stock.analysis.ticker}`]) {
      act(() => go(p));
      await waitFor(() => expect(document.body.textContent).toMatch(p === "/" ? /지금 검토할 후보/ : /가격 계획/));
    }
    expect(calls.filter((c) => c.startsWith("POST") || c.includes("refresh=true") || c.includes("/committee"))).toEqual([]);
  });
});

describe("committee page", () => {
  it("drops a run that returns after another stock was chosen", () => {
    const c = { ticker: "AAA" } as CommitteeResult;
    expect(acceptRun(c, "AAA", "AAA")).toBe(true);
    expect(acceptRun(c, "AAA", "BBB")).toBe(false);
    expect(acceptRun({ ticker: "BBB" } as CommitteeResult, "AAA", "AAA")).toBe(false);
  });
});

describe("portfolio and performance", () => {
  const pf = { valuation_day: "2026-09-25", cash: 1000, invested_value: 900, nav: 1900, unrealized_pnl: 100, sector_weights: { Technology: 0.47 }, theme_weights: {}, hhi: 0.4, beta: null, correlations: [],
    missing_prices: ["ZZZ"], notes: ["가격 데이터 없는 보유 종목(평가금액에서 제외): ZZZ"], currency: "USD", note: "",
    holdings: [{ ticker: "AAA", quantity: 9, cost_basis: 90, price: 100, price_day: "2026-09-25", market_value: 900, unrealized_pnl: 90, unrealized_pct: 0.11, weight: 0.47, sector: "Technology" },
      { ticker: "ZZZ", quantity: 5, cost_basis: 50, price: null, price_day: null, market_value: null, unrealized_pnl: null, unrealized_pct: null, weight: null, sector: "Energy" }] };

  it("a holding without a price is left out, never valued at its cost", async () => {
    serve([[/\/portfolio/, pf], [/\/transactions/, { securities: [], realized_pnl: 0, dividends: 0, note: "" }]]);
    render(<MemoryRouter><Portfolio /></MemoryRouter>);
    const r = await screen.findByTestId("missing-prices");
    expect(r.textContent).toContain("매입가로 대신 계산하지 않습니다");
    expect(screen.getByText("가격 없음 · 평가 제외")).toBeTruthy();
    expect(screen.queryByText("$250.00")).toBeNull(); // 5 × $50 cost would be a made-up value
  });

  const perf = { as_of: "2026-09-25", samples: 12, independent_samples: 8, mature_20d: 3, recommendation_hit_rate: {}, all_recommendations: {}, score_buckets: {}, confidence_buckets: [], factor_ic: [],
    rolling_ic_total_20d: [], paper: {}, paper_account: null, paper_equity_curve: [], paper_skipped: 2, paper_disclaimer: "시뮬레이션", sector_performance: {}, regime_performance: {} };

  it("MOCK performance is labelled as such, with samples, maturity and the benchmark", async () => {
    serve([[/\/performance/, perf], [/\/calibration/, { runs: [], models: [], production_weights: {}, production_version: "v1" }], [/\/system/, system("MOCK")], [/./, {}]]);
    render(<MemoryRouter><StatusProvider><Performance /></StatusProvider></MemoryRouter>);
    expect((await screen.findByTestId("perf-mock")).textContent).toContain("실전 성과가 아니며");
    const basis = screen.getByTestId("perf-basis").textContent ?? "";
    expect(basis).toContain("독립 표본 8개");
    expect(basis).toContain("3개");       // matured
    expect(basis).toContain("9개");       // 12 − 3 still waiting
    expect(basis).toContain("2개(가격 없음 등)");
    expect(basis).toContain("SPY");
    expect(screen.queryByTestId("perf-live")).toBeNull();
  });
});

describe("data preparation stays reachable (owner: '데이터 준비 시작은 어디갔어?')", () => {
  it("LIVE: the home screen always has the preparation button, even with candidates and a ready scanner", async () => {
    const ready = { mode: "LIVE", recommendation_readiness: "NOT READY", readiness_reasons: ["시가총액 확인 종목 70% < 80%"], scanner_status: "SCANNER_READY", scanner_reasons: [], progress: {}, sync: {}, categories: [] };
    serve([[/\/dashboard/, dash([row()], { readiness: ready })], [/\/scan\/status/, { state: null, coverage: null }], [/\/sync\/status/, { job: null }], [/\/system/, system("LIVE")], [/./, {}]]);
    render(<MemoryRouter><StatusProvider><Dashboard /></StatusProvider></MemoryRouter>);
    expect(await screen.findByRole("button", { name: "데이터 준비 시작" })).toBeTruthy();
    expect(screen.getByTestId("goto-data-prep")).toBeTruthy();
  });
  it("MOCK: no preparation button (nothing to download)", async () => {
    serve([[/\/dashboard/, dash([row()])], [/\/scan\/status/, { state: null, coverage: null }], [/\/system/, system("MOCK")], [/./, {}]]);
    render(<MemoryRouter><StatusProvider><Dashboard /></StatusProvider></MemoryRouter>);
    await screen.findAllByTestId("candidate-card");
    expect(screen.queryByRole("button", { name: "데이터 준비 시작" })).toBeNull();
  });
});
