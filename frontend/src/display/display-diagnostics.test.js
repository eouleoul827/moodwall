import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createDisplayDiagnostics } from './display-diagnostics.js';

const response = (body = { accepted: true }, status = 200, headers = {}) => new Response(JSON.stringify(body), { status, headers });
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2026-11-05T09:00:00Z')); });
afterEach(() => vi.useRealTimers());
describe('bounded display diagnostics', () => {
  it('uses only fixed codes and coalesces details without sending message bodies or secrets', async () => {
    const fetcher = vi.fn(async () => response()); const reporter = createDisplayDiagnostics({ fetcher, csrfToken: () => 'private-csrf' });
    reporter.record('RENDER', '<student message and secret>'); reporter.record('RENDER', 'ASSET_FAILURE'); reporter.record('RENDER', 'ASSET_FAILURE');
    await vi.advanceTimersByTimeAsync(0); expect(fetcher).toHaveBeenCalledTimes(1);
    const request = fetcher.mock.calls[0][1], body = JSON.parse(request.body);
    expect(body).toEqual({ component: 'RENDER', code: 'ASSET_FAILURE', state: 'FAILED', version: '1', count: 2, occurredAt: '2026-11-05T09:00:00.000Z' });
    expect(request.headers['X-CSRF-Token']).toBe('private-csrf'); expect(request.body).not.toContain('private-csrf'); reporter.stop();
  });
  it('does not let absent legacy endpoints or authentication rejection form a retry storm', async () => {
    for (const status of [401, 403, 404, 422]) {
      const fetcher = vi.fn(async () => response({ code: 'FAILED' }, status)); const reporter = createDisplayDiagnostics({ fetcher, csrfToken: () => 'csrf' });
      reporter.record('QR', 'QR_UNAVAILABLE'); await vi.advanceTimersByTimeAsync(0); await vi.advanceTimersByTimeAsync(5 * 60_000);
      expect(fetcher).toHaveBeenCalledTimes(1); reporter.stop();
    }
  });
  it('honors Retry-After without blocking the caller', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response({ code: 'RATE_LIMITED' }, 429, { 'Retry-After': '120' })).mockResolvedValue(response());
    const reporter = createDisplayDiagnostics({ fetcher, csrfToken: () => 'csrf' });
    expect(reporter.record('PLAYBACK', 'PLAYBACK_UNKNOWN')).toBeUndefined(); await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(119_999); expect(fetcher).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1); expect(fetcher).toHaveBeenCalledTimes(2); reporter.stop();
  });
  it('expires unsent reports and cancels an in-flight report on teardown', async () => {
    const offline = vi.fn(async () => { throw new TypeError('OFFLINE'); });
    const reporter = createDisplayDiagnostics({ fetcher: offline, csrfToken: () => 'csrf' }); reporter.record('CACHE', 'STORAGE_UNAVAILABLE');
    await vi.advanceTimersByTimeAsync(11 * 60_000); const count = offline.mock.calls.length;
    await vi.advanceTimersByTimeAsync(5 * 60_000); expect(offline).toHaveBeenCalledTimes(count); reporter.stop();
    let signal;
    const pending = createDisplayDiagnostics({ fetcher: vi.fn((_path, options) => { signal = options.signal; return new Promise(() => {}); }), csrfToken: () => 'csrf' });
    pending.record('QR', 'QR_UNAVAILABLE'); await vi.advanceTimersByTimeAsync(0); pending.stop(); expect(signal.aborted).toBe(true);
  });
  it('keeps a new recovery report when the older failure reaches its ten-minute expiry', async () => {
    const fetcher = vi.fn(async () => { throw new TypeError('OFFLINE'); });
    const reporter = createDisplayDiagnostics({ fetcher, csrfToken: () => 'csrf' });
    reporter.record('QR', 'QR_UNAVAILABLE'); await vi.advanceTimersByTimeAsync(599_000);
    fetcher.mockResolvedValue(response()); reporter.record('QR', 'QR_UNAVAILABLE', 'RECOVERED');
    await vi.advanceTimersByTimeAsync(1000);
    expect(JSON.parse(fetcher.mock.calls.at(-1)[1].body).state).toBe('RECOVERED'); reporter.stop();
  });
});
