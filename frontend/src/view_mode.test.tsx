// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Action, Quality, Stmt } from "./components/ui";
import { DetailOnly, ViewModeProvider, ViewModeSwitch, plainKo, useViewMode } from "./viewMode";
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

  it("switches to detail, shows the codes and remembers the choice", () => {
    const { unmount } = render(<ViewModeProvider><ViewModeSwitch /><Action a="WAIT" /><DetailOnly><span>근거 원본</span></DetailOnly></ViewModeProvider>);
    expect(screen.queryByText("근거 원본")).toBeNull();
    fireEvent.click(screen.getByText("자세히 보기"));
    expect(screen.getByText("대기(WAIT)")).toBeTruthy();
    expect(screen.getByText("근거 원본")).toBeTruthy();
    unmount();
    render(<ViewModeProvider><Action a="WAIT" /></ViewModeProvider>);
    expect(screen.getByText("대기(WAIT)")).toBeTruthy();
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

  it("easy view shows Korea time only; detail and pages outside the app keep both clocks", () => {
    // like the app shell, a component reading the mode re-renders on a switch
    function Stamp() { useViewMode(); return <span data-testid="t">{stamp(iso)} | {stampEt(iso)}</span>; }
    const { unmount } = render(<ViewModeProvider><ViewModeSwitch /><Stamp /></ViewModeProvider>);
    expect(screen.getByTestId("t").textContent).toBe("2026-09-26 00:00 (한국 시간) | 09-26(토) 00:00 한국 시간");
    fireEvent.click(screen.getByText("자세히 보기"));
    expect(screen.getByTestId("t").textContent).toBe("2026-09-25 11:00 ET (2026-09-26 00:00 KST) | 09-25(금) 11:00 ET");
    fireEvent.click(screen.getByText("쉽게 보기"));
    unmount();
    expect(stamp(iso)).toBe("2026-09-25 11:00 ET (2026-09-26 00:00 KST)");
  });
});
