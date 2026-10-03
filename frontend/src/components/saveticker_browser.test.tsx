// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SaveTickerConnect } from "./SaveTickerConnect";
import { resetApiCache } from "./useApi";

afterEach(() => { cleanup(); resetApiCache(); vi.unstubAllGlobals(); delete window.__MARKETLENS_API__; });

it("defaults to browser connection and never asks for a source password", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({status:"IDLE", items_fetched:0}))));
  render(<SaveTickerConnect mode="LIVE" />);
  expect(screen.getByRole("button", {name:"브라우저 연결 시작"})).toBeTruthy();
  expect(screen.queryByLabelText("SaveTicker 비밀번호")).toBeNull();
});

it("creates one scoped pairing link and follows actual collection status", async () => {
  window.__MARKETLENS_API__ = "http://127.0.0.1:8769";
  let posts = 0;
  let status = {status:"IDLE", method:"browser", items_fetched:0};
  vi.stubGlobal("fetch", vi.fn(async (_url, opts) => {
    if (opts?.method === "POST") { posts++; status = {status:"WAITING_BROWSER", method:"browser", items_fetched:0}; return new Response(JSON.stringify({key:"f".repeat(64)})); }
    return new Response(JSON.stringify(status));
  }));
  render(<SaveTickerConnect mode="LIVE" />);
  const button = screen.getByRole("button", {name:"브라우저 연결 시작"});
  fireEvent.click(button); fireEvent.click(button);
  await waitFor(() => expect((screen.getByLabelText("Chrome·Edge에서 열 연결 주소") as HTMLInputElement).value).toContain("http://127.0.0.1:8769/saveticker-bridge.html#key="));
  expect(posts).toBe(1);
  await waitFor(() => expect(screen.getByRole("status").textContent).toContain("브라우저 연결 대기"));
  expect(screen.queryByText(/브라우저 뉴스 수집 확인/)).toBeNull();
  status = {status:"CONNECTED", method:"browser", items_fetched:20};
  fireEvent.click(screen.getByRole("button", {name:"수집 상태 확인"}));
  await waitFor(() => expect(screen.getByRole("status").textContent).toContain("수집 확인 · 20건"));
});

it("shows stale receipt as a failure rather than an active browser connection", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({status:"STALE", method:"browser", items_fetched:20, error:"수신 중단"}))));
  render(<SaveTickerConnect mode="LIVE" />);
  await waitFor(() => expect(screen.getByRole("status").textContent).toContain("마지막 뉴스는 원래 시각"));
  expect(screen.getByRole("alert").textContent).toContain("수신 중단");
});
