import test from "node:test";
import assert from "node:assert/strict";
import { SubmissionClient, studentReceiptView } from "./submission-client.mjs";

const ID = "22222222-2222-4222-8222-222222222222";
function receipt(overrides = {}) {
  return { receiptVersion: "2", submissionId: ID, acceptedAt: "2026-11-04T03:00:00Z",
    receiptExpiresAt: "2026-11-05T03:00:00Z", revision: "1", userStatus: "PREPARING",
    processingStatus: "PENDING", presentationStatus: "PENDING", contentStatus: "AVAILABLE",
    reasonCode: null, statusUrl: `/api/v2/submissions/${ID}`, ...overrides };
}
function response(status, value, type = "application/json") {
  return { status, ok: status >= 200 && status < 300,
    headers: { get: name => name === "Content-Type" ? type : null }, json: async () => value };
}
function setup(handler) {
  let sequence = 0;
  const requests = [];
  const client = new SubmissionClient({ fetch: async (url, init) => {
    requests.push({ url, init });
    return handler ? handler(url, init, requests) : response(202, receipt());
  }, csrfToken: () => "csrf", uuid: () => `aaaaaaaa-aaaa-4aaa-8aaa-${String(++sequence).padStart(12, "0")}`,
  wallNow: () => Date.parse("2026-11-04T03:00:00Z"), sleep: async () => {} });
  return { client, requests };
}

test("202 shows the existing completion card immediately and sends only content", async () => {
  const { client, requests } = setup();
  const intent = client.begin("오늘 행복해");
  const result = await client.send();
  assert.equal(result.state, "ACCEPTED");
  assert.equal(result.view.card, "COMPLETE");
  assert.equal(result.view.title, "메시지를 보냈어요!");
  assert.equal(requests[0].url, "/api/v2/submissions");
  assert.equal(requests[0].init.body, '{"content":"오늘 행복해"}');
  assert.equal(requests[0].init.headers["Idempotency-Key"], intent.idempotencyKey);
  assert.equal(requests[0].init.headers["X-CSRF-Token"], "csrf");
  assert.equal(requests[0].init.headers["X-Moodwall-Client-Features"], "support-v1");
  assert.equal(requests[0].init.credentials, "same-origin");
  assert.equal(JSON.stringify(result.receipt).includes("오늘 행복해"), false);
});

test("browser-native fetch and timer defaults never receive the client as this", async t => {
  const requests = [], timers = [], cleared = [];
  t.mock.method(globalThis, "fetch", async function (url) {
    assert.equal(this, undefined, "Window.fetch rejects a class instance receiver");
    requests.push(url);
    return response(url === "/api/v2/submissions" ? 202 : 200, receipt());
  });
  t.mock.method(globalThis, "setTimeout", function (_callback, ms) {
    assert.equal(this, undefined, "Window.setTimeout rejects a class instance receiver");
    timers.push(ms);
    return timers.length;
  });
  t.mock.method(globalThis, "clearTimeout", function (id) {
    assert.equal(this, undefined, "Window.clearTimeout rejects a class instance receiver");
    cleared.push(id);
  });
  const client = new SubmissionClient({ uuid: () => ID, storage: null });
  client.begin("오늘 행복해");
  assert.equal((await client.send()).state, "ACCEPTED");
  assert.equal((await client.recover()).state, "ACCEPTED");
  assert.equal((await client.getReceipt()).state, "ACCEPTED");
  assert.deepEqual(requests, ["/api/v2/submissions", "/api/v2/submission-receipts/lookup", `/api/v2/submissions/${ID}`]);
  assert.deepEqual(timers, [5000, 5000, 5000]);
  assert.deepEqual(cleared, [1, 2, 3]);
});

test("duplicate button presses use a single in-flight request", async () => {
  const { client, requests } = setup();
  client.begin("안녕");
  const first = client.send(), second = client.send();
  assert.equal(first, second);
  await first;
  assert.equal(requests.length, 1);
});

test("network ambiguity keeps the same immutable key and body when explicitly retried", async () => {
  const { client, requests } = setup((url, init, all) => {
    if (all.length === 1) throw Error("connection lost after commit");
    return response(200, receipt());
  });
  const intent = client.begin("안녕");
  assert.equal((await client.send()).state, "UNKNOWN");
  assert.equal(client.begin("안녕"), intent);
  assert.throws(() => client.begin("다른 글"), { code: "PENDING_SUBMISSION_EXISTS" });
  assert.equal((await client.send()).state, "ACCEPTED");
  assert.equal(requests[0].init.headers["Idempotency-Key"], requests[1].init.headers["Idempotency-Key"]);
  assert.equal(requests[0].init.body, requests[1].init.body);
});

test("a rate-limited retry cannot discard an earlier uncertain acceptance", async () => {
  for (const code of ["RATE_LIMITED", "REQUEST_RATE_LIMITED"]) {
    const storage = memoryStorage(), requests = [];
    let keys = 0;
    const client = new SubmissionClient({ storage, wallNow: () => BASE,
      uuid: () => `aaaaaaaa-aaaa-4aaa-8aaa-${String(++keys).padStart(12, "0")}`,
      fetch: async (url, init) => {
        requests.push({ url, init });
        if (requests.length === 1) throw Error("response lost after acceptance");
        if (requests.length === 2) return response(429, { code }, "application/problem+json");
        return response(200, receipt({ userStatus: "WAITING" }));
      } });
    const intent = client.begin("hello");
    assert.equal((await client.send()).state, "UNKNOWN");
    assert.equal((await client.send()).state, "UNKNOWN");
    assert.equal(client.intent, intent);
    assert.equal(client.hasPendingRecovery, true);
    assert.equal(JSON.parse([...storage.values.values()][0]).idempotencyKey, intent.idempotencyKey);
    assert.throws(() => client.begin("another message"), { code: "PENDING_SUBMISSION_EXISTS" });
    const restored = await client.recover();
    assert.equal(restored.state, "ACCEPTED");
    assert.equal(restored.receipt.submissionId, ID);
    assert.equal(requests[0].init.headers["Idempotency-Key"], requests[1].init.headers["Idempotency-Key"]);
    assert.equal(requests[0].init.body, requests[1].init.body);
    assert.equal(requests[2].url, "/api/v2/submission-receipts/lookup");
    assert.deepEqual(JSON.parse(requests[2].init.body), { idempotencyKey: intent.idempotencyKey });
    assert.equal(keys, 1);
  }
});

test("a rate limit on the first attempt remains a definitive rejection", async () => {
  const storage = memoryStorage();
  const client = new SubmissionClient({ storage, wallNow: () => BASE, uuid: () => ID,
    fetch: async () => response(429, { code: "RATE_LIMITED" }, "application/problem+json") });
  client.begin("hello");
  assert.deepEqual(await client.send(), { state: "REJECTED", code: "RATE_LIMITED" });
  assert.equal(client.hasPendingRecovery, false);
  assert.equal(storage.values.size, 0);
  assert.doesNotThrow(() => client.begin("another message"));
});

test("lost response recovers through an owner-scoped lookup with no automatic re-submission", async () => {
  const { client, requests } = setup(url => {
    if (url === "/api/v2/submissions") throw Error("lost");
    return response(200, receipt());
  });
  const intent = client.begin("안녕");
  await client.send();
  assert.equal((await client.recover()).state, "ACCEPTED");
  assert.equal(requests[1].url, "/api/v2/submission-receipts/lookup");
  assert.deepEqual(JSON.parse(requests[1].init.body), { idempotencyKey: intent.idempotencyKey });
  assert.equal(requests.filter(row => row.url === "/api/v2/submissions").length, 1);
});

test("lookup 404 remains unknown and cannot generate a new intent", async () => {
  const { client, requests } = setup(() => response(404, { code: "NOT_FOUND" }));
  const intent = client.begin("안녕");
  assert.equal((await client.recover()).state, "UNKNOWN");
  assert.equal(client.intent, intent);
  assert.throws(() => client.begin("다른 글"), { code: "PENDING_SUBMISSION_EXISTS" });
  assert.equal(requests.length, 1);
});

test("HTML and generic upstream 5xx never produce completion", async () => {
  for (const kind of ["html", "upstream"]) {
    const { client } = setup(() => kind === "html" ? {
      status: 200, headers: { get: () => "text/html" }, json: async () => { throw SyntaxError("html"); },
    } : response(503, { code: "UPSTREAM_FAILED" }));
    client.begin("안녕");
    assert.equal((await client.send()).state, "UNKNOWN");
    assert.equal(client.receipt, null);
  }
});

test("recognized pre-commit problem is actionable without changing its key", async () => {
  const { client } = setup(() => response(403, { code: "ADMISSION_EXPIRED" }, "application/problem+json"));
  const intent = client.begin("안녕");
  assert.deepEqual(await client.send(), { state: "REJECTED", code: "ADMISSION_EXPIRED" });
  assert.equal(client.intent, intent);
});

test("owner receipt polling never follows a response-supplied external URL", async () => {
  const { client, requests } = setup(url => response(url.endsWith(ID) ? 200 : 202, receipt()));
  client.begin("안녕");
  await client.send();
  await client.getReceipt();
  assert.equal(requests[1].url, `/api/v2/submissions/${ID}`);
  await assert.rejects(client.getReceipt("https://elsewhere.example/private"), { code: "INVALID_SUBMISSION_ID" });
});

test("temporary receipt read error preserves the accepted card and receipt", async () => {
  const { client } = setup(url => url.endsWith(ID) ? response(503, { code: "SERVICE_UNAVAILABLE" }) : response(202, receipt()));
  client.begin("안녕"); await client.send();
  const result = await client.getReceipt();
  assert.equal(result.state, "LOOKUP_ERROR");
  assert.equal(client.state, "ACCEPTED");
  assert.equal(result.receipt.submissionId, ID);
});

test("late old-session success cannot restore a submission after authentication reset", async () => {
  let resolve;
  const { client } = setup(() => new Promise(done => { resolve = done; }));
  client.begin("안녕");
  const pending = client.send();
  client.resetAuthentication();
  resolve(response(202, receipt()));
  assert.equal((await pending).state, "STALE");
  assert.equal(client.receipt, null);
  assert.equal(client.intent, null);
});

test("polling is bounded and stops at the terminal outcome", async () => {
  let gets = 0;
  const { client } = setup(url => response(url.endsWith(ID) ? 200 : 202,
    receipt(url.endsWith(ID) ? { revision: String(++gets + 1), userStatus: gets >= 2 ? "SHOWN" : "WAITING" } : {})));
  client.begin("안녕"); await client.send();
  const updates = [];
  const result = await client.watchReceipt({ maxPolls: 10, onChange: value => updates.push(value) });
  assert.equal(result.polls, 2);
  assert.equal(updates.at(-1).view.card, "COMPLETE");
  assert.equal(updates.at(-1).receipt.userStatus, "SHOWN");
});

test("normal stages use one completion card and purged content does not replace outcome", () => {
  for (const userStatus of ["PREPARING", "WAITING", "STARTING", "SHOWING", "SHOWN", "PLAYBACK_UNCONFIRMED"]) {
    assert.equal(studentReceiptView(receipt({ userStatus, contentStatus: "PURGED" })).card, "COMPLETE");
  }
  assert.equal(studentReceiptView(receipt({ userStatus: "NEEDS_REWRITE", contentStatus: "PURGED" })).card, "REWRITE");
});

test("stale lower revisions cannot replace a newer rejection with completion", async () => {
  const { client } = setup(url => response(url.endsWith(ID) ? 200 : 202,
    receipt(url.endsWith(ID) ? { revision: "1" } : { revision: "2", userStatus: "NEEDS_REWRITE" })));
  client.begin("안녕");
  assert.equal((await client.send()).view.card, "REWRITE");
  assert.equal((await client.getReceipt()).view.card, "REWRITE");
});

test("five-second deadline settles an abort-ignoring transport", async () => {
  let expire;
  const client = new SubmissionClient({ fetch: () => new Promise(() => {}),
    uuid: () => ID, setTimer: callback => { expire = callback; return 1; }, clearTimer: () => {} });
  client.begin("안녕");
  const pending = client.send();
  expire();
  assert.equal((await pending).state, "UNKNOWN");
  assert.equal(client.intent.idempotencyKey, ID);
});

test("a private nonpublication result uses a neutral notice without crisis guidance", () => {
  for (const userStatus of ["NEEDS_REWRITE", "SHOWN"]) {
    const view = studentReceiptView(receipt({ userStatus, reasonCode: "SUPPORT_AVAILABLE", contentStatus: "PURGED" }));
    assert.equal(view.card, "NOTICE");
    assert.equal(view.title, "전광판에 표시되지 않았어요");
    assert.match(view.message, /전광판에 표시하지 않았어요/);
    assert.doesNotMatch(`${view.title} ${view.message}`, /바꿔|수정|벌점|도움|선생님|어른|연락|위기|힘들/);
  }
});

test("uncertainty uses a neutral explanation rather than calling the message harmful", () => {
  const view = studentReceiptView(receipt({ userStatus: "NEEDS_REWRITE", reasonCode: "CONTENT_REVIEW_UNCERTAIN" }));
  assert.equal(view.card, "UNCERTAIN");
  assert.match(view.title, /판단하기 어려웠어요/);
  assert.doesNotMatch(view.message, /욕설|괴롭힘|벌점/);
});

test("provider safety failure is a terminal neutral notice without requiring rephrasing", async () => {
  const { client, requests } = setup(() => response(202, receipt({
    processingStatus: "FAILED", userStatus: "FAILED", reasonCode: "CONTENT_REVIEW_UNAVAILABLE", contentStatus: "PURGED",
  })));
  client.begin("오늘 기분을 남겨요");
  const result = await client.send();
  assert.equal(result.view.card, "NOTICE");
  assert.equal(result.view.title, "내용 확인을 완료하지 못했어요");
  assert.equal(result.view.message, "이 글은 아직 전광판에 표시되지 않았어요. 다시 작성할 수 있어요.");
  assert.equal(result.view.terminal, true);
  assert.equal(result.receipt.userStatus, "FAILED");
  assert.doesNotMatch(result.view.message, /위반|욕설|경고|벌점|바꿔|수정/);
  assert.equal(requests.length, 1);
});

test("support receipt releases the retained message body while preserving its recovery key", async () => {
  const { client, requests } = setup(() => response(202, receipt({ userStatus: "NEEDS_REWRITE", reasonCode: "SUPPORT_AVAILABLE", contentStatus: "PURGED" })));
  const intent = client.begin("도와줘 너무 힘들어");
  const result = await client.send();
  assert.equal(result.view.card, "NOTICE");
  assert.equal(client.intent.content, "");
  assert.equal(client.intent.idempotencyKey, intent.idempotencyKey);
  assert.equal((await client.send()).view.card, "NOTICE");
  assert.equal(requests.length, 1);
});

test("client upgrade response preserves the intent and never automatically reposts", async () => {
  const { client, requests } = setup(() => response(409, { code: "CLIENT_UPDATE_REQUIRED" }, "application/problem+json"));
  const intent = client.begin("남겨 둔 초안");
  assert.equal((await client.send()).state, "UPDATE_REQUIRED");
  assert.equal(client.intent, intent);
  assert.throws(() => client.begin("다른 초안"), { code: "PENDING_SUBMISSION_EXISTS" });
  assert.equal((await client.send()).state, "UPDATE_REQUIRED");
  assert.equal(requests.length, 1);
});

test("support capability is present on owner lookup and polling; upgrade stops polling", async () => {
  const { client, requests } = setup(url => response(url.endsWith(ID) ? 409 : 200,
    url.endsWith(ID) ? { code: "CLIENT_UPDATE_REQUIRED" } : receipt()));
  client.begin("안녕");
  await client.recover();
  const changes = [];
  const result = await client.watchReceipt({ maxPolls: 10, onChange: value => changes.push(value) });
  assert.equal(result.polls, 1);
  assert.equal(changes[0].state, "UPDATE_REQUIRED");
  assert.equal(client.receipt.submissionId, ID);
  assert.ok(requests.every(({ init }) => init.headers["X-Moodwall-Client-Features"] === "support-v1"));
});

function memoryStorage() {
  const values = new Map();
  return { values, getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
}
const BASE = Date.parse("2026-11-04T03:00:00Z");

test("recovery metadata is saved before dispatch and contains neither text nor credentials", async () => {
  const storage = memoryStorage();
  const client = new SubmissionClient({ storage, wallNow: () => BASE, uuid: () => ID, csrfToken: () => "private-csrf",
    fetch: async () => {
      const saved = JSON.parse([...storage.values.values()][0]);
      assert.deepEqual(Object.keys(saved).sort(), ["expiresAt", "idempotencyKey", "submissionId", "version"]);
      assert.equal(saved.idempotencyKey, ID);
      assert.equal(saved.submissionId, null);
      return response(202, receipt({ userStatus: "WAITING" }));
    } });
  client.begin("보관하면 안 되는 본문");
  assert.equal(storage.values.size, 0);
  await client.send();
  assert.equal(JSON.parse([...storage.values.values()][0]).submissionId, ID);
  assert.doesNotMatch([...storage.values.values()].join(), /보관|본문|csrf|content|cookie|statusUrl/i);
});

test("reload after a lost acceptance restores an already READY receipt by the original key", async () => {
  const storage = memoryStorage(), calls = [];
  const original = new SubmissionClient({ storage, wallNow: () => BASE, uuid: () => ID,
    fetch: async () => { throw Error("committed but response lost"); } });
  original.begin("다시 저장하지 않는 본문");
  await original.send(); original.resetAuthentication();
  const reloaded = new SubmissionClient({ storage, wallNow: () => BASE + 1000,
    uuid: () => { throw Error("must not create a new UUID on reload"); },
    fetch: async (url, init) => { calls.push({ url, init }); return response(200, receipt({ userStatus: "WAITING", presentationStatus: "READY" })); } });
  assert.equal(reloaded.hasPendingRecovery, true);
  assert.equal(reloaded.canResend, false);
  const result = await reloaded.restorePending();
  assert.equal(result.state, "ACCEPTED");
  assert.equal(result.receipt.presentationStatus, "READY");
  assert.equal(calls[0].url, "/api/v2/submission-receipts/lookup");
  assert.deepEqual(JSON.parse(calls[0].init.body), { idempotencyKey: ID });
  assert.equal((await reloaded.send()).state, "ACCEPTED");
  assert.equal(calls.length, 1);
  assert.equal(reloaded.intent.content, null);
});

test("reload after 202 queries the saved owner receipt without retaining a message body", async () => {
  const storage = memoryStorage(), calls = [];
  const first = new SubmissionClient({ storage, wallNow: () => BASE, uuid: () => ID, fetch: async () => response(202, receipt()) });
  first.begin("초안은 저장하지 않아요"); await first.send();
  const restored = new SubmissionClient({ storage, wallNow: () => BASE + 500,
    fetch: async (url, init) => { calls.push({ url, init }); return response(200, receipt({ userStatus: "SHOWN" })); } });
  assert.equal((await restored.restorePending()).receipt.userStatus, "SHOWN");
  assert.equal(calls[0].url, `/api/v2/submissions/${ID}`);
  assert.equal(calls[0].init.method, "GET");
  assert.equal(calls[0].init.body, undefined);
});

test("an absent restored receipt remains uncertain and cannot repost an empty body or make a fresh key", async () => {
  const storage = memoryStorage(), calls = [];
  const first = new SubmissionClient({ storage, wallNow: () => BASE, uuid: () => ID, fetch: async () => { throw Error("lost"); } });
  first.begin("안녕"); await first.send();
  const restored = new SubmissionClient({ storage, wallNow: () => BASE,
    uuid: () => { throw Error("unexpected UUID"); }, fetch: async (url, init) => { calls.push({ url, init }); return response(404, { code: "NOT_FOUND" }); } });
  assert.equal((await restored.restorePending()).state, "UNKNOWN");
  assert.throws(() => restored.begin("다른 글"), { code: "PENDING_SUBMISSION_EXISTS" });
  assert.equal((await restored.send()).code, "ORIGINAL_CONTENT_UNAVAILABLE");
  assert.equal(calls.length, 1);
  assert.equal(calls.some(({ url }) => url === "/api/v2/submissions"), false);
});

test("a generic 503 problem remains uncertain while an explicit admission rejection is final", async () => {
  for (const [code, expected] of [["SERVICE_UNAVAILABLE", "UNKNOWN"], ["DISPLAY_OFFLINE", "REJECTED"]]) {
    const storage = memoryStorage();
    const client = new SubmissionClient({ storage, wallNow: () => BASE, uuid: () => ID,
      fetch: async () => response(503, { code }, "application/problem+json") });
    client.begin("안녕");
    assert.equal((await client.send()).state, expected);
    assert.equal(storage.values.size, expected === "UNKNOWN" ? 1 : 0);
    if (expected === "UNKNOWN") assert.throws(() => client.begin("새 글"), { code: "PENDING_SUBMISSION_EXISTS" });
  }
});

test("unavailable storage retains the in-memory intent and exposes its recovery limitation", async () => {
  const client = new SubmissionClient({ storage: { getItem() { throw Error("denied"); }, setItem() { throw Error("denied"); } },
    uuid: () => ID, fetch: async () => { throw Error("lost"); } });
  const intent = client.begin("계속 확인할 본문");
  assert.equal((await client.send()).state, "UNKNOWN");
  assert.equal(client.recoveryPersistent, false);
  assert.equal(client.intent, intent);
  assert.equal(client.canResend, true);
});

test("expired or non-whitelisted recovery metadata is discarded without dispatch", async () => {
  for (const extra of [{ expiresAt: new Date(BASE - 1).toISOString() }, { content: "must never restore" }]) {
    const storage = memoryStorage();
    storage.setItem("moodwall.student.recovery.v1", JSON.stringify({ version: 1, idempotencyKey: ID, submissionId: null,
      expiresAt: new Date(BASE + 10000).toISOString(), ...extra }));
    const client = new SubmissionClient({ storage, wallNow: () => BASE, fetch: () => { throw Error("unexpected request"); } });
    await client.restorePending();
    assert.equal(client.hasPendingRecovery, false);
    assert.equal(client.intent, null);
    assert.equal(storage.values.size, 0);
  }
});

test("a fast phone clock does not erase a valid key before server-time calibration", async () => {
  const storage = memoryStorage();
  const first = new SubmissionClient({ storage, wallNow: () => BASE, uuid: () => ID, fetch: async () => { throw Error("lost"); } });
  first.begin("안녕"); await first.send();
  let clock = BASE + 2 * 24 * 60 * 60 * 1000;
  const restored = new SubmissionClient({ storage, wallNow: () => clock, fetch: async () => response(200, receipt()) });
  assert.equal(restored.hasPendingRecovery, true);
  clock = BASE + 1000; // /api/session.serverNow, before any owner lookup.
  assert.equal((await restored.restorePending()).state, "ACCEPTED");
});

test("polling survives five minutes and detects an actionable result near the ten-minute wait limit", async () => {
  let clock = BASE;
  const delays = [], changes = [];
  const client = new SubmissionClient({ storage: null, uuid: () => ID, wallNow: () => clock, now: () => clock - BASE,
    sleep: async ms => { delays.push(ms); clock += ms; },
    fetch: async (url) => response(url.endsWith(ID) ? 200 : 202, receipt(clock >= BASE + 480000
      ? { revision: "2", userStatus: "NEEDS_REWRITE" } : { userStatus: "WAITING" })) });
  client.begin("안녕"); await client.send();
  const result = await client.watchReceipt({ onChange: next => changes.push(next) });
  assert.ok(clock >= BASE + 480000);
  assert.ok(result.polls < 50);
  assert.ok(delays.every(ms => ms <= 30000));
  assert.equal(changes.at(-1).view.card, "REWRITE");
});

test("unchanged receipts stop at their wait deadline without inventing a rejection", async () => {
  let clock = BASE;
  const client = new SubmissionClient({ storage: null, uuid: () => ID, wallNow: () => clock, now: () => clock - BASE,
    sleep: async ms => { clock += ms; }, fetch: async (url) => response(url.endsWith(ID) ? 200 : 202, receipt({ userStatus: "WAITING" })) });
  client.begin("안녕"); await client.send();
  const result = await client.watchReceipt();
  assert.equal(clock, BASE + 630000);
  assert.ok(result.polls < 50);
  assert.equal(result.receipt.userStatus, "WAITING");
  assert.equal(client.state, "ACCEPTED");
});

test("an actual start extends polling beyond the predisplay deadline until the final receipt", async () => {
  let clock = BASE;
  const firstStart = new Date(BASE + 590000).toISOString();
  const client = new SubmissionClient({ storage: null, uuid: () => ID, wallNow: () => clock, now: () => clock - BASE,
    sleep: async ms => { clock += ms; }, fetch: async (url) => response(url.endsWith(ID) ? 200 : 202,
      receipt(clock >= BASE + 590000 ? { revision: clock >= BASE + 660000 ? "3" : "2",
        userStatus: clock >= BASE + 660000 ? "SHOWN" : "SHOWING", firstStartAuthorizedAt: firstStart,
        contentDeleteAt: new Date(BASE + 4190000).toISOString() } : { userStatus: "WAITING" })) });
  client.begin("안녕"); await client.send();
  const result = await client.watchReceipt();
  assert.ok(clock >= BASE + 660000);
  assert.equal(result.receipt.userStatus, "SHOWN");
});
