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
  sleep: async () => {} });
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
  assert.equal(requests[0].init.credentials, "same-origin");
  assert.equal(JSON.stringify(result.receipt).includes("오늘 행복해"), false);
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
