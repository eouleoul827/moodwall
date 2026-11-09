/** Framework-independent v2 player. The renderer MUST draw content as text, never HTML. */
import { secureUuid } from "../uuid.mjs";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DECIMAL = /^(0|[1-9][0-9]{0,18})$/;
const decimal = value => typeof value === "string" && DECIMAL.test(value);
const HOUR = 3_600_000;
const DURATION = 15_000;
// The server picks each attempt's full-screen time from its backlog (5-15 s) and
// enforces the same value on complete. Older servers omit it and keep 15 s.
const MIN_DURATION = 5_000;
const validDuration = value => Number.isInteger(value) && value >= MIN_DURATION && value <= DURATION;
export function playbackDuration(current) {
  return validDuration(current?.durationMs) ? current.durationMs : DURATION;
}
const DROP_PRESENTATION = new Set(["HIDDEN", "FAILED", "EXPIRED", "CANCELLED"]);
const parseTime = value => {
  const time = typeof value === "string" ? Date.parse(value) : NaN;
  if (!Number.isFinite(time)) throw new TypeError("Invalid playback timestamp");
  return time;
};
const failure = (code, status = 0) => Object.assign(new Error(code), { code, status });
const transient = error => !error?.status || error.status === 429 || error.status >= 500;

function wait(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason);
    const timer = setTimeout(done, ms);
    function done() { signal?.removeEventListener("abort", cancel); resolve(); }
    function cancel() { clearTimeout(timer); reject(signal.reason); }
    signal?.addEventListener("abort", cancel, { once: true });
  });
}

export function browserPlaybackClock() {
  return {
    now: () => performance.now(),
    sleep: wait,
    nextFrame: signal => new Promise((resolve, reject) => {
      if (signal?.aborted) return reject(signal.reason);
      const id = requestAnimationFrame(() => {
        signal?.removeEventListener("abort", cancel);
        resolve();
      });
      function cancel() { cancelAnimationFrame(id); reject(signal.reason); }
      signal?.addEventListener("abort", cancel, { once: true });
    }),
    setTimer: (fn, ms) => setTimeout(fn, ms),
    clearTimer: id => clearTimeout(id),
  };
}

/** Same-origin cookies + injected CSRF token; no credentials in a URL or localStorage. */
export class FetchPlaybackTransport {
  constructor({ fetch = globalThis.fetch, csrfToken = () => "", baseUrl = "" } = {}) {
    if (typeof fetch !== "function" || typeof csrfToken !== "function" ||
        (baseUrl !== "" && !baseUrl.startsWith("/")) || baseUrl.startsWith("//")) {
      throw new TypeError("Invalid same-origin playback transport");
    }
    this.fetch = fetch;
    this.csrfToken = csrfToken;
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  async request({ action, attemptId, body, idempotencyKey, signal, clientInstanceId }) {
    let path, method = "POST";
    if (action === "state") {
      method = "GET";
      path = `/api/display/playback?clientInstanceId=${encodeURIComponent(clientInstanceId)}`;
    } else if (action === "heartbeat") path = "/api/display/heartbeat";
    else if (action === "claim") path = "/api/display/playback/claim";
    else if (["start", "frame", "complete", "interrupt"].includes(action) && UUID.test(attemptId)) {
      path = `/api/display/playback/${attemptId}/${action}`;
    } else throw new TypeError("Unknown playback action");
    const headers = { Accept: "application/json" };
    if (method !== "GET") {
      headers["Content-Type"] = "application/json";
      headers["X-CSRF-Token"] = this.csrfToken();
      if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
    }
    const response = await this.fetch(this.baseUrl + path, {
      method, credentials: "same-origin", cache: "no-store", headers, signal,
      ...(method === "POST" ? { body: JSON.stringify(body) } : {}),
    });
    if (response.status === 204) return null;
    let result;
    try { result = await response.json(); }
    catch { throw failure("INVALID_PLAYBACK_RESPONSE", response.ok ? 0 : response.status); }
    if (!response.ok) throw Object.assign(failure(result.code || "PLAYBACK_REQUEST_FAILED", response.status), {
      retryAfterMs: Math.min(5_000, Math.max(0, Number(response.headers.get("Retry-After")) * 1000)) || 250,
    });
    return result;
  }
}

function envelope(value, clientInstanceId, allowForeignOwner = false) {
  if (!value || value.displayProtocolVersion !== "2" || !UUID.test(value.boardId) ||
      !decimal(value.deviceGeneration) || !decimal(value.playbackEpoch) ||
      !decimal(value.revision) || !["MOODWALL", "CHATBOT", "PAUSED"].includes(value.mode) ||
      !(value.clientInstanceId === null || UUID.test(value.clientInstanceId)) ||
      !(value.leaseUntil === null || Number.isFinite(parseTime(value.leaseUntil)))) {
    throw failure("DISPLAY_VERSION_UNSUPPORTED", 409);
  }
  parseTime(value.serverNow);
  if (!allowForeignOwner && value.clientInstanceId !== null && value.clientInstanceId !== clientInstanceId) {
    throw failure("PLAYBACK_OWNED", 409);
  }
  return value;
}

function attempt(value, previous = null) {
  if (value?.status === "DONE" && value.item === null && DROP_PRESENTATION.has(value.presentationStatus) &&
      UUID.test(value.attemptId) && UUID.test(value.submissionId)) {
    parseTime(value.leaseUntil);
    return value;
  }
  // A late DONE acknowledgement has no source body after retention or midnight.
  // It can confirm completion using only an item this instance already received.
  if (value?.status === "DONE" && value.item === null && previous?.attemptId === value.attemptId) {
    value = { ...value, item: previous.item };
  }
  if (!value || !UUID.test(value.attemptId) || !UUID.test(value.submissionId) || !value.item ||
      value.item.id !== value.submissionId || typeof value.item.content !== "string" ||
      !value.item.content.length || value.item.content.length > 2048 ||
      value.item.displayProtocolVersion !== "2" || !decimal(value.item.readySequence) ||
      typeof value.item.sceneId !== "string" || !value.item.sceneId ||
      typeof value.item.sceneVersion !== "string" || !value.item.sceneVersion ||
      !["CLAIMED", "START_AUTHORIZED", "PLAYING", "DONE", "INTERRUPTED"].includes(value.status)) {
    throw failure("INVALID_PLAYBACK_RESPONSE");
  }
  if (value.durationMs !== undefined && !validDuration(value.durationMs)) throw failure("INVALID_PLAYBACK_RESPONSE");
  parseTime(value.leaseUntil);
  parseTime(value.item.ambientUntil);
  return value;
}

/**
 * renderer: {prepare(item,{signal}), show(item,{durationMs}), hide(), float(item)?}.
 * show() synchronously commits the prepared fullscreen frame; no loading inside show.
 * runOnce() owns at most one item; run() continues until its AbortSignal is cancelled.
 * UNKNOWN retains an immutable command and retries it on the next runOnce().
 */
export class DisplayPlaybackController {
  constructor({ transport, renderer, clock = browserPlaybackClock(),
    visible = () => document.visibilityState === "visible", uuid = secureUuid,
    onState = () => {}, onProblem = () => {}, cache = null }) {
    if (typeof transport?.request !== "function" || typeof renderer?.prepare !== "function" ||
        typeof renderer.show !== "function" || typeof renderer.hide !== "function" ||
        !["now", "sleep", "nextFrame", "setTimer", "clearTimer"].every(key => typeof clock[key] === "function")) {
      throw new TypeError("Invalid playback controller configuration");
    }
    this.transport = transport;
    this.renderer = renderer;
    this.clock = clock;
    this.visible = visible;
    this.uuid = uuid;
    this.clientInstanceId = uuid(); // New for every document, deliberately never restored.
    if (!UUID.test(this.clientInstanceId)) throw new TypeError("Invalid client instance UUID");
    this.onState = onState;
    this.onProblem = onProblem;
    this.cache = cache;
    this.generation = 0;
    this.current = null;
    this.pending = null;
    this.running = null;
    this.stopped = false;
    this.status = "IDLE";
    this.operation = 0;
  }

  async #request(command, signal) {
    const started = this.clock.now();
    const requestController = new AbortController();
    const cancel = () => requestController.abort(signal.reason);
    signal?.addEventListener("abort", cancel, { once: true });
    if (signal?.aborted) cancel();
    let timer;
    const timeout = new Promise((_, reject) => {
      timer = this.clock.setTimer(() => {
        const error = failure("DISPLAY_REQUEST_TIMEOUT");
        requestController.abort(error);
        reject(error);
      }, 5000);
    });
    const aborted = new Promise((_, reject) => {
      requestController.signal.addEventListener("abort", () => reject(requestController.signal.reason), { once: true });
    });
    try {
      if (requestController.signal.aborted) throw requestController.signal.reason;
      const data = await Promise.race([
        this.transport.request({ ...command, signal: requestController.signal, clientInstanceId: this.clientInstanceId }),
        timeout, aborted,
      ]);
      const received = this.clock.now();
      if (received < started || received - started >= 5000) throw failure("DISPLAY_REQUEST_TIMEOUT");
      if (signal?.aborted) throw signal.reason;
      return { data, received, rtt: received - started };
    } finally {
      this.clock.clearTimer(timer);
      signal?.removeEventListener("abort", cancel);
    }
  }

  #accept(sample, allowForeignOwner = false) {
    const data = envelope(sample.data, this.clientInstanceId, allowForeignOwner);
    const upper = parseTime(data.serverNow) + sample.rtt;
    const previous = this.authority;
    if (previous && (data.boardId !== previous.data.boardId ||
        BigInt(data.deviceGeneration) < BigInt(previous.data.deviceGeneration) ||
        (data.deviceGeneration === previous.data.deviceGeneration &&
         BigInt(data.playbackEpoch) < BigInt(previous.data.playbackEpoch)))) {
      throw failure("PLAYBACK_STALE", 409);
    }
    this.authority = { data, now: previous ? Math.max(upper, this.#serverNow()) : upper, anchor: sample.received };
    this.onState(data);
    return data;
  }

  #serverNow() {
    return this.authority.now + Math.max(0, this.clock.now() - this.authority.anchor);
  }

  #body() {
    const data = this.authority.data;
    return { clientInstanceId: this.clientInstanceId, deviceGeneration: data.deviceGeneration,
      playbackEpoch: data.playbackEpoch };
  }

  #command(action, extra = {}) {
    return { action, attemptId: this.current?.attemptId,
      body: { ...this.#body(), ...extra }, idempotencyKey: this.uuid(),
      ...(action === "complete" ? { cacheAuthEpoch: this.cache?.authEpoch } : {}) };
  }

  #guard({ starting = false } = {}) {
    if (!this.visible()) throw failure("PAGE_HIDDEN");
    if (this.stopped || this.controller.signal.aborted) throw failure(this.interruptReason || "CLIENT_ERROR");
    const data = this.authority.data;
    if (data.mode !== "MOODWALL") throw failure("PAGE_HIDDEN");
    const now = this.#serverNow();
    const lease = Math.min(parseTime(data.leaseUntil), parseTime(this.current.leaseUntil));
    if (now >= lease) throw failure("NETWORK");
    if (now >= parseTime(this.current.item.ambientUntil)) {
      this.cache?.clear();
      throw failure("DAY_ENDED");
    }
    if (this.current.item.contentDeleteAt !== null && now >= parseTime(this.current.item.contentDeleteAt)) {
      throw failure("PRESENTATION_EXPIRED", 410);
    }
    if (starting && (now >= parseTime(this.current.startNotAfter) || lease - now < 16_000 ||
        parseTime(this.current.item.ambientUntil) - now < 16_000 ||
        parseTime(this.current.item.contentDeleteAt) - now < 16_000)) {
      throw failure("START_AUTHORIZATION_EXPIRED", 409);
    }
  }

  async #send(command, { retryUntil = null } = {}) {
    this.pending = command;
    for (let tries = 0; tries < 3; tries++) {
      try {
        const sample = await this.#request(command, this.controller.signal);
        if (sample.data === null && command.action === "claim") { this.pending = null; return null; }
        const completedReceipt = command.action === "complete" && sample.data?.attempt?.status === "DONE" &&
          sample.data.attempt.attemptId === command.attemptId;
        const data = this.#accept(sample, completedReceipt);
        if (!data.attempt || data.attempt.attemptId !== command.attemptId && command.action !== "claim") {
          throw failure("PLAYBACK_STALE", 409);
        }
        if (data.attempt) this.current = attempt(data.attempt, this.current);
        if (command.action === "complete") this.lastCompletion = {
          data, received: sample.received, started: sample.received - sample.rtt,
          authEpoch: command.cacheAuthEpoch,
        };
        this.pending = null;
        return data;
      } catch (error) {
        if (this.controller.signal.aborted) throw error;
        const early = command.action === "complete" && error?.code === "PLAYBACK_TOO_EARLY";
        if (!early && !transient(error)) { this.pending = null; throw error; }
        if (tries === 2 || retryUntil !== null && this.#serverNow() + 250 >= retryUntil) throw error;
        await this.clock.sleep(early ? Math.min(1000, Math.max(250, error.retryAfterMs || 250)) :
          Math.min(1000, 250 * 2 ** tries), this.controller.signal);
      }
    }
  }

  /** Hides immediately. The in-flight task records an interruption using the original owner fields. */
  pause(reason = "PAGE_HIDDEN") {
    this.stopped = true;
    this.interruptReason = reason;
    try { this.renderer.hide(); }
    finally { this.controller?.abort(failure(reason)); }
  }

  resume() { this.stopped = false; this.interruptReason = null; }

  resetAuthentication() {
    this.generation++;
    try { this.pause("CLIENT_ERROR"); }
    finally {
      this.pending = null;
      this.current = null;
      this.authority = null;
      this.lastCompletion = null;
      this.cache?.clear();
    }
  }

  async #interrupt(reason) {
    this.renderer.hide();
    if (!this.current || this.current.status === "DONE" || !this.authority) return;
    const allowed = ["HIDDEN", "PAGE_HIDDEN", "RELOAD", "FRAME_GAP", "ASSET_FAILURE", "NETWORK", "CLIENT_ERROR"];
    const command = this.pending?.action === "interrupt" ? this.pending : this.#command("interrupt", {
      reason: allowed.includes(reason) ? reason : "CLIENT_ERROR",
    });
    this.pending = command;
    // A cancelled rendering loop must still make a bounded best-effort stop report.
    try {
      const data = this.#accept(await this.#request(command));
      if (data.attempt?.attemptId !== command.attemptId) throw failure("PLAYBACK_STALE", 409);
      this.pending = null;
    } catch (error) {
      if (!transient(error)) this.pending = null;
    }
  }

  async #observe(epoch, operation) {
    const current = () => epoch === this.generation && operation === this.operation &&
      this.current !== null && this.status === "PLAYING" && this.pending?.action !== "complete";
    try {
      const sample = await this.#request({ action: "state" }, this.controller.signal);
      if (!current()) return;
      const data = this.#accept(sample);
      if (data.attempt && DROP_PRESENTATION.has(data.attempt.presentationStatus)) {
        this.cache?.hide(data.attempt.submissionId);
      }
      if (!data.attempt?.item || data.attempt.status !== "PLAYING" || data.attempt.attemptId !== this.current.attemptId ||
          data.playbackEpoch !== this.ownerEpoch || data.mode !== "MOODWALL") {
        this.pause("HIDDEN");
      } else this.current = attempt(data.attempt);
    } catch (error) {
      if (!current()) return;
      if (error?.status === 401) this.resetAuthentication();
      else if (!transient(error)) this.pause("CLIENT_ERROR");
      // A temporary disconnect can finish only the already authorized item before lease expiry.
    }
  }

  async #play() {
    const generation = this.generation;
    const operation = ++this.operation;
    this.controller = new AbortController();
    if (this.stopped || !this.visible()) return { status: "PAUSED" };
    try {
      // Preserve completion evidence on ambiguous responses; never create another attempt first.
      if (this.pending?.action === "complete") {
        await this.#send(this.pending);
        return this.#finished();
      }
      if (this.pending?.action === "interrupt") {
        await this.#interrupt("CLIENT_ERROR");
        if (this.pending) return { status: "UNKNOWN" };
      }
      if (this.pending?.action !== "claim") {
        this.current = null;
        if (this.lastControlHeartbeat === undefined || this.clock.now() - this.lastControlHeartbeat >= 10_000) {
          await this.#request({ action: "heartbeat", body: { clientInstanceId: this.clientInstanceId,
            displayProtocolVersion: "2" } }, this.controller.signal);
          this.lastControlHeartbeat = this.clock.now();
        }
        this.#accept(await this.#request({ action: "state" }, this.controller.signal));
        if (this.authority.data.mode !== "MOODWALL") return { status: "PAUSED" };
        const previous = this.authority.data.attempt;
        if (previous && ["CLAIMED", "START_AUTHORIZED", "PLAYING"].includes(previous.status)) {
          this.current = attempt(previous);
          await this.#interrupt("RELOAD");
          return { status: "INTERRUPTED" };
        }
      }
      const command = this.pending?.action === "claim" ? this.pending : this.#command("claim");
      const claim = await this.#send(command);
      if (claim === null) return { status: "IDLE" };
      this.ownerEpoch = claim.playbackEpoch;
      this.status = "PREPARING";
      this.#guard();
      let assetTimer, cancelAsset;
      try {
        await Promise.race([
          this.renderer.prepare(this.current.item, { signal: this.controller.signal }),
          new Promise((_, reject) => { assetTimer = this.clock.setTimer(() => reject(failure("ASSET_FAILURE")), 5000); }),
          new Promise((_, reject) => {
            cancelAsset = () => reject(this.controller.signal.reason);
            this.controller.signal.addEventListener("abort", cancelAsset, { once: true });
            if (this.controller.signal.aborted) cancelAsset();
          }),
        ]);
      } catch {
        if (this.controller.signal.aborted) throw this.controller.signal.reason;
        throw failure("ASSET_FAILURE");
      } finally {
        this.clock.clearTimer(assetTimer);
        this.controller.signal.removeEventListener("abort", cancelAsset);
      }
      this.#guard();
      const started = await this.#send(this.#command("start"));
      if (started.attempt.status !== "START_AUTHORIZED") throw failure("PLAYBACK_NOT_READY", 409);
      const first = parseTime(this.current.item.firstStartAuthorizedAt);
      if (parseTime(this.current.item.contentDeleteAt) !== first + HOUR) throw failure("INVALID_PLAYBACK_RESPONSE");
      this.#guard({ starting: true });
      await this.clock.nextFrame(this.controller.signal);
      this.#guard({ starting: true });
      const duration = playbackDuration(this.current);
      this.renderer.show(this.current.item, { durationMs: duration });
      const committedAt = this.clock.now();
      // show() commits before the browser paints this animation frame. Start
      // timing on the following visible frame, conservatively after that paint.
      await this.clock.nextFrame(this.controller.signal);
      this.#guard({ starting: true });
      if (this.clock.now() - committedAt >= 1000) throw failure("FRAME_GAP");
      const firstFrame = this.clock.now();
      this.status = "PLAYING";
      const deadline = parseTime(this.current.startNotAfter);
      const framed = await this.#send(this.#command("frame"), { retryUntil: deadline });
      if (framed.attempt.status !== "PLAYING" || this.#serverNow() >= deadline) {
        throw failure("START_AUTHORIZATION_EXPIRED", 409);
      }
      let lastFrame = this.clock.now(), lastPoll = lastFrame, lastHeartbeat = lastFrame;
      let observing = false, heartbeating = false;
      while (this.clock.now() - firstFrame < duration) {
        await this.clock.nextFrame(this.controller.signal);
        const now = this.clock.now();
        if (now < lastFrame || now - lastFrame >= 1000) throw failure("FRAME_GAP");
        lastFrame = now;
        this.#guard();
        if (now - lastPoll >= 1000 && !observing) {
          lastPoll = now; observing = true;
          this.#observe(generation, operation).finally(() => { observing = false; });
        }
        if (now - lastHeartbeat >= 10_000 && !heartbeating) {
          lastHeartbeat = now; heartbeating = true;
          this.#request({ action: "heartbeat", body: { clientInstanceId: this.clientInstanceId,
            displayProtocolVersion: "2" } }, this.controller.signal)
            .catch(error => {
              if (generation === this.generation && operation === this.operation && error?.status === 401) {
                this.resetAuthentication();
              }
            })
            .finally(() => { heartbeating = false; });
        }
      }
      this.#guard();
      await this.#send(this.#command("complete"));
      return this.#finished();
    } catch (error) {
      if (generation !== this.generation) return { status: "PAUSED" };
      if (this.pending?.action === "complete" && (transient(error) || error?.code === "PLAYBACK_TOO_EARLY")) {
        this.status = "UNKNOWN";
        // No next item and no success inferred from time. Hide when local authority expires.
        this.renderer.hide();
        return { status: "UNKNOWN", code: error.code || "NETWORK" };
      }
      if (this.pending?.action === "claim" && transient(error)) {
        this.renderer.hide();
        return { status: "UNKNOWN", code: error.code || "NETWORK" };
      }
      if (error?.status === 401) this.resetAuthentication();
      else await this.#interrupt(error.code);
      this.status = "INTERRUPTED";
      return { status: "INTERRUPTED", code: error.code || "CLIENT_ERROR" };
    }
  }

  #finished() {
    if (this.current?.status !== "DONE") throw failure("PLAYBACK_NOT_READY", 409);
    this.renderer.hide();
    const dropped = DROP_PRESENTATION.has(this.current.presentationStatus);
    if (dropped) this.cache?.hide(this.current.submissionId);
    if (!dropped && typeof this.cache?.acceptCompleted === "function") {
      const receipt = this.lastCompletion;
      const saved = receipt && this.cache.acceptCompleted(receipt.data, this.current.item,
        receipt.received, receipt.started, receipt.authEpoch);
      const code = !saved ? "COMPLETED_CACHE_UNVERIFIED" :
        this.cache.storageAvailable === false ? "CACHE_STORAGE_UNAVAILABLE" : null;
      if (code) {
        // Cache diagnostics must never turn an already confirmed DONE into a
        // failed command or cause that presentation to be replayed.
        try { this.onProblem(failure(code))?.catch?.(() => {}); } catch { /* diagnostic only */ }
      }
    }
    if (!dropped && this.visible() && !this.stopped && this.authority.data.clientInstanceId === this.clientInstanceId &&
        this.#serverNow() < parseTime(this.current.item.ambientUntil)) {
      this.renderer.float?.(this.current.item);
    }
    this.status = "DONE";
    const submissionId = this.current.submissionId;
    this.current = null;
    return { status: "DONE", submissionId };
  }

  runOnce() {
    if (!this.running) this.running = this.#play().finally(() => { this.running = null; });
    return this.running;
  }

  async run({ signal, idleDelayMs = 1000 } = {}) {
    const stop = () => this.pause("PAGE_HIDDEN");
    signal?.addEventListener("abort", stop, { once: true });
    try {
      while (!signal?.aborted && !this.stopped) {
        await this.runOnce();
        await this.clock.sleep(idleDelayMs, signal);
      }
    } catch (error) { if (!signal?.aborted) throw error; }
    finally { signal?.removeEventListener("abort", stop); }
  }
}

/** Bind once; React can call the returned cleanup from its effect cleanup. */
export function bindPlaybackVisibility(player, doc = document, win = window) {
  const visibility = () => {
    if (doc.visibilityState !== "visible") player.pause("PAGE_HIDDEN");
    else player.resume(); // runOnce always reconciles before replaying after resume.
  };
  const unload = () => player.pause("RELOAD");
  doc.addEventListener("visibilitychange", visibility);
  win.addEventListener("pagehide", unload);
  return () => {
    doc.removeEventListener("visibilitychange", visibility);
    win.removeEventListener("pagehide", unload);
    player.pause("RELOAD");
  };
}
