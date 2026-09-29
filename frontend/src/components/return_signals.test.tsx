// @vitest-environment jsdom
/** The 수익 신호 card (PREREGISTRATION §13): every signal says what it found or why it was not computed, the grade is the
 * average, and a weight-0 component is labelled as being validated — never presented as part of the score. */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ReturnSignalsCard, grade } from "./ReturnSignals";

afterEach(cleanup);
const comp = (weight: number, available = true) => ({ name: "return_signals", weight, subscore: 0.7057, available, missing: ["signal.rs_rank"],
  reasons: [{ text: "52주 최고 종가의 94% — 고점 대비 조정 중", sign: 0, refs: ["signal.high52"] }, { text: "F-스코어 7/9 — 재무가 좋아지는 중", sign: 1, refs: ["signal.fscore"] },
            { text: "실적 발표 반응 −5.1%(시장 대비)", sign: -1, refs: ["signal.ear"] }] });

describe("return signals", () => {
  it("shows each signal, a reason for the missing one, and says it is not scored yet", () => {
    render(<ReturnSignalsCard c={comp(0)} />);
    const t = screen.getByTestId("return-signals").textContent!;
    expect(t).toContain("F-스코어 7/9");
    expect(t).toContain("비교할 스캔 종목 분포 없음 — 계산 안 함");
    expect(t).toContain("검증 중 · 점수 반영 0점");
    expect(t).toContain("B");
    expect(document.querySelector(".sig.up .n")!.textContent).toBe("F-스코어(재무 건전성)");
    expect(document.querySelector(".sig.down .n")!.textContent).toBe("실적 발표 후 추세");
  });
  it("adopted: no 'validating' label; unavailable: no grade", () => {
    render(<ReturnSignalsCard c={comp(10)} />);
    expect(screen.getByTestId("return-signals").textContent).not.toContain("검증 중");
    cleanup();
    render(<ReturnSignalsCard c={comp(0, false)} />);
    expect(screen.getByTestId("return-signals").textContent).toContain("계산 불가");
    expect([grade(0.85), grade(0.7), grade(0.5), grade(0.35), grade(0.1), grade(null)]).toEqual(["A", "B", "C", "D", "F", null]);
  });
});
