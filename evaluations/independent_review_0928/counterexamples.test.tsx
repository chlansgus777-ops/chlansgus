// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import fixture from '../src/__fixtures__/stock_mock.json';
import StockDetailPage from '../src/pages/StockDetail';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function show(d: any) {
  vi.stubGlobal('fetch', async () => new Response(JSON.stringify(d), {status: 200}));
  render(<MemoryRouter initialEntries={[`/stocks/${d.analysis.ticker}`]}>
    <Routes><Route path='/stocks/:ticker' element={<StockDetailPage />} /></Routes>
  </MemoryRouter>);
  return await screen.findByTestId('tile-maxbuy');
}

it('uses the backend split-adjusted levels on the main price tiles', async () => {
  const d = structuredClone(fixture);
  Object.assign(d.analysis.entry, {max_buy: 1020, stop: 920, target1: 1200});
  Object.assign(d.recommendation, {split_factor_since: 10, max_buy: 102, stop: 92, target: 120});
  const tile = await show(d);
  expect(tile.textContent).toContain('$102.00');
  expect(tile.textContent).not.toContain('$1,020.00');
});

it('does not label an expired plan as a recommended purchase quantity', async () => {
  const d: any = structuredClone(fixture);
  Object.assign(d.recommendation, {action: 'BUY', current_status: 'EXPIRED', actionable_now: false});
  d.committee = null;
  d.position_plan = {available: true, nav: 100000, size_class: 'FULL', weight: .05,
    amount: 5000, shares: 50, price: 100, risk_amount: 250, risk_pct: .0025, notes: []};
  await show(d);
  expect(screen.queryByText('권장 매수')).toBeNull();
});
