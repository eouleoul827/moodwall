"""Add support-aware receipts, text-free reporting and provider controls."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

from update_playback_contract import TIME, UUID, nullable, obj, ref, text

ROOT = Path(__file__).resolve().parents[1]


def build(contract):
    schemas, paths = contract["components"]["schemas"], contract["paths"]
    contract["info"]["version"] = max(
        "0.10.0", contract["info"]["version"], key=lambda version: tuple(map(int, version.split(".")))
    )
    old = " Offline provider moderation adds support-aware receipts and private reports. Real Gemini transport remains unconfigured and unavailable."
    contract["info"]["description"] = contract["info"]["description"].replace(old, "")
    description = " Provider moderation adds support-aware receipts and private reports. Gemini transport is server-only, with durable accounting, readiness probes and circuit controls; the mock profile performs no external calls."
    if description not in contract["info"]["description"]:
        contract["info"]["description"] += description
    category = {"type": "string", "enum": ["HARASSMENT", "PRIVACY", "THREAT", "OTHER"]}
    resolution = {"type": "string", "enum": ["HIDDEN", "BOARD_PAUSED", "NO_ACTION", "INSUFFICIENT_EVIDENCE"]}
    schemas["ContentReportRequest"] = obj({"category": category, "submissionId": nullable(UUID)}, required=["category"])
    schemas["ReportResolutionRequest"] = obj({"resolution": resolution})
    schemas["ContentReportReceipt"] = obj({"reportId": UUID, "status": {"const": "RECEIVED", "type": "string"}})
    schemas["ContentReportRecord"] = obj(
        {
            "reportId": UUID,
            "boardId": UUID,
            "submissionId": nullable(UUID),
            "category": category,
            "status": {"type": "string", "enum": ["RECEIVED", "RESOLVED"]},
            "receivedAt": TIME,
            "resolvedAt": nullable(TIME),
            "resolution": nullable(resolution),
            "targetAvailable": {"type": "boolean"},
        }
    )
    schemas["ContentReportPage"] = obj(
        {
            "items": {"type": "array", "items": ref("ContentReportRecord"), "maxItems": 50},
            "nextCursor": nullable(text(2048)),
        }
    )
    schemas["ProviderPauseRequest"] = obj({"paused": {"type": "boolean"}})
    number = {"type": "integer", "minimum": 0}
    schemas["ModerationProviderStatus"] = {
        "oneOf": [
            obj({"enabled": {"type": "boolean", "const": False}, "mode": {"type": "string", "const": "LEGACY"}}),
            obj(
                {
                    "enabled": {"type": "boolean", "const": True},
                    "profile": {"type": "string", "enum": ["gemini_mock", "gemini"]},
                    "transportConfigured": {"type": "boolean"},
                    "syntheticAccounting": {"type": "boolean"},
                    "workerReady": {"type": "boolean"},
                    "lastFailureCode": nullable(text(100)),
                    "mode": {"type": "string", "enum": ["OPEN", "PAUSED", "OPEN_CIRCUIT", "HALF_OPEN"]},
                    "releaseId": text(100),
                    "consecutiveFailures": number,
                    "budgetDay": {"type": "string", "format": "date"},
                    "chargedMicroUsd": number,
                    "reservedMicroUsd": number,
                    "dailyBudgetMicroUsd": number,
                    "unknownAttempts": number,
                    "probeSuccesses": number,
                }
            ),
        ]
    }
    for schema in schemas.values():
        if isinstance(schema, dict) and isinstance(schema.get("enum"), list) and "PAUSE_DISPLAY" in schema["enum"]:
            schema["enum"] = list(dict.fromkeys([*schema["enum"], "PAUSE_PROVIDER", "RESUME_PROVIDER"]))
    common = copy.deepcopy(paths["/api/v2/submissions"]["post"]["responses"]["202"]["headers"])

    def success(name, example):
        return {
            "description": "Success",
            "headers": copy.deepcopy(common),
            "content": {"application/json": {"schema": ref(name), "example": example}},
        }

    def operation(identifier, role, *, request=None, keyed=False, parameters=(), description=""):
        template = (
            paths["/api/operator/board/intake"]["post"] if role == "operator" else paths["/api/v2/submissions"]["post"]
        )
        headers = [
            copy.deepcopy(p)
            for p in template["parameters"]
            if p["in"] == "header" and p["name"] in {"Origin", "X-CSRF-Token"}
        ]
        if keyed:
            headers.append({"name": "Idempotency-Key", "in": "header", "required": True, "schema": UUID})
        result = {
            "operationId": identifier,
            "summary": identifier,
            "tags": [role],
            "description": description,
            "security": [{"operatorSession" if role == "operator" else "participantSession": []}],
            "parameters": [*parameters, *(headers if request else [])],
            "responses": {
                str(code): {"$ref": f"#/components/responses/Error{code}"}
                for code in (400, 401, 403, 404, 405, 409, 422, 429, 500, 503)
            },
        }
        if role == "operator":
            result["x-roles"] = ["ADMIN", "MODERATOR"]
        if request:
            result["requestBody"] = {"required": True, "content": {"application/json": {"schema": ref(request)}}}
            result["responses"].update(
                {str(code): {"$ref": f"#/components/responses/Error{code}"} for code in (413, 415)}
            )
        return result

    report_id = "70000000-0000-4000-8000-000000000001"
    received = {"reportId": report_id, "status": "RECEIVED"}
    record = {
        **received,
        "boardId": "10000000-0000-4000-8000-000000000001",
        "submissionId": None,
        "category": "OTHER",
        "receivedAt": "2026-11-04T03:00:00Z",
        "resolvedAt": None,
        "resolution": None,
        "targetAvailable": False,
    }
    endpoint = operation(
        "submitContentReport",
        "student",
        request="ContentReportRequest",
        keyed=True,
        description="Fixed category and optional same-board published ID only. No text/upload. Three new reports per participant per ten minutes. Same key recovers existing receipt without consuming quota. Reports never automatically penalize or hide.",
    )
    endpoint["responses"].update(
        {"202": success("ContentReportReceipt", received), "200": success("ContentReportReceipt", received)}
    )
    paths["/api/v2/reports"] = {"post": endpoint}
    endpoint = operation(
        "listContentReports",
        "operator",
        parameters=[{"name": "cursor", "in": "query", "required": False, "schema": text(2048)}],
        description="Board-scoped text-free report metadata, seven-day retention, signed keyset pagination.",
    )
    endpoint["responses"]["200"] = success("ContentReportPage", {"items": [record], "nextCursor": None})
    paths["/api/operator/reports"] = {"get": endpoint}
    endpoint = operation(
        "resolveContentReport",
        "operator",
        request="ReportResolutionRequest",
        keyed=True,
        parameters=[{"name": "reportId", "in": "path", "required": True, "schema": UUID}],
        description="Records a resolution once; HIDDEN/BOARD_PAUSED require an already verified hide/pause action. Same command key recovers result; conflicting reuse returns409.",
    )
    endpoint["responses"]["200"] = success(
        "ContentReportRecord",
        {**record, "status": "RESOLVED", "resolution": "NO_ACTION", "resolvedAt": "2026-11-04T03:01:00Z"},
    )
    paths["/api/operator/reports/{reportId}/resolve"] = {"post": endpoint}
    provider = {"enabled": False, "mode": "LEGACY"}
    get = operation(
        "getModerationProvider",
        "operator",
        description="Reports provider profile, worker readiness, circuit state, safe failure code and conservative daily accounting. Never returns credentials or provider response bodies; this read performs no provider calls.",
    )
    get["responses"]["200"] = success("ModerationProviderStatus", provider)
    post = operation(
        "setModerationProviderPause",
        "operator",
        request="ProviderPauseRequest",
        description="ADMIN-only audited manual pause. Resume starts two spaced recovery probes; the real profile uses budgeted synthetic-text provider calls. It never bypasses readiness, circuit health or budget checks.",
    )
    post["x-roles"] = ["ADMIN"]
    post["responses"]["200"] = success("ModerationProviderStatus", provider)
    paths["/api/operator/moderation/provider"] = {"get": get, "post": post}
    for path, method in (
        ("/api/v2/submissions", "post"),
        ("/api/v2/submissions/{submissionId}", "get"),
        ("/api/v2/submission-receipts/lookup", "post"),
    ):
        endpoint = paths[path][method]
        endpoint["parameters"] = [
            p for p in endpoint.get("parameters", []) if p["name"] != "X-Moodwall-Client-Features"
        ]
        endpoint["parameters"].append(
            {
                "name": "X-Moodwall-Client-Features",
                "in": "header",
                "required": False,
                "schema": text(100),
                "description": "Contains support-v1 for new provider-profile submissions and receipt recovery. Missing capability returns409 CLIENT_UPDATE_REQUIRED; ownership checked before receipt capability.",
            }
        )
        endpoint["responses"].setdefault("409", {"$ref": "#/components/responses/Error409"})
    schemas["SubmissionReceipt"]["properties"]["reasonCode"]["description"] = (
        "Legacy SUPPORT_AVAILABLE receipts show a neutral nonpublication notice without help guidance or actions; private source content stays hidden. New moderation does not emit SUPPORT. Required capability survives source purge/profile rollback. No new moderation status."
    )
    return contract


def main():
    path = ROOT / "docs/openapi.json"
    contract = build(json.loads(path.read_text(encoding="utf-8")))
    output = (json.dumps(contract, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.write_bytes(output)
    (ROOT / "backend/app/resources/openapi.json").write_bytes(output)
    digest = hashlib.sha256(output).hexdigest()
    test = ROOT / "backend/tests/test_http_contracts.py"
    text_value = test.read_text(encoding="utf-8")
    text_value = re.sub(r'CONTRACT_SHA256 = "[a-f0-9]+"', f'CONTRACT_SHA256 = "{digest}"', text_value)
    operation_count = sum(
        method in {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
        for item in contract["paths"].values() for method in item
    )
    for variable in ("operations", "actual"):
        text_value = re.sub(
            rf"(assert len\({variable}\) == len\(set\({variable}\)\) == )\d+",
            rf"\g<1>{operation_count}", text_value,
        )
    version = contract["info"]["version"]
    text_value = re.sub(r'CONTRACT\["info"\]\["version"\] == "[0-9.]+"', f'CONTRACT["info"]["version"] == "{version}"', text_value)
    test.write_text(text_value, encoding="utf-8")
    print(f"Contract v{version} SHA256:", digest)


if __name__ == "__main__":
    main()
