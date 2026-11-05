import { displayRequest } from './display-session.js';

const CODES = new Set(['CONNECTION_FAILED', 'AUTH_REQUIRED', 'QR_UNAVAILABLE', 'PLAYBACK_OWNED', 'PLAYBACK_INTERRUPTED', 'PLAYBACK_UNKNOWN', 'ASSET_FAILURE', 'STORAGE_UNAVAILABLE', 'RENDER_FAILURE']);
const COMPONENTS = new Set(['SESSION', 'QR', 'PLAYBACK', 'CACHE', 'RENDER']);
const MINUTE = 60_000;

/** Bounded, memory-only diagnostics. A report can never delay a playback command. */
export function createDisplayDiagnostics({ fetcher = fetch, csrfToken = () => '', now = () => Date.now(), schedule = setTimeout, cancel = clearTimeout } = {}) {
  const queue = new Map(), sent = new Map(), states = new Map();
  let timer = null, request = null, busy = false, closed = false, blocked = false, lastSend = -Infinity, retryAt = 0;
  const arrange = delay => { if (!closed && !blocked && timer === null && queue.size) timer = schedule(() => { timer = null; void flush(); }, delay); };
  async function flush() {
    if (closed || blocked || busy || !csrfToken()) return;
    const time = now();
    if (time < retryAt) { arrange(retryAt - time); return; }
    for (const [key, value] of queue) if (time - value.created >= 10 * MINUTE) queue.delete(key);
    const entry = [...queue].find(([key]) => time - (sent.get(key) ?? -Infinity) >= MINUTE);
    if (!entry) { arrange(MINUTE); return; }
    if (time - lastSend < 6000) { arrange(6000 - (time - lastSend)); return; }
    const [key, pending] = entry; queue.delete(key); busy = true; lastSend = time;
    const controller = new AbortController(); request = controller;
    try {
      await displayRequest('/api/display/diagnostics', { fetcher, csrfToken: csrfToken(), body: pending.body, signal: controller.signal });
      sent.set(key, time);
    } catch (error) {
      if (closed) return;
      if (error.status === 429) retryAt = now() + Math.max(MINUTE, error.retryAfterMs || MINUTE);
      if ([401, 403].includes(error.status)) { blocked = true; queue.clear(); }
      else if (![404, 422].includes(error.status) && !queue.has(key) && now() - pending.created < 10 * MINUTE) queue.set(key, pending);
      // An older server may not have this optional endpoint. Never surface it on the wall.
    } finally { busy = false; request = null; arrange(MINUTE); }
  }
  return {
    record(component, code, state = 'FAILED') {
      if (closed || blocked || !COMPONENTS.has(component) || !CODES.has(code) || !['FAILED', 'RECOVERED'].includes(state)) return;
      const key = `${component}:${code}`;
      if (state === 'RECOVERED' && states.get(key) === state) return;
      states.set(key, state);
      const previous = queue.get(key), time = now();
      if (!previous && queue.size >= 20) queue.delete(queue.keys().next().value);
      queue.set(key, { created: previous?.body.state === state ? previous.created : time, body: { component, code, state, version: '1',
        count: Math.min(1000, (previous?.body.count || 0) + 1), occurredAt: new Date(time).toISOString() } });
      arrange(0);
    },
    resume() { blocked = false; arrange(0); },
    stop() { closed = true; request?.abort(); cancel(timer); timer = null; queue.clear(); },
  };
}

export async function reportDisplayRenderFailure() {
  // Boundary reporting survives the failed child's unmount, using the existing cookie only.
  try {
    const session = await displayRequest('/api/display/session');
    await displayRequest('/api/display/diagnostics', { csrfToken: session.csrfToken, body: {
      component: 'RENDER', code: 'RENDER_FAILURE', state: 'FAILED', version: '1', count: 1, occurredAt: new Date().toISOString(),
    } });
  } catch { /* The public fallback never depends on telemetry being available. */ }
}
