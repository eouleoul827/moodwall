/** Minimal mobile v2 adapter. Intents live only in memory; no message text in storage or URLs. */
import { secureUuid } from "../uuid.mjs";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DECIMAL = /^(0|[1-9][0-9]{0,18})$/;
const NORMAL = new Set(["PREPARING", "WAITING", "STARTING", "SHOWING", "SHOWN", "PLAYBACK_UNCONFIRMED"]);
const TERMINAL = new Set(["SHOWN", "NEEDS_REWRITE", "FAILED", "EXPIRED", "CANCELLED", "HIDDEN", "PLAYBACK_UNCONFIRMED"]);
const REJECTIONS = new Set(["INVALID_CONTENT", "VALIDATION_ERROR", "CONTENT_TOO_LONG", "IDEMPOTENCY_CONFLICT",
  "ADMISSION_EXPIRED", "BANNED", "PARTICIPANT_BANNED", "POST_LIMIT_REACHED", "HOURLY_LIMIT_REACHED", "COOLDOWN_ACTIVE",
  "SUBMISSION_IN_PROGRESS", "REQUEST_RATE_LIMITED", "RATE_LIMITED", "DISPLAY_QUEUE_FULL", "MODERATION_BUSY",
  "DAY_ENDING", "MODEL_NOT_READY", "SERVICE_UNAVAILABLE", "PARTICIPANT_SUSPENDED", "SUBMISSION_COOLDOWN",
  "QUOTA_EXCEEDED", "INTAKE_PAUSED", "DISPLAY_OFFLINE"]);
const failure = code => Object.assign(new Error(code), { code });
const time = value => typeof value === "string" && Number.isFinite(Date.parse(value));

function receipt(value, expectedId = null) {
  if (!value || value.receiptVersion !== "2" || !UUID.test(value.submissionId) ||
      expectedId !== null && value.submissionId !== expectedId ||
      !time(value.acceptedAt) || !time(value.receiptExpiresAt) ||
      typeof value.revision !== "string" || !DECIMAL.test(value.revision) ||
      !(NORMAL.has(value.userStatus) || TERMINAL.has(value.userStatus)) ||
      !["AVAILABLE", "PURGED"].includes(value.contentStatus) ||
      value.statusUrl !== `/api/v2/submissions/${value.submissionId}`) {
    throw failure("INVALID_RECEIPT");
  }
  // Whitelist metadata: a receipt must not become another copy of the message.
  return Object.freeze(Object.fromEntries(["receiptVersion", "submissionId", "acceptedAt", "receiptExpiresAt",
    "processingStatus", "presentationStatus", "userStatus", "contentStatus", "lastDisplayStatus", "reasonCode",
    "firstStartAuthorizedAt", "contentDeleteAt", "completedAt", "revision", "statusUrl"].map(key => [key, value[key]])));
}

export function studentReceiptView(value) {
  if (NORMAL.has(value.userStatus)) return { card: "COMPLETE", title: "메시지를 보냈어요!",
    message: "확인 후 전광판에 순서대로 올라가요. 표시되면 15초 동안 크게 보여요.", terminal: TERMINAL.has(value.userStatus) };
  if (value.userStatus === "NEEDS_REWRITE") return { card: "REWRITE", title: "표현을 조금 바꿔 주세요",
    message: "표현을 바꿔 다시 보내 주세요.", terminal: true };
  return { card: "NOTICE", title: "표시하지 못했어요",
    message: value.userStatus === "HIDDEN" ? "이 글은 전광판에서 내려갔어요." : "잠시 후 다시 작성해 주세요.", terminal: true };
}

function wait(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason);
    const timer = setTimeout(done, ms);
    function done() { signal?.removeEventListener("abort", cancel); resolve(); }
    function cancel() { clearTimeout(timer); reject(signal.reason); }
    signal?.addEventListener("abort", cancel, { once: true });
  });
}

/**
 * begin(content) ONLY on an explicit new-post action; send()/recover() keep that intent's UUID.
 * After UNKNOWN, use recover(), or explicitly retry send() with the same intent.
 * 202 means accepted, so the existing completion card can appear immediately.
 */
export class SubmissionClient {
  constructor({ fetch = globalThis.fetch, csrfToken = () => "", uuid = secureUuid,
    now = () => performance.now(), sleep = wait, setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
    this.fetch = fetch; this.csrfToken = csrfToken; this.uuid = uuid; this.now = now;
    this.sleep = sleep; this.setTimer = setTimer; this.clearTimer = clearTimer;
    this.intent = null; this.receipt = null; this.epoch = 0; this.inFlight = null;
    this.controllers = new Set(); this.watchController = null;
    this.state = "IDLE";
  }

  begin(content) {
    if (this.inFlight || this.intent && ["SENDING", "UNKNOWN", "AUTH_REQUIRED"].includes(this.state)) {
      if (this.intent.content === content) return this.intent;
      throw failure("PENDING_SUBMISSION_EXISTS");
    }
    if (typeof content !== "string" || !content.trim() || content.length > 2048) throw failure("INVALID_CONTENT");
    const idempotencyKey = this.uuid();
    if (!UUID.test(idempotencyKey)) throw failure("INVALID_IDEMPOTENCY_KEY");
    this.stopWatching();
    this.intent = Object.freeze({ content, idempotencyKey });
    this.receipt = null;
    this.state = "DRAFT";
    return this.intent;
  }

  async #request(path, { method = "GET", body, key, signal } = {}) {
    const started = this.now(), controller = new AbortController();
    this.controllers.add(controller);
    const cancel = () => controller.abort(signal.reason);
    signal?.addEventListener("abort", cancel, { once: true });
    if (signal?.aborted) cancel();
    const headers = { Accept: "application/json" };
    if (method === "POST") {
      headers["Content-Type"] = "application/json";
      headers["X-CSRF-Token"] = this.csrfToken();
      if (key) headers["Idempotency-Key"] = key;
    }
    let timer;
    const cancelled = new Promise((_, reject) => {
      const rejectAbort = () => reject(controller.signal.reason);
      controller.signal.addEventListener("abort", rejectAbort, { once: true });
      if (controller.signal.aborted) rejectAbort();
      timer = this.setTimer(() => controller.abort(failure("SUBMISSION_REQUEST_TIMEOUT")), 5000);
    });
    try {
      const operation = (async () => {
        const response = await this.fetch(path, { method, headers, cache: "no-store", credentials: "same-origin",
          signal: controller.signal, ...(method === "POST" ? { body: JSON.stringify(body) } : {}) });
        const value = await response.json();
        return { response, value };
      })();
      const result = await Promise.race([operation, cancelled]);
      if (this.now() - started >= 5000 || this.now() < started) throw failure("SUBMISSION_REQUEST_TIMEOUT");
      if (controller.signal.aborted) throw controller.signal.reason;
      return result;
    } finally {
      this.clearTimer(timer); this.controllers.delete(controller);
      signal?.removeEventListener("abort", cancel);
    }
  }

  #applyReceipt(value, expectedId = null) {
    const next = receipt(value, expectedId);
    if (this.receipt && next.submissionId === this.receipt.submissionId &&
        BigInt(next.revision) < BigInt(this.receipt.revision)) return this.#accepted();
    this.receipt = next;
    this.state = "ACCEPTED";
    return this.#accepted();
  }
  #accepted() { return { state: "ACCEPTED", receipt: this.receipt, view: studentReceiptView(this.receipt) }; }
  #unknown(code = "RESULT_UNKNOWN") {
    this.state = "UNKNOWN";
    return { state: "UNKNOWN", code, view: { card: "SENDING", message: "전송 결과를 확인하고 있어요." } };
  }

  send() {
    if (!this.intent) throw failure("NO_SUBMISSION_INTENT");
    if (this.inFlight) return this.inFlight;
    const epoch = this.epoch, intent = this.intent;
    this.state = "SENDING";
    this.inFlight = (async () => {
      try {
        const { response, value } = await this.#request("/api/v2/submissions", {
          method: "POST", body: { content: intent.content }, key: intent.idempotencyKey,
        });
        if (epoch !== this.epoch || this.intent !== intent) return { state: "STALE" };
        if ([200, 202].includes(response.status)) return this.#applyReceipt(value);
        if (response.headers.get("Content-Type")?.includes("application/problem+json") && REJECTIONS.has(value.code)) {
          this.state = "REJECTED";
          return { state: "REJECTED", code: value.code };
        }
        if ([401, 403].includes(response.status)) {
          this.state = "AUTH_REQUIRED";
          return { state: "AUTH_REQUIRED", code: value.code || "SESSION_EXPIRED" };
        }
        return this.#unknown();
      } catch (error) {
        if (epoch !== this.epoch || this.intent !== intent) return { state: "STALE" };
        return this.#unknown(error.code);
      } finally { if (epoch === this.epoch) this.inFlight = null; }
    })();
    return this.inFlight;
  }

  async recover() {
    if (!this.intent) throw failure("NO_SUBMISSION_INTENT");
    const epoch = this.epoch, intent = this.intent;
    try {
      const { response, value } = await this.#request("/api/v2/submission-receipts/lookup", {
        method: "POST", body: { idempotencyKey: intent.idempotencyKey },
      });
      if (epoch !== this.epoch || this.intent !== intent) return { state: "STALE" };
      if (response.status === 200) return this.#applyReceipt(value);
      if ([401, 403].includes(response.status)) {
        this.state = "AUTH_REQUIRED"; return { state: "AUTH_REQUIRED", code: value.code || "SESSION_EXPIRED" };
      }
      // A missing receipt may mean that the original request is still in flight.
      // Do not invent a new key or automatically submit another message.
      return this.#unknown(response.status === 404 ? "RECEIPT_NOT_YET_FOUND" : "RESULT_UNKNOWN");
    } catch (error) {
      if (epoch !== this.epoch || this.intent !== intent) return { state: "STALE" };
      return this.#unknown(error.code);
    }
  }

  async getReceipt(submissionId = this.receipt?.submissionId, { signal } = {}) {
    if (!UUID.test(submissionId)) throw failure("INVALID_SUBMISSION_ID");
    const epoch = this.epoch, intent = this.intent;
    try {
      const { response, value } = await this.#request(`/api/v2/submissions/${submissionId}`, { signal });
      if (epoch !== this.epoch || this.intent !== intent) return { state: "STALE" };
      if (response.status === 200) return this.#applyReceipt(value, submissionId);
      return { state: response.status === 404 ? "UNAVAILABLE" : "LOOKUP_ERROR", code: value.code || "LOOKUP_FAILED",
        receipt: this.receipt }; // Keep an existing success card during a temporary read failure.
    } catch (error) {
      if (epoch !== this.epoch || this.intent !== intent) return { state: "STALE" };
      return { state: "LOOKUP_ERROR", code: error.code || "LOOKUP_FAILED", receipt: this.receipt };
    }
  }

  async watchReceipt({ onChange = () => {}, intervalMs = 3000, maxPolls = 20 } = {}) {
    if (!this.receipt) throw failure("NO_SUBMISSION_RECEIPT");
    if (!Number.isInteger(maxPolls) || maxPolls < 1 || maxPolls > 100 ||
        !Number.isFinite(intervalMs) || intervalMs < 2000) throw failure("INVALID_POLL_POLICY");
    this.stopWatching();
    const controller = new AbortController();
    this.watchController = controller;
    const submissionId = this.receipt.submissionId;
    let lastRevision = this.receipt.revision, polls = 0;
    try {
      while (!controller.signal.aborted && polls < maxPolls && !TERMINAL.has(this.receipt?.userStatus)) {
        await this.sleep(intervalMs, controller.signal);
        if (controller.signal.aborted) break;
        const result = await this.getReceipt(submissionId, { signal: controller.signal });
        polls++;
        if (controller.signal.aborted || result.state === "STALE") break;
        if (result.state === "ACCEPTED" && result.receipt.revision !== lastRevision) {
          lastRevision = result.receipt.revision;
          onChange(result); // The UI may retain the same COMPLETE card for all normal states.
        }
        if (result.state === "UNAVAILABLE") break;
      }
      return { polls, stopped: controller.signal.aborted, receipt: this.receipt };
    } catch (error) { if (!controller.signal.aborted) throw error; return { polls, stopped: true, receipt: this.receipt }; }
    finally { if (this.watchController === controller) this.watchController = null; }
  }

  stopWatching() { this.watchController?.abort(failure("WATCH_STOPPED")); this.watchController = null; }
  resetAuthentication() {
    this.epoch++;
    this.stopWatching();
    for (const controller of this.controllers) controller.abort(failure("AUTHENTICATION_CHANGED"));
    this.intent = null; this.receipt = null; this.inFlight = null; this.state = "IDLE";
  }
}
