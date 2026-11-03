from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from app.db import db_time, one
from app.domain import business_day
from inference.contracts import AnalysisInput, ModelAnalyzed, RuleEvidence, RuleRejected
from inference.fake import FakeAnalyzer

pytestmark = pytest.mark.integration


async def submit(client, content="합격 축하해", key=None):
    return await client.post(
        "/api/submissions",
        json={"content": content, "emojiId": "party"},
        headers={"Idempotency-Key": str(key or uuid4())},
    )


async def approve(system, submission_id):
    claim = await system.state.moderation.claim(UUID(submission_id))
    assert claim
    result = FakeAnalyzer().analyze(AnalysisInput(text=claim["content"]))
    await system.state.moderation.finalize(claim, ModelAnalyzed(analysis=result))


async def test_full_api_pipeline_and_private_ownership(clients, system):
    response = await submit(clients["student"])
    assert response.status_code == 202, response.text
    receipt = response.json()
    assert receipt["moderationStatus"] == "PENDING"
    assert response.headers["location"].endswith(receipt["id"])
    assert (await clients["stranger"].get(response.headers["location"])).status_code == 404
    initial = (await clients["display"].get("/api/display/state")).json()
    assert not initial["bubbles"]
    await approve(system, receipt["id"])
    # Approval is deliverable immediately, even before a heartbeat or reconcile.
    immediate = (await clients["display"].get("/api/display/state")).json()
    assert [card["id"] for card in immediate["bubbles"]] == [receipt["id"]]
    assert immediate["bubbleTotalCount"] == 1 and immediate["nextCursor"] is None
    assert immediate["revision"] > initial["revision"]
    heartbeat = await clients["display"].post("/api/display/heartbeat", json={"clientInstanceId": str(uuid4())})
    assert heartbeat.status_code == 200, heartbeat.text
    await system.state.display.reconcile()
    state = await clients["display"].get("/api/display/state")
    assert state.status_code == 200, state.text
    cards = state.json()["bubbles"]
    assert len(cards) == 1 and "slot" not in cards[0]
    assert cards[0]["emotion"] == "CELEBRATION"
    assert "participantId" not in cards[0] and "ruleEvidence" not in cards[0]
    hidden = await clients["admin"].post(
        f"/api/operator/submissions/{receipt['id']}/hide", json={"reasonCode": "OTHER"}
    )
    assert hidden.status_code == 200, hidden.text
    assert not (await clients["display"].get("/api/display/state")).json()["bubbles"]
    usage = (await clients["student"].get("/api/me/usage")).json()
    assert usage["usedCount"] == 1


async def test_parallel_idempotency_and_conflict(clients, database):
    key = uuid4()
    responses = await asyncio.gather(*(submit(clients["student"], key=key) for _ in range(10)))
    assert [r.status_code for r in responses].count(202) == 1, [r.text for r in responses]
    assert all(r.status_code in (200, 202) for r in responses)
    assert len({r.json()["id"] for r in responses}) == 1
    conflict = await submit(clients["student"], "다른 내용", key)
    assert conflict.status_code == 409 and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    async with database.connection() as conn:
        count = await one(conn, "SELECT count(*) AS n FROM submissions")
    assert count["n"] == 1


async def test_job_registration_rolls_back_with_submission(clients, system, database):
    original = system.state.submissions.enqueue
    async with database.connection() as conn:
        before = (await one(conn, "SELECT count(*) AS n FROM procrastinate_jobs"))["n"]

    async def broken(conn, submission_id):
        await original(conn, submission_id)
        raise RuntimeError("synthetic post-enqueue failure")

    system.state.submissions.enqueue = broken
    response = await submit(clients["student"])
    assert response.status_code == 500, response.text
    async with database.connection() as conn:
        assert (await one(conn, "SELECT count(*) AS n FROM submissions"))["n"] == 0
        assert (await one(conn, "SELECT count(*) AS n FROM procrastinate_jobs"))["n"] == before
        assert (await one(conn, "SELECT count(*) AS n FROM participants WHERE last_submission_at IS NOT NULL"))[
            "n"
        ] == 0


async def test_inflight_and_cooldown_for_distinct_requests(clients, system, database):
    responses = await asyncio.gather(*(submit(clients["student"]) for _ in range(4)))
    assert [r.status_code for r in responses].count(202) == 1
    assert all(r.status_code in (202, 409, 503) for r in responses)
    receipt = next(r.json() for r in responses if r.status_code == 202)
    await approve(system, receipt["id"])
    response = await submit(clients["student"])
    assert response.status_code == 429 and response.json()["code"] == "SUBMISSION_COOLDOWN"


async def test_existing_key_survives_expired_admission(clients, database):
    key = uuid4()
    receipt = await submit(clients["student"], key=key)
    async with database.connection() as conn:
        await conn.execute("UPDATE browser_sessions SET admission_expires_at=clock_timestamp()-interval '1 second'")
    replay = await submit(clients["student"], key=key)
    assert replay.status_code == 200 and replay.json()["id"] == receipt.json()["id"]
    new = await submit(clients["student"])
    assert new.status_code == 403 and new.json()["code"] == "ADMISSION_EXPIRED"


async def test_rule_strikes_warn_ban_and_replay_without_double_strike(clients, system, database):
    first_key = uuid4()
    for index in range(5):
        async with database.connection() as conn:
            await conn.execute("UPDATE participants SET last_submission_at=clock_timestamp()-interval '11 seconds'")
        response = await submit(clients["student"], "씨%발", first_key if index == 0 else uuid4())
        assert response.status_code == 202, response.text
        claim = await system.state.moderation.claim(UUID(response.json()["id"]))
        outcome = RuleRejected(
            lexicon_version="lexicon-v1", matches=[RuleEvidence(rule_id="ko-explicit-001", variant_id="separator")]
        )
        await system.state.moderation.finalize(claim, outcome)
        await system.state.moderation.finalize(claim, outcome)
        usage = (await clients["student"].get("/api/me/usage")).json()
        assert usage["violationCount"] == index + 1 and usage["usedCount"] == 0
        assert usage["warning"] == (index >= 2)
    assert usage["suspendedUntil"]
    replay = await submit(clients["student"], "씨%발", first_key)
    assert replay.status_code == 200
    new = await submit(clients["student"])
    assert new.status_code == 403 and new.json()["code"] == "PARTICIPANT_SUSPENDED"
    async with database.connection() as conn:
        rows = await conn.execute("SELECT * FROM submissions WHERE moderation_status='REJECTED'")
        for row in await rows.fetchall():
            assert row["model_bundle_version"] is None and row["rule_evidence"]


async def test_ai_block_does_not_apply_strike(clients, system):
    response = await submit(clients["student"], "[fixture:block]")
    await approve(system, response.json()["id"])
    result = (await clients["student"].get(f"/api/submissions/{response.json()['id']}")).json()
    assert result["moderationStatus"] == "NEEDS_REWRITE"
    usage = (await clients["student"].get("/api/me/usage")).json()
    assert usage["violationCount"] == 0 and usage["usedCount"] == 0


async def test_stale_control_loop_blocks_new_intent_but_preserves_replay(clients, system, settings):
    key = uuid4()
    first = await submit(clients["student"], key=key)
    assert first.status_code == 202
    settings.maintenance_enabled = True
    system.state.loops = {}
    response = await submit(clients["stranger"])
    assert response.status_code == 503 and response.json()["code"] == "SERVICE_UNAVAILABLE"
    replay = await submit(clients["student"], key=key)
    assert replay.status_code == 200 and replay.json()["id"] == first.json()["id"]


async def test_restore_replay_preserves_new_strikes(clients, database):
    async with database.connection() as conn:
        participant = await one(conn, "SELECT participant_id FROM browser_sessions ORDER BY created_at LIMIT 1")
        now = await db_time(conn)
        day = business_day(now)
        await conn.execute(
            """INSERT INTO daily_usage(participant_id,business_date,violation_count,banned_until)
            VALUES(%s,%s,5,%s)""",
            (participant["participant_id"], day, now + timedelta(minutes=10)),
        )
    path = f"/api/operator/daily-usage/{participant['participant_id']}/{day}/restore"
    key = str(uuid4())
    first = await clients["admin"].post(path, json={"reasonCode": "FALSE_POSITIVE"}, headers={"Idempotency-Key": key})
    assert first.status_code == 200, first.text
    assert first.json()["usage"]["violationCount"] == 0
    async with database.connection() as conn:
        await conn.execute(
            "UPDATE daily_usage SET violation_count=2 WHERE participant_id=%s", (participant["participant_id"],)
        )
    again = await clients["admin"].post(path, json={"reasonCode": "FALSE_POSITIVE"}, headers={"Idempotency-Key": key})
    assert again.status_code == 200, again.text
    assert again.json()["replayed"] is True
    assert again.json()["auditId"] == first.json()["auditId"]
    assert again.json()["usage"]["violationCount"] == 2
    changed = await clients["admin"].post(path, json={"reasonCode": "OTHER"}, headers={"Idempotency-Key": key})
    assert changed.status_code == 409


async def test_revoke_fences_old_device_and_forbidden_roles(clients, system, settings):
    token = clients["display"].cookies.get(settings.cookie_name("display"))
    old = await system.state.auth.authenticate("display", token)
    assert (await clients["student"].get("/api/display/qr")).status_code == 401
    response = await clients["admin"].post("/api/operator/display/revoke", json={"reasonCode": "DEVICE_REPLACED"})
    assert response.status_code == 200, response.text
    from app.errors import Problem

    with pytest.raises(Problem) as error:
        await system.state.display.heartbeat(old, uuid4())
    assert error.value.status == 401
    response = await clients["display"].get("/api/display/state")
    assert response.status_code == 401 and response.json()["code"] == "SESSION_EXPIRED"


async def test_csrf_error_and_expired_get_do_not_change_cookies(clients, settings):
    student = clients["student"]
    bad = await student.post(
        "/api/submissions",
        json={"content": "안녕", "emojiId": "party"},
        headers={"X-CSRF-Token": "bad", "Idempotency-Key": str(uuid4())},
    )
    assert bad.status_code == 403 and "set-cookie" not in bad.headers
    assert (await student.get("/api/session")).status_code == 200
    student.cookies.set(settings.cookie_name("student"), "invalid", domain="moodwall.test", path="/")
    stale = await student.get("/api/session")
    assert stale.status_code == 401 and "set-cookie" not in stale.headers


@pytest.mark.parametrize(
    "raw,status",
    [
        (b'{"content":"a","content":"b"}', 400),
        (b'{"a":1e400}', 400),
        (b'{"a":NaN}', 400),
        (b'{"a":"\\ud800"}', 400),
        (b"[]", 422),
        (b'{"a":"' + b"x" * 4100 + b'"}', 413),
        (b"\xff", 400),
    ],
)
async def test_strict_ingress_rejects_without_strikes(clients, raw, status):
    response = await clients["student"].post(
        "/api/submissions", content=raw, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "input" not in response.json() and response.headers["cache-control"] == "no-store"
