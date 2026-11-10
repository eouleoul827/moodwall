import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useDisplaySession } from './display-session.js';

const session = { csrfToken: 'display-csrf', boardId: '00000000-0000-4000-8000-000000000001',
  mode: 'MOODWALL', credentialExpiresAt: '2027-01-01T00:00:00Z', serverNow: '2026-11-10T00:00:00Z' };
const response = body => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });

describe('display session metadata and runtime identity', () => {
  it('publishes renewed metadata without replacing the runtime startup session', async () => {
    let value = session;
    const fetcher = vi.fn(async () => response(value));
    const hook = renderHook(() => useDisplaySession(fetcher));
    await waitFor(() => expect(hook.result.current.boot).toBe('READY'));
    const startup = hook.result.current.runtimeSession;

    value = { ...session, credentialExpiresAt: '2027-06-30T00:00:00Z', serverNow: '2026-11-10T00:05:00Z' };
    await act(async () => { await hook.result.current.check(); });
    expect(hook.result.current.session).toEqual(value);
    expect(hook.result.current.runtimeSession).toBe(startup);

    // A mode refresh also reaches staff views, while live playback follows its
    // revision-ordered control responses instead of a potentially older lookup.
    value = { ...value, mode: 'PAUSED' };
    await act(async () => { await hook.result.current.check(); });
    expect(hook.result.current.session.mode).toBe('PAUSED');
    expect(hook.result.current.runtimeSession).toBe(startup);
    expect(hook.result.current.runtimeSession.mode).toBe('MOODWALL');
  });

  it('clears both views on invalidation and starts a new identity even after accepting the same values', async () => {
    const fetcher = vi.fn(async () => response(session));
    const hook = renderHook(() => useDisplaySession(fetcher));
    await waitFor(() => expect(hook.result.current.boot).toBe('READY'));
    const startup = hook.result.current.runtimeSession;
    act(() => hook.result.current.invalidate());
    expect(hook.result.current.session).toBeNull();
    expect(hook.result.current.runtimeSession).toBeNull();
    expect(hook.result.current.csrfRef.current).toBe('');
    act(() => hook.result.current.accept({ ...session }));
    expect(hook.result.current.session).toEqual(session);
    expect(hook.result.current.runtimeSession).not.toBe(startup);
    expect(hook.result.current.csrfRef.current).toBe(session.csrfToken);
  });
});
