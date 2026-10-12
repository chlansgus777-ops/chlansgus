// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Action, Quality, Stmt } from "./components/ui";
import { ViewModeProvider, plainKo } from "./viewMode";
import { stamp, stampEt } from "./format";

afterEach(cleanup);
beforeEach(() => { try { window.localStorage.clear(); } catch { /* none */ } });

describe("plainKo", () => {
  it("drops a trailing English code in parentheses and keeps Korean ones", () => {
    expect(plainKo("위험 선호(Risk On)")).toBe("위험 선호");
    expect(plainKo("매수(BUY)")).toBe("매수");
    expect(plainKo("데이터 부족(DATA INSUFFICIENT)")).toBe("데이터 부족");
    expect(plainKo("중립(뚜렷한 국면 없음)")).toBe("중립(뚜렷한 국면 없음)");
  });
});

describe("쉽게 보기 / 자세히 보기", () => {
  it("defaults to easy inside the app: Korean labels only, no English codes", () => {
    render(<ViewModeProvider><Action a="BUY" status="CURRENT" quality="FRESH" /><Quality q="STALE" /></ViewModeProvider>);
    expect(screen.getByText("매수")).toBeTruthy();
    expect(screen.queryByText("매수(BUY)")).toBeNull();
    expect(screen.getByText("오래됨")).toBeTruthy();
  });

  it("keeps an expired call struck through in easy mode too", () => {
    render(<ViewModeProvider><Action a="BUY" status="EXPIRED" quality="FRESH" /></ViewModeProvider>);
    expect(screen.getByTestId("action-expired").querySelector("s")?.textContent).toBe("매수");
  });

  it("a statement in easy mode is the sentence and why — no kind tag or source line", () => {
    const it_ = { kind: "CALC", text: "매출 성장률 33%", why: "업종 기준보다 높습니다", source: "재무", when: "2026-10-01" };
    render(<ViewModeProvider initial="easy"><Stmt it={it_} /></ViewModeProvider>);
    expect(screen.getByText("매출 성장률 33%")).toBeTruthy();
    expect(screen.getByText("업종 기준보다 높습니다")).toBeTruthy();
    expect(screen.queryByText("계산")).toBeNull();
    expect(screen.queryByText(/출처/)).toBeNull();
  });

  it("outside the provider everything stays shown (pages rendered on their own)", () => {
    render(<Action a="BUY" status="CURRENT" quality="FRESH" />);
    expect(screen.getByText("매수(BUY)")).toBeTruthy();
  });
});

describe("시각 표기", () => {
  const iso = "2026-09-25T15:00:00Z"; // 11:00 in New York, midnight in Seoul

  it("a basic screen (easy) shows Korea time only; screens under 고급 기능 keep both clocks", () => {
    function Stamp() { return <span data-testid="t">{stamp(iso)} | {stampEt(iso)}</span>; }
    const { unmount } = render(<ViewModeProvider><Stamp /></ViewModeProvider>);
    expect(screen.getByTestId("t").textContent).toBe("2026-09-26 00:00 (한국 시간) | 09-26(토) 00:00 한국 시간");
    unmount();
    expect(stamp(iso)).toBe("2026-09-25 11:00 ET (2026-09-26 00:00 KST)");
  });
});
