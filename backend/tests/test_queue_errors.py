"""Queue transport failures must roll back admission and remain safe to retry."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import procrastinate
import psycopg
import pytest

from app import queue
from app.db import one

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "cause_type,status,code",
    [
        (psycopg.errors.QueryCanceled, 503, "SERVICE_UNAVAILABLE"),
        (psycopg.OperationalError, 503, "SERVICE_UNAVAILABLE"),
        (psycopg.InterfaceError, 503, "SERVICE_UNAVAILABLE"),
        (psycopg.errors.UniqueViolation, 500, "INTERNAL_ERROR"),
        (RuntimeError, 500, "INTERNAL_ERROR"),
    ],
)
async def test_wrapped_queue_failure_is_atomic_and_same_key_can_retry(
    clients, database, monkeypatch, cause_type, status, code
):
    assert (
        await clients["display"].post(
            "/api/display/heartbeat", json={"clientInstanceId": str(uuid4()), "displayProtocolVersion": "2"}
        )
    ).status_code == 200
    real_queue = queue._app
    error = procrastinate.exceptions.ConnectorException()
    error.__cause__ = cause_type("synthetic private database details")
    task = Mock(configure=Mock(return_value=SimpleNamespace(defer_async=AsyncMock(side_effect=error))))
    monkeypatch.setattr(queue, "_app", SimpleNamespace(tasks={"moodwall.moderate": task}))
    key = str(uuid4())
    payload = {"content": "오늘 기분 좋아"}
    rejected = await clients["student"].post(
        "/api/v2/submissions", json=payload, headers={"Idempotency-Key": key}
    )
    assert rejected.status_code == status and rejected.json()["code"] == code, rejected.text
    assert "synthetic private" not in rejected.text
    if status == 503:
        assert rejected.headers["Retry-After"] == "1"
    async with database.connection() as conn:
        for table in ("submission_receipts", "submissions", "admission_reservations", "board_admission_counters"):
            assert (await one(conn, f"SELECT count(*) AS n FROM {table}"))["n"] == 0
    monkeypatch.setattr(queue, "_app", real_queue)
    retried = await clients["student"].post(
        "/api/v2/submissions", json=payload, headers={"Idempotency-Key": key}
    )
    assert retried.status_code == 202, retried.text
