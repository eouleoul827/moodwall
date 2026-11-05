"""Offline provider runtime: no external service, keys or student data."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.config import Settings, gemini_tariff
from app.db import one
from app.errors import Problem
from app.profanity import ProfanityFilter
from app.provider_control import ProviderControlService, ProviderUnavailable, token_cost
from app.worker import Runtime, _bootstrap_provider, run_submission
from inference.contracts import AnalysisResult, EmotionResult, ModelAnalyzed, SafetyResult
from inference.loader import load_analyzer
from inference.provider_moderation import ProviderModerationError


def mock_settings(**overrides):
    return Settings(
        _env_file=None,
        database_url="postgresql://postgres@localhost/moodwall_test",
        public_origin="https://moodwall.test",
        environment="test",
        secret_key="synthetic-provider-session-key-01234567890",
        qr_key="synthetic-provider-qr-key-0123456789012345",
        moderation_profile="gemini_mock",
        **overrides,
    )


def real_settings(**overrides):
    return Settings(
        _env_file=None,
        database_url="postgresql://postgres@localhost/moodwall_test",
        public_origin="https://moodwall.test",
        environment="test",
        secret_key="synthetic-provider-session-key-01234567890",
        qr_key="synthetic-provider-qr-key-0123456789012345",
        moderation_profile="gemini",
        gemini_api_key="synthetic-test-key-no-network",
        **overrides,
    )


@pytest.fixture
def fixed_tariff_date(monkeypatch):
    monkeypatch.setattr("app.config.datetime", SimpleNamespace(now=lambda *_: datetime(2026, 11, 5, tzinfo=UTC)))


def test_real_configuration_secrets_and_published_defaults(fixed_tariff_date):
    settings = real_settings()
    assert settings.analyzer == "gemini"
    assert settings.provider_max_input_tokens == 16384
    assert settings.provider_input_tpm == 200000
    assert settings.provider_input_micro_usd_per_million == 750000
    assert settings.provider_output_micro_usd_per_million == 3750000
    assert "synthetic-test-key-no-network" not in repr(settings)
    assert "gemini_api_key" not in settings.model_dump()
    assert "gemini_api_key" not in settings.provider_release_parameters


@pytest.mark.parametrize("env_name", ["GEMINI_API_KEY", "MOODWALL_GEMINI_API_KEY"])
def test_gemini_key_environment_alias_never_activates_legacy_profile(monkeypatch, env_name):
    monkeypatch.delenv("MOODWALL_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv(env_name, "synthetic-key-value")
    values = mock_settings().model_dump(
        exclude={
            "moderation_profile",
            "analyzer",
            "model_bundle_version",
            "moderation_policy_version",
        }
    )
    settings = Settings(_env_file=None, **values)
    assert settings.moderation_profile == "legacy"
    assert settings.gemini_api_key.get_secret_value() == "synthetic-key-value"
    assert "synthetic-key-value" not in repr(settings)


def test_tariff_boundaries_are_published_and_never_extrapolated(fixed_tariff_date):
    assert gemini_tariff(date(2026, 12, 31))[2:] == (750000, 3750000)
    assert gemini_tariff(date(2027, 1, 1))[2:] == (1500000, 7500000)
    with pytest.raises(ValueError):
        gemini_tariff(date(2028, 1, 1))
    service = ProviderControlService(None, real_settings())
    with pytest.raises(ProviderUnavailable, match="PROVIDER_TARIFF_EXPIRED"):
        service._require_transport(datetime(2027, 1, 1, tzinfo=UTC))


async def test_real_bootstrap_requires_budgeted_probe_before_ready(fixed_tariff_date):
    settings = real_settings()
    provider = SimpleNamespace(
        recover_stale=AsyncMock(), probe=AsyncMock(return_value=True), summary=AsyncMock(return_value={"mode": "OPEN"})
    )
    runtime = Runtime(None, settings, None, None, None, provider=provider)
    await _bootstrap_provider(runtime)
    provider.probe.assert_awaited_once_with(None, startup=True)
    provider.summary.return_value = {"mode": "PAUSED"}
    provider.probe.return_value = False
    with pytest.raises(ProviderUnavailable):
        await _bootstrap_provider(runtime)


def test_mock_configuration_pins_release_and_disables_strikes():
    settings = mock_settings()
    assert settings.analyzer == "gemini_mock"
    assert settings.inference_timeout_seconds == 15
    assert settings.moderation_p95_seconds == 8
    assert not settings.auto_strikes
    assert settings.model_bundle_version == settings.moderation_policy_version
    assert len(settings.model_bundle_version) <= 100
    assert mock_settings(provider_rpm=5).model_bundle_version != settings.model_bundle_version


@pytest.mark.parametrize(
    "overrides",
    [
        {"environment": "production"},
        {"auto_strikes": True},
        {"analyzer": "fake.other"},
        {"model_bundle_version": "incorrect-release"},
        {"inference_timeout_seconds": 12},
    ],
)
def test_invalid_mock_configuration_is_rejected(overrides):
    with pytest.raises((ValueError, ValidationError)):
        # Avoid duplicate explicit helper arguments.
        values = mock_settings().model_dump(exclude={"model_bundle_version", "moderation_policy_version"})
        values.update(overrides)
        Settings(_env_file=None, **values)


def test_integer_cost_reservation_never_rounds_down():
    settings = mock_settings(provider_input_micro_usd_per_million=1, provider_output_micro_usd_per_million=1)
    assert token_cost(settings, 1, 0) == 1
    assert token_cost(settings, 0, 0) == 0
    assert token_cost(settings, 500000, 500001) == 2


@asynccontextmanager
async def connection():
    yield None


def runtime_with_confirmed_curse(settings, provider):
    now, run_token, submission_id = datetime.now(UTC), uuid4(), uuid4()
    claim = {
        "id": submission_id,
        "content": "씨발 죽고 싶어",
        "processing_run_token": run_token,
        "processing_lease_until": now + timedelta(seconds=15),
        "moderation_policy_version": settings.moderation_policy_version,
    }
    analysis = AnalysisResult(
        model_bundle_version=settings.model_bundle_version,
        inference_policy_version=settings.inference_policy_version,
        safety=SafetyResult(verdict="BLOCK", reason_codes=["SELF_CRISIS"]),
        emotion=None,
    )
    analyzer = SimpleNamespace(
        analyze_moderated=Mock(return_value=analysis), last_usage={"input_tokens": 10, "output_tokens": 10}
    )
    service = SimpleNamespace(
        claim=AsyncMock(side_effect=[claim, None]), finalize=AsyncMock(), retry_before_model=AsyncMock()
    )
    runtime = Runtime(
        SimpleNamespace(connection=connection), settings, service, analyzer, ProfanityFilter(), provider=provider
    )
    return runtime, now, submission_id


async def test_confirmed_curse_is_deferred_so_support_never_becomes_a_strike(monkeypatch):
    provider = SimpleNamespace(begin_dispatch=AsyncMock(), finish_dispatch=AsyncMock())
    runtime, now, submission_id = runtime_with_confirmed_curse(mock_settings(), provider)
    monkeypatch.setattr("app.worker.db_time", AsyncMock(return_value=now))
    await run_submission(runtime, submission_id)
    runtime.analyzer.analyze_moderated.assert_called_once()
    assert runtime.analyzer.analyze_moderated.call_args.kwargs["confirmed_profanity"] is True
    outcome = runtime.service.finalize.call_args.args[1]
    assert isinstance(outcome, ModelAnalyzed)
    assert outcome.analysis.safety.reason_codes == ["SELF_CRISIS"]
    assert provider.begin_dispatch.await_count == provider.finish_dispatch.await_count == 1
    await run_submission(runtime, submission_id)
    assert runtime.analyzer.analyze_moderated.call_count == 1


async def test_failed_or_uncertain_dispatch_ack_never_executes_analyzer(monkeypatch):
    provider = SimpleNamespace(
        begin_dispatch=AsyncMock(side_effect=ProviderUnavailable("PROVIDER_DISPATCH_FORBIDDEN")),
        finish_dispatch=AsyncMock(),
    )
    runtime, now, submission_id = runtime_with_confirmed_curse(mock_settings(), provider)
    monkeypatch.setattr("app.worker.db_time", AsyncMock(return_value=now))
    await run_submission(runtime, submission_id)
    runtime.analyzer.analyze_moderated.assert_not_called()
    assert runtime.service.finalize.call_args.args[1].error_code == "MODEL_NOT_READY"
    runtime.service.retry_before_model.assert_not_awaited()


async def test_probe_usage_cannot_overwrite_finished_submission_usage(monkeypatch):
    provider = SimpleNamespace(begin_dispatch=AsyncMock(), finish_dispatch=AsyncMock())
    runtime, now, submission_id = runtime_with_confirmed_curse(mock_settings(), provider)
    monkeypatch.setattr("app.worker.db_time", AsyncMock(return_value=now))

    class ProbeImmediatelyAfterUnlock:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            # Deterministically simulate a probe replacing/mutating telemetry
            # at the exact point the submission releases analyzer ownership.
            runtime.analyzer.last_usage.update(input_tokens=999, output_tokens=888)

    runtime.inference_lock = ProbeImmediatelyAfterUnlock()
    await run_submission(runtime, submission_id)
    assert runtime.analyzer.last_usage["input_tokens"] == 999
    assert provider.finish_dispatch.call_args.kwargs["usage"] == {"input_tokens": 10, "output_tokens": 10}


@pytest.mark.integration
async def test_parallel_reservations_cannot_exceed_project_rpm(database):
    settings = mock_settings()
    service = ProviderControlService(database, settings)

    async def reserve():
        try:
            async with database.connection() as conn, conn.transaction():
                await service.reserve_submission(conn, uuid4())
            return True
        except Problem as error:
            assert error.status == 503
            return False

    results = await asyncio.gather(*(reserve() for _ in range(8)))
    assert sum(results) == 6
    async with database.connection() as conn:
        assert (await one(conn, "SELECT count(*) AS count FROM moderation_provider_attempts"))["count"] == 6


@pytest.mark.integration
async def test_budget_reservation_release_and_manual_pause(database):
    service = ProviderControlService(database, mock_settings(provider_daily_budget_micro_usd=4608))
    async with database.connection() as conn, conn.transaction():
        await service.reserve_submission(conn, uuid4())
    async with database.connection() as conn, conn.transaction():
        with pytest.raises(Problem):
            await service.reserve_submission(conn, uuid4())
    # No source was ever inserted: this reservation provably did not dispatch.
    assert await service.recover_stale() == 1
    assert (await service.summary())["reservedMicroUsd"] == 0
    assert (await service.set_paused(True))["mode"] == "PAUSED"
    async with database.connection() as conn, conn.transaction():
        with pytest.raises(Problem):
            await service.reserve_submission(conn, uuid4())
    assert (await service.set_paused(False))["mode"] == "OPEN_CIRCUIT"


async def started_attempt(database, service):
    submission_id, run_token = uuid4(), uuid4()
    async with database.connection() as conn, conn.transaction():
        await service.reserve_submission(conn, submission_id)
        await conn.execute(
            """UPDATE moderation_provider_attempts SET status='STARTED',run_token=%s,
            started_at=clock_timestamp() WHERE submission_id=%s""",
            (run_token, submission_id),
        )
    return submission_id, run_token


@pytest.mark.integration
async def test_started_crash_is_unknown_charged_and_never_replayed(database):
    settings = mock_settings()
    service = ProviderControlService(database, settings)
    submission_id, token = await started_attempt(database, service)
    async with database.connection() as conn:
        await conn.execute(
            "UPDATE moderation_provider_attempts SET started_at=clock_timestamp()-interval '16 seconds' WHERE submission_id=%s",
            (submission_id,),
        )
    assert await service.recover_stale() == 1
    summary = await service.summary()
    assert summary["chargedMicroUsd"] == 4608
    assert summary["reservedMicroUsd"] == 0
    assert summary["unknownAttempts"] == 1
    with pytest.raises(ProviderUnavailable):
        await service.begin_dispatch(submission_id, token, settings.moderation_policy_version)
    await service.finish_dispatch(submission_id, token, usage={"input_tokens": 1, "output_tokens": 1}, success=True)
    assert (await service.summary())["chargedMicroUsd"] == 4608


@pytest.mark.integration
async def test_settlement_is_idempotent_and_three_technical_failures_open_circuit(database):
    service = ProviderControlService(database, mock_settings())
    for index in range(3):
        submission_id, token = await started_attempt(database, service)
        await service.finish_dispatch(
            submission_id,
            token,
            usage={"input_tokens": 10, "output_tokens": 10},
            success=False,
            error_code="INFERENCE_TIMEOUT",
        )
        await service.finish_dispatch(submission_id, token, usage=None, success=False, error_code="WORKER_LOST")
    summary = await service.summary()
    assert summary["mode"] == "OPEN_CIRCUIT"
    assert summary["chargedMicroUsd"] == 3 * 35
    assert summary["reservedMicroUsd"] == 0


@pytest.mark.integration
async def test_circuit_recovers_only_after_two_spaced_allow_probes(database):
    settings = mock_settings()
    service = ProviderControlService(database, settings)
    analyzer = load_analyzer(settings)
    await service.set_paused(True)
    await service.set_paused(False)
    assert await service.probe(analyzer)
    assert (await service.summary())["mode"] == "HALF_OPEN"
    assert not await service.probe(analyzer)
    async with database.connection() as conn:
        await conn.execute(
            "UPDATE moderation_provider_control SET last_probe_at=clock_timestamp()-interval '31 seconds' WHERE id='gemini'"
        )
    assert await service.probe(analyzer)
    assert (await service.summary())["mode"] == "OPEN"
    async with database.connection() as conn:
        ledger = await one(conn, "SELECT count(*) AS calls FROM moderation_provider_attempts")
        assert ledger["calls"] == 2


@pytest.mark.integration
async def test_real_reservations_count_preflight_and_generation_per_project(database, fixed_tariff_date):
    service = ProviderControlService(database, real_settings())
    for _ in range(3):
        async with database.connection() as conn, conn.transaction():
            await service.reserve_submission(conn, uuid4())
    async with database.connection() as conn, conn.transaction():
        with pytest.raises(Problem):
            await service.reserve_submission(conn, uuid4())
    assert (await service.summary())["reservedMicroUsd"] == 3 * 14208


@pytest.mark.integration
async def test_real_startup_probe_uses_durable_budget_and_actual_usage(database, fixed_tariff_date):
    settings = real_settings()
    service = ProviderControlService(database, settings)
    result = AnalysisResult(
        model_bundle_version=settings.model_bundle_version,
        inference_policy_version=settings.inference_policy_version,
        safety=SafetyResult(verdict="ALLOW"),
        emotion=EmotionResult(label="NEUTRAL", score=None, source="FALLBACK"),
    )
    analyzer = SimpleNamespace(
        analyze_moderated=Mock(return_value=result), last_usage={"input_tokens": 100, "output_tokens": 20}
    )
    assert await service.probe(analyzer, startup=True)
    summary = await service.summary()
    assert summary["mode"] == "OPEN"
    assert summary["transportConfigured"] is True
    assert summary["syntheticAccounting"] is False
    assert summary["workerReady"] is False  # Supervisor has not published READY.
    assert summary["chargedMicroUsd"] == 150
    assert summary["reservedMicroUsd"] == 0
    async with database.connection() as conn:
        row = await one(conn, "SELECT profile,status,outcome_code FROM moderation_provider_attempts")
        assert row == {"profile": "gemini", "status": "COMPLETED", "outcome_code": "OK"}


@pytest.mark.integration
async def test_authentication_failure_pauses_real_provider_and_records_safe_reason(database, fixed_tariff_date):
    settings = real_settings()
    service = ProviderControlService(database, settings)
    analyzer = SimpleNamespace(
        analyze_moderated=Mock(side_effect=ProviderModerationError("MODEL_NOT_READY", failure_kind="AUTH")),
        last_usage=None,
    )
    assert not await service.probe(analyzer, startup=True)
    summary = await service.summary()
    assert summary["mode"] == "PAUSED"
    assert summary["lastFailureCode"] == "PROVIDER_AUTH_ERROR"
    assert summary["unknownAttempts"] == 1
    assert summary["chargedMicroUsd"] == 14208
    assert not await service.probe(analyzer)
    assert analyzer.analyze_moderated.call_count == 1


@pytest.mark.integration
async def test_rate_limit_respects_retry_after_without_replaying_submission(database, fixed_tariff_date):
    settings = real_settings()
    service = ProviderControlService(database, settings)
    submission_id, token = await started_attempt(database, service)
    await service.finish_dispatch(
        submission_id,
        token,
        success=False,
        error_code="TRANSIENT_DEPENDENCY",
        failure_kind="RATE_LIMIT",
        retry_after_seconds=3661,
    )
    summary = await service.summary()
    assert summary["mode"] == "OPEN_CIRCUIT"
    assert summary["lastFailureCode"] == "PROVIDER_RATE_LIMITED"
    async with database.connection() as conn:
        remaining = await one(
            conn,
            "SELECT EXTRACT(epoch FROM opened_until-clock_timestamp()) AS seconds FROM moderation_provider_control",
        )
        assert 3650 < remaining["seconds"] <= 3661
    with pytest.raises(ProviderUnavailable):
        await service.begin_dispatch(submission_id, token, settings.moderation_policy_version)


@pytest.mark.integration
async def test_provider_safety_refusal_is_nonpublic_without_availability_penalty(database, fixed_tariff_date):
    service = ProviderControlService(database, real_settings(provider_rpm=100, provider_input_tpm=1000000))
    for _ in range(5):
        submission_id, token = await started_attempt(database, service)
        await service.finish_dispatch(
            submission_id, token, success=False, error_code="TRANSIENT_DEPENDENCY", failure_kind="SAFETY"
        )
    summary = await service.summary()
    assert summary["mode"] == "OPEN"
    assert summary["consecutiveFailures"] == 0
    assert summary["lastFailureCode"] == "PROVIDER_SAFETY_BLOCK"


@pytest.mark.integration
async def test_real_daily_accounting_keeps_both_http_per_repeated_workflow(database, fixed_tariff_date):
    service = ProviderControlService(database, real_settings())
    for _ in range(2):
        submission_id, token = await started_attempt(database, service)
        await service.finish_dispatch(
            submission_id, token, usage={"input_tokens": 10, "output_tokens": 10}, success=True
        )
    async with database.connection() as conn:
        daily = await one(conn, "SELECT calls,charged_micro_usd FROM moderation_provider_daily")
    assert daily == {"calls": 4, "charged_micro_usd": 90}
