// @vitest-environment jsdom
/** Recently opened stocks (this viewer only), offered on the 종목 screen. */
import { afterEach, expect, it } from "vitest";
import { recentStocks, rememberStock } from "./recentStocks";

afterEach(() => { try { localStorage.clear(); } catch { /* none */ } });

it("recent stocks keep the newest first, without duplicates, at most 8", () => {
  for (const t of ["A", "B", "C", "A"]) rememberStock(t);
  expect(recentStocks()).toEqual(["A", "C", "B"]);
  for (let i = 0; i < 12; i++) rememberStock(`T${i}`);
  expect(recentStocks()).toHaveLength(8);
});
