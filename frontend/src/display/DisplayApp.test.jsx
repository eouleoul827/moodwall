import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DisplayApp from './DisplayApp.jsx';

vi.mock('qrcode', () => ({ default: { toDataURL: vi.fn(async () => 'data:image/png;base64,cXI=') } }));

const session = { csrfToken: 'display-csrf', boardId: '00000000-0000-4000-8000-000000000001', mode: 'MOODWALL', credentialExpiresAt: '2027-01-01T00:00:00Z', serverNow: '2026-11-04T09:00:00Z' };
const ticket = { joinUrl: 'https://school.example/join#ticket=private-qr-ticket', rotatesAt: '2026-11-04T09:05:00Z', validUntil: '2026-11-04T09:10:00Z', serverNow: '2026-11-04T09:00:00Z' };
const item = { id: '00000000-0000-4000-8000-000000000010', content: '<img src=x onerror=alert(1)>', sceneId: 'joy', sceneVersion: '1', sceneSeed: 'abc', emotion: 'JOY', phase: 'FLOATING' };
const response = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
let events;
const originalFonts = Object.getOwnPropertyDescriptor(document, 'fonts');

beforeEach(() => {
  events = [];
  vi.stubGlobal('EventSource', class {
    listeners = new Map(); close = vi.fn();
    constructor(path) { this.path = path; events.push(this); }
    addEventListener(type, listener) { this.listeners.set(type, listener); }
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
  if (originalFonts) Object.defineProperty(document, 'fonts', originalFonts);
  else Reflect.deleteProperty(document, 'fonts');
});

function harness(fetchOverride) {
  let wiring;
  const runtime = { start: vi.fn(), stop: vi.fn(), resetAuthentication: vi.fn(), refresh: vi.fn(async () => ({})), cache: { clear: vi.fn() } };
  const factory = vi.fn(options => { wiring = options; return runtime; });
  const fetcher = fetchOverride || vi.fn(async path => response(path === '/api/display/session' ? session : ticket));
  const view = render(<DisplayApp runtimeFactory={factory} fetcher={fetcher} />);
  return { ...view, factory, runtime, fetcher, get wiring() { return wiring; } };
}

describe('React display integration', () => {
  it('restores a cookie session, starts the v2 runtime and renders an actual QR image', async () => {
    const app = harness();
    await screen.findByAltText('기분을 남기는 참여 QR 코드', {}, { timeout: 4000 });
    expect(app.factory).toHaveBeenCalledTimes(1);
    expect(app.wiring.boardId).toBe(session.boardId);
    expect(app.wiring.csrfToken()).toBe('display-csrf');
    expect(app.runtime.start).toHaveBeenCalledTimes(1);
    expect(app.fetcher.mock.calls[0][1].credentials).toBe('same-origin');
    expect(screen.queryByText(item.content)).toBeNull();
    expect(screen.getByText('30자 · 1시간에 3번')).toBeTruthy();
    app.unmount();
    expect(app.runtime.stop).toHaveBeenCalledTimes(1);
    expect(events[0].close).toHaveBeenCalledTimes(1);
  });

  it('commits message text synchronously, escapes HTML and hides immediately on interruption', async () => {
    const app = harness();
    await waitFor(() => expect(app.runtime.start).toHaveBeenCalled());
    await app.wiring.renderer.prepare(item, { signal: new AbortController().signal });
    act(() => app.wiring.renderer.show(item));
    const stage = screen.getByRole('region', { name: '새로운 메시지' });
    expect(stage.hidden).toBe(false);
    expect(stage.querySelector('.mw-full-message').textContent).toBe(item.content);
    expect(stage.querySelector('img[src="x"]')).toBeNull();
    act(() => app.wiring.renderer.hide());
    expect(stage.hidden).toBe(true);
    await act(async () => {
      await expect(app.wiring.renderer.prepare({ ...item, sceneId: 'https://evil.invalid/scene' }, { signal: new AbortController().signal })).rejects.toThrow('UNSUPPORTED_SCENE');
    });
  });

  it('uses completed bubble snapshots, retains ambient text and deduplicates frame updates', async () => {
    const app = harness();
    await waitFor(() => expect(app.runtime.start).toHaveBeenCalled());
    const bubble = { ...item, content: '오늘도 수고했어' };
    act(() => app.wiring.renderBubbles({ visible: true, bubbles: [bubble], totalCount: 1 }));
    expect(screen.getByText(bubble.content)).toBeTruthy();
    act(() => app.wiring.renderBubbles({ visible: true, bubbles: [{ ...bubble, phase: 'AMBIENT' }], totalCount: 1 }));
    expect(screen.getByText(bubble.content).closest('.mw-bubble-track').classList.contains('is-ambient')).toBe(true);
    act(() => app.wiring.renderBubbles({ visible: false, bubbles: [], totalCount: 0 }));
    expect(screen.queryByText(bubble.content)).toBeNull();
  });

  it('treats SSE as resynchronization hints and observes mode and authentication failures', async () => {
    const app = harness();
    await waitFor(() => expect(app.runtime.start).toHaveBeenCalled());
    act(() => events[0].listeners.get('state.changed')({ data: '{}' }));
    expect(app.runtime.refresh).toHaveBeenCalledTimes(1);
    const before = app.fetcher.mock.calls.length;
    act(() => events[0].listeners.get('qr.changed')({ data: '{}' }));
    await waitFor(() => expect(app.fetcher.mock.calls.length).toBeGreaterThan(before));
    await act(async () => app.wiring.onProblem({ status: 401, code: 'AUTH_REQUIRED' }));
    expect(await screen.findByLabelText('전광판 등록 코드')).toBeTruthy();
    expect(app.runtime.resetAuthentication).toHaveBeenCalledTimes(1);
    expect(app.runtime.stop).toHaveBeenCalledTimes(1);
  });

  it('pairs a new device with only its one-time code and does not invent a logout endpoint', async () => {
    const fetcher = vi.fn(async (path, options) => {
      if (path === '/api/display/session') return response({ code: 'AUTH_REQUIRED' }, 401);
      if (path === '/api/display/pair') return response(session);
      return response(ticket);
    });
    const app = harness(fetcher);
    const input = await screen.findByLabelText('전광판 등록 코드');
    fireEvent.change(input, { target: { value: 'one-time-pairing-code' } });
    fireEvent.click(screen.getByRole('button', { name: '전광판 연결하기' }));
    await waitFor(() => expect(app.runtime.start).toHaveBeenCalled());
    const pairCall = fetcher.mock.calls.find(([path]) => path === '/api/display/pair');
    expect(JSON.parse(pairCall[1].body)).toEqual({ code: 'one-time-pairing-code' });
    expect(pairCall[1].credentials).toBe('same-origin');
    expect(pairCall[1].headers['X-CSRF-Token']).toBeUndefined();
    expect(fetcher.mock.calls.some(([path]) => String(path).includes('logout'))).toBe(false);
  });

  it('retries the same unconsumed pairing code after the server clears an expired cookie', async () => {
    let pairCalls = 0;
    const fetcher = vi.fn(async path => {
      if (path === '/api/display/session') return response({ code: 'SESSION_EXPIRED' }, 401);
      if (path === '/api/display/pair') return ++pairCalls === 1 ? response({ code: 'SESSION_EXPIRED' }, 401) : response(session);
      return response(ticket);
    });
    const app = harness(fetcher);
    fireEvent.change(await screen.findByLabelText('전광판 등록 코드'), { target: { value: 'same-unconsumed-code' } });
    fireEvent.click(screen.getByRole('button', { name: '전광판 연결하기' }));
    await waitFor(() => expect(app.runtime.start).toHaveBeenCalled());
    const pairs = fetcher.mock.calls.filter(([path]) => path === '/api/display/pair');
    expect(pairs).toHaveLength(2);
    expect(pairs.map(([, options]) => options.body)).toEqual(['{"code":"same-unconsumed-code"}', '{"code":"same-unconsumed-code"}']);
  });

  it('loads the incoming message glyphs before playback and rejects failed font preparation', async () => {
    const load = vi.fn(async () => []);
    Object.defineProperty(document, 'fonts', { configurable: true, value: { load, ready: Promise.resolve() } });
    const app = harness();
    await waitFor(() => expect(app.runtime.start).toHaveBeenCalled());
    await app.wiring.renderer.prepare(item, { signal: new AbortController().signal });
    expect(load).toHaveBeenCalledWith('400 96px "Jua"', item.content);
    load.mockRejectedValueOnce(new Error('FONT_UNAVAILABLE'));
    await expect(app.wiring.renderer.prepare(item, { signal: new AbortController().signal })).rejects.toThrow('FONT_UNAVAILABLE');
    expect(screen.queryByText(item.content)).toBeNull();
    const controller = new AbortController(); controller.abort(new Error('NO_LONGER_VISIBLE'));
    const callsBefore = load.mock.calls.length;
    await expect(app.wiring.renderer.prepare(item, { signal: controller.signal })).rejects.toThrow('NO_LONGER_VISIBLE');
    expect(load.mock.calls).toHaveLength(callsBefore);
  });

  it('links ambient bubble moderation by UUID only without putting message text into a URL', async () => {
    const app = harness();
    await waitFor(() => expect(app.runtime.start).toHaveBeenCalled());
    act(() => app.wiring.renderBubbles({ visible: true, bubbles: [{ ...item, phase: 'AMBIENT' }], totalCount: 1 }));
    fireEvent.click(screen.getByRole('button', { name: '전광판 연결 설정' }));
    const link = screen.getByRole('link', { name: /숨김 관리/ });
    expect(link.getAttribute('href')).toBe(`/operator#hide=${item.id}`);
    expect(link.getAttribute('href')).not.toContain('img');
    expect(link.getAttribute('rel')).toBe('noreferrer');
    expect(link.getAttribute('target')).toBe('_blank');
    expect(link.textContent).toContain(item.content);
    expect(link.querySelector('img')).toBeNull();
  });
});
