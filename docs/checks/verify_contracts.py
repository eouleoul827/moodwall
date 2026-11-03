"""Validate design artifacts. Does not start an API or execute student input."""

from __future__ import annotations

import argparse
import ast
import copy
import json
import re
import sys
import types
from pathlib import Path
from urllib.parse import unquote

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--dependency-path", type=Path, help="Optional isolated pip --target directory")
arguments = parser.parse_args()
if arguments.dependency_path:
    sys.path.insert(0, str(arguments.dependency_path.resolve()))

from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import OpenAPIV31SpecValidator
from pglast import parse_sql
from pydantic import TypeAdapter, ValidationError

ROOT = Path(__file__).resolve().parents[2]
spec = json.loads((ROOT / "docs/openapi.json").read_text(encoding="utf-8-sig"))
OpenAPIV31SpecValidator(spec).validate()
print(f"PASS OpenAPI 3.1: {len(spec['paths'])} paths")


def resolve(reference: str):
    assert reference.startswith("#/"), reference
    value = spec
    for part in reference[2:].split("/"):
        value = value[part.replace("~1", "/").replace("~0", "~")]
    return value


def expand(value):
    if isinstance(value, dict):
        if "$ref" in value:
            value = {**resolve(value["$ref"]), **{k: v for k, v in value.items() if k != "$ref"}}
        return {k: expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v) for v in value]
    return value


expand(spec)
operation_ids = []
example_count = 0
for path, item in spec["paths"].items():
    for method, operation in item.items():
        if method not in ("get", "post", "put", "patch", "delete"):
            continue
        operation_ids.append(operation["operationId"])
        parameters = operation.get("parameters", [])
        identities = [(p["in"], p["name"].lower()) for p in parameters]
        assert len(identities) == len(set(identities)), (path, "duplicate parameter")
        if method == "post":
            assert any(p["name"] == "Origin" and p["required"] for p in parameters), path
            csrf = [p for p in parameters if p["name"] == "X-CSRF-Token"]
            assert len(csrf) == 1, (path, "CSRF contract")
            if operation.get("security"):
                assert csrf[0]["required"], path
        for status, response in operation["responses"].items():
            response = expand(response)
            assert "X-Request-ID" in response["headers"], (path, status)
            assert response["headers"]["Cache-Control"]["schema"]["const"] == "no-store"
            if status.isdigit() and int(status) >= 400:
                assert "application/problem+json" in response["content"], (path, status)
            if status == "405":
                assert "Allow" in response["headers"], path
            for content_type, media in response.get("content", {}).items():
                validator = Draft202012Validator(media["schema"], format_checker=FormatChecker())
                examples = [media["example"]] if "example" in media else []
                examples += [x["value"] for x in media.get("examples", {}).values() if "value" in x]
                for value in examples:
                    validator.validate(value)
                    if content_type == "application/problem+json":
                        assert value["status"] == int(status)
                        assert value["type"] == "/problems/" + value["code"].lower().replace("_", "-")
                        assert value["instance"] == "urn:uuid:" + value["requestId"]
                    example_count += 1
assert len(operation_ids) == len(set(operation_ids)), "duplicate operationId"
print(f"PASS references, auth/header metadata, and {example_count} response examples")

schema_statements = parse_sql((ROOT / "docs/schema.sql").read_text(encoding="utf-8-sig"))
checks_sql = (ROOT / "docs/checks/schema-invariants.sql").read_text(encoding="utf-8-sig")
parse_sql("\n".join(line for line in checks_sql.splitlines() if not line.startswith("\\")))
print(f"PASS PostgreSQL syntax: {len(schema_statements)} schema statements and regression script")

link_count = 0
python_count = 0
json_snippet_count = 0
documents = [ROOT / "README.md", *(ROOT / "docs").rglob("*.md")]
for document in documents:
    text = document.read_text(encoding="utf-8-sig")
    assert "\ufffd" not in text, document
    assert all(line == line.rstrip() for line in text.splitlines()), document
    for _, destination in re.findall(r"\[([^\]]+)\]\(([^\)]+)\)", text):
        if destination.startswith(("https:", "http:", "#")):
            continue
        destination = unquote(destination.split("#")[0].strip("<>"))
        assert (document.parent / destination).exists(), (document.name, destination)
        link_count += 1
    for code in re.findall(r"```sql\n(.*?)```", text, flags=re.S):
        parse_sql(code)
    for code in re.findall(r"```python\n(.*?)```", text, flags=re.S):
        ast.parse(code)
        python_count += 1
    for code in re.findall(r"```json\n(.*?)```", text, flags=re.S):
        json.loads(code)
        json_snippet_count += 1
print(f"PASS {link_count} local links, document encoding/whitespace, {python_count} Python/{json_snippet_count} JSON snippets")

module = types.ModuleType("moodwall_contract_check")
sys.modules[module.__name__] = module
text = (ROOT / "docs/ai-contract.md").read_text(encoding="utf-8-sig")
for code in re.findall(r"```python\n(.*?)```", text, flags=re.S):
    exec(compile(ast.parse(code), "ai-contract.md", "exec"), module.__dict__)
examples = [json.loads(code) for code in re.findall(r"```json\n(.*?)```", text, flags=re.S)]
result_type = module.__dict__["AnalysisResult"]
for example in examples:
    result_type.model_validate(example)
base = examples[0]
invalid = []
for score in (float("nan"), float("inf"), -0.1, 1.1):
    value = copy.deepcopy(base)
    value["safety"]["score"] = score
    invalid.append(value)
value = copy.deepcopy(base)
value["emotion"] = None
invalid.append(value)
value = copy.deepcopy(base)
value["emotion"]["label"] = "UNKNOWN"
invalid.append(value)
value = copy.deepcopy(base)
value["safety"] = {"verdict": "BLOCK", "score": 0.9, "reason_codes": []}
value["emotion"] = None
invalid.append(value)
value = copy.deepcopy(base)
value["safety"] = {"verdict": "BLOCK", "score": 0.9, "reason_codes": ["HARASSMENT"]}
invalid.append(value)
value = copy.deepcopy(base)
value["emotion"] = {"label": "NEUTRAL", "score": 0.8, "source": "FALLBACK"}
invalid.append(value)
value = copy.deepcopy(base)
value["extra"] = "unexpected"
invalid.append(value)
for value in invalid:
    try:
        result_type.model_validate(value)
    except ValidationError:
        continue
    raise AssertionError("AI contract accepted an invalid fixture")
fallback = copy.deepcopy(base)
fallback["emotion"] = {"label": "NEUTRAL", "score": None, "source": "FALLBACK"}
result_type.model_validate(fallback)
print(f"PASS AI contract: {len(examples) + 1} valid and {len(invalid)} rejected cases")

outcome_type = TypeAdapter(module.__dict__["FinalizationOutcome"])
rule_outcome = {
    "kind": "RULE_REJECTED", "lexicon_version": "lexicon-fixture-v1",
    "matches": [{"rule_id": "ko-explicit-001", "variant_id": "exact"}],
}
valid_outcomes = [
    rule_outcome,
    {"kind": "MODEL_ANALYZED", "analysis": base},
    {"kind": "RUN_FAILED", "error_code": "POLICY_VERSION_MISMATCH"},
]
for value in valid_outcomes:
    outcome_type.validate_python(value)
invalid_outcomes = []
for matches in (
    [], rule_outcome["matches"] * 2,
    [{"rule_id": "z", "variant_id": "v"}, {"rule_id": "a", "variant_id": "v"}],
    [{"rule_id": f"r{i}", "variant_id": "v"} for i in range(6)],
    [{"rule_id": "arbitrary text!", "variant_id": "v"}],
):
    value = copy.deepcopy(rule_outcome)
    value["matches"] = matches
    invalid_outcomes.append(value)
invalid_outcomes += [
    {**rule_outcome, "analysis": base},
    {"kind": "MODEL_ANALYZED", "analysis": base, "matches": rule_outcome["matches"]},
    {"kind": "RUN_FAILED", "error_code": "UNSPECIFIED"},
    {"kind": "UNKNOWN"},
]
for value in invalid_outcomes:
    try:
        outcome_type.validate_python(value)
    except ValidationError:
        continue
    raise AssertionError("FinalizationOutcome accepted an invalid fixture")
module.__dict__["FinalizationEnvelope"].model_validate_json(json.dumps({
    "submission_id": "11111111-1111-4111-8111-111111111111",
    "run_token": "22222222-2222-4222-8222-222222222222",
    "outcome": rule_outcome,
}))
print(f"PASS finalization union: {len(valid_outcomes)} valid, {len(invalid_outcomes)} rejected, UUID envelope parsed")

# Exercise floating bubble paging and private/operator separation.
uuid = "11111111-1111-4111-8111-111111111111"
now = "2026-10-29T01:00:00Z"
usage = {
    "windowSeconds": 3600, "limit": 3, "usedCount": 0, "pendingCount": 0,
    "remainingSlots": 3, "nextSlotAt": None, "businessDate": "2026-10-29",
    "violationCount": 0, "warning": False, "suspendedUntil": None,
    "activeSubmissionId": None, "nextSubmissionAt": None,
}
card = {
    "id": uuid, "content": "synthetic fixture", "emojiId": "party",
    "emotion": "JOY", "animationId": "hearts", "acceptedAt": now,
    "expiresAt": "2026-10-29T02:00:00Z",
    "ambientUntil": "2026-10-29T15:00:00Z",
}
display_state = {
    "boardId": uuid, "businessDate": "2026-10-29", "revision": 1,
    "mode": "MOODWALL", "serverNow": now,
    "onlineLeaseUntil": "2026-10-29T01:00:30Z",
    "bubbles": [card], "bubbleTotalCount": 1, "snapshotAt": now, "nextCursor": None,
    "qrRotatesAt": "2026-10-29T01:05:00Z", "ambientUntil": "2026-10-29T15:00:00Z",
    "removedIds": [], "cacheResetToken": "0", "deviceGeneration": "1",
}
evidence = {"ruleId": "ko-explicit-001", "variantId": "exact"}
restore = {"auditId": "9007199254740993", "replayed": True, "businessDate": "2026-10-28", "usage": usage}
audit = {
    "id": "9007199254740993", "operatorUsername": "fixture-admin",
    "action": "RESET_RESTRICTION", "targetId": uuid, "reasonCode": "FALSE_POSITIVE",
    "createdAt": now, "beforeViolationCount": 5, "afterViolationCount": 0,
    "beforeSuspendedUntil": now, "afterSuspendedUntil": None,
}
submission = {
    "id": uuid, "businessDate": "2026-10-29", "moderationStatus": "APPROVED",
    "displayStatus": "FLOATING", "content": "synthetic fixture", "emojiId": "party",
    "emotion": "JOY", "animationId": "hearts", "createdAt": now,
    "deadlineAt": "2026-10-29T01:02:00Z", "finalizedAt": now, "message": "approved",
    "expiresAt": "2026-10-29T02:00:00Z",
}
operator_submission = {
    "submission": submission, "participantId": uuid, "reasonCodes": [],
    "modelBundleVersion": "fixture-v1", "inferencePolicyVersion": "fixture-v1",
    "moderationPolicyVersion": "fixture-v1", "lexiconVersion": "fixture-v1",
    "strikeApplied": False, "ruleEvidence": [],
}
dto_valid = [
    ("DisplayBubble", card), ("RuleEvidence", evidence),
    ("DisplayState", display_state),
    ("RestrictionRestoreResult", restore), ("AuditItem", audit),
    ("Submission", submission), ("OperatorSubmission", operator_submission),
]
dto_invalid = [
    ("DisplayBubble", {k: v for k, v in card.items() if k != "acceptedAt"}),
    ("DisplayBubble", {**card, "slot": 1}),
    ("DisplayState", {**display_state, "bubbles": [card] * 101}),
    ("DisplayState", {**display_state, "nextCursor": "x" * 2049}),
    ("DisplayState", {k: v for k, v in display_state.items() if k != "snapshotAt"}),
    ("DisplayBubble", {k: v for k, v in card.items() if k != "expiresAt"}),
    ("DisplayBubble", {k: v for k, v in card.items() if k != "ambientUntil"}),
    ("DisplayState", {k: v for k, v in display_state.items() if k != "removedIds"}),
    ("DisplayState", {**display_state, "removedIds": [uuid, uuid]}),
    ("DisplayState", {**display_state, "removedIds": [f"00000000-0000-4000-8000-{i:012d}" for i in range(1001)]}),
    ("DisplayState", {**display_state, "cacheResetToken": 1}),
    ("DisplayState", {**display_state, "deviceGeneration": "01"}),
    ("Submission", {k: v for k, v in submission.items() if k != "expiresAt"}),
    ("DisplayBubble", {**card, "participantId": uuid}),
    ("RuleEvidence", {**evidence, "matchedText": "private"}),
    ("RuleEvidence", {**evidence, "ruleId": "invalid id"}),
    ("RestrictionRestoreResult", {**restore, "auditId": 9007199254740993}),
    ("RestrictionRestoreResult", {**restore, "replayed": "true"}),
    ("AuditItem", {**audit, "beforeViolationCount": -1}),
    ("Submission", {**submission, "ruleEvidence": [evidence]}),
    ("OperatorSubmission", {**operator_submission, "ruleEvidence": [evidence] * 6}),
]
for name, value in dto_valid:
    Draft202012Validator(expand(spec["components"]["schemas"][name]), format_checker=FormatChecker()).validate(value)
for name, value in dto_invalid:
    validator = Draft202012Validator(expand(spec["components"]["schemas"][name]), format_checker=FormatChecker())
    assert not validator.is_valid(value), (name, "accepted invalid DTO")
print(f"PASS HTTP DTO boundaries: {len(dto_valid)} valid and {len(dto_invalid)} rejected cases")

tree = (ROOT / "docs/backend-completeness-tree.md").read_text(encoding="utf-8-sig")
leaves = re.findall(r"^- \*\*([A-Z][0-9]{2})\*\*", tree, flags=re.M)
assert len(leaves) == len(set(leaves)), "duplicate design leaf ID"
assert leaves, "missing design leaves"
api_mappings = re.findall(r"^\| `([A-Za-z][A-Za-z0-9]+)` \| (.*?) \|$", tree, flags=re.M)
mapped_ids = [name for name, _ in api_mappings if name in operation_ids]
assert len(mapped_ids) == len(set(mapped_ids)), "duplicate API mapping"
assert set(mapped_ids) == set(operation_ids), ("API coverage", set(operation_ids) - set(mapped_ids))
for name, ids in api_mappings:
    if name in operation_ids:
        references = re.findall(r"\b[A-Z][0-9]{2}\b", ids)
        assert references and set(references).issubset(set(leaves)), (name, ids)
print(f"PASS design coverage references: {len(leaves)} leaves, {len(mapped_ids)} HTTP operations (runtime coverage not claimed)")

problem = Draft202012Validator(expand(spec["components"]["schemas"]["Problem"]), format_checker=FormatChecker())
text = (ROOT / "docs/backend-contracts.md").read_text(encoding="utf-8-sig")
for code in re.findall(r"```json\n(.*?)```", text, flags=re.S):
    problem.validate(json.loads(code))
print("PASS documented Problem example")
