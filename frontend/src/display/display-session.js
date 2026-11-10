import { useCallback, useEffect, useRef, useState } from 'react';

// Only 401 means the device credential is gone. 403 is CSRF expiry, an origin
// mismatch or a school web-filter page: retry it instead of blanking forever.
export const AUTH_ERRORS = new Set([401]);
export const SESSION_CHANNEL = 'moodwall-display-session';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function checkedSession(value) {
  if (!value || typeof value !== 'object' || !UUID.test(value.boardId) ||
      typeof value.csrfToken !== 'string' || !value.csrfToken.length || value.csrfToken.length > 4096 ||
      !['MOODWALL', 'CHATBOT', 'PAUSED'].includes(value.mode) ||
      typeof value.credentialExpiresAt !== 'string' || !Number.isFinite(Date.parse(value.credentialExpiresAt))) {
    throw Object.assign(new Error('INVALID_DISPLAY_RESPONSE'), { code: 'INVALID_DISPLAY_RESPONSE' });
  }
  return value;
}

export async function displayRequest(path, { signal, body, csrfToken, fetcher = fetch } = {}) {
  const controller = new AbortController();
  const cancel = () => controller.abort(signal.reason);
  signal?.addEventListener('abort', cancel, { once: true });
  if (signal?.aborted) cancel();
  const timer = setTimeout(() => controller.abort(new Error('DISPLAY_REQUEST_TIMEOUT')), 8000);
  try {
    const response = await fetcher(path, {
      method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
      headers: { Accept: 'application/json', ...(body === undefined ? {} : {
        'Content-Type': 'application/json', ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
      }) }, ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const header = response.headers.get('Retry-After');
    const seconds = header !== null && /^\d+$/.test(header) ? Number(header) * 1000 : Date.parse(header) - Date.now();
    const retryAfterMs = Number.isFinite(seconds) ? Math.max(0, seconds) : null;
    let value;
    try { value = await response.json(); }
    catch (error) { throw Object.assign(error, { status: response.ok ? undefined : response.status, code: 'INVALID_DISPLAY_RESPONSE', retryAfterMs }); }
    if (!response.ok) {
      throw Object.assign(new Error(value?.code || 'DISPLAY_REQUEST_FAILED'), {
        status: response.status, code: value?.code, retryAfterMs,
      });
    }
    return ['/api/display/session', '/api/display/pair'].includes(path) ? checkedSession(value) : value;
  } finally {
    clearTimeout(timer); signal?.removeEventListener('abort', cancel);
  }
}

function sameCredential(a, b) {
  return a && b && a.boardId === b.boardId && a.csrfToken === b.csrfToken;
}

/** Serial GET recovery. Keep current metadata separate from the runtime's startup session. */
export function useDisplaySession(fetcher = fetch) {
  const [{ session, runtimeSession }, setSessions] = useState({ session: null, runtimeSession: null });
  const [boot, setBoot] = useState('LOADING');
  const [problem, setProblem] = useState(''), [busy, setBusy] = useState(false);
  const actions = useRef(null), csrfRef = useRef('');
  const check = useCallback(() => actions.current?.check(), []);
  const invalidate = useCallback(() => actions.current?.invalidate(), []);
  const accept = useCallback(value => actions.current?.accept(value), []);
  useEffect(() => {
    let active = true, pending = false, timer = null, request = null, epoch = 0, retry = 5000, notBefore = 0;
    const apply = value => {
      csrfRef.current = value.csrfToken;
      // Renewal changes expiry and server metadata, but not the device identity.
      // Staff views receive fresh metadata without interrupting a live playback.
      setSessions(previous => ({ session: value,
        runtimeSession: sameCredential(previous.runtimeSession, value) ? previous.runtimeSession : value }));
      setBoot('READY'); setProblem(''); retry = 5000; notBefore = 0;
    };
    const invalidateSession = () => {
      epoch++; request?.abort(); clearTimeout(timer); csrfRef.current = ''; notBefore = 0;
      setSessions({ session: null, runtimeSession: null }); setBoot('PAIR'); setProblem('');
    };
    const verify = async () => {
      if (!active || pending) return;
      clearTimeout(timer); timer = null; pending = true; setBusy(true);
      if (performance.now() < notBefore) {
        pending = false; setBusy(false); timer = setTimeout(verify, notBefore - performance.now()); return;
      }
      const current = epoch, controller = new AbortController(); request = controller;
      try {
        const value = await displayRequest('/api/display/session', { signal: controller.signal, fetcher });
        if (active && current === epoch && !controller.signal.aborted) apply(value);
      } catch (error) {
        if (!active || current !== epoch || controller.signal.aborted) return;
        if (AUTH_ERRORS.has(error.status)) invalidateSession();
        else {
          setBoot('ERROR'); setProblem('연결을 확인한 뒤 다시 시도해 주세요.');
          if (error.status === undefined || error.status === 403 || error.status === 429 || error.status >= 500) {
            const delay = error.status === 429 ? Math.max(retry, error.retryAfterMs || retry) : retry;
            if (error.status === 429) notBefore = performance.now() + delay;
            timer = setTimeout(verify, delay);
            retry = Math.min(retry * 2, 30_000);
          }
        }
      } finally { pending = false; request = null; if (active) setBusy(false); }
    };
    const acceptSession = value => { epoch++; request?.abort(); clearTimeout(timer); apply(value); };
    actions.current = { check: verify, invalidate: invalidateSession, accept: acceptSession };
    setBoot('LOADING'); setProblem(''); void verify();
    const wake = () => { if (document.visibilityState === 'visible') void verify(); };
    let channel;
    try { if (typeof BroadcastChannel !== 'undefined') { channel = new BroadcastChannel(SESSION_CHANNEL); channel.onmessage = event => { if (event.data === 'recheck') wake(); }; } } catch { /* Explicit reopening still recovers the cookie. */ }
    window.addEventListener('focus', wake); document.addEventListener('visibilitychange', wake);
    return () => {
      active = false; epoch++; request?.abort(); clearTimeout(timer); channel?.close(); actions.current = null;
      window.removeEventListener('focus', wake); document.removeEventListener('visibilitychange', wake);
    };
  }, [fetcher]);
  return { session, runtimeSession, boot, problem, busy, csrfRef, check, invalidate, accept };
}

export function notifyDisplaySession() {
  try { const channel = new BroadcastChannel(SESSION_CHANNEL); channel.postMessage('recheck'); channel.close(); } catch { /* Same-profile reload is always available. */ }
}

export async function pairDisplay(code, fetcher = fetch) {
  let csrfToken = '';
  try { csrfToken = (await displayRequest('/api/display/session', { fetcher })).csrfToken; }
  catch (error) { if (error.status !== 401) throw error; }
  const pair = () => displayRequest('/api/display/pair', { body: { code }, csrfToken, fetcher });
  try {
    let value;
    try { value = await pair(); }
    catch (error) {
      if (error.status === 401 && error.code === 'SESSION_EXPIRED') { csrfToken = ''; value = await pair(); }
      else if (error.status === 403 && error.code === 'CSRF_REJECTED') {
        csrfToken = (await displayRequest('/api/display/session', { fetcher })).csrfToken; value = await pair();
      } else throw error;
    }
    return value;
  } catch (error) {
    if (!error.status || error.status >= 500) {
      try { return await displayRequest('/api/display/session', { fetcher }); } catch { /* Preserve original failure. */ }
    }
    throw error;
  }
}
