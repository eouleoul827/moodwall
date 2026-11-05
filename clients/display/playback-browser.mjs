/**
 * Browser wiring for the team's React renderer. Serve/import over the app origin.
 * No API token, board secret, message HTML or arbitrary model URL belongs here.
 */
import { DisplayPlaybackController, FetchPlaybackTransport, bindPlaybackVisibility } from "./playback-controller.mjs";
import { PlaybackBubbleCache, PlaybackBubbleSynchronizer } from "./playback-bubble-cache.mjs";

/**
 * renderer.prepare awaits document.fonts.ready and permitted image decode promises.
 * renderer.show commits the prepared DOM using textContent (or React text nodes).
 * renderBubbles renders only cache.view() output, including phase FLOATING/AMBIENT.
 * onProblem reports an operator connection problem, never a false success message.
 */
export function createMoodwallDisplayRuntime({ boardId, renderer, renderBubbles,
  csrfToken, fetch = globalThis.fetch, storage, onProblem = () => {}, onState = () => {} }) {
  if (typeof renderBubbles !== "function") throw new TypeError("A bubble renderer is required");
  if (storage === undefined) {
    try { storage = globalThis.localStorage; } catch { storage = null; }
  }
  const cache = new PlaybackBubbleCache({ boardId, storage });
  const transport = new FetchPlaybackTransport({ fetch, csrfToken });
  let closed = false, authInvalid = false, task = null, wake = null, frame = null, inFlightSync = null;
  let unbind = null, lastView = null;
  const report = (error, source) => {
    // Reporting is optional; a failed diagnostics endpoint/callback must never
    // disable authentication cleanup or restart a confirmed presentation.
    const problem = Object.assign(new Error(error?.message || "DISPLAY_RUNTIME_FAILED"), error,
      { source: error?.source ?? source });
    try { onProblem(problem)?.catch?.(() => {}); } catch { /* diagnostic only */ }
  };
  const notify = state => {
    if (closed) return;
    try { onState(state); } catch (error) { fatal(error); }
  };
  const leaseProof = (lease, estimatedServerNow, monotonicNow) => {
    const remaining = Number.isFinite(lease) ? Math.max(0, lease - estimatedServerNow) : 0;
    return { leaseRemainingMs: remaining, monotonicLeaseUntil: monotonicNow + remaining };
  };
  const player = new DisplayPlaybackController({ transport, renderer, cache,
    onProblem: error => report(error, "playback"), onState: state => {
      if (state.mode !== "MOODWALL") cache.disconnect();
      const authority = player.authority;
      const monotonicNow = performance.now();
      const estimatedNow = authority.now + Math.max(0, monotonicNow - authority.anchor);
      notify({ ...state, ...leaseProof(state.leaseUntil === null ? NaN : Date.parse(state.leaseUntil), estimatedNow, monotonicNow),
        source: "playback", connected: true, storageAvailable: cache.storageAvailable });
    } });
  const synchronizer = new PlaybackBubbleSynchronizer({ cache, onSnapshot: snapshot => {
    const { bubbles: _bubbles, ...metadata } = snapshot;
    const monotonicNow = performance.now();
    const estimatedNow = cache.scope.now + Math.max(0, monotonicNow - cache.anchor);
    notify({ ...metadata, ...leaseProof(cache.lease, estimatedNow, monotonicNow), source: "snapshot", connected: true,
      storageAvailable: cache.storageAvailable });
  }, fetchPage: async ({ cursor, signal }) => {
    const response = await fetch(`/api/v2/display/state${cursor === null ? "" : `?cursor=${encodeURIComponent(cursor)}`}`, {
      credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" }, signal,
    });
    let result;
    try { result = await response.json(); }
    catch { throw Object.assign(new Error("DISPLAY_SYNC_FAILED"), { status: response.status }); }
    if (!response.ok) throw Object.assign(new Error(result.code || "DISPLAY_SYNC_FAILED"), {
      status: response.status, code: result.code,
    });
    return result;
  } });
  const refresh = () => {
    if (closed) return Promise.resolve({ completed: false, pages: 0 });
    if (!inFlightSync) inFlightSync = synchronizer.synchronize().catch(error => {
      if (closed) return { completed: false, pages: 0 };
      if ([401, 403].includes(error?.status)) {
        authInvalid = true;
        try { player.resetAuthentication(); } catch (problem) { fatal(problem); }
      }
      notify({ source: "snapshot", connected: false });
      report(error, "snapshot");
      return { completed: false, pages: 0 };
    }).finally(() => { inFlightSync = null; });
    return inFlightSync;
  };
  const loop = async () => {
    let lastSync = -Infinity;
    while (!closed) {
      if (!authInvalid && document.visibilityState === "visible") {
        player.resume();
        if (performance.now() - lastSync >= 5000) { lastSync = performance.now(); void refresh(); }
        const outcome = await player.runOnce();
        if (outcome.status === "DONE") void refresh();
        else if (outcome.code && ["INTERRUPTED", "UNKNOWN"].includes(outcome.status)) {
          report(Object.assign(new Error(outcome.code), { code: outcome.code, outcome: outcome.status }), "playback");
        }
      }
      if (!closed) await new Promise(resolve => { wake = resolve; task = setTimeout(resolve, 1000); });
    }
  };
  const draw = () => {
    if (closed) return;
    try {
      const view = cache.view(performance.now());
      if (view !== lastView) { lastView = view; renderBubbles(view); }
      if (!closed) frame = requestAnimationFrame(draw);
    } catch (error) { fatal(error); }
  };
  function stop() {
    if (closed) return;
    closed = true;
    clearTimeout(task); wake?.(); cancelAnimationFrame(frame);
    // Each cleanup runs even if a broken renderer throws during hide().
    for (const cleanup of [() => unbind?.(), () => player.pause("RELOAD"),
      () => synchronizer.disconnect(), () => renderBubbles({ visible: false, bubbles: [], totalCount: 0 })]) {
      try { cleanup(); } catch { /* continue bounded cleanup */ }
    }
  }
  function fatal(error) {
    if (closed) return;
    stop();
    try { onState({ source: "runtime", connected: false, fatal: true }); } catch { /* diagnostic only */ }
    report(Object.assign(new Error("DISPLAY_RUNTIME_FAILED"), { code: "DISPLAY_RUNTIME_FAILED", fatal: true,
      cause: error }), "runtime");
  }
  return {
    player, cache,
    refresh, // Call after a display SSE change event too; paging stays serialized.
    start() {
      if (unbind || closed) return;
      unbind = bindPlaybackVisibility(player);
      frame = requestAnimationFrame(draw);
      void loop().catch(fatal);
    },
    resetAuthentication() {
      authInvalid = true;
      let problem;
      for (const cleanup of [() => player.resetAuthentication(), () => synchronizer.resetAuthentication(),
        () => renderBubbles({ visible: false, bubbles: [], totalCount: 0 })]) {
        try { cleanup(); } catch (error) { problem ??= error; }
      }
      if (problem) fatal(problem);
    },
    stop,
  };
}
