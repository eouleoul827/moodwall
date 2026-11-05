"""Text-free reporting and verified operator actions against real PostgreSQL."""

from datetime import timedelta
from uuid import uuid4

import pytest
from test_playback_core import approve, claim_player, command, player, submit

from app.db import db_time, one

pytestmark = pytest.mark.integration


async def report(clients, *, key=None, category="OTHER", submission_id=None):
    body = {"category": category}
    if submission_id:
        body["submissionId"] = str(submission_id)
    return await clients["student"].post("/api/v2/reports", json=body, headers={"Idempotency-Key": str(key or uuid4())})


async def visible_target(clients, system):
    identity = await player(clients)
    identifier = await submit(clients)
    await approve(system, identifier)
    state, identity = await claim_player(clients, identity)
    response = await command(clients, state, identity, "start")
    assert response.status_code == 200, response.text
    return identifier


async def test_reports_are_idempotent_and_do_not_echo_content(clients, database):
    key = uuid4()
    response = await report(clients, key=key, category="PRIVACY")
    assert response.status_code == 202, response.text
    assert set(response.json()) == {"reportId", "status"}
    duplicate = await report(clients, key=key, category="PRIVACY")
    assert duplicate.status_code == 200 and duplicate.json() == response.json()
    conflict = await report(clients, key=key, category="THREAT")
    assert conflict.status_code == 409 and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    async with database.connection() as conn:
        assert (await one(conn, "SELECT count(*) AS n FROM content_reports"))["n"] == 1


async def test_reports_reject_unknown_private_and_nonpublic_targets(clients):
    await player(clients)
    identifier = await submit(clients)
    for target in (identifier, uuid4()):
        response = await report(clients, submission_id=target)
        assert response.status_code == 404
    response = await clients["student"].post(
        "/api/v2/reports", json={"category": "OTHER", "content": "private"}, headers={"Idempotency-Key": str(uuid4())}
    )
    assert response.status_code == 422
    assert "private" not in response.text


async def test_reporting_rate_is_distinct_from_post_quota(clients, database):
    for _ in range(3):
        assert (await report(clients)).status_code == 202
    response = await report(clients)
    assert response.status_code == 429 and response.json()["code"] == "REPORT_RATE_LIMITED"
    async with database.connection() as conn:
        assert (await one(conn, "SELECT count(*) AS n FROM submissions"))["n"] == 0
        assert (await one(conn, "SELECT count(*) AS n FROM daily_usage"))["n"] == 0


async def test_report_listing_requires_operator_and_signed_cursor(clients):
    await report(clients, category="HARASSMENT")
    response = await clients["student"].get("/api/operator/reports")
    assert response.status_code == 401
    response = await clients["admin"].get("/api/operator/reports")
    assert response.status_code == 200, response.text
    assert len(response.json()["items"]) == 1
    item = response.json()["items"][0]
    assert item["submissionId"] is None and item["targetAvailable"] is False
    assert not {"content", "reporterId", "participantId", "reasonCodes"}.intersection(item)
    assert (await clients["admin"].get("/api/operator/reports?cursor=forged")).status_code == 422


async def test_resolve_cannot_claim_an_action_that_did_not_happen(clients, system):
    identifier = await visible_target(clients, system)
    response = await report(clients, submission_id=identifier)
    assert response.status_code == 202, response.text
    report_id = response.json()["reportId"]
    path = f"/api/operator/reports/{report_id}/resolve"
    key = str(uuid4())
    response = await clients["admin"].post(path, json={"resolution": "HIDDEN"}, headers={"Idempotency-Key": key})
    assert response.status_code == 409 and response.json()["code"] == "REPORT_ACTION_REQUIRED"
    hidden = await clients["admin"].post(
        f"/api/operator/submissions/{identifier}/hide", json={"reasonCode": "INAPPROPRIATE_CONTENT"}
    )
    assert hidden.status_code == 200, hidden.text
    response = await clients["admin"].post(path, json={"resolution": "HIDDEN"}, headers={"Idempotency-Key": key})
    assert response.status_code == 200 and response.json()["status"] == "RESOLVED"
    repeated = await clients["admin"].post(path, json={"resolution": "HIDDEN"}, headers={"Idempotency-Key": key})
    assert repeated.status_code == 200 and repeated.json() == response.json()
    conflict = await clients["admin"].post(path, json={"resolution": "NO_ACTION"}, headers={"Idempotency-Key": key})
    assert conflict.status_code == 409


async def test_pause_resolution_requires_actual_pause_and_write_csrf(clients):
    response = await report(clients)
    path = f"/api/operator/reports/{response.json()['reportId']}/resolve"
    key = str(uuid4())
    response = await clients["admin"].post(path, json={"resolution": "BOARD_PAUSED"}, headers={"Idempotency-Key": key})
    assert response.status_code == 409
    assert (
        await clients["admin"].post(
            "/api/operator/board/mode", json={"mode": "PAUSED", "reasonCode": "SECURITY_REVIEW"}
        )
    ).status_code == 200
    response = await clients["admin"].post(path, json={"resolution": "BOARD_PAUSED"}, headers={"Idempotency-Key": key})
    assert response.status_code == 200
    bad_csrf = await clients["admin"].post(
        path, json={"resolution": "NO_ACTION"}, headers={"Idempotency-Key": str(uuid4()), "X-CSRF-Token": "bad"}
    )
    assert bad_csrf.status_code == 403


async def test_report_retention_does_not_extend_source_or_retain_text(clients, system, database):
    response = await report(clients)
    async with database.connection() as conn:
        now = await db_time(conn)
        await conn.execute(
            "UPDATE content_reports SET received_at=%s WHERE id=%s",
            (now - timedelta(days=8), response.json()["reportId"]),
        )
    assert await system.state.reports.cleanup() == 1
    assert (await clients["admin"].get("/api/operator/reports")).json()["items"] == []
