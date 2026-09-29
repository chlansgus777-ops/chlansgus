// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PairScreen, PhoneGate } from "./Phone";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function mockFetch(routes: Record<string, (init?: RequestInit) => [number, unknown]>) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const key = Object.keys(routes).find((k) => url.endsWith(k));
    const [status, body] = key ? routes[key]!(init) : [404, { detail: "없음" }];
    return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  }));
  return calls;
}

describe("the phone (owner 2026-09-30: 안드로이드 · 집 와이파이)", () => {
  it("an unpaired phone sees only the pairing screen", async () => {
    mockFetch({ "/api/phone/hello": () => [200, { phone: true, paired: false, enabled: true }] });
    render(<PhoneGate><div>앱 화면</div></PhoneGate>);
    await waitFor(() => expect(screen.getByTestId("pair-screen")).toBeTruthy());
    expect(screen.queryByText("앱 화면")).toBeNull();
  });
  it("a paired phone sees the app with the read-only note; the PC sees no note", async () => {
    mockFetch({ "/api/phone/hello": () => [200, { phone: true, paired: true, enabled: true }] });
    render(<PhoneGate><div>앱 화면</div></PhoneGate>);
    await waitFor(() => expect(screen.getByTestId("phone-ribbon")).toBeTruthy());
    expect(screen.getByText("앱 화면")).toBeTruthy();
    cleanup();
    mockFetch({ "/api/phone/hello": () => [200, { phone: false, paired: false, enabled: false }] });
    render(<PhoneGate><div>앱 화면</div></PhoneGate>);
    await waitFor(() => expect(screen.getByText("앱 화면")).toBeTruthy());
    expect(screen.queryByTestId("phone-ribbon")).toBeNull();
  });
  it("pairs with the six digits (spaces allowed) and shows the server's reason on a wrong code", async () => {
    let n = 0;
    const calls = mockFetch({ "/api/phone/pair": () => (++n === 1 ? [400, { detail: "코드가 맞지 않습니다" }] : [200, { paired: true }]) });
    const done = vi.fn();
    render(<PairScreen onPaired={done} />);
    fireEvent.change(screen.getByLabelText("연결 코드"), { target: { value: "123 456" } });
    fireEvent.click(screen.getByText("연결"));
    await waitFor(() => expect(screen.getByText(/코드가 맞지 않습니다/)).toBeTruthy());
    expect(JSON.parse(String(calls.at(-1)!.init!.body))).toEqual({ code: "123456", name: "내 폰" });
    fireEvent.click(screen.getByText("연결"));
    await waitFor(() => expect(done).toHaveBeenCalled());
  });
});
