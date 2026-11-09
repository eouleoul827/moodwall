/** v2 playback cache: retain already displayed text locally until KST midnight. */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DECIMAL = /^(0|[1-9][0-9]{0,18})$/;
const decimal = value => typeof value === "string" && DECIMAL.test(value);
const HOUR = 3_600_000;
const DAY = 24 * HOUR;
const KST = 9 * HOUR;
const EMPTY_VIEW = Object.freeze({ visible: false, bubbles: Object.freeze([]), totalCount: 0 });
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
const sameCard = (a, b) => a.first === b.first && a.expires === b.expires && a.content === b.content;
const orderedCards = cards => [...cards].sort((a, b) => a.first - b.first || a.id.localeCompare(b.id));

/** Staff-only catalog. Call only with freshly authenticated snapshot pages.
 * This deliberately ignores the online lease and NEVER writes shared storage.
 */
export function readPlaybackBubbleCatalog({ boardId, storage = null, snapshots }) {
  if (!UUID.test(boardId) || !Array.isArray(snapshots) || !snapshots.length) {
    throw new TypeError("Authenticated display snapshots are required");
  }
  let scope = null, revision = -1n, snapshotAt = null;
  const incoming = new Map(), hidden = new Set();
  for (const page of snapshots) {
    const next = validateScope(page, boardId);
    if (scope && (!sameScope(scope, next) || next.now < scope.now || BigInt(page.revision) < revision) ||
        snapshotAt !== null && page.snapshotAt !== snapshotAt || time(page.snapshotAt) > next.now) {
      throw new TypeError("Display catalog scope changed while paging");
    }
    snapshotAt = page.snapshotAt; scope = next; revision = BigInt(page.revision);
    for (const id of page.removedIds) hidden.add(id);
    for (const value of page.bubbles) {
      const card = validateCard(value, scope), previous = incoming.get(card.id);
      if (previous && !sameCard(previous, card)) throw new TypeError("Display catalog card changed");
      incoming.set(card.id, card);
    }
  }
  let storageAvailable = Boolean(storage), cacheValid = false;
  const cards = new Map();
  let raw;
  try { raw = storage?.getItem(`moodwall.display.playback.v1:${boardId}`); }
  catch { storageAvailable = false; }
  try {
    if (raw) {
      if (raw.length > 4_000_000) throw new TypeError("Cache too large");
      const saved = JSON.parse(raw);
      if (saved.version !== 1 || !sameScope(saved.scope, scope) || !Array.isArray(saved.cards) || saved.cards.length > 10_000) {
        throw new TypeError("Stale cache");
      }
      for (const value of saved.cards) { const card = validateCard(value, scope); cards.set(card.id, card); }
      cacheValid = true;
    }
  } catch { cards.clear(); }
  for (const card of incoming.values()) {
    const previous = cards.get(card.id);
    if (previous && !sameCard(previous, card)) throw new TypeError("Stored display card changed");
    cards.set(card.id, card);
  }
  for (const id of hidden) cards.delete(id);
  const bubbles = orderedCards(cards.values()).map(({ first, expires, ...card }) =>
    ({ ...card, phase: scope.now >= expires ? "AMBIENT" : "FLOATING" }));
  return { bubbles, storageAvailable, cacheValid, businessDate: scope.businessDate,
    deviceGeneration: scope.deviceGeneration };
}

export class PlaybackBubbleCache {
  constructor({ boardId, storage = null, maxCards = 5000 }) {
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
    this.authEpoch = 0;
    this.cachedView = null;
    this.nextPhaseAt = Infinity;
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

  #invalidateView() { this.cachedView = null; this.nextPhaseAt = Infinity; }

  #stale(next, revision) {
    return this.scope && (next.businessDate < this.scope.businessDate ||
      BigInt(next.deviceGeneration) < BigInt(this.scope.deviceGeneration) ||
      next.deviceGeneration === this.scope.deviceGeneration && next.businessDate === this.scope.businessDate &&
      (BigInt(revision) < this.revision || BigInt(next.cacheResetToken) < BigInt(this.scope.cacheResetToken)));
  }

  #merge(next, incoming, removedIds, revision, monotonicNow, rtt) {
    if (sameScope(next, this.scope)) {
      for (const card of incoming) {
        const previous = this.cards.get(card.id);
        if (previous && !sameCard(previous, card)) return false;
      }
    }
    if (!sameScope(next, this.scope)) {
      const initial = this.scope === null;
      this.cards.clear(); this.hidden.clear(); this.connected = false;
      if (initial) this.#restore(next);
      else this.#removeStored();
    }
    const oldNow = this.scope ? this.scope.now + Math.max(0, monotonicNow - this.anchor) : 0;
    next.now = Math.max(next.now + rtt, oldNow);
    this.scope = next; this.anchor = monotonicNow; this.revision = BigInt(revision);
    for (const id of removedIds) { this.hidden.add(id); this.cards.delete(id); }
    for (const card of incoming) if (!this.hidden.has(card.id)) this.cards.set(card.id, card);
    const ordered = orderedCards(this.cards.values());
    for (const card of ordered.slice(0, Math.max(0, ordered.length - this.maxCards))) this.cards.delete(card.id);
    this.#invalidateView(); this.#save();
    return true;
  }

  /** Persist only a confirmed DONE under its authenticated scope. An ACK never
   * establishes/extends public visibility, mode or the snapshot online lease.
   * expectedAuthEpoch must be captured before sending the complete command.
   */
  acceptCompleted(ack, item, monotonicNow, requestStartedAt, expectedAuthEpoch) {
    const rtt = monotonicNow - requestStartedAt;
    if (expectedAuthEpoch !== this.authEpoch || !Number.isFinite(monotonicNow) ||
        !Number.isFinite(rtt) || rtt < 0 || rtt >= 5000) return false;
    let next, card;
    try {
      const metadata = ack?.cacheScope;
      next = validateScope({ ...metadata, bubbles: [] }, this.boardId);
      const completed = ack.attempt;
      if (ack.displayProtocolVersion !== "2" || ack.boardId !== next.boardId ||
          ack.deviceGeneration !== next.deviceGeneration || ack.revision !== metadata.revision ||
          ack.mode !== metadata.mode || time(ack.serverNow) !== next.now ||
          !UUID.test(completed?.attemptId) || completed.status !== "DONE" || completed.presentationStatus !== "DONE" ||
          completed.deviceGeneration !== next.deviceGeneration || completed.submissionId !== item?.id ||
          (completed.item !== null && (completed.item.id !== item.id || completed.item.content !== item.content ||
            completed.item.firstStartAuthorizedAt !== item.firstStartAuthorizedAt ||
            completed.item.contentDeleteAt !== item.contentDeleteAt))) return false;
      card = validateCard(item, next);
      const matches = sameScope(next, this.scope);
      if (metadata.removedIds.includes(card.id) || matches && this.hidden.has(card.id)) return false;
      const priorNow = this.scope ? this.scope.now + Math.max(0, monotonicNow - this.anchor) : 0;
      if (Math.max(next.now + rtt, priorNow) >= next.until) return false;
      if (matches && BigInt(metadata.revision) < this.revision) {
        // A later page may have newer board metadata but a snapshotAt before
        // this completion. Keep the confirmed body under the newer scope;
        // never rewind its clock, revision, tombstones, mode or online lease.
        const previous = this.cards.get(card.id);
        if (previous && !sameCard(previous, card)) return false;
        for (const id of metadata.removedIds) { this.hidden.add(id); this.cards.delete(id); }
        this.cards.set(card.id, card);
        const ordered = orderedCards(this.cards.values());
        for (const old of ordered.slice(0, Math.max(0, ordered.length - this.maxCards))) this.cards.delete(old.id);
        this.#invalidateView(); this.#save();
        return true;
      }
      if (this.#stale(next, metadata.revision)) return false;
      if (!this.#merge(next, [card], metadata.removedIds, metadata.revision, monotonicNow, rtt)) return false;
      if (metadata.mode !== "MOODWALL") this.disconnect();
      return true;
    } catch { return false; }
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
    if (this.#stale(next, snapshot.revision)) return false;
    if (!this.#merge(next, incoming, snapshot.removedIds, snapshot.revision, monotonicNow, rtt)) {
      this.disconnect(); return false;
    }
    this.lease = snapshot.onlineLeaseUntil === null ? 0 : time(snapshot.onlineLeaseUntil);
    this.mode = snapshot.mode;
    this.connected = true;
    return true;
  }

  view(monotonicNow) {
    if (!this.scope || !Number.isFinite(monotonicNow) || monotonicNow < this.anchor) return EMPTY_VIEW;
    const now = this.scope.now + monotonicNow - this.anchor;
    if (now >= this.scope.until) { this.clear(); return EMPTY_VIEW; }
    if (!this.connected || this.mode !== "MOODWALL" || now >= this.lease) return EMPTY_VIEW;
    if (!this.cachedView || now >= this.nextPhaseAt) {
      this.nextPhaseAt = Infinity;
      const bubbles = orderedCards(this.cards.values()).map(({ first, expires, ...card }) => {
        if (expires > now) this.nextPhaseAt = Math.min(this.nextPhaseAt, expires);
        return { ...card, phase: now >= expires ? "AMBIENT" : "FLOATING" };
      });
      this.cachedView = { visible: true, bubbles, totalCount: bubbles.length };
    }
    return this.cachedView;
  }

  disconnect() { this.connected = false; }
  hide(id) {
    if (!UUID.test(id)) throw new TypeError("Invalid hidden bubble ID");
    this.hidden.add(id); this.cards.delete(id); this.#invalidateView(); this.#save();
  }
  clear() {
    this.cards.clear(); this.hidden.clear(); this.scope = null; this.connected = false;
    this.revision = -1n; this.authEpoch++; this.#invalidateView(); this.#removeStored();
  }
}

/** Authenticated sequential pagination, with cancellation fencing for reconnect/auth changes. */
export class PlaybackBubbleSynchronizer {
  constructor({ cache, fetchPage, now = () => performance.now(), onSnapshot = () => {} }) {
    if (!(cache instanceof PlaybackBubbleCache) || typeof fetchPage !== "function" || typeof now !== "function") {
      throw new TypeError("Invalid v2 synchronization configuration");
    }
    this.cache = cache;
    this.fetchPage = fetchPage;
    this.now = now;
    this.onSnapshot = onSnapshot;
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
        this.onSnapshot(page);
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
      if (error?.status === 401) this.resetAuthentication();
      else this.cache.disconnect();
      throw error;
    } finally { if (this.controller === controller) this.controller = null; }
  }
}
