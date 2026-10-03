// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SaveTickerHttpConnect as SaveTickerConnect } from "./SaveTickerConnect";
import { resetApiCache } from "./useApi";

afterEach(() => { cleanup(); resetApiCache(); vi.unstubAllGlobals(); });
const idle = { status: "IDLE", authenticated: false, retry_in_s: 0, items_fetched: 0, saved: false };

it("prevents real source collection in MOCK mode", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(idle))));
  render(<SaveTickerConnect mode="MOCK" />);
  expect(screen.getByRole("button", { name: "로그인하고 뉴스 받기" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByLabelText("SaveTicker 비밀번호").hasAttribute("disabled")).toBe(true);
});

it("shows immediate feedback, singleflights clicks, clears credentials and follows server status", async () => {
  let finish!: (r: Response) => void;
  let state = idle;
  let posts = 0;
  vi.stubGlobal("fetch", vi.fn((_url, opts) => {
    if (opts?.method === "POST") { posts++; return new Promise<Response>((r) => { finish = r; }); }
    return Promise.resolve(new Response(JSON.stringify(state)));
  }));
  render(<SaveTickerConnect mode="LIVE" />);
  await waitFor(() => expect(screen.getByRole("button", { name: "로그인하고 뉴스 받기" })).toBeTruthy());
  fireEvent.change(screen.getByLabelText("SaveTicker 이메일"), { target: { value: "test@example.test" } });
  fireEvent.change(screen.getByLabelText("SaveTicker 비밀번호"), { target: { value: "test secret" } });
  const button = screen.getByRole("button", { name: "로그인하고 뉴스 받기" });
  fireEvent.click(button); fireEvent.click(button);
  expect(screen.getByRole("button", { name: "로그인·뉴스 수집 확인 중…" }).hasAttribute("disabled")).toBe(true);
  expect(posts).toBe(1);
  state = { ...idle, status: "CONNECTED", authenticated: true, items_fetched: 20 };
  finish(new Response(JSON.stringify(state)));
  await waitFor(() => expect(screen.getByText(/정상 로그인·수집 확인/)).toBeTruthy());
  expect((screen.getByLabelText("SaveTicker 비밀번호") as HTMLInputElement).value).toBe("");
  expect((screen.getByLabelText("SaveTicker 이메일") as HTMLInputElement).value).toBe("");
});

it("recovers a lost start response by querying status without resubmitting login", async () => {
  let posts = 0;
  let state = idle;
  vi.stubGlobal("fetch", vi.fn(async (_url, opts) => {
    if (opts?.method === "POST") { posts++; state = { ...idle, status: "CONNECTED", authenticated: true, items_fetched: 3 }; throw new TypeError("lost response"); }
    return new Response(JSON.stringify(state));
  }));
  render(<SaveTickerConnect mode="LIVE" />);
  fireEvent.change(screen.getByLabelText("SaveTicker 이메일"), { target: { value: "test@example.test" } });
  fireEvent.change(screen.getByLabelText("SaveTicker 비밀번호"), { target: { value: "test secret" } });
  fireEvent.click(screen.getByRole("button", { name: "로그인하고 뉴스 받기" }));
  await waitFor(() => expect(screen.getByText(/정상 로그인·수집 확인/)).toBeTruthy());
  expect(posts).toBe(1);
});
