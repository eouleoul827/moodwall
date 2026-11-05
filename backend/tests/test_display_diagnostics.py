"""Diagnostics stay bounded, private and independent of playback authority."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.db import db_time, one
from app.dto import DisplayDiagnostic


def report(**changes):
    return {
        "component": "CACHE", "code": "STORAGE_UNAVAILABLE", "state": "FAILED",
        "version": "1", "count": 1, "occurredAt": datetime.now(UTC).isoformat(), **changes,
    }


@pytest.mark.parametrize("changes", [
    {"component": "secret"}, {"code": "student raw text"}, {"state": "ONLINE"},
    {"version": "secret-value"}, {"count": 0}, {"count": 1001}, {"count": True},
    {"occurredAt": "2026-11-05T12:00:00"}, {"content": "student raw text"},
    {"stack": "cookie-or-key"},
])
def test_diagnostic_only_accepts_allowlisted_bounded_fields(changes):
    with pytest.raises(ValidationError):
        DisplayDiagnostic.model_validate_json(json.dumps(report(**changes)))


@pytest.mark.integration
async def test_device_reports_and_operator_reads_without_playback_mutation(clients, database):
    async with database.connection() as conn:
        before = await one(conn, "SELECT * FROM board_runtime")
    accepted = await clients["display"].post("/api/display/diagnostics", json=report())
    assert accepted.status_code == 200, accepted.text
    assert set(accepted.json()) == {"accepted", "receivedAt"}
    response = await clients["admin"].get("/api/operator/display/diagnostics")
    assert response.status_code == 200
    value = response.json()
    assert value["deviceGeneration"] == str(before["device_token_version"])
    assert value["items"][0]["code"] == "STORAGE_UNAVAILABLE"
    assert value["lastReceivedAt"] == value["items"][0]["receivedAt"]
    assert set(value["items"][0]) == {"component", "code", "state", "version", "count", "occurredAt", "receivedAt"}
    async with database.connection() as conn:
        assert await one(conn, "SELECT * FROM board_runtime") == before
        assert (await one(conn, "SELECT count(*) AS n FROM display_diagnostic_events"))["n"] == 1
        assert (await one(conn, "SELECT count(*) AS n FROM submissions"))["n"] == 0
    assert (await clients["student"].get("/api/operator/display/diagnostics")).status_code == 401
    assert (await clients["student"].post("/api/display/diagnostics", json=report())).status_code == 401


@pytest.mark.integration
async def test_diagnostics_require_csrf_origin_and_small_body(clients):
    client = clients["display"]
    assert (await client.post("/api/display/diagnostics", json=report(), headers={"X-CSRF-Token": "wrong"})).status_code == 403
    assert (await client.post("/api/display/diagnostics", json=report(), headers={"Origin": "https://outside.invalid"})).status_code == 403
    oversized = report(extra="x" * 4096)
    assert (await client.post("/api/display/diagnostics", json=oversized)).status_code == 413
    invalid = await client.post("/api/display/diagnostics", json=report(code="private student text"))
    assert invalid.status_code == 422 and "private student text" not in invalid.text


@pytest.mark.integration
async def test_diagnostic_budget_does_not_consume_display_write_budget(clients, database):
    for _ in range(10):
        response = await clients["display"].post("/api/display/diagnostics", json=report())
        assert response.status_code == 200, response.text
    rejected = await clients["display"].post("/api/display/diagnostics", json=report())
    assert rejected.status_code == 429 and int(rejected.headers["Retry-After"]) >= 1
    async with database.connection() as conn:
        assert (await one(conn, "SELECT count(*) AS n FROM rate_limit_buckets WHERE scope='write:display'"))["n"] == 0


@pytest.mark.integration
async def test_late_failed_report_cannot_replace_a_newer_recovery(clients, database):
    async with database.connection() as conn:
        now = await db_time(conn)
    for changes in ({"state": "RECOVERED", "occurredAt": now.isoformat()},
                    {"state": "FAILED", "occurredAt": (now - timedelta(seconds=30)).isoformat()}):
        response = await clients["display"].post("/api/display/diagnostics", json=report(**changes))
        assert response.status_code == 200
    value = (await clients["admin"].get("/api/operator/display/diagnostics")).json()
    assert value["items"][0]["state"] == "RECOVERED"


@pytest.mark.integration
async def test_expiry_generation_fencing_and_bounded_event_storage(clients, database, system):
    first = await clients["display"].post("/api/display/diagnostics", json=report())
    assert first.status_code == 200
    async with database.connection() as conn:
        await conn.execute("UPDATE display_diagnostic_events SET received_at=received_at-interval '8 days'")
        await conn.execute("UPDATE display_diagnostic_latest SET received_at=received_at-interval '8 days'")
    expired = (await clients["admin"].get("/api/operator/display/diagnostics")).json()
    assert expired["items"] == [] and expired["lastReceivedAt"] is None
    await system.state.diagnostics.cleanup()
    async with database.connection() as conn:
        assert (await one(conn, "SELECT count(*) AS n FROM display_diagnostic_events"))["n"] == 0
        await conn.execute("""INSERT INTO display_diagnostic_events(board_id,device_generation,component,code,state,version,count,occurred_at,received_at)
            SELECT id,device_token_version,'CACHE','STORAGE_UNAVAILABLE','FAILED','1',1,clock_timestamp(),clock_timestamp()
            FROM board_runtime CROSS JOIN generate_series(1,1001)""")
    assert (await clients["display"].post("/api/display/diagnostics", json=report())).status_code == 200
    async with database.connection() as conn:
        assert (await one(conn, "SELECT count(*) AS n FROM display_diagnostic_events"))["n"] == 1000
    revoke = await clients["admin"].post("/api/operator/display/revoke", json={"reasonCode": "MAINTENANCE"})
    assert revoke.status_code == 200, revoke.text
    assert (await clients["display"].post("/api/display/diagnostics", json=report())).status_code == 401
    empty = (await clients["admin"].get("/api/operator/display/diagnostics")).json()
    assert empty["items"] == [] and empty["lastReceivedAt"] is None
