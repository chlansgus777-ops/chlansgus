// @vitest-environment node
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { afterEach, expect, test, vi } from 'vitest';

const source = readFileSync(new URL('../public/desktop-start.js', import.meta.url), 'utf8');
function launch(fetch) {
  const nodes = Object.fromEntries(['status', 'progress', 'retry'].map(id => [id, { hidden: false, addEventListener: vi.fn() }]));
  const replace = vi.fn();
  const window = { __MARKETLENS_API__: 'http://127.0.0.1:12345', addEventListener: vi.fn() };
  runInNewContext(source, { window, document: { getElementById: id => nodes[id] }, fetch, location: { replace }, AbortController, setTimeout, clearTimeout, Date });
  return { nodes, replace, window, retry: () => nodes.retry.addEventListener.mock.calls[0][1]() };
}
afterEach(() => vi.useRealTimers());

test('the main screen opens only after the bundled server is ready', async () => {
  vi.useFakeTimers();
  const fetch = vi.fn().mockRejectedValueOnce(new Error('extracting')).mockResolvedValueOnce({ ok: true, json: async () => ({ ready: true }) });
  const app = launch(fetch);
  expect(app.replace).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(251);
  expect(app.replace).toHaveBeenCalledExactlyOnceWith('index.html');
  expect(fetch.mock.calls[0][0]).toBe('http://127.0.0.1:12345/api/health/ready');
  await vi.advanceTimersByTimeAsync(30000);
  expect(fetch).toHaveBeenCalledTimes(2);
});

test('startup failure stops polling, shows a reason, and retry only checks readiness', async () => {
  vi.useFakeTimers();
  const fetch = vi.fn().mockRejectedValue(new Error('not listening'));
  const app = launch(fetch);
  await vi.advanceTimersByTimeAsync(30001);
  expect(app.nodes.progress.hidden).toBe(true);
  expect(app.nodes.retry.hidden).toBe(false);
  expect(app.nodes.status.textContent).toContain('아직 준비되지 않았습니다');
  const count = fetch.mock.calls.length;
  await vi.advanceTimersByTimeAsync(10000);
  expect(fetch).toHaveBeenCalledTimes(count);
  fetch.mockResolvedValue({ ok: true, json: async () => ({ ready: true }) });
  app.retry();
  await vi.advanceTimersByTimeAsync(1);
  expect(app.replace).toHaveBeenCalledExactlyOnceWith('index.html');
});

test('closing the startup page cancels its request and ignores a late response', async () => {
  vi.useFakeTimers();
  let resolve;
  const fetch = vi.fn(() => new Promise(r => { resolve = r; }));
  const app = launch(fetch);
  const pagehide = app.window.addEventListener.mock.calls.find(([name]) => name === 'pagehide')[1];
  const signal = fetch.mock.calls[0][1].signal;
  pagehide();
  expect(signal.aborted).toBe(true);
  resolve({ ok: true, json: async () => ({ ready: true }) });
  await vi.advanceTimersByTimeAsync(10000);
  expect(app.replace).not.toHaveBeenCalled();
  expect(fetch).toHaveBeenCalledTimes(1);
});
