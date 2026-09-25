// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Startup } from "./Startup";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Startup gate", () => {
  it("waits for the backend and then shows the app", async () => {
    let n = 0;
    vi.stubGlobal("fetch", () => {
      n += 1;
      if (n < 3) return Promise.reject(new TypeError("connection refused"));
      return Promise.resolve(new Response(JSON.stringify({ ready: true }), { status: 200 }));
    });
    render(<Startup intervalMs={5}><div>APP</div></Startup>);
    expect(screen.getByRole("status").textContent).toContain("백엔드 시작 중");
    await waitFor(() => expect(screen.getByText("APP")).toBeTruthy());
  });
  it("explains a failed start in Korean with a retry", async () => {
    vi.stubGlobal("fetch", () => Promise.reject(new TypeError("connection refused")));
    render(<Startup intervalMs={5} timeoutMs={30}><div>APP</div></Startup>);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("백엔드가 시작되지 않았습니다"));
    expect(screen.getByText("다시 시도")).toBeTruthy();
  });
});
