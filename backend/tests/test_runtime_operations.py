"""Operational safeguards that preserve recovery of already accepted work."""

from uuid import uuid4

import pytest

from app.config import Settings
from app.db import one


def configured(**changes):
    return Settings(
        _env_file=None,
        database_url="postgresql://invalid.invalid/not_used",
        public_origin="https://moodwall.test",
        secret_key="synthetic-runtime-session-01234567890123456789",
        qr_key="synthetic-runtime-qr-01234567890123456789012345",
        analyzer="inference.fake:make_analyzer",
        **changes,
    )


def test_production_cannot_disable_retention_and_recovery():
    with pytest.raises(ValueError, match="maintenance"):
        configured(environment="production", maintenance_enabled=False)


@pytest.mark.parametrize("environment", ["development", "test"])
def test_test_and_development_can_explicitly_disable_internal_loops(environment):
    assert not configured(environment=environment, maintenance_enabled=False).maintenance_enabled


@pytest.mark.integration
async def test_v2_device_blocks_new_v1_work_but_keeps_existing_key_recovery(clients, database):
    key = str(uuid4())
    payload = {"content": "오늘 행복해", "emojiId": "star"}
    first = await clients["student"].post("/api/submissions", json=payload, headers={"Idempotency-Key": key})
    assert first.status_code == 202, first.text
    heartbeat = await clients["display"].post(
        "/api/display/heartbeat", json={"clientInstanceId": str(uuid4()), "displayProtocolVersion": "2"}
    )
    assert heartbeat.status_code == 200
    recovered = await clients["student"].post("/api/submissions", json=payload, headers={"Idempotency-Key": key})
    assert recovered.status_code == 200 and recovered.json()["id"] == first.json()["id"]
    async with database.connection() as conn:
        await conn.execute("""UPDATE board_runtime SET playback_owner_lease_until=clock_timestamp()-interval '1 second',
            last_heartbeat_at=clock_timestamp()-interval '2 seconds',
            heartbeat_lease_until=clock_timestamp()-interval '1 second'""")
    denied = await clients["stranger"].post("/api/submissions", json=payload, headers={"Idempotency-Key": str(uuid4())})
    assert denied.status_code == 409 and denied.json()["code"] == "CLIENT_UPDATE_REQUIRED"
    async with database.connection() as conn:
        assert (await one(conn, "SELECT count(*) AS n FROM submissions"))["n"] == 1
