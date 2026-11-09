import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DisplaySetupApp from './DisplaySetupApp.jsx';

const session = { csrfToken: 'display-csrf', boardId: '00000000-0000-4000-8000-000000000001', mode: 'PAUSED', credentialExpiresAt: '2027-01-01T00:00:00Z' };
const response = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
beforeEach(() => vi.stubGlobal('BroadcastChannel', class { close() {} postMessage() {} }));
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
const catalogReader = vi.fn(() => ({ bubbles: [], storageAvailable: true }));
function setup(fetcher, props = {}) { return render(<DisplaySetupApp fetcher={fetcher} catalogReader={catalogReader} {...props} />); }

describe('explicit staff display setup', () => {
  it.each([
    ['https://moodwall.library.kr', true],
    [null, false],
  ])('warns staff when the wall is opened outside PUBLIC_ORIGIN (%s)', async (origin, warned) => {
    const joinUrl = `${origin || window.location.origin}/join#ticket=t`;
    const fetcher = vi.fn(async path => path === '/api/display/session' ? response(session)
      : path === '/api/display/qr' ? response({ joinUrl }) : response({ nextCursor: null }));
    setup(fetcher); await screen.findByText('전광판이 등록되어 있어요');
    await waitFor(() => expect(fetcher.mock.calls.some(([path]) => path === '/api/display/qr')).toBe(true));
    if (warned) expect(await screen.findByText(/서버의 공개 주소\(https:\/\/moodwall\.library\.kr\)/)).toBeTruthy();
    else { await act(async () => {}); expect(screen.queryByText(/서버의 공개 주소/)).toBeNull(); }
  });
  it('pairs a device and never claims playback or implicitly resumes a paused board', async () => {
    const fetcher = vi.fn(async path => path === '/api/display/session' ? response({ code: 'AUTH_REQUIRED' }, 401) : path === '/api/display/pair' ? response(session) : response({ nextCursor: null }));
    setup(fetcher);
    fireEvent.change(await screen.findByLabelText('전광판 등록 코드'), { target: { value: 'one-time-pairing-code' } });
    fireEvent.click(screen.getByRole('button', { name: '전광판 연결하기' }));
    await screen.findByText('전광판이 등록되어 있어요');
    const pair = fetcher.mock.calls.find(([path]) => path === '/api/display/pair');
    expect(JSON.parse(pair[1].body)).toEqual({ code: 'one-time-pairing-code' }); expect(pair[1].headers['X-CSRF-Token']).toBeUndefined();
    expect(screen.getByRole('link', { name: '송출 화면 열기' }).getAttribute('href')).toBe('/display');
    expect(fetcher.mock.calls.some(([path]) => /heartbeat|playback|board\/mode|board\/intake|logout/.test(path))).toBe(false);
  });
  it('retries the exact unconsumed code after the server clears an expired cookie', async () => {
    let calls = 0;
    const fetcher = vi.fn(async path => path === '/api/display/session' ? response({ code: 'SESSION_EXPIRED' }, 401) : path === '/api/display/pair' ? (++calls === 1 ? response({ code: 'SESSION_EXPIRED' }, 401) : response(session)) : response({ nextCursor: null }));
    setup(fetcher); fireEvent.change(await screen.findByLabelText('전광판 등록 코드'), { target: { value: 'same-unconsumed-code' } });
    fireEvent.click(screen.getByRole('button', { name: '전광판 연결하기' })); await screen.findByText('전광판이 등록되어 있어요');
    expect(fetcher.mock.calls.filter(([path]) => path === '/api/display/pair').map(([, options]) => options.body)).toEqual(['{"code":"same-unconsumed-code"}', '{"code":"same-unconsumed-code"}']);
  });
  it('recovers a lost pairing response with GET instead of submitting a new code', async () => {
    let paired = false;
    const fetcher = vi.fn(async path => {
      if (path === '/api/display/session') return paired ? response(session) : response({ code: 'AUTH_REQUIRED' }, 401);
      if (path === '/api/display/pair') { paired = true; throw new TypeError('RESPONSE_LOST'); }
      return response({ nextCursor: null });
    });
    setup(fetcher); fireEvent.change(await screen.findByLabelText('전광판 등록 코드'), { target: { value: 'response-lost-code' } });
    fireEvent.click(screen.getByRole('button', { name: '전광판 연결하기' })); await screen.findByText('전광판이 등록되어 있어요');
    expect(fetcher.mock.calls.filter(([path]) => path === '/api/display/pair')).toHaveLength(1);
  });
  it('refreshes CSRF once and retries the same pairing body', async () => {
    let reads = 0, pairs = 0;
    const fetcher = vi.fn(async path => {
      if (path === '/api/display/session') return ++reads <= 2 ? response({ code: 'AUTH_REQUIRED' }, 401) : response(session);
      if (path === '/api/display/pair') return ++pairs === 1 ? response({ code: 'CSRF_REJECTED' }, 403) : response(session);
      return response({ nextCursor: null });
    });
    setup(fetcher); fireEvent.change(await screen.findByLabelText('전광판 등록 코드'), { target: { value: 'csrf-recovery-code' } });
    fireEvent.click(screen.getByRole('button', { name: '전광판 연결하기' })); await screen.findByText('전광판이 등록되어 있어요');
    const calls = fetcher.mock.calls.filter(([path]) => path === '/api/display/pair');
    expect(calls).toHaveLength(2); expect(calls[0][1].body).toBe(calls[1][1].body); expect(calls[1][1].headers['X-CSRF-Token']).toBe(session.csrfToken);
  });
  it('coalesces manual retries and cancels the pending automatic retry', async () => {
    vi.useFakeTimers(); let finish, attempts = 0;
    const fetcher = vi.fn(path => {
      if (path !== '/api/display/session') return Promise.resolve(response({ nextCursor: null }));
      attempts++; if (attempts === 1) return Promise.reject(new TypeError('OFFLINE'));
      return new Promise(resolve => { finish = resolve; });
    });
    const app = setup(fetcher); await act(async () => {});
    const retry = screen.getByRole('button', { name: '다시 연결하기' }); fireEvent.click(retry); fireEvent.click(retry);
    expect(retry.disabled).toBe(true); await act(async () => vi.advanceTimersByTimeAsync(5000)); expect(attempts).toBe(2);
    await act(async () => finish(response(session))); await act(async () => vi.advanceTimersByTimeAsync(30_000)); expect(attempts).toBe(2); app.unmount();
  });
  it('reads all snapshot pages and lists expired-source bubbles as safe UUID-only moderation links', async () => {
    const id = '00000000-0000-4000-8000-000000000010', content = '<img src=x onerror=alert(1)>';
    const reader = vi.fn(() => ({ storageAvailable: true, bubbles: [{ id, content, phase: 'AMBIENT' }] }));
    const storage = { getItem: vi.fn(), setItem: vi.fn(), removeItem: vi.fn() };
    const fetcher = vi.fn(async path => path === '/api/display/session' ? response(session) : response({ nextCursor: path.includes('?') ? null : 'page-two' }));
    setup(fetcher, { catalogReader: reader, storage });
    const link = await screen.findByRole('link', { name: /숨김 관리/ });
    expect(link.getAttribute('href')).toBe(`/operator#hide=${id}`); expect(link.getAttribute('rel')).toBe('noreferrer');
    expect(link.textContent).toContain(content); expect(link.querySelector('img')).toBeNull();
    expect(reader.mock.calls[0][0].snapshots).toHaveLength(2); expect(storage.setItem).not.toHaveBeenCalled(); expect(storage.removeItem).not.toHaveBeenCalled();
  });
  it('rejects repeated pagination cursors and keeps the management failure private', async () => {
    const fetcher = vi.fn(async path => path === '/api/display/session' ? response(session) : response({ nextCursor: 'repeat' }));
    setup(fetcher); await screen.findByText('말풍선 목록을 확인하지 못했어요. 연결 후 다시 확인해 주세요.');
    expect(fetcher.mock.calls.filter(([path]) => path.startsWith('/api/v2/display/state'))).toHaveLength(2);
  });
});
