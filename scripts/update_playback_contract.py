"""Rebuild the additive v0.8 playback contract and its packaged byte copy.

The reviewed legacy contract is the input; rerunning is idempotent. Runtime
OpenAPI generation is deliberately not used to erase legacy response contracts.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def ref(name):
    return {"$ref": "#/components/schemas/" + name}


def nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}


def obj(properties, required=None, **extra):
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties) if required is None else required,
        **extra,
    }


def text(maximum=2048, **extra):
    return {"type": "string", "minLength": 1, "maxLength": maximum, **extra}


UUID = text(36, format="uuid")
TIME = text(40, format="date-time")
DECIMAL = text(19, pattern=r"^(0|[1-9][0-9]{0,18})$")
PRESENTATION = {
    "type": "string",
    "enum": [
        "READY",
        "CLAIMED",
        "START_AUTHORIZED",
        "PLAYING",
        "INTERRUPTED",
        "DONE",
        "FAILED",
        "EXPIRED",
        "CANCELLED",
        "HIDDEN",
    ],
}
INTERRUPT_REASONS = ["HIDDEN", "PAGE_HIDDEN", "RELOAD", "FRAME_GAP", "ASSET_FAILURE", "NETWORK", "CLIENT_ERROR"]


def build(contract):
    schemas, paths = contract["components"]["schemas"], contract["paths"]
    contract["info"]["version"] = "0.8.0"
    contract["info"]["description"] = (
        "Moodwall API: legacy v1 floating bubbles remain compatible. V2 accepts content only, "
        "stores a text-free owner receipt for 24 hours, and orders completed preparation by "
        "committed READY sequence. A fenced display instance claims one message, obtains a "
        "five-second start authorization, reports the first frame, then reports completion "
        "after at least 15 seconds. New arrivals wait. Source text expires one hour after "
        "the first start authorization; local transparent bubbles remain until KST midnight. "
        "Predisplay waiting is separately bounded at ten minutes. Admission reservations "
        "are atomic and rolling-hour post quotas remain distinct from endpoint request limits. "
        "All POST requests require the exact Origin and JSON object body within 4096 bytes. "
        "Authenticated writes require CSRF; command and submission keys use raw UUID headers. "
        "Authentication, owner scopes, request limits and RFC 9457 errors apply to both versions. "
        "Model safety policy remains unchanged; this contract does not authorize model deployment."
    )
    schemas["DisplayStatus"]["enum"] = ["NONE", "QUEUED", "FLOATING", "EXPIRED", "HIDDEN"]
    schemas["SubmissionRequestV2"] = obj(
        {"content": text(2048, description="NFC/trim normalized; server validates 1–30 grapheme clusters.")}
    )
    schemas["ReceiptLookupRequest"] = obj({"idempotencyKey": UUID})
    schemas["PresentationStatus"] = PRESENTATION
    schemas["SubmissionReceipt"] = obj(
        {
            "receiptVersion": {"type": "string", "const": "2"},
            "submissionId": UUID,
            "acceptedAt": TIME,
            "receiptExpiresAt": TIME,
            "processingStatus": ref("ModerationStatus"),
            "presentationStatus": nullable(ref("PresentationStatus")),
            "userStatus": {
                "type": "string",
                "enum": [
                    "PREPARING",
                    "WAITING",
                    "STARTING",
                    "SHOWING",
                    "SHOWN",
                    "NEEDS_REWRITE",
                    "FAILED",
                    "EXPIRED",
                    "CANCELLED",
                    "HIDDEN",
                    "PLAYBACK_UNCONFIRMED",
                ],
            },
            "contentStatus": {"type": "string", "enum": ["AVAILABLE", "PURGED"]},
            "lastDisplayStatus": nullable(ref("PresentationStatus")),
            "reasonCode": nullable(text(100, pattern=r"^[A-Z][A-Z0-9_]{1,99}$")),
            "firstStartAuthorizedAt": nullable(TIME),
            "contentDeleteAt": nullable(TIME),
            "completedAt": nullable(TIME),
            "revision": DECIMAL,
            "statusUrl": text(100, pattern=r"^/api/v2/submissions/[0-9a-f-]{36}$", format="uri-reference"),
            "message": text(500),
        },
        description=(
            "Owner-scoped text-free receipt. GET stays 200 for known terminal outcomes until receiptExpiresAt. "
            "contentStatus is independent of the publication outcome. UI may show one completion card "
            "for PREPARING through SHOWN; analysis stages and scores need not be exposed."
        ),
    )
    schemas["PlaybackItem"] = obj(
        {
            "displayProtocolVersion": {"type": "string", "const": "2"},
            "id": UUID,
            "content": text(2048),
            "emotion": ref("Emotion"),
            "animationId": {
                "type": "string",
                "enum": [
                    "fireworks",
                    "hearts",
                    "sparkles",
                    "petals",
                    "clouds",
                    "rain",
                    "embers",
                    "float",
                ],
            },
            "sceneId": {
                "type": "string",
                "enum": [
                    "celebration",
                    "joy",
                    "support",
                    "excitement",
                    "tired",
                    "sadness",
                    "anger",
                    "neutral",
                ],
            },
            "sceneVersion": {"type": "string", "const": "1"},
            "sceneSeed": text(16, minLength=16, pattern=r"^[0-9a-f]{16}$"),
            "readySequence": text(19, pattern=r"^[1-9][0-9]{0,18}$"),
            "acceptedAt": TIME,
            "firstStartAuthorizedAt": nullable(TIME),
            "contentDeleteAt": nullable(TIME),
            "ambientUntil": TIME,
        },
        description=(
            "Approved scene with no student-selected emoji. The server withholds text at its logical "
            "expiry even if a physical cleanup batch is late. ambientUntil governs local-only retention."
        ),
    )
    schemas["PlaybackAttempt"] = obj(
        {
            "attemptId": UUID,
            "submissionId": UUID,
            "status": {"type": "string", "enum": ["CLAIMED", "START_AUTHORIZED", "PLAYING", "DONE", "INTERRUPTED"]},
            "presentationStatus": ref("PresentationStatus"),
            "reasonCode": nullable(text(100, pattern=r"^[A-Z][A-Z0-9_]{1,99}$")),
            "attemptNumber": {"type": "integer", "minimum": 1, "maximum": 2},
            "clientInstanceId": UUID,
            "deviceGeneration": DECIMAL,
            "playbackEpoch": DECIMAL,
            "claimedAt": TIME,
            "startAuthorizedAt": nullable(TIME),
            "startNotAfter": nullable(TIME),
            "firstFrameAt": nullable(TIME),
            "completedAt": nullable(TIME),
            "leaseUntil": TIME,
            "item": nullable(ref("PlaybackItem")),
        }
    )
    schemas["PlaybackState"] = obj(
        {
            "displayProtocolVersion": {"type": "string", "const": "2"},
            "boardId": UUID,
            "deviceGeneration": DECIMAL,
            "playbackEpoch": DECIMAL,
            "clientInstanceId": nullable(UUID),
            "leaseUntil": nullable(TIME),
            "serverNow": TIME,
            "revision": DECIMAL,
            "mode": ref("BoardMode"),
            "waitingCount": {"type": "integer", "minimum": 0, "maximum": 32},
            "attempt": nullable(ref("PlaybackAttempt")),
        },
        description=(
            "Current server-owned playback state. Time and decimal-string revisions are fresh on "
            "idempotent replay; startNotAfter is immutable for each attempt. Heartbeat alone does "
            "not confirm a frame or complete a message."
        ),
    )
    schemas["PlaybackDisplayState"] = obj(
        {
            "displayProtocolVersion": {"type": "string", "const": "2"},
            "boardId": UUID,
            "businessDate": text(10, format="date"),
            "deviceGeneration": DECIMAL,
            "revision": DECIMAL,
            "serverNow": TIME,
            "snapshotAt": TIME,
            "ambientUntil": TIME,
            "mode": ref("BoardMode"),
            "onlineLeaseUntil": nullable(TIME),
            "removedIds": {"type": "array", "maxItems": 1000, "uniqueItems": True, "items": UUID},
            "cacheResetToken": DECIMAL,
            "bubbles": {"type": "array", "maxItems": 100, "items": ref("PlaybackItem")},
            "bubbleTotalCount": {"type": "integer", "minimum": 0},
            "nextCursor": nullable(text(2048)),
        },
        description=(
            "Paged v2 completed bubbles only. Read every nextCursor page to synchronize; page size "
            "is not a display-count cap. Absence after source expiry must not erase an already "
            "cached transparent bubble. removedIds, cacheResetToken, generation and midnight do erase it."
        ),
    )
    schemas["PlaybackClaimRequest"] = obj(
        {
            "clientInstanceId": UUID,
            "deviceGeneration": DECIMAL,
            "playbackEpoch": nullable(DECIMAL),
        },
        required=["clientInstanceId", "deviceGeneration"],
    )
    command_properties = {"clientInstanceId": UUID, "deviceGeneration": DECIMAL, "playbackEpoch": DECIMAL}
    schemas["PlaybackCommandRequest"] = obj(command_properties)
    schemas["PlaybackInterruptRequest"] = obj(
        {
            **command_properties,
            "reason": {"type": "string", "enum": INTERRUPT_REASONS},
        }
    )
    schemas["HeartbeatRequest"]["properties"]["displayProtocolVersion"] = nullable({"type": "string", "const": "2"})

    common_headers = copy.deepcopy(paths["/api/submissions"]["post"]["responses"]["200"]["headers"])
    write_headers = copy.deepcopy(paths["/api/submissions"]["post"]["parameters"])

    def success(name, example, description="Success"):
        return {
            "description": description,
            "headers": copy.deepcopy(common_headers),
            "content": {"application/json": {"schema": ref(name), "example": example}},
        }

    def errors(*statuses):
        return {str(status): {"$ref": f"#/components/responses/Error{status}"} for status in statuses}

    def operation(operation_id, role, *, request=None, parameters=None, description=""):
        params = [] if parameters is None else copy.deepcopy(parameters)
        if request:
            params.extend(copy.deepcopy(write_headers))
        result = {
            "operationId": operation_id,
            "summary": operation_id,
            "tags": [role],
            "description": description,
            "security": [{"participantSession" if role == "student" else "displaySession": []}],
            "parameters": params,
            "responses": errors(400, 401, 403, 404, 405, 409, 410, 422, 429, 500, 503),
        }
        if request:
            result["requestBody"] = {"required": True, "content": {"application/json": {"schema": ref(request)}}}
            result["responses"].update(errors(413, 415))
        return result

    timestamp = "2026-11-04T03:00:00Z"
    sid = "20000000-0000-4000-8000-000000000001"
    iid = "30000000-0000-4000-8000-000000000001"
    aid = "40000000-0000-4000-8000-000000000001"
    receipt = {
        "receiptVersion": "2",
        "submissionId": sid,
        "acceptedAt": timestamp,
        "receiptExpiresAt": "2026-11-05T03:00:00Z",
        "processingStatus": "PENDING",
        "presentationStatus": None,
        "userStatus": "PREPARING",
        "contentStatus": "AVAILABLE",
        "lastDisplayStatus": None,
        "reasonCode": None,
        "firstStartAuthorizedAt": None,
        "contentDeleteAt": None,
        "completedAt": None,
        "revision": "1",
        "statusUrl": "/api/v2/submissions/" + sid,
        "message": "메시지를 보냈어요!",
    }
    item = {
        "displayProtocolVersion": "2",
        "id": sid,
        "content": "오늘도 힘내!",
        "emotion": "SUPPORT",
        "animationId": "sparkles",
        "sceneId": "support",
        "sceneVersion": "1",
        "sceneSeed": "0123456789abcdef",
        "readySequence": "1",
        "acceptedAt": timestamp,
        "firstStartAuthorizedAt": "2026-11-04T03:00:02Z",
        "contentDeleteAt": "2026-11-04T04:00:02Z",
        "ambientUntil": "2026-11-04T15:00:00Z",
    }
    attempt = {
        "attemptId": aid,
        "submissionId": sid,
        "status": "PLAYING",
        "presentationStatus": "PLAYING",
        "reasonCode": None,
        "attemptNumber": 1,
        "clientInstanceId": iid,
        "deviceGeneration": "1",
        "playbackEpoch": "1",
        "claimedAt": timestamp,
        "startAuthorizedAt": "2026-11-04T03:00:02Z",
        "startNotAfter": "2026-11-04T03:00:07Z",
        "firstFrameAt": "2026-11-04T03:00:03Z",
        "completedAt": None,
        "leaseUntil": "2026-11-04T03:00:32Z",
        "item": item,
    }
    state = {
        "displayProtocolVersion": "2",
        "boardId": "10000000-0000-4000-8000-000000000001",
        "deviceGeneration": "1",
        "playbackEpoch": "1",
        "clientInstanceId": iid,
        "leaseUntil": "2026-11-04T03:00:32Z",
        "serverNow": "2026-11-04T03:00:03Z",
        "revision": "4",
        "mode": "MOODWALL",
        "waitingCount": 0,
        "attempt": attempt,
    }
    snapshot = {
        "displayProtocolVersion": "2",
        "boardId": state["boardId"],
        "businessDate": "2026-11-04",
        "deviceGeneration": "1",
        "revision": "5",
        "serverNow": "2026-11-04T03:00:20Z",
        "snapshotAt": "2026-11-04T03:00:20Z",
        "ambientUntil": item["ambientUntil"],
        "mode": "MOODWALL",
        "onlineLeaseUntil": "2026-11-04T03:00:32Z",
        "removedIds": [],
        "cacheResetToken": "0",
        "bubbles": [item],
        "bubbleTotalCount": 1,
        "nextCursor": None,
    }

    def path_id(name):
        return {"name": name, "in": "path", "required": True, "schema": UUID}

    create = operation(
        "submitMentionV2",
        "student",
        request="SubmissionRequestV2",
        description=(
            "202 means durable acceptance, not publication. A same-key/same-normalized-input retry returns "
            "the owner receipt with 200 even after source deletion; conflicting input returns 409. "
            "Receipt retention is 24h, preparation 120s, predisplay at most ten minutes, and active "
            "reservations at most 32. Actual configured budgets may be lower."
        ),
    )
    create["responses"]["200"] = success("SubmissionReceipt", receipt, "Existing durable receipt")
    create["responses"]["202"] = success("SubmissionReceipt", receipt, "New work durably accepted")
    create["responses"]["202"]["headers"].update(
        {
            "Location": {"schema": {"type": "string", "format": "uri-reference"}},
            "Retry-After": {"schema": {"type": "integer", "const": 2}},
        }
    )
    paths["/api/v2/submissions"] = {"post": create}
    own = operation(
        "getOwnSubmissionV2",
        "student",
        parameters=[path_id("submissionId")],
        description=(
            "Only the submitting participant can read the text-free receipt. Known terminal outcomes are "
            "200, not an HTTP failure. Missing, other-owner and expired receipts are indistinguishable 404."
        ),
    )
    own["responses"]["200"] = success("SubmissionReceipt", receipt)
    paths["/api/v2/submissions/{submissionId}"] = {"get": own}
    lookup = operation("lookupSubmissionReceipt", "student", request="ReceiptLookupRequest")
    lookup["parameters"] = [p for p in lookup["parameters"] if p["name"] != "Idempotency-Key"]
    lookup["responses"]["200"] = success("SubmissionReceipt", receipt)
    paths["/api/v2/submission-receipts/lookup"] = {"post": lookup}
    current = operation(
        "getDisplayPlayback",
        "display",
        parameters=[
            {
                "name": "clientInstanceId",
                "in": "query",
                "required": True,
                "schema": UUID,
            }
        ],
    )
    current["responses"]["200"] = success("PlaybackState", state)
    paths["/api/display/playback"] = {"get": current}
    bubbles = operation(
        "getDisplayStateV2",
        "display",
        parameters=[
            {
                "name": "cursor",
                "in": "query",
                "required": False,
                "schema": text(2048),
            }
        ],
    )
    bubbles["responses"]["200"] = success("PlaybackDisplayState", snapshot)
    paths["/api/v2/display/state"] = {"get": bubbles}
    claim = operation(
        "claimDisplayPlayback",
        "display",
        request="PlaybackClaimRequest",
        description=(
            "Claims the earliest committed READY sequence. An interrupted current item has priority "
            "for one replay. Empty queue returns 204 with no body. Only one client instance owns the "
            "30-second renewable board lease; generation and epoch fence stale commands."
        ),
    )
    claim_state = copy.deepcopy(state)
    claim_state["attempt"].update(
        status="CLAIMED",
        presentationStatus="CLAIMED",
        startAuthorizedAt=None,
        startNotAfter=None,
        firstFrameAt=None,
        leaseUntil="2026-11-04T03:00:10Z",
    )
    claim_state["attempt"]["item"].update(firstStartAuthorizedAt=None, contentDeleteAt=None)
    claim["responses"]["200"] = success("PlaybackState", claim_state)
    claim["responses"]["204"] = {"description": "No playable item", "headers": copy.deepcopy(common_headers)}
    paths["/api/display/playback/claim"] = {"post": claim}
    descriptions = {
        "start": "Authorize a first frame before immutable startNotAfter=start+5s. First authorization anchors source expiry at +1h.",
        "frame": "Report the first actual visible frame before startNotAfter. A late report cannot begin playback.",
        "complete": "Confirm PLAYING only after server firstFrameAt+15s. Too-early 409 is transient and does not consume the command key.",
        "interrupt": "Interrupt on page hidden, asset failure, reload, frame gap or network loss. At most one replay; expiry is never renewed.",
    }
    for action in ("start", "frame", "complete", "interrupt"):
        endpoint = operation(
            action + "DisplayPlayback",
            "display",
            request="PlaybackInterruptRequest" if action == "interrupt" else "PlaybackCommandRequest",
            parameters=[path_id("attemptId")],
            description=descriptions[action],
        )
        action_state = copy.deepcopy(state)
        if action == "start":
            action_state["attempt"].update(
                status="START_AUTHORIZED", presentationStatus="START_AUTHORIZED", firstFrameAt=None
            )
        elif action == "complete":
            action_state["attempt"].update(status="DONE", presentationStatus="DONE", completedAt="2026-11-04T03:00:18Z")
            action_state["serverNow"] = "2026-11-04T03:00:18Z"
        elif action == "interrupt":
            action_state["attempt"].update(status="INTERRUPTED", presentationStatus="INTERRUPTED")
            action_state["waitingCount"] = 1
        endpoint["responses"]["200"] = success("PlaybackState", action_state)
        paths[f"/api/display/playback/{{attemptId}}/{action}"] = {"post": endpoint}

    gone = copy.deepcopy(contract["components"]["responses"]["Error409"])
    gone["description"] = "Source or presentation expired; this command cannot publish it."
    gone["content"]["application/problem+json"]["example"].update(
        type="/problems/presentation-expired", title="Gone", status=410, code="PRESENTATION_EXPIRED"
    )
    contract["components"]["responses"]["Error410"] = gone
    heartbeat = paths["/api/display/heartbeat"]["post"]
    heartbeat["description"] += (
        (
            " With displayProtocolVersion='2', returns PlaybackState and renews the fenced instance lease. "
            "Without it, preserves the legacy HeartbeatResponse. Neither form proves rendering."
        )
        if "With displayProtocolVersion='2'" not in heartbeat["description"]
        else ""
    )
    media = heartbeat["responses"]["200"]["content"]["application/json"]
    media["schema"] = {"oneOf": [ref("HeartbeatResponse"), ref("PlaybackState")]}
    media["examples"] = {
        "legacy": {"value": {"serverNow": timestamp, "onlineLeaseUntil": "2026-11-04T03:00:30Z", "revision": 1}},
        "v2": {"value": state},
    }
    for path in paths.values():
        for method, endpoint in path.items():
            if method not in METHODS or not endpoint.get("security"):
                continue
            endpoint["responses"].setdefault("429", {"$ref": "#/components/responses/Error429"})
            if method == "get":
                endpoint["responses"].setdefault("403", {"$ref": "#/components/responses/Error403"})
    return contract


def main():
    source = ROOT / "docs" / "openapi.json"
    contract = build(json.loads(source.read_text(encoding="utf-8")))
    output = (json.dumps(contract, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    source.write_bytes(output)
    (ROOT / "backend" / "app" / "resources" / "openapi.json").write_bytes(output)
    print("Contract v0.8.0 SHA256:", hashlib.sha256(output).hexdigest())


if __name__ == "__main__":
    main()
