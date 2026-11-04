// Offline design-artifact checks; this is not the production moderation validator.
// No credentials, network, database or model calls are used.
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const read = (path) => readFileSync(resolve(root, path), "utf8");
const schema = JSON.parse(read("docs/gemini-moderation-response.schema.json"));
const cases = JSON.parse(read("docs/checks/gemini-moderation-cases.json"));
const prompt = read("docs/gemini-moderation-prompt.txt");
const segmenter = new Intl.Segmenter("ko", { granularity: "grapheme" });
const mapping = {
  CP01: ["HARASSMENT"], CP02: ["HATE"], CP03: ["SEXUAL_CONTENT"],
  CP04: ["THREAT"], CP05: ["PERSONAL_INFORMATION"], CP06: ["OTHER_UNSAFE"],
  CP07: ["OTHER_UNSAFE"], CP08: ["SELF_CRISIS"], CP09: ["OTHER_UNSAFE"],
  CP10: ["INSUFFICIENT_CONTEXT", "OUT_OF_DOMAIN"],
};
const sameSet = (a, b) => assert.deepEqual([...a].sort(), [...b].sort());

// Validate only the deliberately small schema vocabulary in this artifact.
function shape(node, value) {
  if (node.type === "object") {
    assert.ok(value && typeof value === "object" && !Array.isArray(value));
    for (const key of node.required) assert.ok(Object.hasOwn(value, key));
    for (const key of Object.keys(value)) {
      assert.ok(Object.hasOwn(node.properties, key));
      shape(node.properties[key], value[key]);
    }
  } else if (node.type === "array") {
    assert.ok(Array.isArray(value));
    assert.ok(value.length <= node.maxItems);
    value.forEach((item) => shape(node.items, item));
  } else {
    assert.equal(node.type, "string");
    assert.equal(typeof value, "string");
    assert.ok(node.enum.includes(value));
  }
}

function semantic(value, flags) {
  shape(schema, value);
  const { decision, reason_codes: reasons, policy_rule_ids: rules } = value;
  assert.equal(new Set(reasons).size, reasons.length);
  assert.equal(new Set(rules).size, rules.length);
  if (decision === "ALLOW") {
    sameSet(reasons, []); sameSet(rules, []);
  } else {
    assert.ok(reasons.length && rules.length);
    for (const cp of rules) assert.ok(mapping[cp].some((reason) => reasons.includes(reason)));
    for (const reason of reasons) assert.ok(rules.some((cp) => mapping[cp].includes(reason)));
    if (decision === "UNCERTAIN") {
      sameSet(rules, ["CP10"]); assert.equal(reasons.length, 1);
    } else {
      assert.ok(!rules.includes("CP10"));
      if (decision === "SUPPORT") assert.ok(rules.includes("CP08"));
      else { assert.equal(decision, "BLOCK"); assert.ok(!rules.includes("CP08")); }
    }
  }
  if (flags?.restricted_identifier) assert.ok(rules.includes("CP05"));
  if (flags?.confirmed_profanity) assert.ok(rules.includes("CP06"));
}

sameSet(Object.keys(schema.properties), ["schema_version", "decision", "reason_codes", "policy_rule_ids"]);
sameSet(schema.required, Object.keys(schema.properties));
assert.equal(schema.additionalProperties, false);
sameSet(schema.properties.decision.enum, ["ALLOW", "BLOCK", "UNCERTAIN", "SUPPORT"]);
sameSet(schema.properties.policy_rule_ids.items.enum, Object.keys(mapping));
sameSet(schema.properties.reason_codes.items.enum, [...new Set(Object.values(mapping).flat())]);
assert.equal(schema.properties.reason_codes.maxItems, 5);
assert.equal(schema.properties.policy_rule_ids.maxItems, 5);
assert.ok(cases.length >= 40);
const seen = new Set();
const counts = { ALLOW: 0, BLOCK: 0, UNCERTAIN: 0, SUPPORT: 0 };
let maximumGraphemes = 0;
for (const item of cases) {
  sameSet(Object.keys(item), ["case_id", "scenario_group_id", "text", "local_findings", "expected"]);
  assert.ok(item.case_id && item.scenario_group_id && !seen.has(item.case_id));
  seen.add(item.case_id);
  assert.equal(typeof item.text, "string");
  assert.equal(item.text.normalize("NFC"), item.text);
  const length = [...segmenter.segment(item.text)].length;
  assert.ok(length >= 1 && length <= 30, item.case_id);
  assert.ok(Buffer.byteLength(item.text, "utf8") <= 2048);
  maximumGraphemes = Math.max(maximumGraphemes, length);
  sameSet(Object.keys(item.local_findings), ["restricted_identifier", "confirmed_profanity"]);
  Object.values(item.local_findings).forEach((flag) => assert.equal(typeof flag, "boolean"));
  // In this fixture set, trusted identifier evidence is represented by a redacted span.
  assert.equal(item.local_findings.restricted_identifier,
    /\[(?:연락처|이메일|비밀정보)\]/u.test(item.text), item.case_id);
  semantic(item.expected, item.local_findings);
  counts[item.expected.decision]++;
}

let promptExamples = 0;
for (const match of prompt.matchAll(/^입력: (\{.+\})\r?\n출력: (\{.+\})$/gm)) {
  const input = JSON.parse(match[1]);
  semantic(JSON.parse(match[2]), input.local_findings);
  promptExamples++;
}
assert.ok(promptExamples >= 6);

const allow = { schema_version: "1", decision: "ALLOW", reason_codes: [], policy_rule_ids: [] };
const invalid = [
  { ...allow, schema_version: "2" },
  { ...allow, decision: "allow" },
  { ...allow, explanation: "extra field" },
  { ...allow, reason_codes: ["HATE"] },
  { ...allow, decision: "BLOCK" },
  { ...allow, decision: "SUPPORT", reason_codes: ["HATE"], policy_rule_ids: ["CP02"] },
  { ...allow, decision: "BLOCK", reason_codes: ["SELF_CRISIS"], policy_rule_ids: ["CP08"] },
  { ...allow, decision: "BLOCK", reason_codes: ["HATE", "HATE"], policy_rule_ids: ["CP02"] },
  { ...allow, decision: "BLOCK", reason_codes: ["HATE"], policy_rule_ids: ["CP01"] },
  { ...allow, decision: "UNCERTAIN", reason_codes: ["INSUFFICIENT_CONTEXT", "OUT_OF_DOMAIN"], policy_rule_ids: ["CP10"] },
  { ...allow, decision: "BLOCK", reason_codes: ["OTHER_UNSAFE"], policy_rule_ids: ["CP99"] },
  { ...allow, reason_codes: null },
  { ...allow, policy_rule_ids: ["CP01", "CP02", "CP03", "CP04", "CP05", "CP06"] },
];
for (const value of invalid) assert.throws(() => semantic(value));
assert.throws(() => semantic(allow, { restricted_identifier: true }));
assert.throws(() => semantic(allow, { confirmed_profanity: true }));

const documents = [
  "README.md", "docs/ai-contract.md", "docs/ai-team-request.md", "docs/content-policy.md",
  "docs/profanity-policy.md", "docs/security-and-operations.md", "docs/student-ux.md",
  "docs/submission-status-errors.md", "docs/implementation-status.md",
  "docs/gemini-moderation-design.md", "docs/gemini-moderation-evaluation.md",
  "docs/gemini-moderation-operations.md",
];
let localLinks = 0;
let jsonSnippets = 0;
let tables = 0;
for (const document of documents) {
  const text = read(document);
  assert.ok(!text.includes("\ufffd"), document);
  assert.ok(text.split(/\r?\n/).every((line) => line === line.trimEnd()), document);
  assert.equal((text.match(/^```/gm) ?? []).length % 2, 0, document);
  let fenced = false;
  let columns = null;
  for (const line of text.split(/\r?\n/)) {
    if (line.startsWith("```")) fenced = !fenced;
    if (!fenced && line.startsWith("|")) {
      const count = (line.replace(/\\\|/g, "").match(/\|/g) ?? []).length;
      if (columns === null) { columns = count; tables++; }
      assert.equal(count, columns, `${document}: table columns`);
    } else columns = null;
  }
  for (const match of text.matchAll(/\[[^\]]+\]\(([^)]+)\)/g)) {
    if (/^(?:https?:|#)/.test(match[1])) continue;
    const target = decodeURIComponent(match[1].split("#")[0].replace(/^<|>$/g, ""));
    assert.ok(existsSync(resolve(root, dirname(document), target)), `${document}: ${target}`);
    localLinks++;
  }
  for (const match of text.matchAll(/```json\r?\n([\s\S]*?)```/g)) {
    JSON.parse(match[1]); jsonSnippets++;
  }
}
console.log(JSON.stringify({
  scope: "offline design artifacts only; not API/model/runtime evaluation",
  cases: cases.length, decisions: counts, maximumGraphemes, promptExamples,
  invalidSemanticExamplesRejected: invalid.length + 2,
  documents: documents.length, localLinks, tables, jsonSnippets, providerCalls: 0,
}, null, 2));
