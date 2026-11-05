from __future__ import annotations

import asyncio
import hmac
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.db import db_time, one
from app.errors import Problem
from app.request_limits import bootstrap_limit, limit, read_limit


def _settings(**overrides):
    return Settings(
        _env_file=None,
        database_url="postgresql://test:test@127.0.0.1/moodwall_test",
        secret_key="synthetic-limits-session-key-01234567890123456789",
        qr_key="synthetic-limits-qr-key-01234567890123456789012345",
        **overrides,
    )


def _request(settings, db):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings, db=db)))


def test_role_budgets_are_configurable_without_changing_personal_quota():
    settings = _settings(bootstrap_student_ip_per_minute=1200, read_student_per_minute=90)
    assert bootstrap_limit(settings, "student") == 1200
    assert bootstrap_limit(settings, "display") == 120
    assert bootstrap_limit(settings, "operator") == 120
    assert read_limit(settings, "student") == 90
    assert read_limit(settings, "display") == 240
    assert read_limit(settings, "operator") == 120
    assert settings.write_per_minute == 120


@pytest.mark.parametrize("role", ["", "admin", "__dict__"])
def test_unknown_role_cannot_select_a_settings_attribute(role):
    settings = _settings()
    for selector in (bootstrap_limit, read_limit):
        with pytest.raises(ValueError, match="Unknown request role"):
            selector(settings, role)


@pytest.mark.parametrize(
    "field",
    [
        "bootstrap_student_ip_per_minute",
        "bootstrap_display_ip_per_minute",
        "bootstrap_operator_ip_per_minute",
        "read_student_per_minute",
        "read_display_per_minute",
        "read_operator_per_minute",
        "write_per_minute",
    ],
)
@pytest.mark.parametrize("value", [0, -1, 100001])
def test_invalid_endpoint_budgets_fail_startup(field, value):
    with pytest.raises(ValidationError):
        _settings(**{field: value})


async def test_hashed_bucket_and_retry_after_use_database_clock(monkeypatch):
    import app.request_limits as module

    captured = []
    now = datetime(2026, 11, 4, 3, 14, 59, 900000, tzinfo=timezone.utc)

    @asynccontextmanager
    async def connection():
        yield object()

    async def clock(_conn):
        return now

    async def result(_conn, _sql, params):
        captured.append(params)
        return {"hits": 3}

    monkeypatch.setattr(module, "db_time", clock)
    monkeypatch.setattr(module, "one", result)
    settings = _settings()
    request = _request(settings, SimpleNamespace(connection=connection))
    with pytest.raises(Problem) as rejected:
        await limit(request, "read:student", "student-opaque-id", 2)
    assert rejected.value.status == 429
    assert rejected.value.code == "RATE_LIMITED"
    assert rejected.value.retry_after == 1
    scope, digest, window, expires_at = captured[0]
    assert scope == "read:student"
    assert digest == hmac.digest(settings.secret_key.encode(), b"rate:v1:read:student:student-opaque-id", "sha256")
    assert len(digest) == 32
    assert window == now.replace(second=0, microsecond=0)
    assert expires_at == window + timedelta(minutes=2)


@pytest.mark.integration
async def test_concurrent_requests_cannot_exceed_one_bucket_budget(database, settings, monkeypatch):
    import app.request_limits as module

    # A real fixed-window boundary legitimately opens a fresh budget. Freeze
    # this test's clock so a run at xx:xx:59 cannot accidentally test two windows.
    async with database.connection() as conn:
        fixed_now = await db_time(conn)

    async def clock(_conn):
        return fixed_now

    monkeypatch.setattr(module, "db_time", clock)
    request = _request(settings, database)

    async def attempt():
        try:
            await limit(request, "test-concurrent", "synthetic", 3)
            return "allowed"
        except Problem as error:
            assert error.code == "RATE_LIMITED"
            return "limited"

    results = await asyncio.gather(*(attempt() for _ in range(12)))
    assert results.count("allowed") == 3
    assert results.count("limited") == 9
    async with database.connection() as conn:
        row = await one(conn, "SELECT sum(hits) AS hits FROM rate_limit_buckets WHERE scope='test-concurrent'")
    assert row["hits"] == 12


@pytest.mark.integration
async def test_request_subject_and_scope_budgets_are_isolated(database, settings):
    request = _request(settings, database)
    await limit(request, "read:student", "first", 1)
    await limit(request, "read:student", "second", 1)
    await limit(request, "write:student", "first", 1)
    with pytest.raises(Problem, match="RATE_LIMITED"):
        await limit(request, "read:student", "first", 1)
