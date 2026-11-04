import test from "node:test";
import assert from "node:assert/strict";
import { PlaybackBubbleCache, PlaybackBubbleSynchronizer } from "./playback-bubble-cache.mjs";

const BOARD = "11111111-1111-4111-8111-111111111111";
const ITEM = "22222222-2222-4222-8222-222222222222";
const BASE = Date.parse("2026-11-04T03:00:00Z");
const iso = ms => new Date(ms).toISOString();
function card(overrides = {}) {
  return { id: ITEM, content: "오늘 행복해", emotion: "JOY", animationId: "hearts", sceneId: "joy",
    sceneVersion: "1", sceneSeed: "seed", readySequence: "9007199254740993",
    firstStartAuthorizedAt: iso(BASE), contentDeleteAt: iso(BASE + 3_600_000),
    ambientUntil: "2026-11-04T15:00:00.000Z", displayProtocolVersion: "2", ...overrides };
}
function snapshot(overrides = {}) {
  return { displayProtocolVersion: "2", boardId: BOARD, businessDate: "2026-11-04",
    deviceGeneration: "1", cacheResetToken: "0", revision: "9007199254740994",
    serverNow: iso(BASE), ambientUntil: "2026-11-04T15:00:00.000Z", onlineLeaseUntil: iso(BASE + 30_000),
    mode: "MOODWALL", bubbles: [card()], removedIds: [], snapshotAt: iso(BASE), nextCursor: null, ...overrides };
}
function memoryStorage() {
  const data = new Map();
  return { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value),
    removeItem: key => data.delete(key), data };
}

test("v2 uses first start rather than accepted time and requires no student emoji", () => {
  const cache = new PlaybackBubbleCache({ boardId: BOARD });
  assert.equal(cache.synchronize(snapshot(), 0), true);
  assert.equal(cache.view(0).bubbles[0].phase, "FLOATING");
  assert.equal(cache.view(0).bubbles[0].content, "오늘 행복해");
});

test("source expiry preserves exactly the same text as ambient", () => {
  const cache = new PlaybackBubbleCache({ boardId: BOARD });
  assert.equal(cache.synchronize(snapshot(), 0), true);
  assert.equal(cache.synchronize(snapshot({ serverNow: iso(BASE + 3_600_000), bubbles: [],
    onlineLeaseUntil: iso(BASE + 3_630_000) }), 3_600_000), true);
  assert.deepEqual(cache.view(3_600_000).bubbles.map(row => [row.content, row.phase]), [["오늘 행복해", "AMBIENT"]]);
});

test("an absent page never erases already displayed text", () => {
  const cache = new PlaybackBubbleCache({ boardId: BOARD });
  cache.synchronize(snapshot(), 0);
  cache.synchronize(snapshot({ bubbles: [] }), 1);
  assert.equal(cache.view(1).bubbles.length, 1);
});

test("explicit hidden IDs remove ambient text and prevent replay from another page", () => {
  const cache = new PlaybackBubbleCache({ boardId: BOARD });
  cache.synchronize(snapshot(), 0);
  cache.synchronize(snapshot({ bubbles: [], removedIds: [ITEM], revision: "9007199254740995" }), 1);
  cache.synchronize(snapshot({ revision: "9007199254740995" }), 2);
  assert.equal(cache.view(2).bubbles.length, 0);
});

test("cold restore hides storage until authenticated matching scope arrives", () => {
  const storage = memoryStorage();
  const first = new PlaybackBubbleCache({ boardId: BOARD, storage });
  first.synchronize(snapshot(), 0);
  const second = new PlaybackBubbleCache({ boardId: BOARD, storage });
  assert.equal(second.view(0).visible, false);
  assert.equal(second.synchronize(snapshot({ bubbles: [] }), 0), true);
  assert.equal(second.view(0).bubbles.length, 1);
});

test("midnight clears memory and persistent data even while offline", () => {
  const storage = memoryStorage();
  const cache = new PlaybackBubbleCache({ boardId: BOARD, storage });
  cache.synchronize(snapshot(), 0);
  cache.disconnect();
  assert.equal(cache.view(12 * 3_600_000).visible, false);
  assert.equal(cache.cards.size, 0);
  assert.equal(storage.getItem(cache.key), null);
});

test("server cannot accidentally give a new first-start TTL on replay", () => {
  const cache = new PlaybackBubbleCache({ boardId: BOARD });
  assert.equal(cache.synchronize(snapshot({ bubbles: [card({ contentDeleteAt: iso(BASE + 3_600_001) })] }), 0), false);
});

test("a replay cannot move both first-start and deletion time forward for the same ID", () => {
  const cache = new PlaybackBubbleCache({ boardId: BOARD });
  cache.synchronize(snapshot(), 0);
  const moved = card({ firstStartAuthorizedAt: iso(BASE + 1000), contentDeleteAt: iso(BASE + 3_601_000) });
  assert.equal(cache.synchronize(snapshot({ serverNow: iso(BASE + 1000), bubbles: [moved] }), 1000), false);
  assert.equal(cache.cards.get(ITEM).contentDeleteAt, iso(BASE + 3_600_000));
  assert.equal(cache.view(1000).visible, false);
});

test("numeric revisions cannot lose precision silently", () => {
  const cache = new PlaybackBubbleCache({ boardId: BOARD });
  assert.equal(cache.synchronize(snapshot({ revision: 9007199254740994 }), 0), false);
});

test("request latency expires authority conservatively", () => {
  const cache = new PlaybackBubbleCache({ boardId: BOARD });
  assert.equal(cache.synchronize(snapshot({ onlineLeaseUntil: iso(BASE + 1000) }), 1200, 0), true);
  assert.equal(cache.view(1200).visible, false);
});

test("unsafe integer revisions remain strings and stale responses cannot restore a hidden card", () => {
  const cache = new PlaybackBubbleCache({ boardId: BOARD });
  cache.synchronize(snapshot(), 0);
  cache.synchronize(snapshot({ removedIds: [ITEM], bubbles: [], revision: "9007199254740995" }), 1);
  assert.equal(cache.synchronize(snapshot(), 2), false);
  assert.equal(cache.view(2).bubbles.length, 0);
});

test("generation and reset-token changes discard old cache", () => {
  const cache = new PlaybackBubbleCache({ boardId: BOARD });
  cache.synchronize(snapshot(), 0);
  cache.synchronize(snapshot({ deviceGeneration: "2", bubbles: [] }), 1);
  assert.equal(cache.view(1).bubbles.length, 0);
  cache.synchronize(snapshot({ deviceGeneration: "2" }), 2);
  cache.synchronize(snapshot({ deviceGeneration: "2", cacheResetToken: "1", bubbles: [] }), 3);
  assert.equal(cache.view(3).bubbles.length, 0);
});

test("blocked storage keeps memory and reports persistence unavailable", () => {
  const storage = { getItem() { throw Error(); }, setItem() { throw Error(); }, removeItem() { throw Error(); } };
  const cache = new PlaybackBubbleCache({ boardId: BOARD, storage });
  assert.equal(cache.synchronize(snapshot(), 0), true);
  assert.equal(cache.storageAvailable, false);
  assert.equal(cache.view(0).bubbles.length, 1);
});

test("legacy acceptedAt-based storage is discarded rather than silently converted", () => {
  const storage = memoryStorage();
  storage.setItem(`moodwall.display.day.v2:${BOARD}`, "old format");
  new PlaybackBubbleCache({ boardId: BOARD, storage });
  assert.equal(storage.getItem(`moodwall.display.day.v2:${BOARD}`), null);
});

test("generation change cannot rewind server time and reveal stale text", () => {
  const cache = new PlaybackBubbleCache({ boardId: BOARD });
  cache.synchronize(snapshot({ serverNow: iso(BASE + 2000), onlineLeaseUntil: iso(BASE + 30_000) }), 0);
  cache.synchronize(snapshot({ serverNow: iso(BASE + 1000), deviceGeneration: "2", bubbles: [] }), 1);
  assert.ok(cache.scope.now >= BASE + 2001);
});

test("v2 synchronizer merges all pages without treating absence as deletion", async () => {
  const cache = new PlaybackBubbleCache({ boardId: BOARD });
  const requests = [];
  const sync = new PlaybackBubbleSynchronizer({ cache, now: () => 1,
    fetchPage: async ({ cursor }) => {
      requests.push(cursor);
      return cursor === null ? snapshot({ nextCursor: "next" }) : snapshot({ bubbles: [] });
    } });
  assert.deepEqual(await sync.synchronize(), { completed: true, pages: 2 });
  assert.deepEqual(requests, [null, "next"]);
  assert.equal(cache.view(1).bubbles.length, 1);
});

test("old successful response cannot restore cache after authentication reset", async () => {
  const cache = new PlaybackBubbleCache({ boardId: BOARD });
  let resolve;
  const sync = new PlaybackBubbleSynchronizer({ cache, now: () => 0,
    fetchPage: () => new Promise(done => { resolve = done; }) });
  const pending = sync.synchronize();
  sync.resetAuthentication();
  resolve(snapshot());
  assert.deepEqual(await pending, { completed: false, pages: 0 });
  assert.equal(cache.scope, null);
});

test("repeated page cursor hides the display instead of looping indefinitely", async () => {
  const cache = new PlaybackBubbleCache({ boardId: BOARD });
  const sync = new PlaybackBubbleSynchronizer({ cache, now: () => 0,
    fetchPage: async () => snapshot({ nextCursor: "repeat" }) });
  await assert.rejects(sync.synchronize(), /Invalid display cursor/);
  assert.equal(cache.view(0).visible, false);
});
