"""HTTP recovery and administration across v1/v2 retention boundaries."""

from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.db import one
from inference.contracts import AnalysisInput, ModelAnalyzed
from inference.fake import FakeAnalyzer

pytestmark = pytest.mark.integration


async def prepared(clients, system):
    instance = str(uuid4())
    heartbeat = await clients["display"].post(
        "/api/display/heartbeat", json={"clientInstanceId": instance, "displayProtocolVersion": "2"}
    )
    assert heartbeat.status_code == 200, heartbeat.text
    identity = {
        "clientInstanceId": instance,
        "deviceGeneration": heartbeat.json()["deviceGeneration"],
        "playbackEpoch": heartbeat.json()["playbackEpoch"],
    }
    response = await clients["student"].post(
        "/api/v2/submissions", json={"content": "오늘 즐거웠어"}, headers={"Idempotency-Key": str(uuid4())}
    )
    assert response.status_code == 202, response.text
    identifier = UUID(response.json()["submissionId"])
    claim = await system.state.moderation.claim(identifier)
    await system.state.moderation.finalize(
        claim, ModelAnalyzed(analysis=FakeAnalyzer().analyze(AnalysisInput(text=claim["content"])))
    )
    response = await clients["display"].post(
        "/api/display/playback/claim", json=identity, headers={"Idempotency-Key": str(uuid4())}
    )
    assert response.status_code == 200, response.text
    identity["playbackEpoch"] = response.json()["playbackEpoch"]
    return identifier, response.json()["attempt"]["attemptId"], identity


async def command(client, attempt_id, identity, action, **extra):
    return await client.post(
        f"/api/display/playback/{attempt_id}/{action}",
        json={**identity, **extra},
        headers={"Idempotency-Key": str(uuid4())},
    )


async def test_active_hide_fences_commands_and_leaves_id_tombstone(clients, system, database):
    identifier, attempt, identity = await prepared(clients, system)
    assert (await command(clients["display"], attempt, identity, "start")).status_code == 200
    assert (await command(clients["display"], attempt, identity, "frame")).status_code == 200
    hidden = await clients["admin"].post(
        f"/api/operator/submissions/{identifier}/hide", json={"reasonCode": "MAINTENANCE"}
    )
    assert hidden.status_code == 200, hidden.text
    receipt = (await clients["student"].get(f"/api/v2/submissions/{identifier}")).json()
    assert receipt["userStatus"] == "HIDDEN" and receipt["contentStatus"] == "PURGED"
    assert (await command(clients["display"], attempt, identity, "complete")).status_code == 409
    state = (await clients["display"].get("/api/v2/display/state")).json()
    assert str(identifier) in state["removedIds"] and state["bubbles"] == []
    async with database.connection() as conn:
        assert await one(conn, "SELECT id FROM submissions WHERE id=%s", (identifier,)) is None
        assert (await one(conn, "SELECT reserved_count FROM board_admission_counters"))["reserved_count"] == 0
        assert (await one(conn, "SELECT quota_status FROM submission_receipts WHERE id=%s", (identifier,)))[
            "quota_status"
        ] == "CONSUMED"


async def test_hide_after_physical_source_purge_updates_receipt(clients, system, monkeypatch):
    identifier, attempt, identity = await prepared(clients, system)
    started = await command(clients["display"], attempt, identity, "start")
    assert started.status_code == 200
    assert (await command(clients["display"], attempt, identity, "frame")).status_code == 200
    first_start = datetime.fromisoformat(started.json()["attempt"]["startAuthorizedAt"])

    async def complete_clock(conn):
        return first_start + timedelta(seconds=18)

    monkeypatch.setattr("app.playback.db_time", complete_clock)
    assert (await command(clients["display"], attempt, identity, "complete")).status_code == 200

    async def purged_clock(conn):
        return first_start + timedelta(hours=1)

    monkeypatch.setattr("app.playback.db_time", purged_clock)
    await system.state.playback.cleanup()
    for _ in range(2):
        response = await clients["admin"].post(
            f"/api/operator/submissions/{identifier}/hide", json={"reasonCode": "MAINTENANCE"}
        )
        assert response.status_code == 200, response.text
    receipt = (await clients["student"].get(f"/api/v2/submissions/{identifier}")).json()
    assert receipt["userStatus"] == "HIDDEN" and receipt["contentStatus"] == "PURGED"


async def test_old_epoch_cannot_control_replay(clients, system):
    identifier, attempt, identity = await prepared(clients, system)
    assert (await command(clients["display"], attempt, identity, "interrupt", reason="RELOAD")).status_code == 200
    response = await clients["display"].post(
        "/api/display/playback/claim", json=identity, headers={"Idempotency-Key": str(uuid4())}
    )
    assert response.status_code == 200, response.text
    new_attempt = response.json()["attempt"]["attemptId"]
    assert new_attempt != attempt and response.json()["playbackEpoch"] != identity["playbackEpoch"]
    stale = await command(clients["display"], attempt, identity, "start")
    assert stale.status_code == 409 and stale.json()["code"] == "PLAYBACK_STALE"
    state = await clients["display"].get(
        "/api/display/playback", params={"clientInstanceId": identity["clientInstanceId"]}
    )
    assert state.json()["attempt"]["attemptId"] == new_attempt
    assert state.json()["attempt"]["submissionId"] == str(identifier)


async def test_v1_and_v2_intents_cannot_reuse_each_others_key(clients, system):
    first_key, second_key = str(uuid4()), str(uuid4())
    v1 = await clients["student"].post(
        "/api/submissions", json={"content": "안녕", "emojiId": "star"}, headers={"Idempotency-Key": first_key}
    )
    assert v1.status_code == 202, v1.text
    # Retained v1 receipts must remain isolated after the device switches to v2;
    # new v1 work is intentionally rejected once that switch has happened.
    assert (
        await clients["display"].post(
            "/api/display/heartbeat", json={"clientInstanceId": str(uuid4()), "displayProtocolVersion": "2"}
        )
    ).status_code == 200
    v2_reuse = await clients["student"].post(
        "/api/v2/submissions", json={"content": "안녕"}, headers={"Idempotency-Key": first_key}
    )
    assert v2_reuse.status_code == 409 and v2_reuse.json()["code"] == "IDEMPOTENCY_CONFLICT"
    v2 = await clients["stranger"].post(
        "/api/v2/submissions", json={"content": "반가워"}, headers={"Idempotency-Key": second_key}
    )
    assert v2.status_code == 202, v2.text
    v1_reuse = await clients["stranger"].post(
        "/api/submissions", json={"content": "반가워", "emojiId": "star"}, headers={"Idempotency-Key": second_key}
    )
    assert v1_reuse.status_code == 409 and v1_reuse.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert (await clients["stranger"].get(f"/api/submissions/{v2.json()['submissionId']}")).status_code == 404


async def test_unknown_enqueue_failure_rolls_back_receipt_source_and_capacity(clients, system, database):
    assert (
        await clients["display"].post(
            "/api/display/heartbeat", json={"clientInstanceId": str(uuid4()), "displayProtocolVersion": "2"}
        )
    ).status_code == 200

    async def broken_enqueue(conn, identifier):
        raise RuntimeError("synthetic queue transport failure")

    system.state.playback.enqueue = broken_enqueue
    key = str(uuid4())
    response = await clients["student"].post(
        "/api/v2/submissions", json={"content": "좋은 하루"}, headers={"Idempotency-Key": key}
    )
    assert response.status_code == 500
    async with database.connection() as conn:
        for table in ("submission_receipts", "submissions", "admission_reservations", "board_admission_counters"):
            assert (await one(conn, f"SELECT count(*) AS n FROM {table}"))["n"] == 0
    assert (
        await clients["student"].post("/api/v2/submission-receipts/lookup", json={"idempotencyKey": key})
    ).status_code == 404


async def test_v2_reads_are_rate_limited_and_do_not_renew_lease(clients, system, database):
    _identifier, _attempt, identity = await prepared(clients, system)
    system.state.settings.read_display_per_minute = 2
    async with database.connection() as conn:
        original = await one(conn, "SELECT playback_owner_lease_until FROM board_runtime")
        await conn.execute("DELETE FROM rate_limit_buckets WHERE scope='read:display'")
    # Other tests verify the limiter's HMAC key; this fresh fixture has made no
    # authenticated display GET yet, so exactly two reads are available here.
    params = {"clientInstanceId": identity["clientInstanceId"]}
    assert (await clients["display"].get("/api/display/playback", params=params)).status_code == 200
    assert (await clients["display"].get("/api/display/playback", params=params)).status_code == 200
    limited = await clients["display"].get("/api/display/playback", params=params)
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) >= 1
    async with database.connection() as conn:
        assert await one(conn, "SELECT playback_owner_lease_until FROM board_runtime") == original
