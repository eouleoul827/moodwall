/** v2 playback cache: retain already displayed text locally until KST midnight. */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DECIMAL = /^(0|[1-9][0-9]{0,18})$/;
const decimal = value => typeof value === "string" && DECIMAL.test(value);
const HOUR = 3_600_000;
const DAY = 24 * HOUR;
const KST = 9 * HOUR;
function time(value) {
  const result = typeof value === "string" ? Date.parse(value) : NaN;
  if (!Number.isFinite(result)) throw new TypeError("Invalid cache timestamp");
  return result;
}
function validateScope(snapshot, boardId) {
  const now = time(snapshot.serverNow);
  const businessDate = new Date(now + KST).toISOString().slice(0, 10);
  const until = Date.parse(`${businessDate}T00:00:00+09:00`) + DAY;
  if (snapshot.displayProtocolVersion !== "2" || snapshot.boardId !== boardId ||
      snapshot.businessDate !== businessDate || time(snapshot.ambientUntil) !== until ||
      !decimal(snapshot.deviceGeneration) || !decimal(snapshot.cacheResetToken) ||
      !decimal(snapshot.revision) || !Array.isArray(snapshot.bubbles) || snapshot.bubbles.length > 100 ||
      !Array.isArray(snapshot.removedIds) || snapshot.removedIds.length > 1000 ||
      !snapshot.removedIds.every(id => typeof id === "string" && UUID.test(id)) ||
      !["MOODWALL", "CHATBOT", "PAUSED"].includes(snapshot.mode)) {
    throw new TypeError("Invalid v2 cache snapshot");
  }
  if (snapshot.onlineLeaseUntil !== null) time(snapshot.onlineLeaseUntil);
  return { boardId, businessDate, deviceGeneration: snapshot.deviceGeneration,
    cacheResetToken: snapshot.cacheResetToken, until, now };
}
function validateCard(value, scope) {
  if (!value || !UUID.test(value.id) || typeof value.content !== "string" || !value.content.length ||
      value.content.length > 2048 || value.displayProtocolVersion !== "2" ||
      !decimal(value.readySequence) || typeof value.emotion !== "string" || value.emotion.length > 50 ||
      typeof value.sceneId !== "string" || !value.sceneId || value.sceneId.length > 50 ||
      typeof value.sceneVersion !== "string" || value.sceneVersion.length > 50 ||
      typeof value.sceneSeed !== "string" || value.sceneSeed.length > 100 ||
      !(value.animationId === null || typeof value.animationId === "string" && value.animationId.length <= 50)) {
    throw new TypeError("Invalid v2 cached bubble");
  }
  const first = time(value.firstStartAuthorizedAt), expires = time(value.contentDeleteAt);
  if (expires !== first + HOUR || time(value.ambientUntil) !== scope.until || first > scope.now ||
      new Date(first + KST).toISOString().slice(0, 10) !== scope.businessDate) {
    throw new TypeError("Invalid v2 bubble lifetime");
  }
  // Whitelist fields so neither arbitrary payload fields nor private inference data persist.
  return { id: value.id, content: value.content, emotion: value.emotion, animationId: value.animationId,
    sceneId: value.sceneId, sceneVersion: value.sceneVersion, sceneSeed: value.sceneSeed,
    readySequence: value.readySequence, firstStartAuthorizedAt: value.firstStartAuthorizedAt,
    contentDeleteAt: value.contentDeleteAt, ambientUntil: value.ambientUntil,
    displayProtocolVersion: "2", first, expires };
}
const sameScope = (a, b) => a && b && a.boardId === b.boardId && a.businessDate === b.businessDate &&
  a.deviceGeneration === b.deviceGeneration && a.cacheResetToken === b.cacheResetToken;

export class PlaybackBubbleCache {
  constructor({ boardId, storage = null, maxCards = 2000 }) {
    if (!UUID.test(boardId) || !Number.isInteger(maxCards) || maxCards < 1 || maxCards > 10_000) {
      throw new TypeError("Invalid v2 cache configuration");
    }
    this.boardId = boardId;
    this.storage = storage;
    this.maxCards = maxCards;
    this.key = `moodwall.display.playback.v1:${boardId}`;
    this.cards = new Map();
    this.hidden = new Set();
    this.scope = null;
    this.connected = false;
    this.storageAvailable = Boolean(storage);
    this.revision = -1n;
    // Never reinterpret acceptedAt + 1h legacy records as first-start records.
    try {
      storage?.removeItem(`moodwall.display.day.v1:${boardId}`);
      storage?.removeItem(`moodwall.display.day.v2:${boardId}`);
    } catch { this.storageAvailable = false; }
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
      if (saved.version !== 1 || !sameScope(saved.scope, scope) || !Array.isArray(saved.cards) ||
          saved.cards.length > this.maxCards) throw new TypeError("Stale cache");
      for (const value of saved.cards) {
        const card = validateCard(value, scope);
        this.cards.set(card.id, card);
      }
    } catch { this.cards.clear(); this.#removeStored(); }
  }

  #save() {
    try {
      this.storage?.setItem(this.key, JSON.stringify({ version: 1, scope: this.scope,
        cards: [...this.cards.values()] }));
      this.storageAvailable = Boolean(this.storage);
    } catch { this.storageAvailable = false; }
  }

  synchronize(snapshot, monotonicNow, requestStartedAt = monotonicNow) {
    const rtt = monotonicNow - requestStartedAt;
    if (!Number.isFinite(rtt) || rtt < 0 || rtt >= 5000 || !Number.isFinite(monotonicNow)) {
      this.disconnect(); return false;
    }
    let next, incoming;
    try {
      next = validateScope(snapshot, this.boardId);
      incoming = snapshot.bubbles.map(value => validateCard(value, next));
    } catch { this.disconnect(); return false; }
    if (this.scope && (next.businessDate < this.scope.businessDate ||
        BigInt(next.deviceGeneration) < BigInt(this.scope.deviceGeneration) ||
        next.deviceGeneration === this.scope.deviceGeneration &&
        next.businessDate === this.scope.businessDate && (BigInt(snapshot.revision) < this.revision ||
          BigInt(next.cacheResetToken) < BigInt(this.scope.cacheResetToken)))) return false;
    if (sameScope(next, this.scope)) {
      const staged = new Map(this.cards);
      for (const card of incoming) {
        const existing = staged.get(card.id);
        if (existing && (existing.first !== card.first || existing.expires !== card.expires ||
            existing.content !== card.content)) { this.disconnect(); return false; }
        staged.set(card.id, card);
      }
    }
    if (!sameScope(next, this.scope)) {
      const initial = this.scope === null;
      this.cards.clear(); this.hidden.clear();
      if (initial) this.#restore(next);
      else this.#removeStored();
    }
    const oldNow = this.scope ? this.scope.now + Math.max(0, monotonicNow - this.anchor) : 0;
    next.now = Math.max(next.now + rtt, oldNow);
    this.scope = next;
    this.anchor = monotonicNow;
    this.revision = BigInt(snapshot.revision);
    this.lease = snapshot.onlineLeaseUntil === null ? 0 : time(snapshot.onlineLeaseUntil);
    this.mode = snapshot.mode;
    for (const id of snapshot.removedIds) { this.hidden.add(id); this.cards.delete(id); }
    // Missing IDs may be another page or deleted source rows; absence never removes local text.
    for (const card of incoming) if (!this.hidden.has(card.id)) this.cards.set(card.id, card);
    const ordered = [...this.cards.values()].sort((a, b) => a.first - b.first || a.id.localeCompare(b.id));
    for (const card of ordered.slice(0, Math.max(0, ordered.length - this.maxCards))) this.cards.delete(card.id);
    this.connected = true;
    this.#save();
    return true;
  }

  view(monotonicNow) {
    const empty = { visible: false, bubbles: [], totalCount: 0 };
    if (!this.scope || !Number.isFinite(monotonicNow) || monotonicNow < this.anchor) return empty;
    const now = this.scope.now + monotonicNow - this.anchor;
    if (now >= this.scope.until) { this.clear(); return empty; }
    if (!this.connected || this.mode !== "MOODWALL" || now >= this.lease) return empty;
    const bubbles = [...this.cards.values()].sort((a, b) => a.first - b.first || a.id.localeCompare(b.id))
      .map(({ first, expires, ...card }) => ({ ...card, phase: now >= expires ? "AMBIENT" : "FLOATING" }));
    return { visible: true, bubbles, totalCount: bubbles.length };
  }

  disconnect() { this.connected = false; }
  hide(id) {
    if (!UUID.test(id)) throw new TypeError("Invalid hidden bubble ID");
    this.hidden.add(id); this.cards.delete(id); this.#save();
  }
  clear() {
    this.cards.clear(); this.hidden.clear(); this.scope = null; this.connected = false;
    this.revision = -1n; this.#removeStored();
  }
}

/** Authenticated sequential pagination, with cancellation fencing for reconnect/auth changes. */
export class PlaybackBubbleSynchronizer {
  constructor({ cache, fetchPage, now = () => performance.now() }) {
    if (!(cache instanceof PlaybackBubbleCache) || typeof fetchPage !== "function" || typeof now !== "function") {
      throw new TypeError("Invalid v2 synchronization configuration");
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
    const epoch = this.epoch, controller = new AbortController();
    this.controller = controller;
    let cursor = null, snapshotAt = null, pages = 0, restarted = false;
    const seen = new Set();
    try {
      while (pages < 10_000) {
        const started = this.now();
        let timer, rejectAbort;
        const timeout = new Promise((_, reject) => {
          rejectAbort = () => reject(controller.signal.reason);
          controller.signal.addEventListener("abort", rejectAbort, { once: true });
          timer = setTimeout(() => controller.abort(Object.assign(new Error("Display request timed out"), {
            code: "DISPLAY_REQUEST_TIMEOUT",
          })), 5000);
        });
        let page;
        try { page = await Promise.race([this.fetchPage({ cursor, signal: controller.signal }), timeout]); }
        catch (error) {
          if (epoch !== this.epoch) return { completed: false, pages };
          if (cursor !== null && !restarted && error?.status === 422 && error?.code === "INVALID_CURSOR") {
            cursor = null; snapshotAt = null; seen.clear(); restarted = true; continue;
          }
          throw error;
        } finally {
          clearTimeout(timer);
          controller.signal.removeEventListener("abort", rejectAbort);
        }
        if (epoch !== this.epoch || controller.signal.aborted) return { completed: false, pages };
        if (snapshotAt !== null && page.snapshotAt !== snapshotAt) throw new TypeError("Snapshot changed while paging");
        time(page.snapshotAt);
        if (time(page.snapshotAt) > time(page.serverNow)) throw new TypeError("Invalid snapshot time");
        if (!this.cache.synchronize(page, this.now(), started)) throw new TypeError("Invalid or stale v2 snapshot");
        pages++;
        snapshotAt = page.snapshotAt;
        cursor = page.nextCursor;
        if (cursor === null) return { completed: true, pages };
        if (typeof cursor !== "string" || !cursor.length || cursor.length > 2048 || seen.has(cursor)) {
          throw new TypeError("Invalid display cursor");
        }
        seen.add(cursor);
      }
      throw new TypeError("Display page limit exceeded");
    } catch (error) {
      if (epoch !== this.epoch) return { completed: false, pages };
      if ([401, 403].includes(error?.status)) this.resetAuthentication();
      else this.cache.disconnect();
      throw error;
    } finally { if (this.controller === controller) this.controller = null; }
  }
}
