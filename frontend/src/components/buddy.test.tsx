// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { BrandMark, Buddy } from "./icons";
import { Empty } from "./ui";

afterEach(() => cleanup());

it("the lens buddy is decoration only: hidden from assistive tech and never part of the text", () => {
  render(<Empty hint="힌트">신호가 없습니다</Empty>);
  const box = screen.getByTestId("empty");
  expect(box.textContent).toBe("신호가 없습니다힌트");
  const svg = box.querySelector("svg")!;
  expect(svg.getAttribute("aria-hidden")).toBe("true");
  for (const mood of ["calm", "sleepy", "happy"] as const) {
    const { container } = render(<Buddy mood={mood} />);
    expect(container.textContent).toBe("");
    expect(container.querySelector("svg")!.getAttribute("aria-hidden")).toBe("true");
  }
  const { container } = render(<BrandMark />);
  expect(container.querySelector("svg")!.getAttribute("aria-hidden")).toBe("true");
});
