import test from "node:test";
import assert from "node:assert/strict";
import { ReportClient } from "./report-client.mjs";

const KEY = "11111111-1111-4111-8111-111111111111";
const ID = "22222222-2222-4222-8222-222222222222";

test("a report sends fixed metadata only with CSRF and the intent key", async () => {
  const calls = [];
  const client = new ReportClient({ uuid: () => KEY, csrfToken: () => "csrf",
    request: async (...args) => { calls.push(args); return { reportId: ID, status: "RECEIVED", content: "ignored" }; } });
  client.begin("PRIVACY");
  assert.deepEqual(await client.send(), { state: "ACCEPTED", reportId: ID, status: "RECEIVED" });
  assert.deepEqual(calls, [["/api/v2/reports", { method: "POST", body: { category: "PRIVACY" }, csrfToken: "csrf", idempotencyKey: KEY }]]);
});

test("unknown report delivery locks the category and retries exactly the same intent", async () => {
  const calls = [];
  const client = new ReportClient({ uuid: () => KEY, request: async (...args) => {
    calls.push(args); if (calls.length === 1) throw Error("lost response"); return { reportId: ID, status: "RECEIVED" };
  } });
  const intent = client.begin("THREAT");
  assert.equal((await client.send()).state, "UNKNOWN");
  assert.throws(() => client.begin("OTHER"), { code: "PENDING_REPORT_EXISTS" });
  assert.equal(client.begin("THREAT"), intent);
  assert.equal((await client.send()).state, "ACCEPTED");
  assert.deepEqual(calls[0], calls[1]);
});

test("a report double click shares the in-flight promise", async () => {
  let resolve;
  const client = new ReportClient({ uuid: () => KEY, request: () => new Promise(done => { resolve = done; }) });
  client.begin("OTHER");
  const first = client.send(), second = client.send();
  assert.equal(first, second);
  resolve({ reportId: ID, status: "RECEIVED" });
  await first;
});

test("invalid categories and external target URLs cannot be reported", () => {
  const client = new ReportClient({ uuid: () => KEY });
  assert.throws(() => client.begin("original student text"), { code: "INVALID_REPORT" });
  assert.throws(() => client.begin("PRIVACY", "https://example.com"), { code: "INVALID_REPORT" });
});

test("authentication reset fences late report receipts", async () => {
  let resolve;
  const client = new ReportClient({ uuid: () => KEY, request: () => new Promise(done => { resolve = done; }) });
  client.begin("OTHER");
  const pending = client.send(); client.reset();
  resolve({ reportId: ID, status: "RECEIVED" });
  assert.equal((await pending).state, "STALE");
  assert.equal(client.intent, null);
});

test("a definitive rate limit allows a fresh deliberate report rather than claiming acceptance", async () => {
  const client = new ReportClient({ uuid: () => KEY, request: async () => { throw Object.assign(Error("synthetic"), { status: 429, code: "RATE_LIMITED" }); } });
  client.begin("OTHER");
  assert.equal((await client.send()).state, "REJECTED");
  assert.equal(client.begin("PRIVACY").body.category, "PRIVACY");
});
