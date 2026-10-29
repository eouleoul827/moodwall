/**
 * Browser-side day cache. A database expiry changes opacity, not the content.
 * Synchronize only authenticated /api/display/state pages; never render storage first.
 * Use monotonic milliseconds (performance.now), not the browser wall clock.
 */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DECIMAL = /^(0|[1-9][0-9]{0,18})$/;
const DAY = 86_400_000;
const HOUR = 3_600_000;
const KST = 9 * HOUR;

function timestamp(value) {
  const result = typeof value === "string" ? Date.parse(value) : NaN;
  if (!Number.isFinite(result)) throw new TypeError("Invalid display time");
  return result;
}

function context(snapshot, boardId) {
  const now = timestamp(snapshot.serverNow);
  const businessDate = new Date(now + KST).toISOString().slice(0, 10);
  const until = Date.parse(`${businessDate}T00:00:00+09:00`) + DAY;
  if (snapshot.boardId !== boardId || snapshot.businessDate !== businessDate ||
      timestamp(snapshot.ambientUntil) !== until || timestamp(snapshot.snapshotAt) > now ||
      typeof snapshot.deviceGeneration !== "string" || !DECIMAL.test(snapshot.deviceGeneration) ||
      typeof snapshot.cacheResetToken !== "string" || !DECIMAL.test(snapshot.cacheResetToken) ||
      !Number.isSafeInteger(snapshot.revision) || snapshot.revision < 0 ||
      !Array.isArray(snapshot.removedIds) || snapshot.removedIds.length > 1000 ||
      !snapshot.removedIds.every(id => typeof id === "string" && UUID.test(id)) ||
      new Set(snapshot.removedIds).size !== snapshot.removedIds.length ||
      !Array.isArray(snapshot.bubbles) || snapshot.bubbles.length > 100 ||
      !Number.isSafeInteger(snapshot.bubbleTotalCount) || snapshot.bubbleTotalCount < snapshot.bubbles.length ||
      !(snapshot.nextCursor === null || (typeof snapshot.nextCursor === "string" &&
        snapshot.nextCursor.length > 0 && snapshot.nextCursor.length <= 2048)) ||
      !["MOODWALL", "CHATBOT", "PAUSED"].includes(snapshot.mode)) {
    throw new TypeError("Invalid display snapshot");
  }
  return { boardId, businessDate, deviceGeneration: snapshot.deviceGeneration,
    cacheResetToken: snapshot.cacheResetToken, until, now };
}

function card(value, scope) {
  if (!value || typeof value.id !== "string" || !UUID.test(value.id) ||
      typeof value.content !== "string" || !value.content.length || value.content.length > 2048 ||
      typeof value.emojiId !== "string" || value.emojiId.length > 50 ||
      typeof value.emotion !== "string" || value.emotion.length > 50 ||
      !(value.animationId === null || (typeof value.animationId === "string" && value.animationId.length <= 50)) ||
      timestamp(value.ambientUntil) !== scope.until) throw new TypeError("Invalid cached bubble");
  const accepted = timestamp(value.acceptedAt);
  const expires = timestamp(value.expiresAt);
  if (expires !== accepted + HOUR ||
      new Date(accepted + KST).toISOString().slice(0, 10) !== scope.businessDate || accepted > scope.now) {
    throw new TypeError("Bubble belongs to another day or lifetime");
  }
  return { id: value.id, content: value.content, emojiId: value.emojiId, emotion: value.emotion,
    animationId: value.animationId, acceptedAt: value.acceptedAt, expiresAt: value.expiresAt,
    ambientUntil: value.ambientUntil, expires, accepted };
}

function sameScope(left, right) {
  return left && left.boardId === right.boardId && left.businessDate === right.businessDate &&
    left.deviceGeneration === right.deviceGeneration && left.cacheResetToken === right.cacheResetToken;
}

export class BubbleCache {
  constructor({ boardId, storage = null, maxCards = 2000 }) {
    if (!UUID.test(boardId) || !Number.isInteger(maxCards) || maxCards < 1 || maxCards > 10_000) {
      throw new TypeError("Invalid display cache configuration");
    }
    this.boardId = boardId;
    this.storage = storage;
    this.key = `moodwall.display.day.v2:${boardId}`;
    this.legacyKey = `moodwall.display.day.v1:${boardId}`;
    this.maxCards = maxCards;
    this.cards = new Map();
    this.hidden = new Set();
    this.scope = null;
    this.connected = false;
    this.revision = -1;
    this.sampledNow = -1;
    this.storageAvailable = Boolean(storage);
    // The old spotlight format is deliberately discarded, never displayed or converted.
    try { this.storage?.removeItem(this.legacyKey); }
    catch { this.storageAvailable = false; }
  }

  #removeStored() {
    try { this.storage?.removeItem(this.key); }
    catch { this.storageAvailable = false; }
  }

  #restore(scope) {
    try {
      const raw = this.storage?.getItem(this.key);
      if (!raw) return;
      if (raw.length > 4_000_000) throw new TypeError("Cache too large");
      const saved = JSON.parse(raw);
      if (saved.version !== 2 || !sameScope(saved.scope, scope) || !Array.isArray(saved.cards) ||
          saved.cards.length > this.maxCards) throw new TypeError("Stale display cache");
      for (const value of saved.cards) {
        const restored = card(value, scope);
        this.cards.set(restored.id, restored);
      }
    } catch {
      this.cards.clear();
      this.#removeStored();
    }
  }

  #save() {
    if (!this.storage || !this.scope) return;
    try {
      this.storage.setItem(this.key, JSON.stringify({ version: 2, scope: this.scope,
        cards: [...this.cards.values()] }));
      this.storageAvailable = true;
    } catch { this.storageAvailable = false; } // Preserve memory when storage is blocked or full.
  }

  synchronize(snapshot, monotonicNow) {
    if (!Number.isFinite(monotonicNow)) throw new TypeError("A monotonic timestamp is required");
    let next, incoming;
    try {
      next = context(snapshot, this.boardId);
      incoming = snapshot.bubbles.map(value => card(value, next));
      if (snapshot.onlineLeaseUntil !== null) timestamp(snapshot.onlineLeaseUntil);
    } catch {
      this.disconnect();
      return false;
    }
    if (this.scope) {
      if (next.businessDate < this.scope.businessDate ||
          BigInt(next.deviceGeneration) < BigInt(this.scope.deviceGeneration)) return false;
      if (next.businessDate === this.scope.businessDate &&
          next.deviceGeneration === this.scope.deviceGeneration && snapshot.revision < this.revision) return false;
      if (next.businessDate === this.scope.businessDate &&
          next.deviceGeneration === this.scope.deviceGeneration && snapshot.revision === this.revision &&
          next.now < this.sampledNow) return false;
    }
    if (!sameScope(this.scope, next)) {
      const firstSynchronization = this.scope === null;
      this.cards.clear();
      this.hidden.clear();
      if (firstSynchronization) this.#restore(next);
      else this.#removeStored();
    }
    this.sampledNow = next.now;
    // A delayed response must never rewind the transparent transition or midnight.
    if (this.scope && next.businessDate === this.scope.businessDate &&
        next.deviceGeneration === this.scope.deviceGeneration) {
      next.now = Math.max(next.now, this.scope.now + Math.max(0, monotonicNow - this.anchor));
    }
    this.scope = next;
    for (const id of snapshot.removedIds) {
      this.hidden.add(id);
      this.cards.delete(id);
    }
    // A missing ID can be on another page or deleted from SQL. Absence is not a removal command.
    for (const value of incoming) if (!this.hidden.has(value.id)) this.cards.set(value.id, value);
    if (this.cards.size > this.maxCards) {
      const ordered = [...this.cards.values()].sort((a, b) => a.accepted - b.accepted || a.id.localeCompare(b.id));
      for (const value of ordered.slice(0, this.cards.size - this.maxCards)) this.cards.delete(value.id);
    }
    this.revision = snapshot.revision;
    this.anchor = monotonicNow;
    this.mode = snapshot.mode;
    this.lease = snapshot.onlineLeaseUntil === null ? 0 : timestamp(snapshot.onlineLeaseUntil);
    this.connected = true;
    this.#save();
    return true;
  }

  view(monotonicNow) {
    const empty = { visible: false, bubbles: [], totalCount: 0 };
    if (!this.scope || !Number.isFinite(monotonicNow) || monotonicNow < this.anchor) return empty;
    const now = this.scope.now + monotonicNow - this.anchor;
    if (now >= this.scope.until) {
      this.clear();
      return empty;
    }
    if (!this.connected || this.mode !== "MOODWALL" || now >= this.lease) return empty;
    const bubbles = [...this.cards.values()]
      .sort((a, b) => a.accepted - b.accepted || a.id.localeCompare(b.id))
      .map(({ accepted, expires, ...value }) => ({ ...value, phase: now >= expires ? "AMBIENT" : "FLOATING" }));
    return { visible: true, bubbles, totalCount: bubbles.length };
  }

  disconnect() { this.connected = false; } // Hide offline; retain the day cache for resynchronization.

  clear() {
    this.cards.clear();
    this.hidden.clear();
    this.scope = null;
    this.connected = false;
    this.revision = -1;
    this.sampledNow = -1;
    this.#removeStored();
  }
}

/**
 * Sequential full-page synchronization for one authenticated display session.
 * fetchPage({cursor, signal}) returns decoded JSON or throws {status, code}.
 * Starting another sync, disconnecting, or resetting auth fences old responses.
 */
export class DisplaySynchronizer {
  constructor({ cache, fetchPage, now = () => performance.now() }) {
    if (!(cache instanceof BubbleCache) || typeof fetchPage !== "function" || typeof now !== "function") {
      throw new TypeError("Invalid display synchronization configuration");
    }
    this.cache = cache;
    this.fetchPage = fetchPage;
    this.now = now;
    this.epoch = 0;
    this.controller = null;
  }

  #invalidate() {
    this.epoch++;
    this.controller?.abort();
    this.controller = null;
  }

  resetAuthentication() { this.#invalidate(); this.cache.clear(); }
  disconnect() { this.#invalidate(); this.cache.disconnect(); }

  async synchronize() {
    this.#invalidate();
    const epoch = this.epoch;
    const controller = new AbortController();
    this.controller = controller;
    const current = () => epoch === this.epoch && !controller.signal.aborted;
    let cursor = null, snapshotAt = null, pages = 0, restarted = false;
    const seen = new Set();
    try {
      while (pages < 10_000) {
        let page;
        try { page = await this.fetchPage({ cursor, signal: controller.signal }); }
        catch (error) {
          if (!current()) return { completed: false, pages };
          if (cursor !== null && !restarted && error?.status === 422 && error?.code === "INVALID_CURSOR") {
            cursor = null;
            snapshotAt = null;
            seen.clear();
            restarted = true;
            continue;
          }
          throw error;
        }
        if (!current()) return { completed: false, pages };
        if (snapshotAt !== null && page?.snapshotAt !== snapshotAt) {
          throw new TypeError("Display page snapshot changed during pagination");
        }
        if (!this.cache.synchronize(page, this.now())) {
          throw new TypeError("Invalid or stale display page");
        }
        pages++;
        snapshotAt = page.snapshotAt;
        cursor = page.nextCursor;
        if (cursor === null) return { completed: true, pages };
        if (seen.has(cursor)) throw new TypeError("Repeated display cursor");
        seen.add(cursor);
      }
      throw new TypeError("Display page limit exceeded");
    } catch (error) {
      if (!current()) return { completed: false, pages };
      if (error?.status === 401 || error?.status === 403) this.resetAuthentication();
      else this.cache.disconnect();
      throw error;
    } finally {
      if (this.controller === controller) this.controller = null;
    }
  }
}
