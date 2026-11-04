import { describe, expect, it, vi } from 'vitest';
import { createStudentApi, takeAdmissionTicket } from './api.js';

const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
const session = { csrfToken: 'existing-csrf', admissionValid: true, usage: { remainingSlots: 3 } };

describe('student admission transport', () => {
  it('removes the admission credential from browser history', () => {
    const history = { replaceState: vi.fn() };
    expect(takeAdmissionTicket({ hash: '#ticket=qr-secret', pathname: '/join', search: '' }, history)).toBe('qr-secret');
    expect(history.replaceState).toHaveBeenCalledExactlyOnceWith(null, '', '/join');
  });

  it('uses the existing session CSRF for exchange and confirms the resulting cookie', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(json(session)).mockResolvedValueOnce(json(session)).mockResolvedValueOnce(json(session));
    expect(await createStudentApi(fetcher).connect('qr-secret')).toEqual(session);
    expect(fetcher.mock.calls.map(([path]) => path)).toEqual(['/api/session', '/api/admissions/exchange', '/api/session']);
    expect(fetcher.mock.calls[1][1].headers['X-CSRF-Token']).toBe('existing-csrf');
    expect(fetcher.mock.calls[1][1].body).toBe(JSON.stringify({ ticket: 'qr-secret' }));
  });

  it('recovers a lost exchange response using the cookie, with no extra exchange', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(json({ code: 'SESSION_REQUIRED' }, 401))
      .mockRejectedValueOnce(new TypeError('connection lost')).mockResolvedValueOnce(json(session));
    expect(await createStudentApi(fetcher).connect('qr-secret')).toEqual(session);
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it('stops issuing sessions if the browser does not retain cookies', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(json({ code: 'SESSION_REQUIRED' }, 401))
      .mockResolvedValueOnce(json(session)).mockResolvedValueOnce(json({ code: 'SESSION_REQUIRED' }, 401));
    await expect(createStudentApi(fetcher).connect('qr-secret')).rejects.toMatchObject({ code: 'COOKIES_REQUIRED' });
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it('does not repeat an expired QR exchange', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(json(session)).mockResolvedValueOnce(json({ code: 'QR_EXPIRED' }, 400));
    await expect(createStudentApi(fetcher).connect('old-qr')).rejects.toMatchObject({ code: 'QR_EXPIRED' });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
