import test from "node:test";
import assert from "node:assert/strict";
import { secureUuid } from "./uuid.mjs";
import { SubmissionClient } from "./student/submission-client.mjs";
import { DisplayPlaybackController } from "./display/playback-controller.mjs";

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

test("uses native randomUUID with its crypto receiver before the fallback", () => {
  const expected = "aabbccdd-eeff-4011-8233-445566778899";
  const source = {
    randomUUID() { assert.equal(this, source); return expected; },
    getRandomValues() { assert.fail("native source must take priority"); },
  };
  assert.equal(secureUuid(source), expected);
});

test("fallback requests 128 secure random bits and sets the RFC v4 version and variant", () => {
  const source = { getRandomValues(bytes) {
    assert.equal(this, source);
    assert.ok(bytes instanceof Uint8Array);
    assert.equal(bytes.length, 16);
    bytes.set(Array.from({ length: 16 }, (_, index) => index));
    return bytes;
  } };
  const result = secureUuid(source);
  assert.equal(result, "00010203-0405-4607-8809-0a0b0c0d0e0f");
  assert.match(result, UUID_V4);
});

test("absence of a secure RNG fails explicitly instead of generating a weak identity", () => {
  for (const source of [null, {}, { randomUUID: null, getRandomValues: null }]) {
    assert.throws(() => secureUuid(source), error => error.code === "SECURE_RANDOM_UNAVAILABLE");
  }
});

test("student and display adapters create distinct valid defaults on HTTP-like crypto", () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  let sequence = 0;
  Object.defineProperty(globalThis, "crypto", { configurable: true, value: {
    getRandomValues(bytes) { bytes.fill(++sequence); return bytes; },
  } });
  try {
    const student = new SubmissionClient();
    const intent = student.begin("오늘도 힘내");
    const display = new DisplayPlaybackController({
      transport: { request() {} },
      renderer: { prepare() {}, show() {}, hide() {} },
      visible: () => true,
    });
    assert.match(intent.idempotencyKey, UUID_V4);
    assert.match(display.clientInstanceId, UUID_V4);
    assert.notEqual(intent.idempotencyKey, display.clientInstanceId);
    assert.equal(sequence, 2);
  } finally {
    if (original) Object.defineProperty(globalThis, "crypto", original);
    else delete globalThis.crypto;
  }
});
