import test from "node:test";
import assert from "node:assert/strict";
import { createMoodwallDisplayRuntime } from "./playback-browser.mjs";

const BOARD = "11111111-1111-4111-8111-111111111111";
const ITEM = "22222222-2222-4222-8222-222222222222";
const ATTEMPT = "33333333-3333-4333-8333-333333333333";
const BASE = Date.parse("2026-11-04T03:00:00Z");
const iso = value => new Date(BASE + value).toISOString();
const flush = async () => { for (let index = 0; index < 30; index++) await Promise.resolve(); };
function fixture(t, options = {}) {
  let now = 0, sequence = 0, owner = null, active = null;
  const timers = new Map(), frames = new Map(), originals = new Map();
  function replace(key, value) {
    originals.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  }
  const target = () => ({ addEventListener() {}, removeEventListener() {} });
  replace("document", { ...target(), visibilityState: "visible" }); replace("window", target());
  replace("performance", { now: () => now });
  replace("setTimeout", (fn, delay) => { const id = ++sequence; timers.set(id, { fn, at: now + delay }); return id; });
  replace("clearTimeout", id => timers.delete(id));
  replace("requestAnimationFrame", fn => { const id = ++sequence; frames.set(id, fn); return id; });
  replace("cancelAnimationFrame", id => frames.delete(id));
  const states = [], problems = [], views = [], calls = [];
  const snapshot = () => ({ displayProtocolVersion: "2", boardId: BOARD, businessDate: "2026-11-04",
    deviceGeneration: "1", cacheResetToken: "0", revision: "1", serverNow: iso(0),
    snapshotAt: iso(0), ambientUntil: "2026-11-04T15:00:00Z", onlineLeaseUntil: iso(30_000),
    mode: "MOODWALL", bubbles: [], removedIds: [], nextCursor: null });
  const envelope = () => ({ displayProtocolVersion: "2", boardId: BOARD, deviceGeneration: "1",
    playbackEpoch: active ? "1" : "0", clientInstanceId: owner, leaseUntil: owner ? iso(30_000) : null,
    serverNow: iso(now), revision: "1", mode: "MOODWALL", attempt: active });
  const response = (body, status = 200) => new Response(JSON.stringify(body), { status,
    headers: { "Content-Type": "application/json" } });
  const fetcher = async (path, request = {}) => {
    calls.push({ path, request });
    if (options.fetcher) return options.fetcher(path, request, { snapshot, response, advance: value => { now += value; } });
    if (path.startsWith("/api/v2/display/state")) return response(snapshot());
    if (path === "/api/display/heartbeat") owner = JSON.parse(request.body).clientInstanceId;
    if (path === "/api/display/playback/claim") active = {
      attemptId: ATTEMPT, submissionId: ITEM, status: "CLAIMED", leaseUntil: iso(10_000),
      item: { id: ITEM, content: "오늘 행복해", displayProtocolVersion: "2", readySequence: "1",
        sceneId: "joy", sceneVersion: "1", ambientUntil: "2026-11-04T15:00:00Z",
        firstStartAuthorizedAt: null, contentDeleteAt: null },
    };
    if (path.endsWith("/interrupt")) active.status = "INTERRUPTED";
    return response(envelope());
  };
  const runtime = createMoodwallDisplayRuntime({ boardId: BOARD, storage: null, csrfToken: () => "csrf",
    fetch: fetcher, renderer: { prepare: options.prepare ?? (async () => { throw new Error("FONT_UNAVAILABLE"); }),
      show() {}, hide: options.hide ?? (() => {}) },
    renderBubbles: view => { views.push(view); options.renderBubbles?.(view); },
    onState: state => { states.push(state); options.onState?.(state); },
    onProblem: problem => { problems.push(problem); return options.onProblem?.(problem); } });
  t.after(async () => {
    runtime.stop(); await flush();
    for (const [key, descriptor] of originals) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor); else Reflect.deleteProperty(globalThis, key);
    }
  });
  return { runtime, states, problems, views, calls, frames, timers,
    async draw() { const first = frames.entries().next().value; if (first) { frames.delete(first[0]); first[1](); } await flush(); },
    async tick(ms) { now += ms; for (const [id, timer] of [...timers]) if (timer.at <= now) { timers.delete(id); timer.fn(); } await flush(); } };
}

test("runtime delivers interrupted preparation diagnostics without failing the control loop", async t => {
  const app = fixture(t, { onProblem() { throw new Error("DIAGNOSTICS_UNAVAILABLE"); } });
  app.runtime.start(); await app.runtime.player.running; await flush();
  assert.ok(app.problems.some(problem => problem.code === "ASSET_FAILURE" && problem.source === "playback"));
  assert.equal(app.runtime.player.status, "INTERRUPTED");
  assert.ok(app.states.some(state => state.source === "snapshot" && state.connected));
  assert.ok(app.states.some(state => state.source === "playback" && state.connected));
  assert.ok(!app.problems.some(problem => problem.fatal));
});

test("snapshot callbacks expose conservative lease proof including request latency", async t => {
  const app = fixture(t, { fetcher: async (_path, _request, context) => {
    context.advance(1200);
    return context.response({ ...context.snapshot(), onlineLeaseUntil: iso(1000) });
  } });
  assert.equal((await app.runtime.refresh()).completed, true);
  const state = app.states.at(-1);
  assert.equal(state.leaseRemainingMs, 0); assert.equal(state.monotonicLeaseUntil, 1200);
  assert.equal("bubbles" in state, false);
  assert.equal(app.runtime.cache.view(1200).visible, false);
});

test("non-JSON authentication failures clear retained state and do not get retried as connectivity", async t => {
  const app = fixture(t, { fetcher: async () => new Response("Access denied", { status: 401 }) });
  app.runtime.start(); await flush();
  assert.equal(app.runtime.cache.scope, null);
  assert.ok(app.problems.some(problem => problem.status === 401 && problem.source === "snapshot"));
  const before = app.calls.length;
  await app.tick(1000);
  assert.equal(app.calls.length, before);
});

test("a fatal RAF rendering error stops all runtime work and can be reported quietly", async t => {
  const app = fixture(t, { prepare: () => new Promise(() => {}),
    renderBubbles() { throw new Error("BROKEN_RENDERER"); } });
  app.runtime.start(); await flush();
  await app.draw(); await flush();
  assert.ok(app.problems.some(problem => problem.code === "DISPLAY_RUNTIME_FAILED" && problem.fatal && problem.source === "runtime"));
  assert.ok(app.states.some(state => state.source === "runtime" && state.fatal));
  assert.equal(app.runtime.player.stopped, true);
  assert.equal(app.frames.size, 0);
  assert.equal((await app.runtime.refresh()).completed, false);
  app.runtime.stop(); // repeated cleanup is safe even when renderBubbles always throws
});

test("render-loop frames reuse stable cache data and cleanup aborts unfinished preparation", async t => {
  const app = fixture(t, { prepare: () => new Promise(() => {}) });
  app.runtime.start(); await flush();
  await app.draw(); const before = app.views.length;
  for (let index = 0; index < 60; index++) await app.draw();
  assert.equal(app.views.length, before);
  const pending = app.runtime.player.running;
  app.runtime.stop(); await pending; await flush();
  assert.equal(app.frames.size, 0); assert.equal(app.timers.size, 0);
  assert.equal(app.views.at(-1).visible, false);
});
