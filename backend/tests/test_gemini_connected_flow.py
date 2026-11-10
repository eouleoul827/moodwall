"""Real-profile DB/ASGI/worker flows using only in-memory synthetic HTTP."""

import json
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from test_gemini_http import SYNTHETIC_KEY, envelope, response
from test_playback_core import player, submit
from test_provider_support_flow import assert_private_and_released

from app.config import Settings
from app.db import one
from app.profanity import ProfanityFilter
from app.provider_control import ProviderControlService, ProviderUnavailable, token_cost
from app.worker import Runtime, _bootstrap_provider, run_submission
from inference.contracts import EmotionResult
from inference.gemini_http import COUNT_TOKENS_PATH, INTERACTIONS_PATH
from inference.loader import load_analyzer

pytestmark = pytest.mark.integration
FEATURE_HEADER = "X-Moodwall-Client-Features"
CRISIS = "씨발 죽고 싶다"


@pytest.fixture(autouse=True)
def use_connected_provider(settings, monkeypatch):
    configured = Settings(
        _env_file=None,
        database_url=settings.database_url,
        public_origin=settings.public_origin,
        secret_key=settings.secret_key,
        qr_key=settings.qr_key,
        environment="test",
        maintenance_enabled=False,
        moderation_profile="gemini",
        gemini_api_key=SecretStr(SYNTHETIC_KEY),
    )
    settings.__dict__.update(configured.__dict__)

    async def no_real_network(*_args, **_kwargs):
        pytest.fail("Real Gemini networking is forbidden in this test")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_real_network)


@pytest.fixture
def feature_clients(clients):
    for who in ("student", "stranger"):
        clients[who].headers[FEATURE_HEADER] = "support-v1"
    return clients


def connected_runtime(database, settings, system):
    """Use the real loader/compiler/parser; replace only the TCP boundary."""
    calls = []
    mode = {
        "generation_error": False,
        "generation_safety": False,
        "counter_oversize": False,
        "generation_input_tokens": 100,
    }

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == COUNT_TOKENS_PATH:
            return response(payload={"totalTokens": 16384 if mode["counter_oversize"] else 100})
        assert request.url.path == INTERACTIONS_PATH
        if mode["generation_error"]:
            return response(401, {"error": {"code": "unauthorized", "message": "synthetic private " + SYNTHETIC_KEY}})
        if mode["generation_safety"]:
            return response(400, {"error": {"code": "safety", "message": "synthetic private body"}})
        text = json.loads(json.loads(request.content)["input"])["text"]
        result = None
        if text == CRISIS:
            result = {
                "schema_version": "1",
                "decision": "BLOCK",
                "reason_codes": ["OTHER_UNSAFE"],
                "policy_rule_ids": ["CP06"],
            }
        return response(payload=envelope(result=result, input_tokens=mode["generation_input_tokens"]))

    analyzer = load_analyzer(settings)
    assert analyzer.moderation_profile == "gemini" and analyzer.last_usage is None
    analyzer._transport._transport = httpx.MockTransport(handler)
    runtime = Runtime(
        database,
        settings,
        system.state.moderation,
        analyzer,
        ProfanityFilter(),
        provider=ProviderControlService(database, settings),
    )
    return runtime, calls, mode


async def test_startup_probe_is_durably_charged_before_allow_becomes_ready(feature_clients, database, settings, system):
    clients = feature_clients
    runtime, calls, _mode = connected_runtime(database, settings, system)
    await _bootstrap_provider(runtime)
    async with database.connection() as conn:
        probe = await one(
            conn,
            "SELECT profile,status,outcome_code,actual_input_tokens,actual_output_tokens FROM moderation_provider_attempts",
        )
        assert probe == {
            "profile": "gemini",
            "status": "COMPLETED",
            "outcome_code": "OK",
            "actual_input_tokens": 100,
            "actual_output_tokens": 72,
        }
    await player(clients)
    identifier = await submit(clients, content="오늘 너무 우울해")
    await run_submission(runtime, identifier)
    receipt = (await clients["student"].get(f"/api/v2/submissions/{identifier}")).json()
    assert receipt["processingStatus"] == "APPROVED" and receipt["presentationStatus"] == "READY"
    assert receipt["userStatus"] == "WAITING"
    assert calls == [COUNT_TOKENS_PATH, INTERACTIONS_PATH] * 2
    summary = await runtime.provider.summary()
    assert summary["mode"] == "OPEN" and summary["syntheticAccounting"] is False
    assert summary["chargedMicroUsd"] == 2 * token_cost(settings, 100, 72)
    assert summary["reservedMicroUsd"] == 0


async def test_real_parser_blocks_independent_strong_profanity_without_support_routing(
    feature_clients, database, settings, system
):
    clients = feature_clients
    runtime, calls, _mode = connected_runtime(database, settings, system)
    await _bootstrap_provider(runtime)
    await player(clients)
    identifier = await submit(clients, content=CRISIS)
    assert runtime.filter.scan(CRISIS).decision == "CONFIRMED"
    await run_submission(runtime, identifier)
    receipt = await assert_private_and_released(clients, database, identifier, reason="CONTENT_REQUIRES_REWRITE")
    assert receipt["processingStatus"] == "NEEDS_REWRITE" and CRISIS not in json.dumps(receipt, ensure_ascii=False)
    assert calls == [COUNT_TOKENS_PATH, INTERACTIONS_PATH] * 2
    async with database.connection() as conn:
        attempt = await one(
            conn, "SELECT status,outcome_code FROM moderation_provider_attempts WHERE submission_id=%s", (identifier,)
        )
        assert attempt == {"status": "COMPLETED", "outcome_code": "OK"}


@pytest.mark.parametrize("content", ["살기싫다", "살기 싫다", "죽고싶다", "죽고 싶다"])
async def test_negative_personal_feelings_allow_the_ordinary_emotion_and_ready_flow(
    feature_clients, database, settings, system, content
):
    clients = feature_clients
    runtime, calls, _mode = connected_runtime(database, settings, system)
    await _bootstrap_provider(runtime)
    emotion_inputs = []

    def predict_emotion(value):
        emotion_inputs.append(value.text)
        return EmotionResult(label="SADNESS", source="MODEL", score=0.9)

    runtime.analyzer._emotion_predictor = predict_emotion
    await player(clients)
    identifier = await submit(clients, content=content)
    await run_submission(runtime, identifier)
    receipt = (await clients["student"].get(f"/api/v2/submissions/{identifier}")).json()
    assert receipt["processingStatus"] == "APPROVED" and receipt["presentationStatus"] == "READY"
    assert receipt["userStatus"] == "WAITING" and receipt["reasonCode"] != "SUPPORT_AVAILABLE"
    assert emotion_inputs == [content]
    assert calls == [COUNT_TOKENS_PATH, INTERACTIONS_PATH] * 2
    async with database.connection() as conn:
        source = await one(
            conn,
            "SELECT safety_verdict,reason_codes,emotion,emotion_source,strike_applied FROM submissions WHERE id=%s",
            (identifier,),
        )
        assert source == {
            "safety_verdict": "ALLOW",
            "reason_codes": [],
            "emotion": "SADNESS",
            "emotion_source": "MODEL",
            "strike_applied": False,
        }
        assert (await one(conn, "SELECT reserved_count FROM board_admission_counters"))["reserved_count"] == 1


async def test_generation_auth_failure_pauses_provider_and_never_approves_or_replays(
    feature_clients, database, settings, system
):
    clients = feature_clients
    runtime, calls, mode = connected_runtime(database, settings, system)
    await _bootstrap_provider(runtime)
    mode["generation_error"] = True
    await player(clients)
    identifier = await submit(clients, content="오늘 너무 우울해")
    await run_submission(runtime, identifier)
    await run_submission(runtime, identifier)
    receipt = await assert_private_and_released(clients, database, identifier, reason="PREPARATION_UNAVAILABLE")
    assert receipt["processingStatus"] == "FAILED"
    summary = await runtime.provider.summary()
    assert summary["mode"] == "PAUSED" and summary["lastFailureCode"] == "PROVIDER_AUTH_ERROR"
    assert calls == [COUNT_TOKENS_PATH, INTERACTIONS_PATH] * 2
    rejected = await clients["stranger"].post(
        "/api/v2/submissions", json={"content": "오늘 너무 우울해"}, headers={"Idempotency-Key": str(uuid4())}
    )
    assert rejected.status_code == 503
    async with database.connection() as conn:
        attempt = await one(conn, "SELECT * FROM moderation_provider_attempts WHERE submission_id=%s", (identifier,))
        assert attempt["status"] == "UNKNOWN" and attempt["outcome_code"] == "PROVIDER_AUTH_ERROR"
        assert SYNTHETIC_KEY not in str(attempt)


async def test_provider_safety_failure_preserves_neutral_receipt_without_strikes_or_replay(
    feature_clients, database, settings, system
):
    clients = feature_clients
    runtime, calls, mode = connected_runtime(database, settings, system)
    await _bootstrap_provider(runtime)
    mode["generation_safety"] = True
    await player(clients)
    identifier = await submit(clients, content="오늘 기분을 남겨요")
    await run_submission(runtime, identifier)
    await run_submission(runtime, identifier)

    receipt = await assert_private_and_released(clients, database, identifier, reason="CONTENT_REVIEW_UNAVAILABLE")
    assert receipt["processingStatus"] == receipt["userStatus"] == "FAILED"
    assert receipt["message"] == "이 글은 아직 전광판에 표시되지 않았어요. 다시 작성할 수 있어요."
    assert calls == [COUNT_TOKENS_PATH, INTERACTIONS_PATH] * 2
    summary = await runtime.provider.summary()
    assert summary["mode"] == "OPEN" and summary["consecutiveFailures"] == 0
    assert summary["lastFailureCode"] == "PROVIDER_SAFETY_BLOCK"
    async with database.connection() as conn:
        attempt = await one(
            conn, "SELECT status,outcome_code FROM moderation_provider_attempts WHERE submission_id=%s", (identifier,)
        )
        assert attempt == {"status": "UNKNOWN", "outcome_code": "PROVIDER_SAFETY_BLOCK"}


async def test_count_limit_stops_before_generation_and_records_nonpublic_failure(
    feature_clients, database, settings, system
):
    clients = feature_clients
    runtime, calls, mode = connected_runtime(database, settings, system)
    await _bootstrap_provider(runtime)
    mode["counter_oversize"] = True
    await player(clients)
    identifier = await submit(clients, content="오늘 너무 우울해")
    await run_submission(runtime, identifier)
    receipt = await assert_private_and_released(clients, database, identifier, reason="PREPARATION_UNAVAILABLE")
    assert receipt["processingStatus"] == "FAILED"
    assert calls == [COUNT_TOKENS_PATH, INTERACTIONS_PATH, COUNT_TOKENS_PATH]
    summary = await runtime.provider.summary()
    assert summary["mode"] == "PAUSED" and summary["lastFailureCode"] == "PROVIDER_INPUT_LIMIT"


async def test_same_idempotency_key_never_repeats_the_connected_workflow(feature_clients, database, settings, system):
    clients = feature_clients
    runtime, calls, _mode = connected_runtime(database, settings, system)
    await _bootstrap_provider(runtime)
    await player(clients)
    key = uuid4()
    identifier = await submit(clients, content="오늘 너무 우울해", key=key)
    duplicate = await clients["student"].post(
        "/api/v2/submissions", json={"content": "오늘 너무 우울해"}, headers={"Idempotency-Key": str(key)}
    )
    assert duplicate.status_code == 200 and duplicate.json()["submissionId"] == str(identifier)
    assert calls == [COUNT_TOKENS_PATH, INTERACTIONS_PATH]
    await run_submission(runtime, identifier)
    await run_submission(runtime, identifier)
    duplicate = await clients["student"].post(
        "/api/v2/submissions", json={"content": "오늘 너무 우울해"}, headers={"Idempotency-Key": str(key)}
    )
    assert duplicate.status_code == 200
    assert calls == [COUNT_TOKENS_PATH, INTERACTIONS_PATH] * 2
    async with database.connection() as conn:
        assert (await one(conn, "SELECT count(*) AS n FROM moderation_provider_attempts"))["n"] == 2


async def test_failed_connected_startup_cannot_publish_worker_readiness(database, settings, system):
    runtime, calls, mode = connected_runtime(database, settings, system)
    mode["generation_error"] = True
    async with database.connection() as conn:
        await conn.execute("DELETE FROM worker_readiness WHERE worker_name='moderation'")
    with pytest.raises(ProviderUnavailable):
        await _bootstrap_provider(runtime)
    assert calls == [COUNT_TOKENS_PATH, INTERACTIONS_PATH]
    summary = await runtime.provider.summary()
    assert summary["mode"] == "PAUSED" and summary["workerReady"] is False
    assert summary["lastFailureCode"] == "PROVIDER_AUTH_ERROR"


async def test_valid_measured_overrun_is_charged_at_actual_usage_and_pauses_provider(
    feature_clients, database, settings, system
):
    clients = feature_clients
    runtime, calls, mode = connected_runtime(database, settings, system)
    await _bootstrap_provider(runtime)
    mode["generation_input_tokens"] = 20000
    await player(clients)
    identifier = await submit(clients, content="오늘 너무 우울해")
    await run_submission(runtime, identifier)
    receipt = await assert_private_and_released(clients, database, identifier, reason="INVALID_ANALYSIS_RESULT")
    assert receipt["processingStatus"] == "FAILED"
    async with database.connection() as conn:
        attempt = await one(conn, "SELECT * FROM moderation_provider_attempts WHERE submission_id=%s", (identifier,))
    assert attempt["status"] == "COMPLETED" and attempt["outcome_code"] == "PROVIDER_INPUT_LIMIT"
    assert attempt["actual_input_tokens"] == 20000 and attempt["actual_output_tokens"] == 72
    assert attempt["charged_micro_usd"] == token_cost(settings, 20000, 72)
    assert attempt["charged_micro_usd"] > attempt["reserved_micro_usd"]
    summary = await runtime.provider.summary()
    assert summary["mode"] == "PAUSED" and summary["lastFailureCode"] == "PROVIDER_INPUT_LIMIT"
    assert summary["chargedMicroUsd"] == token_cost(settings, 100, 72) + token_cost(settings, 20000, 72)
    assert calls == [COUNT_TOKENS_PATH, INTERACTIONS_PATH] * 2
