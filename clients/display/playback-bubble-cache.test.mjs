import test from "node:test";
import assert from "node:assert/strict";
import { PlaybackBubbleCache, PlaybackBubbleSynchronizer, readPlaybackBubbleCatalog } from "./playback-bubble-cache.mjs";

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
function completeAck(metadata = {}) {
  const scope = snapshot({ bubbles: [], serverNow: iso(BASE + 15_000), ...metadata });
  return { displayProtocolVersion: "2", boardId: BOARD, deviceGeneration: scope.deviceGeneration,
    revision: scope.revision, serverNow: scope.serverNow, mode: scope.mode, cacheScope: scope,
    attempt: { attemptId: "33333333-3333-4333-8333-333333333333", submissionId: ITEM,
      status: "DONE", presentationStatus: "DONE", deviceGeneration: scope.deviceGeneration, item: card() } };
}

test("a confirmed DONE persists before the first snapshot without granting public visibility", () => {
  const storage = memoryStorage(), cache = new PlaybackBubbleCache({ boardId: BOARD, storage });
  assert.equal(cache.acceptCompleted(completeAck(), card(), 15_000, 15_000, cache.authEpoch), true);
  assert.equal(cache.cards.size, 1);
  assert.equal(JSON.parse(storage.getItem(cache.key)).cards[0].content, card().content);
  assert.equal(cache.view(15_000).visible, false);
  // The source has now disappeared from the server. The stored completion is
  // restored only after a matching authenticated snapshot, as an ambient bubble.
  const reopened = new PlaybackBubbleCache({ boardId: BOARD, storage });
  assert.equal(reopened.synchronize(snapshot({ bubbles: [], serverNow: iso(BASE + 3_600_000),
    onlineLeaseUntil: iso(BASE + 3_630_000) }), 0), true);
  assert.equal(reopened.view(0).bubbles[0].phase, "AMBIENT");
});

test("DONE caching neither reconnects a disconnected cache nor extends its snapshot lease", () => {
  const cache = new PlaybackBubbleCache({ boardId: BOARD });
  cache.synchronize(snapshot({ bubbles: [], onlineLeaseUntil: iso(BASE + 1000) }), 0);
  const ack = completeAck({ onlineLeaseUntil: iso(BASE + 60_000) });
  assert.equal(cache.acceptCompleted(ack, card(), 15_000, 15_000, cache.authEpoch), true);
  assert.equal(cache.lease, BASE + 1000);
  assert.equal(cache.view(15_000).visible, false);
  cache.disconnect();
  assert.equal(cache.acceptCompleted(ack, card(), 15_001, 15_001, cache.authEpoch), true);
  assert.equal(cache.connected, false);
});

test("a verified DONE immediately joins an already authorized cache without another snapshot", () => {
  const cache = new PlaybackBubbleCache({ boardId: BOARD });
  cache.synchronize(snapshot({ bubbles: [] }), 0);
  assert.equal(cache.acceptCompleted(completeAck(), card(), 15_000, 15_000, cache.authEpoch), true);
  assert.equal(cache.view(15_000).bubbles[0].content, card().content);
});

test("a late ACK cannot undo hidden IDs, a reset, authentication clearing or KST midnight", () => {
  const cache = new PlaybackBubbleCache({ boardId: BOARD });
  cache.synchronize(snapshot({ removedIds: [ITEM], bubbles: [] }), 0);
  assert.equal(cache.acceptCompleted(completeAck(), card(), 15_000, 15_000, cache.authEpoch), false);
  cache.synchronize(snapshot({ cacheResetToken: "1", bubbles: [] }), 15_001);
  assert.equal(cache.acceptCompleted(completeAck(), card(), 15_002, 15_002, cache.authEpoch), false);
  const epoch = cache.authEpoch;
  cache.clear();
  assert.equal(cache.acceptCompleted(completeAck(), card(), 15_003, 15_003, epoch), false);
  assert.equal(cache.scope, null);
  const midnight = completeAck({ serverNow: "2026-11-04T14:59:59.500Z" });
  assert.equal(cache.acceptCompleted(midnight, card(), 1000, 0, cache.authEpoch), false);
  assert.equal(cache.cards.size, 0);
});

test("completion requires matching authenticated metadata, immutable text and a DONE presentation", () => {
  for (const change of [ack => { ack.attempt.status = "PLAYING"; },
    ack => { ack.attempt.presentationStatus = "HIDDEN"; },
    ack => { ack.cacheScope.deviceGeneration = "2"; },
    ack => { ack.cacheScope.revision = "1"; },
    ack => { ack.cacheScope.removedIds = [ITEM]; },
    ack => { ack.attempt.item.content = "다른 글"; }]) {
    const cache = new PlaybackBubbleCache({ boardId: BOARD }), ack = completeAck(); change(ack);
    assert.equal(cache.acceptCompleted(ack, card(), 0, 0, cache.authEpoch), false);
    assert.equal(cache.cards.size, 0);
  }
  const cache = new PlaybackBubbleCache({ boardId: BOARD });
  cache.synchronize(snapshot(), 0);
  const moved = card({ firstStartAuthorizedAt: iso(BASE + 1000), contentDeleteAt: iso(BASE + 3_601_000) });
  const ack = completeAck(); ack.attempt.item = moved;
  assert.equal(cache.acceptCompleted(ack, moved, 15_000, 15_000, cache.authEpoch), false);
  assert.equal(cache.cards.get(ITEM).contentDeleteAt, card().contentDeleteAt);
});

test("a confirmed late DONE with a purged source can persist its already received item", () => {
  const cache = new PlaybackBubbleCache({ boardId: BOARD });
  const ack = completeAck({ serverNow: iso(BASE + 3_600_000) }); ack.attempt.item = null;
  assert.equal(cache.acceptCompleted(ack, card(), 0, 0, cache.authEpoch), true);
  assert.equal(cache.cards.get(ITEM).content, card().content);
  assert.equal(cache.view(0).visible, false);
});

test("a late DONE can retain its body under a newer matching scope without rewinding authority", () => {
  const cache = new PlaybackBubbleCache({ boardId: BOARD });
  cache.synchronize(snapshot({ bubbles: [], serverNow: iso(BASE + 20_000), revision: "9007199254740995" }), 20_000);
  const revision = cache.revision, anchor = cache.anchor, lease = cache.lease;
  assert.equal(cache.acceptCompleted(completeAck(), card(), 21_000, 21_000, cache.authEpoch), true);
  assert.equal(cache.cards.get(ITEM).content, card().content);
  assert.equal(cache.revision, revision); assert.equal(cache.anchor, anchor); assert.equal(cache.lease, lease);
  cache.hide(ITEM);
  assert.equal(cache.acceptCompleted(completeAck(), card(), 22_000, 22_000, cache.authEpoch), false);
});

test("the staff catalog can read ambient cards after lease expiry without writing shared storage", () => {
  const storage = memoryStorage(), cache = new PlaybackBubbleCache({ boardId: BOARD, storage });
  cache.synchronize(snapshot(), 0);
  const before = [...storage.data];
  const staff = readPlaybackBubbleCatalog({ boardId: BOARD, storage, snapshots: [snapshot({
    bubbles: [], serverNow: iso(BASE + 3_600_000), onlineLeaseUntil: iso(BASE + 1000), mode: "PAUSED" })] });
  assert.deepEqual(staff.bubbles.map(row => [row.content, row.phase]), [[card().content, "AMBIENT"]]);
  assert.equal(staff.storageAvailable, true); assert.equal(staff.cacheValid, true);
  assert.deepEqual([...storage.data], before);
  const hidden = readPlaybackBubbleCatalog({ boardId: BOARD, storage, snapshots: [snapshot({ bubbles: [], removedIds: [ITEM] })] });
  assert.deepEqual(hidden.bubbles, []);
  assert.deepEqual([...storage.data], before);
});

test("staff catalog rejects stale scopes and pages while retaining storage read-only", () => {
  const storage = memoryStorage(), cache = new PlaybackBubbleCache({ boardId: BOARD, storage });
  cache.synchronize(snapshot(), 0);
  const before = [...storage.data];
  const changed = readPlaybackBubbleCatalog({ boardId: BOARD, storage, snapshots: [snapshot({ deviceGeneration: "2", bubbles: [] })] });
  assert.deepEqual(changed.bubbles, []); assert.equal(changed.cacheValid, false);
  assert.equal(changed.storageAvailable, true);
  assert.throws(() => readPlaybackBubbleCatalog({ boardId: BOARD, storage, snapshots: [snapshot(), snapshot({ cacheResetToken: "1" })] }), /scope changed/);
  assert.throws(() => readPlaybackBubbleCatalog({ boardId: BOARD, storage, snapshots: [] }), /Authenticated/);
  assert.deepEqual([...storage.data], before);
});

test("stable animation frames reuse a view and only rebuild at a phase boundary", () => {
  const cache = new PlaybackBubbleCache({ boardId: BOARD });
  cache.synchronize(snapshot({ serverNow: iso(BASE + 3_599_000), onlineLeaseUntil: iso(BASE + 3_630_000) }), 0);
  const before = cache.view(0);
  assert.equal(cache.view(500), before);
  assert.equal(cache.view(999), before);
  const after = cache.view(1000);
  assert.notEqual(after, before); assert.equal(after.bubbles[0].phase, "AMBIENT");
  assert.equal(cache.view(1100), after);
  cache.hide(ITEM);
  assert.equal(cache.view(1200).bubbles.length, 0);
});

test("the default cache retains a full seventeen-hour day and reuses 4080-card views", t => {
  const first = BASE - 6 * 3_600_000, end = first + 4079 * 15_000;
  const segmenter = new Intl.Segmenter("ko", { granularity: "grapheme" });
  const samples = [["Korean", String.fromCodePoint(0xac00).repeat(30)],
    ["emoji", String.fromCodePoint(0x1f600).repeat(30)],
    ["family ZWJ", String.fromCodePoint(0x1f468, 0x200d, 0x1f469, 0x200d, 0x1f467, 0x200d, 0x1f466).repeat(30)]];
  for (const [kind, content] of samples) {
    assert.equal([...segmenter.segment(content)].length, 30);
    const storage = memoryStorage(), cache = new PlaybackBubbleCache({ boardId: BOARD, storage });
    assert.equal(cache.maxCards, 5000);
    const cards = Array.from({ length: 4080 }, (_, index) => card({
      id: `22222222-2222-4222-8222-${String(index + 1).padStart(12, "0")}`,
      content, readySequence: String(index + 1), firstStartAuthorizedAt: iso(first + index * 15_000),
      contentDeleteAt: iso(first + index * 15_000 + 3_600_000),
    }));
    const metadata = { serverNow: iso(end), onlineLeaseUntil: iso(end + 30_000), snapshotAt: iso(end) };
    for (let offset = 0; offset < cards.length; offset += 100) {
      assert.equal(cache.synchronize(snapshot({ ...metadata, revision: String(offset + 1),
        bubbles: cards.slice(offset, offset + 100) }), 0), true);
    }
    const raw = storage.getItem(cache.key);
    assert.ok(raw.length < 4_000_000); // Existing UTF-16 code-unit read guard is unchanged.
    const started = performance.now(), view = cache.view(0), firstViewMs = performance.now() - started;
    assert.equal(view.totalCount, 4080); assert.equal(view.bubbles.length, 4080);
    const stableStarted = performance.now();
    for (let frame = 0; frame < 60_000; frame++) assert.equal(cache.view(frame / 10), view);
    const stableViewsMs = performance.now() - stableStarted;
    const reopened = new PlaybackBubbleCache({ boardId: BOARD, storage });
    assert.equal(reopened.synchronize(snapshot({ ...metadata, revision: "4081", bubbles: [] }), 0), true);
    assert.equal(reopened.view(0).bubbles.length, 4080);
    t.diagnostic(`${kind}: UTF-8 ${Buffer.byteLength(raw, "utf8")} bytes; UTF-16 ${Buffer.byteLength(raw, "utf16le")} bytes; ` +
      `first view ${firstViewMs.toFixed(2)} ms; 60000 stable reads ${stableViewsMs.toFixed(2)} ms (synthetic Node only)`);
  }
});

test("the expanded default cache still enforces a bounded configurable capacity and size guard", () => {
  for (const maxCards of [0, 10_001, Infinity]) {
    assert.throws(() => new PlaybackBubbleCache({ boardId: BOARD, maxCards }), /configuration/);
  }
  const storage = memoryStorage(), cache = new PlaybackBubbleCache({ boardId: BOARD, storage, maxCards: 2 });
  const cards = [0, 1, 2].map(index => card({
    id: `22222222-2222-4222-8222-${String(index + 1).padStart(12, "0")}`,
    firstStartAuthorizedAt: iso(BASE - 3000 + index * 1000), contentDeleteAt: iso(BASE - 3000 + index * 1000 + 3_600_000),
  }));
  assert.equal(cache.synchronize(snapshot({ bubbles: cards }), 0), true);
  assert.deepEqual(cache.view(0).bubbles.map(row => row.id), cards.slice(1).map(row => row.id));
  storage.setItem(cache.key, " ".repeat(4_000_001));
  const reopened = new PlaybackBubbleCache({ boardId: BOARD, storage });
  assert.equal(reopened.synchronize(snapshot({ bubbles: [] }), 0), true);
  assert.equal(reopened.view(0).bubbles.length, 0);
});

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
