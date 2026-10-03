// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useAnalysisJob } from "./useAnalysisJob";
import { resetApiCache } from "./useApi";

afterEach(() => { cleanup(); resetApiCache(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function Harness() {
  const job = useAnalysisJob("NVDA", () => {});
  return <button disabled={job.busy} onClick={job.start}>{job.busy ? "진행 중" : job.verifyOnly ? "상태 확인" : "시작"}</button>;
}

it("recovers a lost start response and disables further commands while the job runs", async () => {
  let started = false;
  let posts = 0;
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
    if (init.method === "POST") { started = true; posts++; throw new TypeError("lost acknowledgement"); }
    return new Response(JSON.stringify({ status: started ? "RUNNING" : "IDLE" }));
  }));
  render(<Harness />);
  await waitFor(() => expect(screen.getByRole("button").textContent).toBe("시작"));
  fireEvent.click(screen.getByRole("button"));
  fireEvent.click(screen.getByRole("button"));
  await waitFor(() => expect((screen.getByRole("button") as HTMLButtonElement).disabled).toBe(true));
  expect(posts).toBe(1);
  expect(screen.getByRole("button").textContent).toBe("진행 중");
});
