import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DisplayApp from './DisplayApp.jsx';

vi.mock('qrcode', () => ({ default: { toDataURL: vi.fn(async () => 'data:image/png;base64,cXI=') } }));
const session = { csrfToken: 'display-csrf', boardId: '00000000-0000-4000-8000-000000000001', mode: 'MOODWALL', credentialExpiresAt: '2027-01-01T00:00:00Z' };
const ticket = { joinUrl: 'https://school.example/join#ticket=private-qr-ticket', rotatesAt: '2026-11-04T09:05:00Z', validUntil: '2026-11-04T09:10:00Z', serverNow: '2026-11-04T09:00:00Z' };
const item = { id: '00000000-0000-4000-8000-000000000010', content: '<img src=x onerror=alert(1)>', sceneId: 'joy', sceneVersion: '1', sceneSeed: 'abc', emotion: 'JOY', phase: 'FLOATING' };
const response = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
let events;
const originalFonts = Object.getOwnPropertyDescriptor(document, 'fonts');
beforeEach(() => {
  events = [];
  vi.stubGlobal('BroadcastChannel', class { close() {} postMessage() {} });
  vi.stubGlobal('EventSource', class {
    listeners = new Map(); close = vi.fn();
    constructor(path) { this.path = path; events.push(this); }
    addEventListener(type, listener) { this.listeners.set(type, listener); }
  });
});
afterEach(() => {
  vi.useRealTimers(); vi.unstubAllGlobals();
  if (originalFonts) Object.defineProperty(document, 'fonts', originalFonts); else Reflect.deleteProperty(document, 'fonts');
});
function harness(fetchOverride, ready = true) {
  let wiring;
  const runtime = { start: vi.fn(() => {
    if (ready) {
      wiring.onState({ source: 'snapshot', connected: true, mode: 'MOODWALL', leaseRemainingMs: 30_000,
        monotonicLeaseUntil: performance.now() + 30_000 });
      wiring.renderBubbles({ visible: true, bubbles: [], totalCount: 0 });
    }
  }), stop: vi.fn(), resetAuthentication: vi.fn(), refresh: vi.fn(async () => ({})) };
  const factory = vi.fn(options => { wiring = options; return runtime; });
  const fetcher = fetchOverride || vi.fn(async path => response(path === '/api/display/session' ? session : path === '/api/display/diagnostics' ? { accepted: true } : ticket));
  const view = render(<DisplayApp runtimeFactory={factory} fetcher={fetcher} />);
  return { ...view, factory, runtime, fetcher, get wiring() { return wiring; } };
}
function expectQuiet() {
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.queryByRole('button')).toBeNull();
  expect(screen.queryByLabelText('전광판 등록 코드')).toBeNull();
  expect(screen.queryByText(/연결을 다시|새로고침|참여 QR을.*준비|등록 코드를/)).toBeNull();
}
describe('public display', () => {
  it('restores the cookie, starts one runtime and renders a real QR after online authority', async () => {
    vi.useFakeTimers();
    const app = harness();
    await act(async () => {});
    expect(screen.getByAltText('기분을 남기는 참여 QR 코드')).toBeTruthy();
    expect(app.factory).toHaveBeenCalledTimes(1); expect(app.runtime.start).toHaveBeenCalledTimes(1);
    expect(app.wiring.boardId).toBe(session.boardId); expect(app.wiring.csrfToken()).toBe('display-csrf');
    expect(app.fetcher.mock.calls[0][1].credentials).toBe('same-origin');
    expect(screen.getByText('30자 · 1시간에 3번')).toBeTruthy(); expectQuiet();
    app.unmount(); expect(app.runtime.stop).toHaveBeenCalledTimes(1); expect(events[0].close).toHaveBeenCalledTimes(1);
  });
  it('does not publish a QR, count or forecast before synchronized authority', async () => {
    const app = harness(undefined, false);
    await waitFor(() => expect(app.runtime.start).toHaveBeenCalled());
    expect(screen.queryByAltText('기분을 남기는 참여 QR 코드')).toBeNull();
    expect(screen.queryByRole('heading', { name: /개의 기분/ })).toBeNull(); expectQuiet();
    app.unmount();
  });
  it('commits escaped text synchronously and hides immediately on interruption', async () => {
    const app = harness(); await waitFor(() => expect(app.runtime.start).toHaveBeenCalled());
    await app.wiring.renderer.prepare(item, { signal: new AbortController().signal });
    act(() => app.wiring.renderer.show(item));
    const stage = screen.getByRole('region', { name: '새로운 메시지' });
    expect(stage.hidden).toBe(false); expect(stage.querySelector('.mw-full-message').textContent).toBe(item.content);
    expect(stage.querySelector('img[src="x"]')).toBeNull();
    act(() => app.wiring.renderer.hide()); expect(stage.hidden).toBe(true);
    await expect(app.wiring.renderer.prepare({ ...item, sceneId: 'https://evil.invalid/scene' }, { signal: new AbortController().signal })).rejects.toThrow('UNSUPPORTED_SCENE');
    expectQuiet();
  });
  it('retains ambient text and never shows disconnected cached text', async () => {
    const app = harness(); await waitFor(() => expect(app.runtime.start).toHaveBeenCalled());
    const bubble = { ...item, content: '오늘도 수고했어' };
    act(() => app.wiring.renderBubbles({ visible: true, bubbles: [bubble], totalCount: 1 }));
    expect(screen.getByText(bubble.content)).toBeTruthy();
    act(() => app.wiring.renderBubbles({ visible: true, bubbles: [{ ...bubble, phase: 'AMBIENT' }], totalCount: 1 }));
    expect(screen.getByText(bubble.content).closest('.mw-bubble-track').classList.contains('is-ambient')).toBe(true);
    act(() => app.wiring.renderBubbles({ visible: false, bubbles: [], totalCount: 0 }));
    expect(screen.queryByText(bubble.content)).toBeNull(); expectQuiet();
  });
  it('renders the new cloud artwork and labels statistics with the visible cache scope', async () => {
    const app = harness(); await waitFor(() => expect(app.runtime.start).toHaveBeenCalled());
    act(() => app.wiring.renderBubbles({ visible: true, bubbles: [{ ...item, content: '오늘도 수고했어' }], totalCount: 450 }));
    expect(screen.getByRole('heading', { name: /지금 1개의 구름이\s*머물고 있어요/ })).toBeTruthy();
    expect(screen.getAllByText('이 화면에 남은 기분 기준')).toHaveLength(2);
    expect(screen.queryByText(/450|°C|약 1분|3회 경고|5회 정지/)).toBeNull();
    expect(screen.getByText('오늘도 수고했어').closest('.mw-bubble').dataset.cloudShape).toMatch(/^cloud-\d{2}\.svg$/);
    act(() => app.wiring.renderer.show(item));
    expect(screen.getByRole('region', { name: '새로운 메시지' }).querySelector('.mw-duration-track').dataset.durationSeconds).toBe('15');
    expectQuiet();
  });
  it('uses SSE hints and silently stops on authentication failure without a registration form', async () => {
    const app = harness(); await waitFor(() => expect(app.runtime.start).toHaveBeenCalled());
    act(() => events[0].listeners.get('state.changed')({ data: '{}' })); expect(app.runtime.refresh).toHaveBeenCalledTimes(1);
    const before = app.fetcher.mock.calls.length;
    act(() => events[0].listeners.get('qr.changed')({ data: '{}' }));
    await waitFor(() => expect(app.fetcher.mock.calls.length).toBeGreaterThan(before));
    await act(async () => app.wiring.onProblem({ status: 401, code: 'AUTH_REQUIRED' }));
    expect(app.runtime.resetAuthentication).toHaveBeenCalledTimes(1); expect(app.runtime.stop).toHaveBeenCalledTimes(1);
    expect(screen.queryByAltText('기분을 남기는 참여 QR 코드')).toBeNull(); expectQuiet();
  });
  it('keeps an authorized message on snapshot trouble and does not disclose ownership errors', async () => {
    const app = harness(); await waitFor(() => expect(app.runtime.start).toHaveBeenCalled());
    act(() => app.wiring.renderer.show(item));
    act(() => app.wiring.onState({ source: 'snapshot', connected: false }));
    act(() => app.wiring.onProblem({ status: 409, code: 'PLAYBACK_OWNED' }));
    expect(screen.getByText(item.content)).toBeTruthy();
    expect(screen.queryByAltText('기분을 남기는 참여 QR 코드')).toBeNull();
    expect(app.runtime.stop).not.toHaveBeenCalled(); expectQuiet();
  });
  it('loads incoming glyphs and rejects failed or cancelled preparation without a public notice', async () => {
    const load = vi.fn(async () => []);
    Object.defineProperty(document, 'fonts', { configurable: true, value: { load, ready: Promise.resolve() } });
    const app = harness(); await waitFor(() => expect(app.runtime.start).toHaveBeenCalled());
    await app.wiring.renderer.prepare(item, { signal: new AbortController().signal });
    expect(load).toHaveBeenCalledWith('400 96px "Jua"', item.content);
    expect(load).toHaveBeenCalledWith('700 96px "Noto Sans KR"', item.content);
    load.mockRejectedValueOnce(new Error('FONT_UNAVAILABLE'));
    await expect(app.wiring.renderer.prepare(item, { signal: new AbortController().signal })).rejects.toThrow('FONT_UNAVAILABLE');
    const cancelled = new AbortController(); cancelled.abort(new Error('NO_LONGER_VISIBLE'));
    await expect(app.wiring.renderer.prepare(item, { signal: cancelled.signal })).rejects.toThrow('NO_LONGER_VISIBLE'); expectQuiet();
  });
  it('keeps the runtime alive when QR conversion fails', async () => {
    const { default: QRCode } = await import('qrcode'); QRCode.toDataURL.mockRejectedValueOnce(new Error('SECRET_QR_FAILURE'));
    const app = harness(); await waitFor(() => expect(QRCode.toDataURL).toHaveBeenCalled());
    expect(app.runtime.stop).not.toHaveBeenCalled(); expect(screen.queryByText('SECRET_QR_FAILURE')).toBeNull(); expectQuiet();
  });
  it('does not restart a fifteen-second playback on same-credential session checks', async () => {
    const app = harness(); await waitFor(() => expect(app.runtime.start).toHaveBeenCalled());
    act(() => app.wiring.renderer.show(item));
    const before = app.fetcher.mock.calls.filter(([path]) => path === '/api/display/session').length;
    fireEvent.focus(window);
    await waitFor(() => expect(app.fetcher.mock.calls.filter(([path]) => path === '/api/display/session').length).toBe(before + 1));
    expect(app.factory).toHaveBeenCalledTimes(1); expect(app.runtime.stop).not.toHaveBeenCalled(); expect(screen.getByText(item.content)).toBeTruthy();
  });
  it('turns runtime fatal errors into a quiet backdrop', async () => {
    const app = harness(); await waitFor(() => expect(app.runtime.start).toHaveBeenCalled());
    act(() => app.wiring.renderer.show(item)); act(() => app.wiring.onState({ source: 'runtime', connected: false, fatal: true }));
    expect(screen.queryByText(item.content)).toBeNull(); expectQuiet();
  });
  it.each([{ mode: 'PAUSED' }, { removedIds: [item.id] }])('immediately hides an active message on authoritative control %j', async control => {
    const app = harness(); await waitFor(() => expect(app.runtime.start).toHaveBeenCalled());
    app.runtime.player = { pause: vi.fn() };
    act(() => app.wiring.renderer.show(item));
    act(() => app.wiring.onState({ source: 'snapshot', connected: true, ...control }));
    expect(screen.queryByText(item.content)).toBeNull(); expect(app.runtime.player.pause).toHaveBeenCalledWith('HIDDEN'); expectQuiet();
  });
  it('silently retries a slow-starting network at serial 5 to 30 second intervals', async () => {
    vi.useFakeTimers(); let attempts = 0;
    const app = harness(vi.fn(async path => {
      if (path !== '/api/display/session') return response(ticket);
      attempts++; if (attempts === 1) throw new TypeError('OFFLINE');
      return attempts <= 6 ? response({ code: 'UNAVAILABLE' }, 503) : response(session);
    }));
    await act(async () => {}); expectQuiet(); expect(attempts).toBe(1);
    for (const [index, delay] of [5000, 10_000, 20_000, 30_000, 30_000, 30_000].entries()) {
      await act(async () => vi.advanceTimersByTimeAsync(delay - 1)); expect(attempts).toBe(index + 1);
      await act(async () => vi.advanceTimersByTimeAsync(1)); expect(attempts).toBe(index + 2);
    }
    expect(app.runtime.start).toHaveBeenCalledTimes(1);
    expect(app.fetcher.mock.calls.some(([path]) => path === '/api/display/pair')).toBe(false); app.unmount();
  });
  it.each([401, 403])('silently stops startup retries for HTTP %s, including an HTML error', async status => {
    vi.useFakeTimers(); const app = harness(vi.fn(async () => new Response('Access denied', { status })));
    await act(async () => {}); expectQuiet(); await act(async () => vi.advanceTimersByTimeAsync(120_000));
    expect(app.fetcher).toHaveBeenCalledTimes(1); expect(app.runtime.start).not.toHaveBeenCalled(); app.unmount();
  });
  it('recovers from malformed successful proxy responses without exposing them', async () => {
    vi.useFakeTimers(); let attempts = 0;
    const app = harness(vi.fn(async path => path === '/api/display/session'
      ? (++attempts === 1 ? new Response('<html>private proxy failure</html>', { status: 200 }) : response(session)) : response(ticket)));
    await act(async () => {}); expectQuiet();
    await act(async () => vi.advanceTimersByTimeAsync(5000));
    expect(attempts).toBe(2); expect(app.runtime.start).toHaveBeenCalledTimes(1); app.unmount();
  });
  it('honors the server retry delay during startup rate limiting', async () => {
    vi.useFakeTimers(); let attempts = 0;
    const app = harness(vi.fn(async path => path === '/api/display/session'
      ? (++attempts === 1 ? new Response(JSON.stringify({ code: 'RATE_LIMITED' }), { status: 429, headers: { 'Retry-After': '60' } }) : response(session)) : response(ticket)));
    await act(async () => {}); await act(async () => vi.advanceTimersByTimeAsync(59_999));
    expect(attempts).toBe(1); expectQuiet(); await act(async () => vi.advanceTimersByTimeAsync(1)); expect(attempts).toBe(2); app.unmount();
  });
  it.each([null, {}, { ...session, boardId: 'invalid' }, { ...session, mode: 'INVALID' },
    { ...session, csrfToken: '' }, { ...session, credentialExpiresAt: 'invalid' }])('retries invalid successful session data %j', async invalid => {
    vi.useFakeTimers(); let attempts = 0;
    const app = harness(vi.fn(async path => path === '/api/display/session'
      ? response(++attempts === 1 ? invalid : session) : response(ticket)));
    await act(async () => {}); expect(app.runtime.start).not.toHaveBeenCalled(); expectQuiet();
    await act(async () => vi.advanceTimersByTimeAsync(5000));
    expect(app.runtime.start).toHaveBeenCalledTimes(1); app.unmount();
  });
  it('preserves HTML 429 retry headers across focus and visibility hints', async () => {
    vi.useFakeTimers(); let attempts = 0;
    const app = harness(vi.fn(async path => path === '/api/display/session'
      ? (++attempts === 1 ? new Response('private proxy failure', { status: 429, headers: { 'Retry-After': '60' } }) : response(session)) : response(ticket)));
    await act(async () => {});
    for (let index = 0; index < 3; index++) {
      fireEvent.focus(window); fireEvent(document, new Event('visibilitychange'));
      await act(async () => vi.advanceTimersByTimeAsync(10_000));
    }
    expect(attempts).toBe(1); await act(async () => vi.advanceTimersByTimeAsync(29_999)); expect(attempts).toBe(1);
    await act(async () => vi.advanceTimersByTimeAsync(1)); expect(attempts).toBe(2); expectQuiet(); app.unmount();
  });
  it('coalesces QR hints during an in-flight request and its Retry-After delay', async () => {
    vi.useFakeTimers(); let qrRequests = 0, finish;
    const app = harness(vi.fn(async path => {
      if (path === '/api/display/session') return response(session);
      if (path !== '/api/display/qr') return response({ accepted: true });
      qrRequests++; return qrRequests === 1 ? new Promise(resolve => { finish = resolve; }) : response(ticket);
    }));
    await act(async () => {}); expect(qrRequests).toBe(1);
    act(() => events[0].listeners.get('qr.changed')({}));
    await act(async () => finish(new Response('rate limited', { status: 429, headers: { 'Retry-After': '60' } })));
    expect(qrRequests).toBe(1);
    await act(async () => vi.advanceTimersByTimeAsync(30_000));
    act(() => events[0].listeners.get('qr.changed')({}));
    await act(async () => vi.advanceTimersByTimeAsync(29_999)); expect(qrRequests).toBe(1);
    await act(async () => vi.advanceTimersByTimeAsync(1)); expect(qrRequests).toBe(2); expectQuiet(); app.unmount();
  });
  it('ignores delayed older control responses and does not reopen a newer pause', async () => {
    const app = harness(); await waitFor(() => expect(app.runtime.start).toHaveBeenCalled());
    app.runtime.player = { pause: vi.fn() };
    const control = { connected: true, deviceGeneration: '1', serverNow: ticket.serverNow };
    act(() => app.wiring.onState({ ...control, source: 'playback', revision: '11', mode: 'MOODWALL' }));
    act(() => app.wiring.renderer.show(item));
    act(() => app.wiring.onState({ ...control, source: 'snapshot', revision: '10', mode: 'PAUSED', removedIds: [item.id] }));
    expect(screen.getByText(item.content)).toBeTruthy(); expect(app.runtime.player.pause).not.toHaveBeenCalled();
    act(() => app.wiring.onState({ ...control, source: 'snapshot', revision: '12', mode: 'PAUSED' }));
    expect(screen.queryByText(item.content)).toBeNull(); expect(app.runtime.player.pause).toHaveBeenCalledWith('HIDDEN');
    act(() => app.wiring.onState({ ...control, source: 'playback', revision: '11', mode: 'MOODWALL' }));
    expect(() => app.wiring.renderer.show(item)).toThrow('PAGE_HIDDEN'); expectQuiet();
  });
  it('cancels retries and unfinished lookups when unmounted', async () => {
    vi.useFakeTimers(); const failed = harness(vi.fn(async () => { throw new TypeError('OFFLINE'); }));
    await act(async () => {}); failed.unmount(); await act(async () => vi.advanceTimersByTimeAsync(60_000)); expect(failed.fetcher).toHaveBeenCalledTimes(1);
    let finish, requestSignal;
    const pending = harness(vi.fn((_path, options) => { requestSignal = options.signal; return new Promise(resolve => { finish = resolve; }); }));
    pending.unmount(); expect(requestSignal.aborted).toBe(true);
    await act(async () => finish(response(session))); expect(pending.runtime.start).not.toHaveBeenCalled();
  });
  it('ignores an old successful lookup after the request lifecycle changes', async () => {
    let finish, oldSignal;
    const app = harness(vi.fn((_path, options) => { oldSignal = options.signal; return new Promise(resolve => { finish = resolve; }); }));
    app.rerender(<DisplayApp runtimeFactory={app.factory} fetcher={vi.fn(async () => response({ code: 'AUTH_REQUIRED' }, 401))} />);
    expect(oldSignal.aborted).toBe(true); await act(async () => {}); await act(async () => finish(response(session)));
    expect(app.runtime.start).not.toHaveBeenCalled(); expectQuiet();
  });
});
