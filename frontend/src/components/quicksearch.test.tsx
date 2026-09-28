// @vitest-environment jsdom
/** Quick stock search (usability 2026-09-28): "/" focuses it, suggestions match ticker or company, Enter opens. */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { QuickSearch, recentStocks, rememberStock } from "./QuickSearch";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); try { localStorage.clear(); } catch { /* none */ } });

function Stock() { const { ticker } = useParams(); return <div data-testid="opened">{ticker}</div>; }

it("finds a candidate by company name and opens it with Enter; '/' focuses the box", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(String(url).includes("/opportunities")
    ? { rows: [{ ticker: "NVDA", company: "NVIDIA Corp", rank: 1 }, { ticker: "AAPL", company: "Apple Inc", rank: 2 }] } : []))));
  render(<MemoryRouter><QuickSearch /><Routes><Route path="/stocks/:ticker" element={<Stock />} /><Route path="*" element={null} /></Routes></MemoryRouter>);
  const box = screen.getByRole("combobox");
  fireEvent.keyDown(window, { key: "/" });
  expect(document.activeElement).toBe(box);
  fireEvent.focus(box);
  fireEvent.change(box, { target: { value: "apple" } });
  await waitFor(() => expect(screen.getByRole("option", { name: /AAPL/ })).toBeTruthy());
  fireEvent.keyDown(box, { key: "Enter" });
  expect(screen.getByTestId("opened").textContent).toBe("AAPL");
});

it("any valid ticker typed in full can be opened even if it is not in the lists", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([]))));
  render(<MemoryRouter><QuickSearch /><Routes><Route path="/stocks/:ticker" element={<Stock />} /><Route path="*" element={null} /></Routes></MemoryRouter>);
  const box = screen.getByRole("combobox");
  fireEvent.focus(box);
  fireEvent.change(box, { target: { value: "brk.b" } });
  expect(screen.getByRole("option", { name: /이 코드로 분석/ })).toBeTruthy();
  fireEvent.keyDown(box, { key: "Enter" });
  expect(screen.getByTestId("opened").textContent).toBe("BRK.B");
});

it("recent stocks keep the newest first, without duplicates, at most 8", () => {
  for (const t of ["A", "B", "C", "A"]) rememberStock(t);
  expect(recentStocks()).toEqual(["A", "C", "B"]);
  for (let i = 0; i < 12; i++) rememberStock(`T${i}`);
  expect(recentStocks()).toHaveLength(8);
});
