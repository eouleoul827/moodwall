"""Check real ASGI responses against the reviewed, packaged OpenAPI schemas."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from app.db import db_time, one
from app.domain import business_day
from app.security import hash_token
from inference.contracts import AnalysisInput, ModelAnalyzed, RuleEvidence, RuleRejected
from inference.fake import FakeAnalyzer

CONTRACT_PATH = Path(__file__).resolve().parents[1] / "app" / "resources" / "openapi.json"
CONTRACT = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
CONTRACT_SHA256 = "1ba86360b0d28a7440237297649198a2a304b76096f11f970acf26475f0eac71"
METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
PRIVATE_FIELDS = {
    "participantId",
    "csrfToken",
    "token",
    "tokenHash",
    "ruleEvidence",
    "reasonCodes",
    "modelBundleVersion",
    "inferencePolicyVersion",
    "moderationPolicyVersion",
    "lexiconVersion",
    "strikeApplied",
    "processingRunToken",
    "score",
}


def _validate(response, name, status=200):
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith(
        "application/problem+json" if name == "Problem" else "application/json"
    )
    UUID(response.headers["x-request-id"])
    validator = Draft202012Validator(
        {"$ref": f"#/components/schemas/{name}", "components": CONTRACT["components"]},
        format_checker=FormatChecker(),
    )
    value = response.json()
    validator.validate(value)
    if name == "Problem":
        assert value["requestId"] == response.headers["x-request-id"]
        assert value["status"] == response.status_code
    return value


def _no_private_fields(value):
    if isinstance(value, dict):
        assert not PRIVATE_FIELDS.intersection(value)
        for child in value.values():
            _no_private_fields(child)
    elif isinstance(value, list):
        for child in value:
            _no_private_fields(child)


async def _submit(client, content="합격 축하해"):
    return await client.post(
        "/api/submissions",
        json={"content": content, "emojiId": "party"},
        headers={"Idempotency-Key": str(uuid4())},
    )


def test_packaged_contract_matches_pinned_and_repository_copy():
    """Containers retain the reviewed bytes even when ../docs is not mounted."""
    assert hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest() == CONTRACT_SHA256
    source = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"
    if source.exists():
        assert source.read_bytes() == CONTRACT_PATH.read_bytes()
    operations = [
        entry["operationId"]
        for path in CONTRACT["paths"].values()
        for method, entry in path.items()
        if method in METHODS
    ]
    assert len(operations) == len(set(operations)) == 44


def test_server_expiry_and_local_bubble_retention_are_separate_contracts():
    assert CONTRACT["info"]["version"] == "0.12.1"
    assert CONTRACT["components"]["schemas"]["DisplayState"]["properties"]["bubbles"]["maxItems"] == 100
    assert "slot" not in CONTRACT["components"]["schemas"]["DisplayBubble"]["properties"]
    assert "remainingMs" not in CONTRACT["components"]["schemas"]["DisplayBubble"]["properties"]
    assert "acceptedAt" in CONTRACT["components"]["schemas"]["DisplayBubble"]["required"]
    for name in ("Submission", "DisplayBubble"):
        schema = CONTRACT["components"]["schemas"][name]
        assert "expiresAt" in schema["required"]
        assert schema["properties"]["expiresAt"]["format"] == "date-time"
    for name in ("DisplayBubble", "DisplayState"):
        schema = CONTRACT["components"]["schemas"][name]
        assert "ambientUntil" in schema["required"]
        assert schema["properties"]["ambientUntil"]["format"] == "date-time"
    state = CONTRACT["components"]["schemas"]["DisplayState"]
    assert {"removedIds", "cacheResetToken", "deviceGeneration"} <= set(state["required"])
    assert {"snapshotAt", "bubbles", "bubbleTotalCount", "nextCursor"} <= set(state["required"])
    assert not {"spotlights", "ambient", "queuedCount", "ambientTotalCount"}.intersection(state["properties"])
    assert CONTRACT["components"]["schemas"]["DisplayStatus"]["enum"] == [
        "NONE",
        "QUEUED",
        "FLOATING",
        "EXPIRED",
        "HIDDEN",
    ]
    operation = CONTRACT["paths"]["/api/display/state"]["get"]
    cursor = next(parameter for parameter in operation["parameters"] if parameter["name"] == "cursor")
    assert cursor["in"] == "query" and cursor["schema"]["maxLength"] == 2048
    assert "422" in operation["responses"]
    assert state["properties"]["removedIds"]["maxItems"] == 1000
    assert state["properties"]["removedIds"]["uniqueItems"] is True
    for field in ("cacheResetToken", "deviceGeneration"):
        assert state["properties"][field]["type"] == "string"
        assert state["properties"][field]["pattern"] == "^(0|[1-9][0-9]*)$"


def _parsed_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _schema_validator(name):
    return Draft202012Validator(
        {"$ref": f"#/components/schemas/{name}", "components": CONTRACT["components"]},
        format_checker=FormatChecker(),
    )


def test_v2_receipt_survives_purge_without_source_or_model_private_fields():
    receipt = dict(
        CONTRACT["paths"]["/api/v2/submissions"]["post"]["responses"]["202"]["content"]["application/json"]["example"]
    )
    receipt.update(
        processingStatus="APPROVED",
        presentationStatus="DONE",
        lastDisplayStatus="DONE",
        userStatus="SHOWN",
        contentStatus="PURGED",
        firstStartAuthorizedAt="2026-11-04T03:00:02Z",
        contentDeleteAt="2026-11-04T04:00:02Z",
        completedAt="2026-11-04T03:00:18Z",
    )
    validator = _schema_validator("SubmissionReceipt")
    validator.validate(receipt)
    assert not {"content", "emojiId", "emotion", "participantId", "score"}.intersection(receipt)
    assert list(validator.iter_errors({**receipt, "content": "must not leak"}))


def test_v2_playback_identity_and_scene_contracts_reject_stale_shape_errors():
    claim = _schema_validator("PlaybackClaimRequest")
    command = _schema_validator("PlaybackCommandRequest")
    identity = {"clientInstanceId": str(uuid4()), "deviceGeneration": "1", "playbackEpoch": "1"}
    claim.validate(identity)
    claim.validate({"clientInstanceId": identity["clientInstanceId"], "deviceGeneration": "1"})
    command.validate(identity)
    assert list(command.iter_errors({"clientInstanceId": identity["clientInstanceId"], "deviceGeneration": "1"}))
    for invalid in (1, "-1", "01", "1.0", "1" * 20):
        assert list(command.iter_errors({**identity, "playbackEpoch": invalid}))
    assert list(command.iter_errors({**identity, "unexpected": True}))
    interrupt = _schema_validator("PlaybackInterruptRequest")
    interrupt.validate({**identity, "reason": "PAGE_HIDDEN"})
    assert list(interrupt.iter_errors({**identity, "reason": "pretend-completed"}))


def test_heartbeat_version_is_optional_and_both_response_versions_are_documented():
    request = _schema_validator("HeartbeatRequest")
    base = {"clientInstanceId": str(uuid4())}
    request.validate(base)
    request.validate({**base, "displayProtocolVersion": "2"})
    assert list(request.iter_errors({**base, "displayProtocolVersion": "3"}))
    media = CONTRACT["paths"]["/api/display/heartbeat"]["post"]["responses"]["200"]["content"]["application/json"]
    assert {entry["$ref"] for entry in media["schema"]["oneOf"]} == {
        "#/components/schemas/HeartbeatResponse",
        "#/components/schemas/PlaybackState",
    }
    validator = Draft202012Validator(
        {**media["schema"], "components": CONTRACT["components"]}, format_checker=FormatChecker()
    )
    for example in media["examples"].values():
        validator.validate(example["value"])
    attempt = media["examples"]["v2"]["value"]["attempt"]
    attempt_validator = _schema_validator("PlaybackAttempt")
    attempt_validator.validate(attempt)
    for field in ("presentationStatus", "reasonCode"):
        assert list(attempt_validator.iter_errors({key: value for key, value in attempt.items() if key != field}))
    assert list(validator.iter_errors({"displayProtocolVersion": "2", "serverNow": "2026-11-04T03:00:00Z"}))


@pytest.mark.integration
async def test_runtime_operation_methods_and_normalized_paths_match_contract(system):
    def normalize(path):
        return re.sub(r"\{[^{}]+\}", "{}", path)

    expected = {
        (entry["operationId"], method, normalize(path))
        for path, path_item in CONTRACT["paths"].items()
        for method, entry in path_item.items()
        if method in METHODS
    }
    actual = []
    for route in system.routes:
        operation_id = getattr(route, "operation_id", None)
        if not operation_id:
            continue
        actual.extend((operation_id, method.lower(), normalize(route.path)) for method in route.methods)
    assert len(actual) == len(set(actual)) == 44
    assert set(actual) == expected


@pytest.mark.integration
async def test_role_sessions_usage_and_emoji_shapes(clients):
    for role, path, schema in (
        ("admin", "/api/operator/session", "OperatorSession"),
        ("display", "/api/display/session", "DisplaySession"),
        ("student", "/api/session", "ParticipantSession"),
    ):
        value = _validate(await clients[role].get(path), schema)
        assert value["csrfToken"] == clients[role].headers["X-CSRF-Token"]
    _validate(await clients["student"].get("/api/me/usage"), "Usage")
    options = _validate(await clients["student"].get("/api/emoji-options"), "EmojiOptions")
    assert len({item["id"] for item in options["items"]}) == len(options["items"])


@pytest.mark.integration
async def test_display_qr_heartbeat_and_snapshot_shapes(clients):
    qr = _validate(await clients["display"].get("/api/display/qr"), "QrTicket")
    assert "#ticket=" in qr["joinUrl"]
    beat = _validate(
        await clients["display"].post("/api/display/heartbeat", json={"clientInstanceId": str(uuid4())}),
        "HeartbeatResponse",
    )
    snapshot = _validate(await clients["display"].get("/api/display/state"), "DisplayState")
    assert snapshot["revision"] >= beat["revision"]
    assert snapshot["onlineLeaseUntil"] == beat["onlineLeaseUntil"]
    assert snapshot["removedIds"] == []
    assert snapshot["cacheResetToken"] == "0"
    assert int(snapshot["deviceGeneration"]) >= 1
    assert _parsed_time(snapshot["ambientUntil"]) > _parsed_time(snapshot["serverNow"])
    _no_private_fields(snapshot)


@pytest.mark.integration
async def test_pending_approved_and_floating_responses_keep_private_fields_out(clients, system):
    response = await _submit(clients["student"])
    pending = _validate(response, "Submission", 202)
    assert pending["moderationStatus"] == "PENDING"
    assert _parsed_time(pending["expiresAt"]) - _parsed_time(pending["createdAt"]) == timedelta(hours=1)
    assert _parsed_time(pending["deadlineAt"]) <= _parsed_time(pending["createdAt"]) + timedelta(seconds=120)
    _no_private_fields(pending)
    claim = await system.state.moderation.claim(UUID(pending["id"]))
    assert claim is not None
    await system.state.moderation.finalize(
        claim, ModelAnalyzed(analysis=FakeAnalyzer().analyze(AnalysisInput(text=claim["content"])))
    )
    approved = _validate(await clients["student"].get(response.headers["location"]), "Submission")
    assert approved["moderationStatus"] == "APPROVED"
    _no_private_fields(approved)
    _validate(
        await clients["display"].post("/api/display/heartbeat", json={"clientInstanceId": str(uuid4())}),
        "HeartbeatResponse",
    )
    await system.state.display.reconcile()
    floating = _validate(await clients["display"].get("/api/display/state"), "DisplayState")
    assert len(floating["bubbles"]) == 1
    assert floating["bubbles"][0]["id"] == pending["id"]
    assert _parsed_time(floating["bubbles"][0]["expiresAt"]) == _parsed_time(pending["expiresAt"])
    assert _parsed_time(floating["bubbles"][0]["acceptedAt"]) == _parsed_time(pending["createdAt"])
    assert floating["bubbles"][0]["ambientUntil"] == floating["ambientUntil"]
    assert floating["bubbleTotalCount"] == 1 and floating["nextCursor"] is None
    assert approved["displayStatus"] == "FLOATING"
    _no_private_fields(floating)


@pytest.mark.integration
async def test_rule_rejection_is_owner_private_and_operator_evidence_is_typed(clients, system):
    response = await _submit(clients["student"], "씨%발")
    pending = _validate(response, "Submission", 202)
    claim = await system.state.moderation.claim(UUID(pending["id"]))
    assert claim is not None
    await system.state.moderation.finalize(
        claim,
        RuleRejected(
            lexicon_version="lexicon-v1",
            matches=[RuleEvidence(rule_id="ko-explicit-001", variant_id="gap")],
        ),
    )
    rejected = _validate(await clients["student"].get(response.headers["location"]), "Submission")
    assert rejected["moderationStatus"] == "REJECTED"
    _no_private_fields(rejected)
    denied = _validate(await clients["stranger"].get(response.headers["location"]), "Problem", 404)
    assert "씨%발" not in json.dumps(denied, ensure_ascii=False)
    public = _validate(await clients["display"].get("/api/display/state"), "DisplayState")
    assert not public["bubbles"] and public["bubbleTotalCount"] == 0 and public["nextCursor"] is None
    _no_private_fields(public)
    operators = _validate(
        await clients["admin"].get("/api/operator/submissions", params={"status": "REJECTED"}), "OperatorSubmissionList"
    )
    assert len(operators["items"]) == 1
    item = operators["items"][0]
    assert item["ruleEvidence"] == [{"ruleId": "ko-explicit-001", "variantId": "gap"}]
    assert item["strikeApplied"] is True and item["modelBundleVersion"] is None


@pytest.mark.integration
async def test_operator_usage_restore_replay_and_audit_response_shapes(clients, database, settings):
    token = clients["student"].cookies.get(settings.cookie_name("student"))
    async with database.connection() as conn:
        participant = await one(
            conn, "SELECT participant_id FROM browser_sessions WHERE token_hash=%s", (hash_token(token),)
        )
        now = await db_time(conn)
        day = business_day(now)
        await conn.execute(
            """INSERT INTO daily_usage(participant_id,business_date,violation_count,banned_until)
               VALUES(%s,%s,5,%s)""",
            (participant["participant_id"], day, now + timedelta(minutes=10)),
        )
    participant_id = participant["participant_id"]
    usage = _validate(await clients["admin"].get(f"/api/operator/participants/{participant_id}/usage"), "Usage")
    assert usage["violationCount"] == 5
    path = f"/api/operator/daily-usage/{participant_id}/{day}/restore"
    headers = {"Idempotency-Key": str(uuid4())}
    first = _validate(
        await clients["admin"].post(path, json={"reasonCode": "FALSE_POSITIVE"}, headers=headers),
        "RestrictionRestoreResult",
    )
    replay = _validate(
        await clients["admin"].post(path, json={"reasonCode": "FALSE_POSITIVE"}, headers=headers),
        "RestrictionRestoreResult",
    )
    assert first["replayed"] is False and replay["replayed"] is True
    assert first["auditId"] == replay["auditId"]
    audits = _validate(await clients["admin"].get("/api/operator/audit"), "AuditList")
    matching = [item for item in audits["items"] if item["id"] == first["auditId"]]
    assert len(matching) == 1
    assert matching[0]["beforeViolationCount"] == 5 and matching[0]["afterViolationCount"] == 0


@pytest.mark.integration
async def test_role_denials_have_safe_problem_contract_and_matching_request_ids(clients):
    for path in ("/api/display/state", "/api/operator/audit"):
        problem = _validate(await clients["student"].get(path), "Problem", 401)
        assert problem["code"] == "SESSION_REQUIRED"
        _no_private_fields(problem)
    problem = _validate(await clients["student"].get(f"/api/submissions/{uuid4()}"), "Problem", 404)
    assert problem["code"] == "NOT_FOUND"
