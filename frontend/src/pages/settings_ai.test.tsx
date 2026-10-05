// @vitest-environment jsdom
/** The AI review was removed (owner 2026-10-05: "ai위원회기능 쓰지도않는데 없애버려"). Settings no longer offers it:
 * no tab, an old ?tab=ai link opens the first tab, and the keys it used are not listed. (Replaces the 2026-09-28 test
 * of the AI key form, which no longer exists; key masking stays covered by usability.test.tsx and toss.test.tsx.) */
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import Settings from "./Settings";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const SETTINGS = {
  mode: "LIVE", keys_configured: { FINNHUB_API_KEY: true, OPENAI_API_KEY: true, ANTHROPIC_API_KEY: false }, weights: {}, decision: {}, entry: {}, scanner: {}, calibration: {}, sector_models: [], note: "",
  scheduler: { enabled: false, interval_minutes: 60 },
};

it("has no AI review tab; an old ?tab=ai link opens 데이터 연결 and the AI keys are not listed", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(SETTINGS))));
  render(<MemoryRouter initialEntries={["/settings?tab=ai"]}><Settings /></MemoryRouter>);
  await screen.findByText(/초기 설정 · API 키/);
  expect(screen.queryByRole("tab", { name: /AI/ })).toBeNull();
  expect(screen.getByLabelText(/Finnhub API 키/)).toBeTruthy();
  expect(document.body.textContent).not.toMatch(/OPENAI|ANTHROPIC|AI 검토/);
});

it("folds the read-only model values under 연결 상태 (an old ?tab=model link lands there)", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(SETTINGS))));
  render(<MemoryRouter initialEntries={["/settings?tab=model"]}><Settings /></MemoryRouter>);
  const fold = await screen.findByTestId("model-settings");
  expect(fold.tagName).toBe("DETAILS");
  expect(fold.hasAttribute("open")).toBe(false);
  expect(screen.queryByRole("tab", { name: "모델 설정" })).toBeNull();
});
