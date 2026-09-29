// @vitest-environment jsdom
// Screens seen in real-usage scenarios (2026-09-29: LIVE code path, pre-market / weekend / provider outage / fresh install)
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { errCauseKo, errKo } from "../format";
import { StatusBadge } from "./ui";

afterEach(cleanup);

describe("provider failures read as words, not exception text", () => {
  it("names the data, the provider and what to do", () => {
    const raw = "ProviderUnavailableForView: all calendar providers failed: [('finnhub', 'ProviderUnavailable: http error: ConnectError')]";
    const t = errKo(raw);
    expect(t).toContain("일정 공급자(finnhub)");
    expect(t).toContain("네트워크");
    expect(t).not.toMatch(/Provider|ConnectError|\[\(/);
  });
  it("tells a rejected key and a rate limit apart", () => {
    expect(errCauseKo("ProviderUnavailable: http error 401")).toContain("API 키");
    expect(errCauseKo("ProviderUnavailable: http error 429 Too Many Requests")).toContain("요청 한도");
  });
  it("leaves a sentence that is already Korean unchanged", () => {
    expect(errKo("거시 데이터 없음")).toBe("거시 데이터 없음");
  });
});

describe("an undecided call has no plan that could expire", () => {
  it("shows 판단 보류, never 만료, right after an analysis that held the call", () => {
    render(<StatusBadge s="EXPIRED" action="DATA INSUFFICIENT" />);
    expect(screen.getByText("판단 보류")).toBeTruthy();
    expect(screen.queryByText("만료")).toBeNull();
  });
  it("keeps the real status for a call that was made", () => {
    render(<StatusBadge s="EXPIRED" action="BUY" />);
    expect(screen.getByText("만료")).toBeTruthy();
  });
});
