import test from "node:test";
import assert from "node:assert/strict";
import { BubbleCache, DisplaySynchronizer } from "./bubble-cache.mjs";

const BOARD = "10000000-0000-4000-8000-000000000001";
const ID = "20000000-0000-4000-8000-000000000001";
const START = Date.parse("2026-10-29T03:00:00Z");
const HOUR = 3_600_000;
const MIDNIGHT = Date.parse("2026-10-29T15:00:00Z");
const CONTENT = "오늘도 응원해";
const iso = time => new Date(time).toISOString();
const id = number => `20000000-0000-4000-8000-${String(number).padStart(12, "0")}`;

class Storage {
  values = new Map();
  getItem(key) { return this.values.get(key) ?? null; }
  setItem(key, value) { this.values.set(key, value); }
  removeItem(key) { this.values.delete(key); }
}

function mention(overrides = {}) {
  return { id: ID, content: CONTENT, emojiId: "heart", emotion: "SUPPORT", animationId: "sparkles",
    acceptedAt: iso(START), expiresAt: iso(START + HOUR), ambientUntil: iso(MIDNIGHT), ...overrides };
}

function state(time = START, overrides = {}) {
  return { boardId: BOARD, businessDate: "2026-10-29", deviceGeneration: "1", cacheResetToken: "0",
    revision: 1, serverNow: iso(time), snapshotAt: iso(time), ambientUntil: iso(MIDNIGHT), removedIds: [],
    onlineLeaseUntil: iso(time + 30_000), mode: "MOODWALL", bubbles: [], bubbleTotalCount: 0,
    nextCursor: null, ...overrides };
}

function page(bubbles, time = START, overrides = {}) {
  return state(time, { bubbles, bubbleTotalCount: bubbles.length, ...overrides });
}

test("five approved bubbles float immediately without spotlight slots or waiting", () => {
  const cache = new BubbleCache({ boardId: BOARD });
  assert.equal(cache.synchronize(page(Array.from({ length: 5 }, (_, n) => mention({ id: id(n + 1) }))), 0), true);
  const result = cache.view(0);
  assert.equal(result.totalCount, 5);
  assert.equal(result.bubbles.length, 5);
  assert.ok(result.bubbles.every(value => value.phase === "FLOATING"));
  assert.ok(result.bubbles.every(value => !("slot" in value) && !("remainingMs" in value)));
  assert.equal("spotlights" in result, false);
});

test("one-hour database expiry preserves the same content in a transparent bubble", () => {
  const cache = new BubbleCache({ boardId: BOARD });
  cache.synchronize(page([mention()]), 0);
  cache.synchronize(page([mention()], START + HOUR - 10_000, { revision: 2 }), 100);
  assert.equal(cache.view(10_099).bubbles[0].phase, "FLOATING");
  assert.equal(cache.view(10_100).bubbles[0].phase, "AMBIENT");
  assert.equal(cache.view(10_100).bubbles[0].content, CONTENT);
});

test("missing IDs from another page do not demote, delete, or duplicate cached bubbles", () => {
  const cache = new BubbleCache({ boardId: BOARD });
  cache.synchronize(page([mention()], START, { nextCursor: "page-two", bubbleTotalCount: 2 }), 0);
  cache.synchronize(page([mention({ id: id(2) })], START, { bubbleTotalCount: 2 }), 1);
  cache.synchronize(page([mention()], START + 1), 2);
  assert.equal(cache.view(2).bubbles.length, 2);
  assert.ok(cache.view(2).bubbles.every(value => value.phase === "FLOATING"));
});

test("an empty server snapshot after physical deletion must not clear a bubble", () => {
  const cache = new BubbleCache({ boardId: BOARD });
  cache.synchronize(page([mention()]), 0);
  cache.synchronize(state(START + HOUR, { revision: 2 }), 1);
  assert.equal(cache.view(1).bubbles[0].id, ID);
  assert.equal(cache.view(1).bubbles[0].phase, "AMBIENT");
});

test("same-browser reload restores bubbles only after authenticated resynchronization", () => {
  const storage = new Storage();
  const first = new BubbleCache({ boardId: BOARD, storage });
  first.synchronize(page([mention()]), 0);
  const reload = new BubbleCache({ boardId: BOARD, storage });
  assert.deepEqual(reload.view(0), { visible: false, bubbles: [], totalCount: 0 });
  reload.synchronize(state(START + HOUR, { revision: 2 }), 0);
  assert.equal(reload.view(0).bubbles[0].content, CONTENT);
  assert.equal(reload.view(0).bubbles[0].phase, "AMBIENT");
});

test("midnight removes local content and its stored copy, not merely its opacity", () => {
  const storage = new Storage();
  const cache = new BubbleCache({ boardId: BOARD, storage });
  cache.synchronize(page([mention()]), 0);
  cache.synchronize(state(MIDNIGHT - 10_000, { revision: 2 }), 100);
  assert.equal(cache.view(10_099).bubbles.length, 1);
  assert.equal(cache.view(10_100).visible, false);
  assert.equal(cache.cards.size, 0);
  assert.equal(storage.values.size, 0);
});

test("a new KST day discards yesterday even when the app was closed at midnight", () => {
  const storage = new Storage();
  const old = new BubbleCache({ boardId: BOARD, storage });
  old.synchronize(page([mention()]), 0);
  const next = new BubbleCache({ boardId: BOARD, storage });
  next.synchronize(state(MIDNIGHT + 1000, { revision: 3, businessDate: "2026-10-30",
    ambientUntil: iso(MIDNIGHT + 86_400_000) }), 0);
  assert.deepEqual(next.view(0).bubbles, []);
  assert.equal(next.cards.size, 0);
});

test("explicit hide removes retained content and stale snapshots cannot resurrect it", () => {
  const cache = new BubbleCache({ boardId: BOARD });
  cache.synchronize(page([mention()]), 0);
  cache.synchronize(state(START + HOUR, { revision: 3, removedIds: [ID] }), 1);
  assert.deepEqual(cache.view(1).bubbles, []);
  assert.equal(cache.synchronize(page([mention()], START, { revision: 2 }), 2), false);
  cache.synchronize(page([mention()], START + HOUR + 1000, { revision: 4 }), 3);
  assert.equal(cache.cards.size, 0);
});

test("bounded hidden-feed reset and device generation invalidate old cache scopes", () => {
  const storage = new Storage();
  const cache = new BubbleCache({ boardId: BOARD, storage });
  cache.synchronize(page([mention()]), 0);
  cache.synchronize(state(START + 1000, { revision: 2, cacheResetToken: "42" }), 1);
  assert.equal(cache.cards.size, 0);
  cache.synchronize(page([mention()], START + 2000, { revision: 3, cacheResetToken: "42" }), 2);
  cache.synchronize(state(START + 3000, { revision: 4, deviceGeneration: "2" }), 3);
  assert.equal(cache.cards.size, 0);
  assert.equal(cache.synchronize(page([mention()], START, { revision: 5 }), 4), false);
});

test("offline lease and PAUSED hide the view while preserving content for a safe return", () => {
  const cache = new BubbleCache({ boardId: BOARD });
  cache.synchronize(page([mention()]), 0);
  assert.equal(cache.view(30_000).visible, false);
  assert.equal(cache.cards.size, 1);
  cache.disconnect();
  assert.equal(cache.view(1).visible, false);
  cache.synchronize(state(START + HOUR, { revision: 2, mode: "PAUSED" }), 2);
  assert.equal(cache.view(2).visible, false);
  cache.synchronize(state(START + HOUR + 1000, { revision: 3 }), 3);
  assert.equal(cache.view(3).bubbles[0].id, ID);
});

test("blocked browser storage falls back to memory without dropping a bubble", () => {
  const storage = { getItem() { throw Error("blocked"); }, setItem() { throw Error("full"); },
    removeItem() { throw Error("blocked"); } };
  const cache = new BubbleCache({ boardId: BOARD, storage });
  assert.equal(cache.synchronize(page([mention()]), 0), true);
  assert.equal(cache.storageAvailable, false);
  cache.synchronize(state(START + HOUR, { revision: 2 }), 1);
  assert.equal(cache.view(1).bubbles[0].content, CONTENT);
});

test("view returns every cached bubble rather than a rotating window of forty", () => {
  const cache = new BubbleCache({ boardId: BOARD });
  for (let batch = 0; batch < 3; batch++) {
    cache.synchronize(page(Array.from({ length: 50 }, (_, n) => mention({ id: id(batch * 50 + n + 1) })),
      START + batch, { revision: batch + 1 }), batch);
  }
  cache.synchronize(state(START + HOUR, { revision: 4 }), 3);
  assert.equal(cache.view(3).bubbles.length, 150);
  assert.equal(cache.view(3).totalCount, 150);
  assert.ok(cache.view(3).bubbles.every(value => value.phase === "AMBIENT"));
});

test("configured memory safety cap keeps the latest bubbles independently of display slots", () => {
  const cache = new BubbleCache({ boardId: BOARD, maxCards: 3 });
  cache.synchronize(page(Array.from({ length: 5 }, (_, n) => mention({ id: id(n + 1) }))), 0);
  assert.deepEqual(cache.view(0).bubbles.map(value => value.id), [id(3), id(4), id(5)]);
});

test("malformed snapshots never disclose persisted content", () => {
  for (const invalid of [
    { ambientUntil: iso(MIDNIGHT + 1) },
    { nextCursor: "x".repeat(2049) },
  ]) {
    const storage = new Storage();
    const cache = new BubbleCache({ boardId: BOARD, storage });
    cache.synchronize(page([mention()]), 0);
    assert.equal(cache.synchronize(state(START, invalid), 1), false);
    assert.equal(cache.view(1).visible, false);
  }
});

test("late equal-revision snapshots cannot rewind the transparent transition", () => {
  const cache = new BubbleCache({ boardId: BOARD });
  cache.synchronize(page([mention()], START + HOUR - 10_000), 0);
  assert.equal(cache.view(10_000).bubbles[0].phase, "AMBIENT");
  assert.equal(cache.synchronize(page([mention()], START), 11_000), false);
  cache.synchronize(page([mention()], START + HOUR - 1000, { revision: 2 }), 12_000);
  assert.equal(cache.view(12_000).bubbles[0].phase, "AMBIENT");
});

test("legacy spotlight cache is discarded without restoring unauthenticated content", () => {
  const storage = new Storage();
  storage.setItem(`moodwall.display.day.v1:${BOARD}`, JSON.stringify({ content: "old" }));
  const cache = new BubbleCache({ boardId: BOARD, storage });
  assert.equal(storage.values.size, 0);
  assert.equal(cache.view(0).visible, false);
});

test("pagination synchronizer receives all 105 approved bubbles and starts each poll at page one", async () => {
  const cache = new BubbleCache({ boardId: BOARD });
  const all = Array.from({ length: 105 }, (_, n) => mention({ id: id(n + 1) }));
  const cursors = [];
  const sync = new DisplaySynchronizer({ cache, now: () => 0, fetchPage: async ({ cursor }) => {
    cursors.push(cursor);
    return cursor === null ? page(all.slice(0, 100), START, { bubbleTotalCount: 105, nextCursor: "second" }) :
      page(all.slice(100), START, { bubbleTotalCount: 105 });
  } });
  assert.deepEqual(await sync.synchronize(), { completed: true, pages: 2 });
  assert.equal(cache.view(0).bubbles.length, 105);
  assert.equal(new Set(cache.view(0).bubbles.map(value => value.id)).size, 105);
  await sync.synchronize();
  assert.deepEqual(cursors, [null, "second", null, "second"]);
  assert.equal(cache.cards.size, 105);
});

test("same-millisecond server samples across three pages do not reject the local monotonic clock", async () => {
  const cache = new BubbleCache({ boardId: BOARD });
  let tick = 0;
  const sync = new DisplaySynchronizer({ cache, now: () => tick++, fetchPage: async ({ cursor }) => {
    const n = cursor === null ? 1 : Number(cursor);
    return page([mention({ id: id(n) })], START, { bubbleTotalCount: 3, nextCursor: n === 3 ? null : String(n + 1) });
  } });
  assert.deepEqual(await sync.synchronize(), { completed: true, pages: 3 });
  assert.equal(cache.view(3).totalCount, 3);
});

test("INVALID_CURSOR restarts from the first page once and retains already observed bubbles", async () => {
  const cache = new BubbleCache({ boardId: BOARD });
  const calls = [];
  let failed = false;
  const sync = new DisplaySynchronizer({ cache, now: () => 0, fetchPage: async ({ cursor }) => {
    calls.push(cursor);
    if (cursor !== null && !failed) {
      failed = true;
      throw Object.assign(Error("expired cursor"), { status: 422, code: "INVALID_CURSOR" });
    }
    if (cursor === null) return page([mention()], START, { nextCursor: "next", bubbleTotalCount: 2 });
    return page([mention({ id: id(2) })], START, { bubbleTotalCount: 2 });
  } });
  assert.deepEqual(await sync.synchronize(), { completed: true, pages: 3 });
  assert.deepEqual(calls, [null, "next", null, "next"]);
  assert.equal(cache.view(0).totalCount, 2);
});

test("a different 422 is not retried and hides the view while preserving cache", async () => {
  const cache = new BubbleCache({ boardId: BOARD });
  let calls = 0;
  const sync = new DisplaySynchronizer({ cache, now: () => 0, fetchPage: async ({ cursor }) => {
    calls++;
    if (cursor === null) return page([mention()], START, { nextCursor: "next", bubbleTotalCount: 2 });
    throw Object.assign(Error("invalid input"), { status: 422, code: "INVALID_INPUT" });
  } });
  await assert.rejects(sync.synchronize(), /invalid input/);
  assert.equal(calls, 2);
  assert.equal(cache.cards.size, 1);
  assert.equal(cache.view(0).visible, false);
});

test("current authentication failure clears both memory and persisted content", async () => {
  const storage = new Storage();
  const cache = new BubbleCache({ boardId: BOARD, storage });
  cache.synchronize(page([mention()]), 0);
  const sync = new DisplaySynchronizer({ cache, now: () => 0, fetchPage: async () => {
    throw Object.assign(Error("revoked"), { status: 401 });
  } });
  await assert.rejects(sync.synchronize(), /revoked/);
  assert.equal(cache.cards.size, 0);
  assert.equal(storage.values.size, 0);
});

test("an old successful request cannot restore content after an authentication reset", async () => {
  const cache = new BubbleCache({ boardId: BOARD });
  let resolve;
  let signal;
  const sync = new DisplaySynchronizer({ cache, now: () => 0, fetchPage: options => {
    signal = options.signal;
    return new Promise(done => { resolve = done; });
  } });
  const pending = sync.synchronize();
  sync.resetAuthentication();
  assert.equal(signal.aborted, true);
  resolve(page([mention()])); // The transport deliberately ignores abort to exercise the epoch fence.
  assert.deepEqual(await pending, { completed: false, pages: 0 });
  assert.equal(cache.cards.size, 0);
});

test("an old 401 cannot clear the cache from a newer successful request", async () => {
  const cache = new BubbleCache({ boardId: BOARD });
  let reject;
  let calls = 0;
  const sync = new DisplaySynchronizer({ cache, now: () => 0, fetchPage: () => {
    calls++;
    return calls === 1 ? new Promise((_, fail) => { reject = fail; }) : Promise.resolve(page([mention()]));
  } });
  const old = sync.synchronize();
  assert.equal((await sync.synchronize()).completed, true);
  reject(Object.assign(Error("old auth"), { status: 401 }));
  assert.deepEqual(await old, { completed: false, pages: 0 });
  assert.equal(cache.view(0).bubbles[0].content, CONTENT);
});

test("disconnect aborts requests and hides without deleting the day cache", async () => {
  const cache = new BubbleCache({ boardId: BOARD });
  cache.synchronize(page([mention()]), 0);
  let resolve;
  const sync = new DisplaySynchronizer({ cache, now: () => 0, fetchPage: () =>
    new Promise(done => { resolve = done; }) });
  const pending = sync.synchronize();
  sync.disconnect();
  resolve(state(START + 1000));
  assert.equal((await pending).completed, false);
  assert.equal(cache.cards.size, 1);
  assert.equal(cache.view(0).visible, false);
});

test("repeated cursors or changed snapshot boundaries fail closed instead of looping or merging", async () => {
  for (const changed of [false, true]) {
    const cache = new BubbleCache({ boardId: BOARD });
    const sync = new DisplaySynchronizer({ cache, now: () => 0, fetchPage: async ({ cursor }) =>
      page([mention()], START + (changed && cursor ? 1 : 0), { nextCursor: "same", bubbleTotalCount: 2 }) });
    await assert.rejects(sync.synchronize(), changed ? /snapshot changed/ : /Repeated display cursor/);
    assert.equal(cache.view(0).visible, false);
  }
});
