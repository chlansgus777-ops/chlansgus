// @vitest-environment jsdom
/** 이동평균선 위치: the stock page says which of the 20 / 50 / 200-day lines the price touches, with the same rule as
 * the backend alert (a quarter ATR, at least 0.5 % of the line), on today's share basis. */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { MaTouch, maBand, maState } from "./MaTouch";

afterEach(cleanup);

it("uses a quarter ATR, never under half a percent", () => {
  expect(maBand(200, 4)).toBe(1);
  expect(maBand(200, 0.4)).toBe(1);
  expect(maState(200.9, 200, 4)).toBe("AT");
  expect(maState(201.2, 200, 4)).toBe("ABOVE");
  expect(maState(198.8, 200, 4)).toBe("BELOW");
});

it("names the line being touched and where the price sits against the others", () => {
  render(<MaTouch technicals={{ sma20: 230, sma50: 220, sma200: 200, atr14: 4 }} price={200.5} alerts={false} />);
  const box = screen.getByTestId("ma-touch");
  expect(box.textContent).toContain("지금 200일선에 닿아 있음");
  expect(box.textContent).toContain("보유·관심 종목에 넣으면 닿을 때 알림이 옵니다");
  expect([...box.querySelectorAll(".ma-chip")].map((c) => c.textContent)).toEqual(["아래", "아래", "닿음"]);
});

it("is on today's share basis after a split, and hides without lines or a price", () => {
  render(<MaTouch technicals={{ sma20: 2000, sma50: 1800, sma200: 1500, atr14: 40 }} price={210} split={10} alerts />);
  expect(screen.getByTestId("ma-touch").textContent).toContain("모든 이동평균선 위 (상승 추세)");
  expect(screen.getByTestId("ma-touch").textContent).toContain("$200.00");
  cleanup();
  const { container } = render(<MaTouch technicals={{}} price={210} alerts />);
  expect(container.textContent).toBe("");
});
