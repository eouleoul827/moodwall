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
  csrfToken, fetch = globalThis.fetch, storage, onProblem = () => {} }) {
  if (typeof renderBubbles !== "function") throw new TypeError("A bubble renderer is required");
  if (storage === undefined) {
    try { storage = globalThis.localStorage; } catch { storage = null; }
  }
  const cache = new PlaybackBubbleCache({ boardId, storage });
  const transport = new FetchPlaybackTransport({ fetch, csrfToken });
  let closed = false, task = null, wake = null, frame = null, inFlightSync = null;
  const player = new DisplayPlaybackController({ transport, renderer, cache,
    onState: state => { if (state.mode !== "MOODWALL") cache.disconnect(); } });
  const synchronizer = new PlaybackBubbleSynchronizer({ cache, fetchPage: async ({ cursor, signal }) => {
    const response = await fetch(`/api/v2/display/state${cursor === null ? "" : `?cursor=${encodeURIComponent(cursor)}`}`, {
      credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" }, signal,
    });
    const result = await response.json();
    if (!response.ok) throw Object.assign(new Error(result.code || "DISPLAY_SYNC_FAILED"), {
      status: response.status, code: result.code,
    });
    return result;
  } });
  const refresh = () => {
    if (closed) return Promise.resolve({ completed: false, pages: 0 });
    if (!inFlightSync) inFlightSync = synchronizer.synchronize().catch(error => {
      if ([401, 403].includes(error?.status)) player.resetAuthentication();
      onProblem(error);
      return { completed: false, pages: 0 };
    }).finally(() => { inFlightSync = null; });
    return inFlightSync;
  };
  let unbind = null;
  const loop = async () => {
    let lastSync = -Infinity;
    while (!closed) {
      if (document.visibilityState === "visible") {
        player.resume();
        if (performance.now() - lastSync >= 5000) { lastSync = performance.now(); void refresh(); }
        const outcome = await player.runOnce();
        if (outcome.status === "DONE") void refresh();
      }
      if (!closed) await new Promise(resolve => { wake = resolve; task = setTimeout(resolve, 1000); });
    }
  };
  const draw = () => {
    if (closed) return;
    renderBubbles(cache.view(performance.now()));
    frame = requestAnimationFrame(draw);
  };
  return {
    player, cache,
    refresh, // Call after a display SSE change event too; paging stays serialized.
    start() {
      if (unbind || closed) return;
      unbind = bindPlaybackVisibility(player);
      frame = requestAnimationFrame(draw);
      void loop().catch(onProblem);
    },
    resetAuthentication() {
      player.resetAuthentication(); synchronizer.resetAuthentication();
      renderBubbles({ visible: false, bubbles: [], totalCount: 0 });
    },
    stop() {
      closed = true;
      clearTimeout(task);
      wake?.();
      cancelAnimationFrame(frame);
      unbind?.();
      player.pause("RELOAD");
      synchronizer.disconnect();
      renderBubbles({ visible: false, bubbles: [], totalCount: 0 });
    },
  };
}
