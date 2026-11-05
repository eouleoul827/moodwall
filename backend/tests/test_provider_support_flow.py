"""Real DB/ASGI/worker integration with an entirely offline moderation mock."""

import json
from uuid import uuid4

import pytest
from test_playback_core import player, submit

from app.config import Settings
from app.db import one
from app.profanity import ProfanityFilter
from app.worker import Runtime, run_submission
from inference.loader import load_analyzer

pytestmark = pytest.mark.integration
FEATURE_HEADER = "X-Moodwall-Client-Features"
CRISIS = "씨발 죽고 싶다"


async def test_provider_controls_are_private_and_audited(clients, database):
    path = "/api/operator/moderation/provider"
    assert (await clients["student"].get(path)).status_code == 401
    response = await clients["admin"].get(path)
    assert response.status_code == 200 and response.json()["profile"] == "gemini_mock"
    assert response.json()["transportConfigured"] is False
    assert response.json()["syntheticAccounting"] is True
    response = await clients["admin"].post(path, json={"paused": True})
    assert response.status_code == 200 and response.json()["mode"] == "PAUSED"
    response = await clients["admin"].post(path, json={"paused": False})
    assert response.status_code == 200 and response.json()["mode"] == "OPEN_CIRCUIT"
    assert (await clients["admin"].post(path, json={"paused": False})).status_code == 200
    async with database.connection() as conn:
        for action in ("PAUSE_PROVIDER", "RESUME_PROVIDER"):
            assert (await one(conn, "SELECT count(*) AS n FROM operator_audit_logs WHERE action=%s", (action,)))[
                "n"
            ] == 1


@pytest.fixture(autouse=True)
def use_offline_provider(settings):
    """Switch the shared settings object before database/system dependencies."""
    configured = Settings(
        _env_file=None,
        database_url=settings.database_url,
        public_origin=settings.public_origin,
        secret_key=settings.secret_key,
        qr_key=settings.qr_key,
        environment="test",
        maintenance_enabled=False,
        moderation_profile="gemini_mock",
    )
    settings.__dict__.update(configured.__dict__)


@pytest.fixture
def feature_clients(clients):
    for who in ("student", "stranger"):
        clients[who].headers[FEATURE_HEADER] = "support-v1"
    return clients


def runtime_for(database, settings, system):
    return Runtime(database, settings, system.state.moderation, load_analyzer(settings), ProfanityFilter())


async def assert_private_and_released(clients, database, identifier, *, reason):
    response = await clients["student"].get(f"/api/v2/submissions/{identifier}")
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["reasonCode"] == reason
    assert receipt["contentStatus"] == "PURGED"
    assert receipt["presentationStatus"] is None
    assert not {"content", "text", "emotion", "safety", "reason_codes", "policy_rule_ids"} & set(receipt)
    usage = (await clients["student"].get("/api/me/usage")).json()
    assert usage["usedCount"] == usage["pendingCount"] == usage["violationCount"] == 0
    assert usage["suspendedUntil"] is None and usage["activeSubmissionId"] is None
    async with database.connection() as conn:
        assert await one(conn, "SELECT id FROM submissions WHERE id=%s", (identifier,)) is None
        assert (
            await one(conn, "SELECT submission_id FROM presentation_items WHERE submission_id=%s", (identifier,))
            is None
        )
        assert (await one(conn, "SELECT reserved_count FROM board_admission_counters"))["reserved_count"] == 0
    return receipt


async def test_crisis_with_confirmed_profanity_is_private_support_without_strikes(
    feature_clients, database, settings, system
):
    clients = feature_clients
    await player(clients)
    identifier = await submit(clients, content=CRISIS)
    runtime = runtime_for(database, settings, system)
    assert runtime.filter.scan(CRISIS).decision == "CONFIRMED"
    await run_submission(runtime, identifier)
    receipt = await assert_private_and_released(clients, database, identifier, reason="SUPPORT_AVAILABLE")
    assert receipt["processingStatus"] == receipt["userStatus"] == "NEEDS_REWRITE"
    assert CRISIS not in json.dumps(receipt, ensure_ascii=False)
    async with database.connection() as conn:
        attempt = await one(
            conn, "SELECT status,outcome_code FROM moderation_provider_attempts WHERE submission_id=%s", (identifier,)
        )
        assert attempt == {"status": "COMPLETED", "outcome_code": "OK"}


async def test_support_snapshot_survives_profile_change_and_same_key_recovery(
    feature_clients, database, settings, system
):
    clients = feature_clients
    await player(clients)
    key = uuid4()
    identifier = await submit(clients, content=CRISIS, key=key)
    await run_submission(runtime_for(database, settings, system), identifier)
    settings.moderation_profile = "legacy"
    clients["student"].headers.pop(FEATURE_HEADER)
    paths = [
        await clients["student"].get(f"/api/v2/submissions/{identifier}"),
        await clients["student"].post("/api/v2/submission-receipts/lookup", json={"idempotencyKey": str(key)}),
        await clients["student"].post(
            "/api/v2/submissions", json={"content": CRISIS}, headers={"Idempotency-Key": str(key)}
        ),
    ]
    assert [response.status_code for response in paths] == [409, 409, 409]
    assert all(response.json()["code"] == "CLIENT_UPDATE_REQUIRED" for response in paths)
    clients["student"].headers[FEATURE_HEADER] = "support-v1"
    duplicate = await clients["student"].post(
        "/api/v2/submissions", json={"content": CRISIS}, headers={"Idempotency-Key": str(key)}
    )
    lookup = await clients["student"].post("/api/v2/submission-receipts/lookup", json={"idempotencyKey": str(key)})
    assert duplicate.status_code == lookup.status_code == 200
    assert duplicate.json()["submissionId"] == str(identifier)
    assert duplicate.json()["reasonCode"] == lookup.json()["reasonCode"] == "SUPPORT_AVAILABLE"
    async with database.connection() as conn:
        assert (await one(conn, "SELECT count(*) AS n FROM moderation_provider_attempts"))["n"] == 1


async def test_old_client_is_rejected_before_any_quota_or_provider_reservation(clients, database):
    # No player heartbeat: capability rejection takes precedence over availability.
    response = await clients["student"].post(
        "/api/v2/submissions", json={"content": "오늘 너무 우울해"}, headers={"Idempotency-Key": str(uuid4())}
    )
    assert response.status_code == 409 and response.json()["code"] == "CLIENT_UPDATE_REQUIRED"
    async with database.connection() as conn:
        for table in ("moderation_provider_attempts", "submission_receipts", "admission_reservations", "daily_usage"):
            assert (await one(conn, f"SELECT count(*) AS n FROM {table}"))["n"] == 0


async def test_stranger_cannot_discover_support_receipts_even_without_feature_header(
    feature_clients, database, settings, system
):
    clients = feature_clients
    await player(clients)
    key = uuid4()
    identifier = await submit(clients, content=CRISIS, key=key)
    await run_submission(runtime_for(database, settings, system), identifier)
    clients["stranger"].headers.pop(FEATURE_HEADER)
    foreign = await clients["stranger"].get(f"/api/v2/submissions/{identifier}")
    lookup = await clients["stranger"].post("/api/v2/submission-receipts/lookup", json={"idempotencyKey": str(key)})
    assert foreign.status_code == lookup.status_code == 404
    assert "SUPPORT_AVAILABLE" not in foreign.text + lookup.text


async def test_unknown_mock_text_is_uncertain_and_never_prepared_for_display(
    feature_clients, database, settings, system
):
    clients = feature_clients
    await player(clients)
    identifier = await submit(clients, content="새롭게 쓴 이름 없는 문장")
    await run_submission(runtime_for(database, settings, system), identifier)
    receipt = await assert_private_and_released(clients, database, identifier, reason="CONTENT_REVIEW_UNCERTAIN")
    assert receipt["processingStatus"] == "NEEDS_REWRITE"


async def test_reviewed_allow_becomes_ready_with_neutral_emotion_fallback(feature_clients, database, settings, system):
    clients = feature_clients
    await player(clients)
    identifier = await submit(clients, content="오늘 너무 우울해")
    await run_submission(runtime_for(database, settings, system), identifier)
    receipt = (await clients["student"].get(f"/api/v2/submissions/{identifier}")).json()
    assert receipt["processingStatus"] == "APPROVED"
    assert receipt["presentationStatus"] == "READY" and receipt["userStatus"] == "WAITING"
    async with database.connection() as conn:
        source = await one(
            conn,
            "SELECT safety_verdict,emotion,emotion_source,strike_applied FROM submissions WHERE id=%s",
            (identifier,),
        )
        assert source == {
            "safety_verdict": "ALLOW",
            "emotion": "NEUTRAL",
            "emotion_source": "FALLBACK",
            "strike_applied": False,
        }
        assert (await one(conn, "SELECT reserved_count FROM board_admission_counters"))["reserved_count"] == 1


async def test_invalid_wire_output_fails_closed_and_cannot_dispatch_twice(feature_clients, database, settings, system):
    clients = feature_clients
    await player(clients)
    identifier = await submit(clients, content="오늘 너무 우울해")
    runtime = runtime_for(database, settings, system)
    calls = []

    class MalformedTransport:
        def respond(self, _request):
            calls.append(True)
            return {
                "status": "completed",
                "steps": [{"type": "model_output", "content": [{"type": "text", "text": "ALLOW"}]}],
            }

    runtime.analyzer._transport = MalformedTransport()
    await run_submission(runtime, identifier)
    await run_submission(runtime, identifier)
    receipt = await assert_private_and_released(clients, database, identifier, reason="INVALID_ANALYSIS_RESULT")
    assert receipt["processingStatus"] == "FAILED" and len(calls) == 1
    async with database.connection() as conn:
        assert (
            await one(conn, "SELECT status FROM moderation_provider_attempts WHERE submission_id=%s", (identifier,))
        )["status"] == "UNKNOWN"


async def test_confirmed_rule_does_not_bypass_provider_or_apply_legacy_penalty(
    feature_clients, database, settings, system
):
    clients = feature_clients
    await player(clients)
    identifier = await submit(clients, content="씨발")
    runtime = runtime_for(database, settings, system)
    calls = []

    class AllowingTransport:
        def respond(self, _request):
            calls.append(True)
            return {
                "status": "completed",
                "steps": [
                    {
                        "type": "model_output",
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {
                                        "schema_version": "1",
                                        "decision": "ALLOW",
                                        "reason_codes": [],
                                        "policy_rule_ids": [],
                                    }
                                ),
                            }
                        ],
                    }
                ],
            }

    runtime.analyzer._transport = AllowingTransport()
    await run_submission(runtime, identifier)
    receipt = await assert_private_and_released(clients, database, identifier, reason="CONTENT_REQUIRES_REWRITE")
    assert receipt["processingStatus"] == "NEEDS_REWRITE" and calls == [True]
