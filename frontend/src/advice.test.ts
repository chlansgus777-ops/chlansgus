import { describe, expect, it } from "vitest";
import { advise, priceZone } from "./advice";
import { GLOSSARY, tip } from "./glossary";
import { ACTION_INFO } from "./i18n";

describe("action sentences (숫자 → 의미 → 행동)", () => {
  it("tells a beginner to wait for a pullback instead of chasing", () => {
    const a = advise({ action: "WAIT", price: 230, maxBuy: 220, idealEntry: 212, stop: 200, rr: 1.4 });
    expect(a.headline).toContain("추격 매수보다");
    expect(a.headline).toContain("$212.00");
  });
  it("explains BUY SMALL because of an upcoming event", () => {
    expect(advise({ action: "BUY SMALL", price: 100, maxBuy: 105, eventRisk: "HIGH" }).headline).toContain("이벤트가 가까워 비중을 작게");
  });
  it("explains BUY SMALL because of portfolio concentration", () => {
    expect(advise({ action: "BUY SMALL", price: 100, maxBuy: 105, sizeLimit: "SMALL" }).headline).toContain("포트폴리오 쏠림");
  });
  it("never encourages acting on an expired buy signal", () => {
    expect(advise({ action: "BUY", price: 100, maxBuy: 105, status: "EXPIRED" }).headline).toContain("유효하지 않습니다");
  });
  it("is honest about missing data", () => {
    expect(advise({ action: "DATA INSUFFICIENT", price: null }).headline).toContain("판단을 강하게 내리지 않습니다");
  });
  it("has a sentence for every action", () => {
    for (const k of Object.keys(ACTION_INFO)) expect(advise({ action: k, price: 100, maxBuy: 105, stop: 95 }).headline).not.toBe("판단 정보가 없습니다.");
  });
  it("interprets the price against the plan", () => {
    expect(priceZone({ action: "BUY", price: 100, maxBuy: 105, stop: 95 })).toEqual({ text: "적정 매수 구간 안", tone: "pos" });
    expect(priceZone({ action: "WAIT", price: 110, maxBuy: 105, stop: 95 }).tone).toBe("warn");
    expect(priceZone({ action: "WAIT", price: 94, maxBuy: 105, stop: 95 }).tone).toBe("neg");
  });
});

describe("glossary", () => {
  it("explains the jargon the audit named, with direction", () => {
    for (const k of ["forward_pe", "peg", "drawdown", "iv_rank", "rotce", "affo", "rr", "confidence"]) {
      expect(GLOSSARY[k]?.short.length).toBeGreaterThan(10);
      expect(tip(k)).toContain(GLOSSARY[k]!.short);
    }
    expect(tip("forward_pe")).toContain("낮을수록 유리");
  });
});
