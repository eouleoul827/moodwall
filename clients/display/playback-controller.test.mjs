import test from "node:test";
import assert from "node:assert/strict";
import { DisplayPlaybackController, FetchPlaybackTransport } from "./playback-controller.mjs";
import { PlaybackBubbleCache } from "./playback-bubble-cache.mjs";

const BOARD = "11111111-1111-4111-8111-111111111111";
const ITEM = "22222222-2222-4222-8222-222222222222";
const ATTEMPT = "33333333-3333-4333-8333-333333333333";
const BASE = Date.parse("2026-11-04T03:00:00Z");
const iso = ms => new Date(ms).toISOString();
const error = (code, status = 0) => Object.assign(new Error(code), { code, status });

function harness(options = {}) {
  let now = options.initialNow ?? 0, nextTimer = 0, sequence = 0, owner = null, epoch = "0", active = null;
  const calls = [], draws = [], timers = new Map();
  const clock = {
    now: () => now,
    sleep: async (ms, signal) => { if (signal?.aborted) throw signal.reason; advance(ms); },
    nextFrame: async signal => {
      if (signal?.aborted) throw signal.reason;
      advance(options.frameDuration?.(now) ?? 16);
      options.onFrame?.(now, player);
    },
    setTimer: (fn, ms) => { const id = ++nextTimer; timers.set(id, { fn, at: now + ms }); return id; },
    clearTimer: id => timers.delete(id),
  };
  function advance(ms) {
    now += ms;
    for (const [id, timer] of timers) if (timer.at <= now) { timers.delete(id); timer.fn(); }
  }
  function snapshot() {
    return { displayProtocolVersion: "2", boardId: BOARD, deviceGeneration: "1", playbackEpoch: epoch,
      clientInstanceId: owner, leaseUntil: owner ? iso(BASE + now + 30_000) : null,
      serverNow: iso(BASE + now), revision: "1", mode: options.mode?.(now) || "MOODWALL",
      waitingCount: 1, attempt: active ? structuredClone(active) : null };
  }
  async function standard(command) {
    if (command.action === "state") return snapshot();
    if (command.action === "heartbeat") return { playback: snapshot() };
    if (command.action === "claim") {
      if (options.empty) return null;
      owner = command.body.clientInstanceId; epoch = "1";
      active = { attemptId: ATTEMPT, submissionId: ITEM, status: "CLAIMED", attemptNumber: 1,
        ...(options.durationMs === undefined ? {} : { durationMs: options.durationMs }),
        startAuthorizedAt: null, startNotAfter: null, leaseUntil: iso(BASE + now + 10_000),
        item: { id: ITEM, content: "오늘 행복해", emotion: "JOY", animationId: "hearts", sceneId: "joy",
          sceneVersion: "1", sceneSeed: "abc", readySequence: "9007199254740993",
          acceptedAt: iso(BASE - 60_000), firstStartAuthorizedAt: null, contentDeleteAt: null,
          ambientUntil: "2026-11-04T15:00:00.000Z", displayProtocolVersion: "2" } };
    } else if (command.action === "start") {
      active.status = "START_AUTHORIZED";
      active.startAuthorizedAt = iso(BASE + now);
      active.startNotAfter = iso(BASE + now + 5000);
      active.leaseUntil = iso(BASE + now + 30_000);
      active.item.firstStartAuthorizedAt = active.startAuthorizedAt;
      active.item.contentDeleteAt = iso(BASE + now + 3_600_000);
    } else if (command.action === "frame") active.status = "PLAYING";
    else if (command.action === "complete") active.status = "DONE";
    else if (command.action === "interrupt") active.status = "INTERRUPTED";
    return snapshot();
  }
  const transport = { request: async command => {
    calls.push({ ...command, body: command.body && { ...command.body }, at: now });
    return options.request ? options.request(command, standard, { advance, snapshot, get active() { return active; } }) : standard(command);
  } };
  const renderer = {
    prepare: async item => { draws.push({ action: "prepare", at: now, item }); options.prepare?.(item); },
    show: (item, display) => draws.push({ action: "show", at: now, item, display }),
    hide: () => draws.push({ action: "hide", at: now }),
    float: item => draws.push({ action: "float", at: now, item }),
  };
  let visible = true;
  const player = new DisplayPlaybackController({ transport, renderer, clock,
    visible: () => visible,
    uuid: () => `aaaaaaaa-aaaa-4aaa-8aaa-${String(++sequence).padStart(12, "0")}` });
  return { player, calls, draws, clock, advance, setVisible: value => { visible = value; } };
}

test("prepared item holds first visible frame for at least fifteen monotonic seconds", async () => {
  const { player, calls, draws } = harness();
  assert.equal((await player.runOnce()).status, "DONE");
  const first = draws.find(row => row.action === "show");
  const floated = draws.find(row => row.action === "float");
  assert.ok(floated.at - first.at >= 15_000);
  assert.deepEqual(calls.filter(row => !["state", "heartbeat"].includes(row.action)).map(row => row.action),
    ["claim", "start", "frame", "complete"]);
  assert.ok(calls.find(row => row.action === "complete").at - first.at >= 15_000);
});

test("an attempt created before fixed playback keeps its recorded duration", async () => {
  const { player, calls, draws } = harness({ durationMs: 8000 });
  assert.equal((await player.runOnce()).status, "DONE");
  const first = draws.find(row => row.action === "show");
  assert.deepEqual(first.display, { durationMs: 8000 });
  const completedAfter = calls.find(row => row.action === "complete").at - first.at;
  assert.ok(completedAfter >= 8000 && completedAfter < 9000, String(completedAfter));
});

test("a server without durationMs keeps the fifteen-second contract", async () => {
  const { player, draws } = harness();
  assert.equal((await player.runOnce()).status, "DONE");
  assert.deepEqual(draws.find(row => row.action === "show").display, { durationMs: 15_000 });
});

for (const durationMs of [4999, 15_001, 8000.5, "8000"]) {
  test(`an out-of-contract duration ${JSON.stringify(durationMs)} is never played`, async () => {
    const { player, draws } = harness({ durationMs });
    assert.notEqual((await player.runOnce()).status, "DONE");
    assert.equal(draws.some(row => row.action === "show"), false);
  });
}

test("simultaneous ticks coalesce and cannot claim a second item", async () => {
  const { player, calls } = harness();
  const first = player.runOnce(), second = player.runOnce();
  assert.equal(first, second);
  await Promise.all([first, second]);
  assert.equal(calls.filter(row => row.action === "claim").length, 1);
});

test("a rapid pause and resume stops as soon as the same server attempt is INTERRUPTED", async () => {
  let polls = 0, interruptedAt = null;
  const app = harness({ request: async (command, standard, ctx) => {
    if (command.action === "state" && ++polls === 3) {
      ctx.active.status = "INTERRUPTED";
      ctx.active.presentationStatus = "INTERRUPTED";
      interruptedAt = app.clock.now();
    }
    return standard(command);
  } });
  assert.equal((await app.player.runOnce()).status, "INTERRUPTED");
  assert.ok(interruptedAt !== null);
  // This async harness advances frames while the state response settles. The
  // interruption must hide promptly, rather than finish the remaining 13s.
  assert.ok(app.draws.find(row => row.action === "hide").at - interruptedAt < 250);
  assert.ok(!app.calls.some(row => row.action === "complete"));
  assert.ok(!app.draws.some(row => row.action === "float"));
});

test("an actual DONE ACK stores its verified body before any following snapshot request", async () => {
  const data = new Map();
  const cache = new PlaybackBubbleCache({ boardId: BOARD, storage: { getItem: key => data.get(key) ?? null,
    setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key) } });
  const app = harness({ request: async (command, standard) => {
    const result = await standard(command);
    if (command.action === "complete") {
      result.attempt.presentationStatus = "DONE"; result.attempt.deviceGeneration = result.deviceGeneration;
      result.cacheScope = { displayProtocolVersion: "2", boardId: BOARD, businessDate: "2026-11-04",
        deviceGeneration: result.deviceGeneration, cacheResetToken: "0", revision: result.revision,
        serverNow: result.serverNow, ambientUntil: "2026-11-04T15:00:00.000Z", mode: result.mode,
        onlineLeaseUntil: result.leaseUntil, removedIds: [] };
    }
    return result;
  } });
  app.player.cache = cache;
  delete app.player.renderer.float; // The real React renderer has no float callback.
  assert.equal((await app.player.runOnce()).status, "DONE");
  assert.equal(cache.cards.get(ITEM).content, "오늘 행복해");
  assert.equal(JSON.parse(data.get(cache.key)).cards[0].id, ITEM);
  assert.equal(cache.view(app.clock.now()).visible, false); // no snapshot authorization yet
});

test("a broken hide callback still cancels an active request and clears authentication", () => {
  const app = harness();
  app.player.controller = new AbortController();
  app.player.renderer.hide = () => { throw new Error("BROKEN_RENDERER"); };
  app.player.pending = { action: "complete" }; app.player.current = { attemptId: ATTEMPT };
  let cleared = false;
  app.player.cache = { clear() { cleared = true; } };
  assert.throws(() => app.player.resetAuthentication(), /BROKEN_RENDERER/);
  assert.equal(app.player.controller.signal.aborted, true);
  assert.equal(app.player.pending, null); assert.equal(app.player.current, null);
  assert.equal(cleared, true);
});

test("failed persistence reports a private cache problem without replaying a confirmed DONE", async () => {
  const problems = [], app = harness();
  app.player.cache = { authEpoch: 0, storageAvailable: false, acceptCompleted: () => true };
  app.player.onProblem = problem => problems.push(problem.code);
  assert.equal((await app.player.runOnce()).status, "DONE");
  assert.deepEqual(problems, ["CACHE_STORAGE_UNAVAILABLE"]);
  assert.equal(app.calls.filter(row => row.action === "claim").length, 1);
});

test("empty queue never shows fullscreen", async () => {
  const { player, draws } = harness({ empty: true });
  assert.equal((await player.runOnce()).status, "IDLE");
  assert.equal(draws.filter(row => row.action === "show").length, 0);
});

test("asset and font preparation finishes before server start authorization", async () => {
  const { player, calls, draws } = harness({ request: async (command, standard) => {
    if (command.action === "start") assert.equal(draws[0].action, "prepare");
    return standard(command);
  } });
  assert.equal((await player.runOnce()).status, "DONE");
  assert.ok(calls.find(row => row.action === "start"));
});

test("expired immutable start authorization never displays text", async () => {
  const { player, draws, calls } = harness({ request: async (command, standard, ctx) => {
    const data = await standard(command);
    if (command.action === "start") data.serverNow = iso(BASE + 6000);
    return data;
  } });
  assert.equal((await player.runOnce()).code, "START_AUTHORIZATION_EXPIRED");
  assert.equal(draws.filter(row => row.action === "show").length, 0);
  assert.equal(calls.at(-1).action, "interrupt");
});

test("start needs sixteen seconds of conservative lease remaining", async () => {
  const { player, draws } = harness({ request: async (command, standard) => {
    const data = await standard(command);
    if (command.action === "start") data.attempt.leaseUntil = iso(BASE + 15_999);
    return data;
  } });
  assert.equal((await player.runOnce()).code, "START_AUTHORIZATION_EXPIRED");
  assert.ok(!draws.some(row => row.action === "show"));
});

test("lost completion responses retain exactly the same command before any next claim", async () => {
  let attempts = 0;
  const { player, calls, draws } = harness({ request: async (command, standard) => {
    if (command.action === "complete" && ++attempts <= 3) {
      await standard(command); // Server committed, response lost.
      throw error("NETWORK");
    }
    return standard(command);
  } });
  assert.equal((await player.runOnce()).status, "UNKNOWN");
  assert.ok(!draws.some(row => row.action === "float"));
  assert.equal((await player.runOnce()).status, "DONE");
  const completes = calls.filter(row => row.action === "complete");
  assert.equal(completes.length, 4);
  assert.equal(new Set(completes.map(row => row.idempotencyKey)).size, 1);
  assert.deepEqual(completes[0].body, completes[3].body);
  assert.equal(calls.filter(row => row.action === "claim").length, 1);
});

test("TOO_EARLY completion retries the same key and is never accepted as done", async () => {
  let attempts = 0;
  const { player, calls } = harness({ request: async (command, standard) => {
    if (command.action === "complete" && attempts++ === 0) throw error("PLAYBACK_TOO_EARLY", 409);
    return standard(command);
  } });
  assert.equal((await player.runOnce()).status, "DONE");
  const completes = calls.filter(row => row.action === "complete");
  assert.equal(completes[0].idempotencyKey, completes[1].idempotencyKey);
  assert.ok(completes[1].at > completes[0].at);
});

test("server frame-clock skew keeps TOO_EARLY pending across retry batches", async () => {
  let attempts = 0;
  const { player, calls } = harness({ request: async (command, standard) => {
    if (command.action === "complete" && ++attempts <= 4) throw error("PLAYBACK_TOO_EARLY", 409);
    return standard(command);
  } });
  assert.equal((await player.runOnce()).status, "UNKNOWN");
  assert.equal((await player.runOnce()).status, "DONE");
  const completes = calls.filter(row => row.action === "complete");
  assert.equal(new Set(completes.map(row => row.idempotencyKey)).size, 1);
  assert.ok(!calls.some(row => row.action === "interrupt"));
});

test("hidden page interrupts without reporting success", async () => {
  let paused = false;
  const { player, calls, draws } = harness({ onFrame: (now, current) => {
    if (now > 1000 && !paused) { paused = true; current.pause("PAGE_HIDDEN"); }
  } });
  assert.equal((await player.runOnce()).status, "INTERRUPTED");
  assert.ok(!calls.some(row => row.action === "complete"));
  assert.equal(calls.at(-1).body.reason, "PAGE_HIDDEN");
  assert.ok(!draws.some(row => row.action === "float"));
});

test("one-second frame gap interrupts rather than counting suspension as display time", async () => {
  const { player, calls } = harness({ frameDuration: now => now > 500 ? 1100 : 16 });
  assert.equal((await player.runOnce()).code, "FRAME_GAP");
  assert.equal(calls.at(-1).body.reason, "FRAME_GAP");
  assert.ok(!calls.some(row => row.action === "complete"));
});

test("late first-frame acknowledgement interrupts even when server processed it", async () => {
  const { player, calls } = harness({ request: async (command, standard) => {
    const data = await standard(command);
    if (command.action === "frame") data.serverNow = iso(BASE + 5001);
    return data;
  } });
  assert.equal((await player.runOnce()).code, "START_AUTHORIZATION_EXPIRED");
  assert.ok(!calls.some(row => row.action === "complete"));
});

test("operator hiding during playback stops current item", async () => {
  const { player, calls } = harness({ mode: now => now > 2000 ? "PAUSED" : "MOODWALL" });
  assert.equal((await player.runOnce()).status, "INTERRUPTED");
  assert.ok(!calls.some(row => row.action === "complete"));
});

test("long transport round trip is rejected even if timer callback has not executed", async () => {
  const { player, draws } = harness({ request: async (command, standard, ctx) => {
    if (command.action === "start") ctx.advance(5000);
    return standard(command);
  } });
  assert.equal((await player.runOnce()).status, "INTERRUPTED");
  assert.ok(!draws.some(row => row.action === "show"));
});

test("asset preparation failure interrupts without a first frame or start", async () => {
  const { player, calls, draws } = harness({ prepare: () => { throw Error("font unavailable"); } });
  assert.equal((await player.runOnce()).code, "ASSET_FAILURE");
  assert.equal(calls.at(-1).body.reason, "ASSET_FAILURE");
  assert.ok(!calls.some(row => row.action === "start"));
  assert.ok(!draws.some(row => row.action === "show"));
});

test("ambiguous claim is retried with its original key and body before loading", async () => {
  let saved, count = 0;
  const { player, calls } = harness({ request: async (command, standard, ctx) => {
    if (command.action !== "claim") return standard(command);
    count++;
    if (!saved) saved = await standard(command);
    if (count <= 3) throw error("NETWORK");
    return { ...saved, serverNow: ctx.snapshot().serverNow };
  } });
  assert.equal((await player.runOnce()).status, "UNKNOWN");
  assert.equal((await player.runOnce()).status, "DONE");
  const claims = calls.filter(row => row.action === "claim");
  assert.equal(claims.length, 4);
  assert.equal(new Set(claims.map(row => row.idempotencyKey)).size, 1);
  assert.deepEqual(claims[0].body, claims[3].body);
});

test("lost first-frame response retries exactly the same command", async () => {
  let count = 0;
  const { player, calls } = harness({ request: async (command, standard) => {
    const result = await standard(command);
    if (command.action === "frame" && count++ === 0) throw error("NETWORK");
    return result;
  } });
  assert.equal((await player.runOnce()).status, "DONE");
  const frames = calls.filter(row => row.action === "frame");
  assert.equal(frames.length, 2);
  assert.equal(frames[0].idempotencyKey, frames[1].idempotencyKey);
});

test("late completed receipt without source text confirms completion without extending lifetime", async () => {
  const { player, draws } = harness({ request: async (command, standard) => {
    const data = await standard(command);
    if (command.action === "complete") data.attempt.item = null;
    return data;
  } });
  assert.equal((await player.runOnce()).status, "DONE");
  assert.equal(draws.find(row => row.action === "float").item.contentDeleteAt, iso(BASE + 3_600_000));
});

test("confirmed old completion cannot render or take over a newer owner's item", async () => {
  const { player, draws, calls } = harness({ request: async (command, standard) => {
    const data = await standard(command);
    if (command.action === "complete") {
      data.clientInstanceId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
      data.playbackEpoch = "2";
    }
    return data;
  } });
  assert.equal((await player.runOnce()).status, "DONE");
  assert.ok(!draws.some(row => row.action === "float"));
  assert.equal(calls.filter(row => row.action === "claim").length, 1);
});

test("late DONE acknowledgement for a hidden item cannot restore local text", async () => {
  const hidden = [];
  const { player, draws } = harness({ request: async (command, standard) => {
    const data = await standard(command);
    if (command.action === "complete") {
      data.attempt.item = null;
      data.attempt.presentationStatus = "HIDDEN";
      data.attempt.reasonCode = "DISPLAY_HIDDEN";
    }
    return data;
  } });
  player.cache = { hide: id => hidden.push(id) };
  assert.equal((await player.runOnce()).status, "DONE");
  assert.deepEqual(hidden, [ITEM]);
  assert.ok(!draws.some(row => row.action === "float"));
});

test("ownership change during playback hides and never completes another tab's item", async () => {
  const { player, draws, calls } = harness({ request: async (command, standard, ctx) => {
    const data = await standard(command);
    if (command.action === "state" && calls.filter(row => row.action === "state").length > 2) {
      data.clientInstanceId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
      data.playbackEpoch = "2";
    }
    return data;
  } });
  assert.equal((await player.runOnce()).status, "INTERRUPTED");
  assert.ok(!calls.some(row => row.action === "complete"));
  assert.ok(!draws.some(row => row.action === "float"));
});

test("current authentication revocation clears cache and stops the active renderer", async () => {
  let clears = 0;
  const { player, draws, calls } = harness({ request: async (command, standard) => {
    if (command.action === "state" && calls.filter(row => row.action === "state").length > 2) {
      throw error("SESSION_EXPIRED", 401);
    }
    return standard(command);
  } });
  player.cache = { clear: () => { clears++; } };
  assert.equal((await player.runOnce()).status, "PAUSED");
  assert.equal(clears, 1);
  assert.ok(!calls.some(row => row.action === "complete"));
  assert.equal(draws.at(-1).action, "hide");
});

test("a 403 (CSRF expiry, origin mismatch or web filter) never revokes the device", async () => {
  let clears = 0;
  const { player, calls } = harness({ request: async (command, standard) => {
    if (command.action === "state" && calls.filter(row => row.action === "state").length > 2) {
      throw error("CSRF_REJECTED", 403);
    }
    return standard(command);
  } });
  player.cache = { clear: () => { clears++; } };
  await player.runOnce();
  assert.equal(clears, 0);
});

test("read-only state cannot make an already expired attempt lease valid", async () => {
  const { player, calls } = harness({ request: async (command, standard) => {
    const data = await standard(command);
    if (command.action === "state" && calls.filter(row => row.action === "state").length > 1) {
      data.serverNow = iso(BASE + 31_000);
    }
    return data;
  } });
  assert.equal((await player.runOnce()).code, "NETWORK");
  assert.ok(!calls.some(row => row.action === "complete"));
});

test("a start cannot squeeze a partial fifteen-second presentation across KST midnight", async () => {
  const { player, calls, draws } = harness({ initialNow: 12 * 3_600_000 - 10_000 });
  assert.equal((await player.runOnce()).code, "START_AUTHORIZATION_EXPIRED");
  assert.ok(!draws.some(row => row.action === "show"));
  assert.ok(!calls.some(row => row.action === "complete"));
});

test("heartbeat explicitly negotiates v2 while keeping student data out", async () => {
  const { player, calls } = harness({ empty: true });
  await player.runOnce();
  assert.deepEqual(calls.find(row => row.action === "heartbeat").body,
    { clientInstanceId: player.clientInstanceId, displayProtocolVersion: "2" });
});

test("fetch transport sends exact JSON and same-origin CSRF/idempotency headers", async () => {
  const requests = [];
  const transport = new FetchPlaybackTransport({ csrfToken: () => "csrf", fetch: async (url, init) => {
    requests.push({ url, init });
    return { status: 200, ok: true, json: async () => ({ ok: true }) };
  } });
  await transport.request({ action: "frame", attemptId: ATTEMPT, body: { playbackEpoch: "1" }, idempotencyKey: ITEM });
  assert.equal(requests[0].url, `/api/display/playback/${ATTEMPT}/frame`);
  assert.equal(requests[0].init.headers["X-CSRF-Token"], "csrf");
  assert.equal(requests[0].init.headers["Idempotency-Key"], ITEM);
  assert.equal(requests[0].init.credentials, "same-origin");
  assert.equal(requests[0].init.body, '{"playbackEpoch":"1"}');
  assert.throws(() => new FetchPlaybackTransport({ baseUrl: "https://elsewhere.example" }));
});
