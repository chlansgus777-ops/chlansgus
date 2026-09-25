// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Action, Err, FreshnessTable, ModeBanner, Quality } from "./ui";

afterEach(cleanup);

describe("Action badge", () => {
  it("shows a current BUY normally with a Korean label and explanation", () => {
    render(<Action a="BUY" status="CURRENT" quality="FRESH" />);
    const b = screen.getByText("매수(BUY)");
    expect(b.className).toContain("a-BUY");
    expect(b.getAttribute("title")).toContain("최대 매수가");
  });
  it("never shows an expired BUY as a normal BUY", () => {
    render(<Action a="BUY" status="EXPIRED" quality="FRESH" />);
    const b = screen.getByTestId("action-expired");
    expect(b.className).toContain("a-EXPIRED");
    expect(b.textContent).toContain("만료");
    expect(b.querySelector("s")?.textContent).toBe("매수(BUY)");
  });
  it("treats a BUY made on stale data as not actionable", () => {
    render(<Action a="BUY SMALL" status="CURRENT" quality="STALE" />);
    expect(screen.getByTestId("action-expired")).toBeTruthy();
  });
  it("does not mark non-bullish actions as expired", () => {
    render(<Action a="WAIT" status="EXPIRED" quality="FRESH" />);
    expect(screen.queryByTestId("action-expired")).toBeNull();
    expect(screen.getByText("대기(WAIT)")).toBeTruthy();
  });
});

describe("data state components", () => {
  it("labels data quality in Korean and keeps the original term", () => {
    render(<><Quality q="STALE" /><Quality q="PARTIAL" /><Quality q={null} /></>);
    expect(screen.getByText("오래됨(STALE)")).toBeTruthy();
    expect(screen.getByText("일부 누락(PARTIAL)")).toBeTruthy();
    expect(screen.getByText("없음(MISSING)")).toBeTruthy();
  });
  it("distinguishes MOCK and LIVE clearly", () => {
    const { rerender } = render(<ModeBanner mode="MOCK" />);
    expect(screen.getByTestId("banner-mock").textContent).toContain("실제 시장 데이터가 아니며");
    rerender(<ModeBanner mode="LIVE" />);
    expect(screen.getByTestId("banner-live").textContent).toContain("대체하지 않습니다");
  });
  it("offers a retry on errors", () => {
    const retry = vi.fn();
    render(<Err error="백엔드에 연결할 수 없습니다" retry={retry} />);
    fireEvent.click(screen.getByText("다시 시도"));
    expect(retry).toHaveBeenCalledOnce();
    expect(screen.getByRole("alert").textContent).toContain("백엔드");
  });
  it("renders per-type freshness with Korean reasons", () => {
    render(<FreshnessTable checks={[
      { data_type: "fundamentals", quality: "STALE", effective: "2023-06-30", published: "2023-08-05", age: 1183, unit: "days", fresh_max: 190, usable_max: 280, reason_ko: "재무제표(분기): 최신 공시 분기의 결산일 기준 1183일 경과 (오래됨)" },
      { data_type: "price", quality: "DELAYED", effective: "2026-09-25T15:00:00Z", published: null, age: 1, unit: "minutes", fresh_max: 2, usable_max: 20, reason_ko: "현재가: 지연 시세" },
    ]} />);
    expect(screen.getByText("재무제표(분기)")).toBeTruthy();
    expect(screen.getByText(/1183일 경과/)).toBeTruthy();
    expect(screen.getByText(/2026-09-25 11:00 ET/)).toBeTruthy();
  });
});

describe("ladderMarks", () => {
  it("merges equal levels into one label instead of drawing them on top of each other", async () => {
    const { ladderMarks } = await import("./ui");
    const m = ladderMarks([["손절", 90, "neg"], ["이상적", 100, ""], ["최대 매수", 100, "warn"], ["1차 목표", 120, "pos"], ["2차 목표", 120.5, "pos"]], 89, 122);
    expect(m.map((x) => x.label)).toEqual(["손절", "이상적·최대 매수", "1차 목표·2차 목표"]);
  });
  it("puts close-but-different neighbours on alternating rows", async () => {
    const { ladderMarks } = await import("./ui");
    const m = ladderMarks([["손절", 90, "neg"], ["이상적", 93, ""], ["최대 매수", 96, "warn"], ["1차 목표", 120, "pos"]], 89, 122);
    expect(m.map((x) => x.row)).toEqual([0, 1, 0, 0]);
  });
});
