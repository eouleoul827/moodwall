/** Minimal mobile v2 adapter. Message bodies stay in memory; recovery stores UUID metadata only. */
import { secureUuid } from "../uuid.mjs";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DECIMAL = /^(0|[1-9][0-9]{0,18})$/;
const NORMAL = new Set(["PREPARING", "WAITING", "STARTING", "SHOWING", "SHOWN", "PLAYBACK_UNCONFIRMED"]);
const TERMINAL = new Set(["SHOWN", "NEEDS_REWRITE", "FAILED", "EXPIRED", "CANCELLED", "HIDDEN", "PLAYBACK_UNCONFIRMED"]);
const REJECTIONS = new Set(["INVALID_CONTENT", "VALIDATION_ERROR", "CONTENT_TOO_LONG", "IDEMPOTENCY_CONFLICT",
  "ADMISSION_EXPIRED", "BANNED", "PARTICIPANT_BANNED", "POST_LIMIT_REACHED", "HOURLY_LIMIT_REACHED", "COOLDOWN_ACTIVE",
  "SUBMISSION_IN_PROGRESS", "REQUEST_RATE_LIMITED", "RATE_LIMITED", "DISPLAY_QUEUE_FULL", "MODERATION_BUSY",
  "DAY_ENDING", "MODEL_NOT_READY", "PARTICIPANT_SUSPENDED", "SUBMISSION_COOLDOWN",
  "QUOTA_EXCEEDED", "INTAKE_PAUSED", "DISPLAY_OFFLINE"]);
const failure = code => Object.assign(new Error(code), { code });
const time = value => typeof value === "string" && Number.isFinite(Date.parse(value));
const RECOVERY_KEY = "moodwall.student.recovery.v1";
const RECEIPT_TTL_MS = 24 * 60 * 60 * 1000;
function sessionStorageOrNull() { try { return globalThis.sessionStorage ?? null; } catch { return null; } }

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
  if (value.reasonCode === "SUPPORT_AVAILABLE") return { card: "NOTICE", title: "전광판에 표시되지 않았어요",
    message: "이 메시지는 전광판에 표시하지 않았어요.", terminal: true };
  if (value.reasonCode === "CONTENT_REVIEW_UNCERTAIN") return { card: "UNCERTAIN", title: "공개 여부를 판단하기 어려웠어요",
    message: "뜻이 잘 전해지도록 조금 더 구체적으로 써 주세요.", terminal: true };
  if (value.reasonCode === "CONTENT_REVIEW_UNAVAILABLE") return { card: "NOTICE", title: "내용 확인을 완료하지 못했어요",
    message: "다시 써서 보내 주세요.", terminal: true };
  if (NORMAL.has(value.userStatus)) return { card: "COMPLETE", title: "메시지를 보냈어요!",
    message: "확인 후 전광판에 순서대로 올라가요. 표시되면 15초 동안 크게 보여요.", terminal: TERMINAL.has(value.userStatus) };
  if (value.userStatus === "NEEDS_REWRITE") return { card: "REWRITE", title: "표현을 조금 바꿔 주세요",
    message: "욕설·비하·개인정보는 올릴 수 없어요. 표현을 바꿔 다시 보내 주세요.", terminal: true };
  return { card: "NOTICE", ...closedNotice(value), terminal: true };
}

/** Post-acceptance outcomes that are not about wording: say what happened and what the student can do. */
function closedNotice({ userStatus, reasonCode }) {
  if (reasonCode === "DAY_ENDED") return { title: "오늘 전광판이 마감됐어요", message: "내일 다시 보내 주세요." };
  if (reasonCode === "PARTICIPANT_SUSPENDED") return { title: "오늘은 글을 올릴 수 없어요", message: "내일 다시 참여해 주세요." };
  if (userStatus === "EXPIRED") return { title: "시간이 지나 표시되지 않았어요", message: "다시 보내 주세요." };
  if (userStatus === "CANCELLED") return { title: "표시가 취소됐어요", message: "잠시 후 다시 보내 주세요." };
  return { title: "전광판에 올리지 못했어요", message: "잠시 후 다시 보내 주세요." };
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
    now = () => performance.now(), wallNow = () => Date.now(), storage = sessionStorageOrNull(),
    sleep = wait, setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
    // Window APIs reject a class instance as their receiver (Illegal invocation).
    // Call injected functions as plain functions, including native browser defaults.
    this.fetch = (...args) => fetch(...args); this.csrfToken = csrfToken; this.uuid = uuid; this.now = now;
    this.sleep = sleep; this.setTimer = (...args) => setTimer(...args);
    this.clearTimer = (...args) => clearTimer(...args);
    this.intent = null; this.receipt = null; this.epoch = 0; this.inFlight = null;
    this.controllers = new Set(); this.watchController = null;
    this.state = "IDLE";
    this.wallNow = wallNow; this.storage = storage; this.recoveryMetadata = null;
    this.recoveryPersistent = Boolean(storage);
    try {
      const raw = storage?.getItem(RECOVERY_KEY);
      if (raw) {
        const value = JSON.parse(raw);
        const keys = Object.keys(value ?? {}).sort().join(",");
        if (raw.length > 512 || keys !== "expiresAt,idempotencyKey,submissionId,version" || value.version !== 1 ||
            !(value.idempotencyKey === null || UUID.test(value.idempotencyKey)) ||
            !(value.submissionId === null || UUID.test(value.submissionId)) ||
            !(value.idempotencyKey || value.submissionId) || !time(value.expiresAt)) {
          storage.removeItem(RECOVERY_KEY);
        } else {
          this.recoveryMetadata = Object.freeze(value);
          this.intent = Object.freeze({ content: null, idempotencyKey: value.idempotencyKey });
          this.state = "UNKNOWN";
        }
      }
    } catch { this.recoveryPersistent = false; }
  }

  get hasPendingRecovery() { return Boolean(this.recoveryMetadata); }
  get canResend() { return typeof this.intent?.content === "string" && Boolean(this.intent.content.trim()) && UUID.test(this.intent.idempotencyKey); }

  #remember({ submissionId = null, expiresAt = new Date(this.wallNow() + RECEIPT_TTL_MS).toISOString() } = {}) {
    const idempotencyKey = this.intent?.idempotencyKey ?? this.recoveryMetadata?.idempotencyKey ?? null;
    this.recoveryMetadata = Object.freeze({ version: 1, idempotencyKey, submissionId, expiresAt });
    try {
      if (!this.storage) throw failure("RECOVERY_STORAGE_UNAVAILABLE");
      this.storage.setItem(RECOVERY_KEY, JSON.stringify(this.recoveryMetadata));
      this.recoveryPersistent = true;
    } catch { this.recoveryPersistent = false; }
  }

  #forgetRecovery() {
    this.recoveryMetadata = null;
    try { this.storage?.removeItem(RECOVERY_KEY); } catch { this.recoveryPersistent = false; }
  }

  async restorePending({ signal } = {}) {
    if (!this.recoveryMetadata) return null;
    // The UI restores only after /api/session calibrated wallNow against serverNow.
    // A phone with a fast clock must not discard a still-valid recovery key at startup.
    if (Date.parse(this.recoveryMetadata.expiresAt) <= this.wallNow()) {
      this.#forgetRecovery(); this.intent = null; this.state = "IDLE"; return null;
    }
    if (!this.intent) {
      this.intent = Object.freeze({ content: null, idempotencyKey: this.recoveryMetadata.idempotencyKey });
      this.state = "UNKNOWN";
    }
    if (!this.recoveryMetadata.submissionId) return this.recover({ signal });
    const next = await this.getReceipt(this.recoveryMetadata.submissionId, { signal });
    return ["UNAVAILABLE", "LOOKUP_ERROR"].includes(next.state) ? this.#unknown(next.code) : next;
  }

  begin(content) {
    if (this.inFlight || this.intent && ["SENDING", "UNKNOWN", "AUTH_REQUIRED", "UPDATE_REQUIRED"].includes(this.state)) {
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
    const headers = { Accept: "application/json", "X-Moodwall-Client-Features": "support-v1" };
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
    if (next.reasonCode === "SUPPORT_AVAILABLE" && this.intent) {
      this.intent = Object.freeze({ ...this.intent, content: "" });
    }
    this.state = "ACCEPTED";
    this.#remember({ submissionId: next.submissionId, expiresAt: next.receiptExpiresAt });
    return this.#accepted();
  }
  #accepted() { return { state: "ACCEPTED", receipt: this.receipt, view: studentReceiptView(this.receipt) }; }
  #updateRequired() {
    this.stopWatching();
    this.state = "UPDATE_REQUIRED";
    return { state: "UPDATE_REQUIRED", code: "CLIENT_UPDATE_REQUIRED", receipt: this.receipt };
  }
  #unknown(code = "RESULT_UNKNOWN") {
    this.state = "UNKNOWN";
    return { state: "UNKNOWN", code, view: { card: "SENDING", message: "전송 결과를 확인하고 있어요." } };
  }

  send() {
    if (!this.intent) throw failure("NO_SUBMISSION_INTENT");
    if (this.state === "UPDATE_REQUIRED") return Promise.resolve(this.#updateRequired());
    if (this.state === "ACCEPTED" && this.receipt) return Promise.resolve(this.#accepted());
    if (!this.canResend) return Promise.resolve(this.#unknown("ORIGINAL_CONTENT_UNAVAILABLE"));
    if (this.inFlight) return this.inFlight;
    const epoch = this.epoch, intent = this.intent;
    const unresolved = ["UNKNOWN", "AUTH_REQUIRED"].includes(this.state);
    this.state = "SENDING";
    // Persist the key before dispatch, including when the POST response is lost.
    this.#remember();
    this.inFlight = (async () => {
      try {
        const { response, value } = await this.#request("/api/v2/submissions", {
          method: "POST", body: { content: intent.content }, key: intent.idempotencyKey,
        });
        if (epoch !== this.epoch || this.intent !== intent) return { state: "STALE" };
        if ([200, 202].includes(response.status)) return this.#applyReceipt(value);
        if (response.status === 409 && value.code === "CLIENT_UPDATE_REQUIRED") return this.#updateRequired();
        if (response.headers.get("Content-Type")?.includes("application/problem+json") && REJECTIONS.has(value.code)) {
          // Request limiting runs before the server checks the idempotency key.
          // It rejects this retry but cannot settle an earlier uncertain POST.
          if (unresolved && ["RATE_LIMITED", "REQUEST_RATE_LIMITED"].includes(value.code)) return this.#unknown(value.code);
          this.state = "REJECTED";
          this.#forgetRecovery();
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

  async recover({ signal } = {}) {
    if (!this.intent) throw failure("NO_SUBMISSION_INTENT");
    if (!this.intent.idempotencyKey) {
      if (this.recoveryMetadata?.submissionId) return this.getReceipt(this.recoveryMetadata.submissionId, { signal });
      return this.#unknown("RECOVERY_KEY_UNAVAILABLE");
    }
    const epoch = this.epoch, intent = this.intent;
    try {
      const { response, value } = await this.#request("/api/v2/submission-receipts/lookup", {
        method: "POST", body: { idempotencyKey: intent.idempotencyKey }, signal,
      });
      if (epoch !== this.epoch || this.intent !== intent) return { state: "STALE" };
      if (response.status === 200) return this.#applyReceipt(value);
      if (response.status === 409 && value.code === "CLIENT_UPDATE_REQUIRED") return this.#updateRequired();
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
      if (response.status === 409 && value.code === "CLIENT_UPDATE_REQUIRED") return this.#updateRequired();
      if ([401, 403].includes(response.status) && !this.receipt) {
        this.state = "AUTH_REQUIRED";
        return { state: "AUTH_REQUIRED", code: value.code || "SESSION_EXPIRED" };
      }
      return { state: response.status === 404 ? "UNAVAILABLE" : "LOOKUP_ERROR", code: value.code || "LOOKUP_FAILED",
        receipt: this.receipt }; // Keep an existing success card during a temporary read failure.
    } catch (error) {
      if (epoch !== this.epoch || this.intent !== intent) return { state: "STALE" };
      return { state: "LOOKUP_ERROR", code: error.code || "LOOKUP_FAILED", receipt: this.receipt };
    }
  }

  async watchReceipt({ onChange = () => {}, intervalMs = 3000, maxPolls = 512 } = {}) {
    if (!this.receipt) throw failure("NO_SUBMISSION_RECEIPT");
    if (!Number.isInteger(maxPolls) || maxPolls < 1 || maxPolls > 512 ||
        !Number.isFinite(intervalMs) || intervalMs < 2000 || intervalMs > 30000) throw failure("INVALID_POLL_POLICY");
    this.stopWatching();
    const controller = new AbortController();
    this.watchController = controller;
    const submissionId = this.receipt.submissionId;
    let lastRevision = this.receipt.revision, polls = 0, delay = intervalMs;
    const started = this.now();
    const deadline = () => Math.min(Date.parse(this.receipt.receiptExpiresAt),
      time(this.receipt.firstStartAuthorizedAt)
        ? time(this.receipt.contentDeleteAt) ? Date.parse(this.receipt.contentDeleteAt) + 30000 : Date.parse(this.receipt.firstStartAuthorizedAt) + 3630000
        : Date.parse(this.receipt.acceptedAt) + 630000);
    try {
      while (!controller.signal.aborted && polls < maxPolls && !TERMINAL.has(this.receipt?.userStatus)) {
        const remaining = deadline() - this.wallNow();
        // Query once at the deadline too: maintenance can have finalized the receipt.
        await this.sleep(Math.min(delay, Math.max(0, remaining)), controller.signal);
        if (controller.signal.aborted) break;
        const result = await this.getReceipt(submissionId, { signal: controller.signal });
        polls++;
        if (result.state === "UPDATE_REQUIRED") { onChange(result); break; }
        if (controller.signal.aborted || result.state === "STALE") break;
        if (result.state === "ACCEPTED" && result.receipt.revision !== lastRevision) {
          lastRevision = result.receipt.revision;
          onChange(result); // The UI may retain the same COMPLETE card for all normal states.
          delay = intervalMs;
        } else { delay = Math.min(30000, delay * 1.5); }
        if (result.state === "UNAVAILABLE") {
          onChange(result);
          break;
        }
        // Absolute receipt time and monotonic lifetime independently bound polling.
        if (this.wallNow() >= deadline() || this.now() - started >= 65 * 60 * 1000) break;
      }
      return { polls, stopped: controller.signal.aborted, receipt: this.receipt };
    } catch (error) { if (!controller.signal.aborted) throw error; return { polls, stopped: true, receipt: this.receipt }; }
    finally { if (this.watchController === controller) this.watchController = null; }
  }

  stopWatching() { this.watchController?.abort(failure("WATCH_STOPPED")); this.watchController = null; }
  resetAuthentication({ clearRecovery = false } = {}) {
    this.epoch++;
    this.stopWatching();
    for (const controller of this.controllers) controller.abort(failure("AUTHENTICATION_CHANGED"));
    this.intent = null; this.receipt = null; this.inFlight = null; this.state = "IDLE";
    if (clearRecovery) this.#forgetRecovery(); // Unmount/reload retains metadata for owner-scoped recovery.
  }
}
