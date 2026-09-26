// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { SegmentTable } from "./Performance";

afterEach(cleanup);

describe("segment calibration (evaluation 2, item 14)", () => {
  it("shows a segment proposal as review-only and never as an applied model", () => {
    render(<SegmentTable segments={{
      "sector:Tech": { status: "SEGMENT_PROPOSAL", label: "검토용", mature_samples: 80, min_samples: 60, applied: false },
      "regime:Risk Off": { status: "SEGMENT_INSUFFICIENT", label: "부족", mature_samples: 12, min_samples: 60, applied: false },
      "sector:Energy": "업종/국면 전용 모델",
    }} />);
    const box = screen.getByTestId("segments");
    expect(box.textContent).toContain("전용 가중치 제안(검토용)");
    expect(box.textContent).toContain("표본 부족 → 전체 모델");
    expect(box.textContent).toContain("80 / 60");
    expect(box.textContent).toContain("이전 형식 기록");
    expect(box.textContent).not.toContain("운영 적용예");
    expect(screen.getAllByText("아니오").length).toBe(2);
  });
});
