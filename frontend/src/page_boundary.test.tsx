// @vitest-environment jsdom
import { lazy, Suspense } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PageBoundary } from "./App";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("keeps a recovery action after a page chunk fails instead of blanking the app", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  const Broken = lazy(() => Promise.reject(new Error("Failed to fetch dynamically imported module")));
  render(<><nav>주 메뉴 유지</nav><PageBoundary><Suspense fallback={<span>로딩</span>}><Broken /></Suspense></PageBoundary></>);
  expect(await screen.findByRole("button", { name: "화면 다시 열기" })).toBeTruthy();
  expect(screen.getByText("주 메뉴 유지")).toBeTruthy();
  expect(screen.queryByText("로딩")).toBeNull();
});
