// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { NotReady, ReadinessBanner, statusKo, type ReadinessInfo } from "../components/Readiness";
import type { Analysis } from "../types";
import { confidenceLevel, missingData } from "./StockDetail";

afterEach(cleanup);

const notReady: ReadinessInfo = {
  mode: "LIVE", recommendation_readiness: "NOT READY", readiness_reasons: ["스캐너 데이터 준비가 끝나지 않아…"], scanner_status: "SCANNER_NOT_READY",
  scanner_reasons: ["저장된 거래일 20일 < 60일 — 스캐너 최소 요건(60거래일) 미달"], progress: { price_history: 0.43, market_cap: 0.94, fundamentals: 0.81 },
  sync: { status: "SYNC_PARTIAL" }, categories: [],
};

describe("readiness UI", () => {
  it("explains an empty list as 'data not ready', with progress, not as 'no opportunities'", () => {
    render(<MemoryRouter><NotReady r={notReady} /></MemoryRouter>);
    expect(screen.getByText(/‘살 종목이 없다’는 뜻이 아닙니다/)).toBeTruthy();
    expect(screen.getByText("43%")).toBeTruthy();
    expect(screen.getByText("94%")).toBeTruthy();
    expect(screen.getByText(/60거래일/)).toBeTruthy();
  });
  it("shows the gate in plain Korean", () => {
    // the data page shows every state; the working screens only NOT READY (owner 2026-09-29: "제한적 참고 — 거슬려")
    const { container } = render(<MemoryRouter><ReadinessBanner r={{ ...notReady, recommendation_readiness: "LIMITED" }} /></MemoryRouter>);
    expect(container.textContent).toBe("");
    cleanup();
    render(<MemoryRouter><ReadinessBanner r={{ ...notReady, recommendation_readiness: "LIMITED" }} always /></MemoryRouter>);
    expect(screen.getByText(/추천 준비도: 제한적 참고/)).toBeTruthy();
    expect(statusKo("PARTIAL (NOT_LIVE_VERIFIED)")).toBe("부분 · 실제 검증 전");
    expect(statusKo("ACCUMULATING")).toBe("누적 중");
  });
});

describe("the banner names the actual reason (owner report: 'not finished' while a key was missing)", () => {
  it("shows the first specific reason, a missing key before the progress", () => {
    const r = { ...notReady, readiness_reasons: ["스캐너 데이터 준비가 끝나지 않아 추천을 실전 판단에 쓰면 안 됨", "FINNHUB_API_KEY가 없어 현재가를 확인할 수 없음"] };
    render(<MemoryRouter><ReadinessBanner r={r} /></MemoryRouter>);
    expect(screen.getByText(/이유: FINNHUB_API_KEY가 없어/)).toBeTruthy();
  });
  it("falls back to the first data reason", () => {
    render(<MemoryRouter><ReadinessBanner r={notReady} /></MemoryRouter>);
    expect(screen.getByText(/이유: 저장된 거래일 20일/)).toBeTruthy();
  });
});

describe("stock page wording", () => {
  it("confidence is a level, not a probability", () => {
    expect(confidenceLevel(76)).toBe("높음");
    expect(confidenceLevel(55)).toBe("보통");
    expect(confidenceLevel(30)).toBe("낮음");
  });
  it("lists exactly what is missing when no decision is made", () => {
    const a = {
      fundamental_rules: { subscore: null, coverage: 0.4, critical_missing: ["rotce"], items: [{ metric: "rotce", label: "ROTCE", value: null, subscore: null, weight: 3 }, { metric: "cet1", label: "CET1 자본비율", value: null, subscore: null, weight: 2 }] },
      valuation_rules: { subscore: 0.5, coverage: 1, items: [] },
      data_quality: { checks: [{ data_type: "options", quality: "MISSING" }, { data_type: "price", quality: "FRESH" }] },
      decision: { vetoes: ["INSUFFICIENT_MODEL_COVERAGE"] },
    } as unknown as Analysis;
    const m = missingData(a);
    expect(m).toContain("ROTCE (핵심)");
    expect(m).toContain("CET1 자본비율");
    expect(m.some((x) => x.includes("없음"))).toBe(true);
    expect(m.some((x) => x.includes("가격"))).toBe(false);
  });
});
