// @vitest-environment jsdom
/** AI 검토 settings (2026-09-28): the OpenAI key is sent once and never shown, a budget always goes with it. */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import Settings from "./Settings";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const SETTINGS = {
  mode: "LIVE", keys_configured: { OPENAI_API_KEY: true }, weights: {}, decision: {}, entry: {}, scanner: {}, calibration: {}, sector_models: [], note: "",
  llm: { provider: "none", available: false, base_url: null, fast_model: "claude-haiku-4-5", deep_model: "claude-opus-5", openai_key: true, budget_usd: null, spent_usd: null, reason: "LLM_PROVIDER 미설정" },
  scheduler: { enabled: false, interval_minutes: 60, ai_committee: false },
};

it("sends provider, models, budget and the new key; the stored key is never displayed", async () => {
  const puts: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
    if (init?.method === "PUT") { puts.push(JSON.parse(String(init.body))); return new Response(JSON.stringify({ note: "저장했습니다. 앱을 다시 시작하면 적용됩니다." })); }
    return new Response(JSON.stringify(SETTINGS));
  }));
  render(<MemoryRouter initialEntries={["/settings?tab=ai"]}><Settings /></MemoryRouter>);
  const keyInput = await screen.findByLabelText(/OpenAI API 키/) as HTMLInputElement;
  expect(keyInput.type).toBe("password");
  expect(keyInput.value).toBe("");
  expect(screen.getByText("(설정됨)")).toBeTruthy();
  fireEvent.change(keyInput, { target: { value: "sk-test-123" } });
  fireEvent.change(screen.getByLabelText(/예산 한도/), { target: { value: "8" } });
  fireEvent.click(screen.getByText("OpenAI로 AI 검토 사용"));
  await waitFor(() => expect(puts.length).toBe(1));
  expect(puts[0]).toEqual({ values: { LLM_PROVIDER: "openai", FAST_MODEL: "gpt-4o-mini", DEEP_MODEL: "gpt-4.1-mini", LLM_BUDGET_USD: "8", OPENAI_API_KEY: "sk-test-123" } });
  await waitFor(() => expect((screen.getByLabelText(/OpenAI API 키/) as HTMLInputElement).value).toBe(""));
  expect(document.body.textContent).not.toContain("sk-test-123");
});
