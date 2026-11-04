import { afterEach, describe, expect, it, vi } from 'vitest';
import { request } from './api.js';

afterEach(() => vi.unstubAllGlobals());

describe('operator command transport', () => {
  it('sends report resolution metadata with the session CSRF and immutable command key', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'RESOLVED' }), { headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetcher);
    await request('/api/operator/reports/report-id/resolve', { method: 'POST', body: { resolution: 'NO_ACTION' }, csrfToken: 'csrf', idempotencyKey: 'same-key' });
    const [, options] = fetcher.mock.calls[0];
    expect(options.headers['X-CSRF-Token']).toBe('csrf');
    expect(options.headers['Idempotency-Key']).toBe('same-key');
    expect(options.credentials).toBe('same-origin');
    expect(options.cache).toBe('no-store');
    expect(JSON.parse(options.body)).toEqual({ resolution: 'NO_ACTION' });
  });

  it('does not automatically repeat an uncertain operator write', async () => {
    const fetcher = vi.fn().mockRejectedValue(new TypeError('lost response'));
    vi.stubGlobal('fetch', fetcher);
    await expect(request('/api/operator/reports/report-id/resolve', { method: 'POST', idempotencyKey: 'same-key' })).rejects.toMatchObject({ code: 'NETWORK_ERROR' });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
