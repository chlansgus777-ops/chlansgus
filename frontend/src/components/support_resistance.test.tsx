// @vitest-environment jsdom
/** 지지선·저항선 on the stock page (owner 2026-10-05): the analysis' levels split by the price now, nearest first. */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { SupportResistance, srLevels } from "./SupportResistance";

afterEach(cleanup);

it("splits the levels by the current price, nearest three each side, on today's share basis", () => {
  const t = { supports: [1001, 980, 950, 900], resistances: [1108.8, 1254.9, 1300] };
  // the price fell below an old support: that level is now overhead
  expect(srLevels(t, 990)).toEqual({ supports: [980, 950, 900], resistances: [1001, 1108.8, 1254.9] });
  expect(srLevels({ supports: [200], resistances: [300] }, 25, 10)).toEqual({ supports: [20], resistances: [30] });
});

it("shows them with the distance from the price and says when there is nothing above", () => {
  render(<SupportResistance technicals={{ supports: [1001.7, 950], resistances: [1108.8], high_52w: 1120, low_52w: 600 }} price={1059.82} />);
  const box = screen.getByTestId("support-resistance");
  expect(box.textContent).toContain("저항 1$1,108.80+4.6%");
  expect(box.textContent).toContain("지지 1$1,001.70-5.5%");
  expect(box.textContent).toContain("52주 최저 $600.00 · 최고 $1,120.00");
  cleanup();
  render(<SupportResistance technicals={{ supports: [90], resistances: [] }} price={100} />);
  expect(screen.getByTestId("support-resistance").textContent).toContain("위쪽 저항 없음 (신고가 구간)");
  cleanup();
  const { container } = render(<SupportResistance technicals={{}} price={100} />);
  expect(container.textContent).toBe("");
});
