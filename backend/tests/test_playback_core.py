"""V2 publication, fencing and durable outcomes against real PostgreSQL."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.db import db_time, one
from inference.contracts import AnalysisInput, ModelAnalyzed, RuleEvidence, RuleRejected
from inference.fake import FakeAnalyzer

pytestmark = pytest.mark.integration


async def player(clients):
    instance = str(uuid4())
    response = await clients["display"].post(
        "/api/display/heartbeat", json={"clientInstanceId": instance, "displayProtocolVersion": "2"}
    )
    assert response.status_code == 200, response.text
    state = response.json()
    return {
        "clientInstanceId": instance,
        "deviceGeneration": state["deviceGeneration"],
        "playbackEpoch": state["playbackEpoch"],
    }


async def submit(clients, *, who="student", content="오늘 행복해", key=None):
    response = await clients[who].post(
        "/api/v2/submissions", json={"content": content}, headers={"Idempotency-Key": str(key or uuid4())}
    )
    assert response.status_code == 202, response.text
    assert "content" not in response.json()
    return UUID(response.json()["submissionId"])


async def approve(system, identifier):
    claim = await system.state.moderation.claim(identifier)
    assert claim
    await system.state.moderation.finalize(
        claim, ModelAnalyzed(analysis=FakeAnalyzer().analyze(AnalysisInput(text=claim["content"])))
    )
    return claim


async def claim_player(clients, identity, key=None):
    response = await clients["display"].post(
        "/api/display/playback/claim", json=identity, headers={"Idempotency-Key": str(key or uuid4())}
    )
    assert response.status_code == 200, response.text
    state = response.json()
    identity = {**identity, "playbackEpoch": state["playbackEpoch"]}
    return state, identity


async def command(clients, state, identity, action, key=None, **extra):
    return await clients["display"].post(
        f"/api/display/playback/{state['attempt']['attemptId']}/{action}",
        json={**identity, **extra},
        headers={"Idempotency-Key": str(key or uuid4())},
    )


async def test_v2_acceptance_is_durable_and_owner_scoped(clients, system, database):
    await player(clients)
    key = uuid4()
    identifier = await submit(clients, key=key)
    duplicate = await clients["student"].post(
        "/api/v2/submissions", json={"content": "오늘 행복해"}, headers={"Idempotency-Key": str(key)}
    )
    assert duplicate.status_code == 200 and duplicate.json()["submissionId"] == str(identifier)
    lookup = await clients["student"].post("/api/v2/submission-receipts/lookup", json={"idempotencyKey": str(key)})
    assert lookup.status_code == 200
    assert (await clients["stranger"].get(f"/api/v2/submissions/{identifier}")).status_code == 404
    conflict = await clients["student"].post(
        "/api/v2/submissions", json={"content": "다른 글"}, headers={"Idempotency-Key": str(key)}
    )
    assert conflict.status_code == 409
    async with database.connection() as conn:
        assert (await one(conn, "SELECT reserved_count FROM board_admission_counters"))["reserved_count"] == 1
        assert (await one(conn, "SELECT count(*) AS n FROM submission_receipts"))["n"] == 1


async def test_ready_order_follows_committed_preparation_not_acceptance(clients, system):
    identity = await player(clients)
    a = await submit(clients)
    b = await submit(clients, who="stranger")
    await approve(system, b)
    await approve(system, a)
    state, identity = await claim_player(clients, identity)
    assert state["attempt"]["submissionId"] == str(b)
    assert state["attempt"]["item"]["readySequence"] == "1"
    blocked = await clients["display"].post(
        "/api/display/playback/claim", json=identity, headers={"Idempotency-Key": str(uuid4())}
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "PLAYBACK_NOT_READY"


async def test_full_fifteen_seconds_and_same_complete_key_recovers(clients, system, database, monkeypatch):
    identity = await player(clients)
    identifier = await submit(clients)
    await approve(system, identifier)
    assert (await clients["display"].get("/api/v2/display/state")).json()["bubbles"] == []
    state, identity = await claim_player(clients, identity)
    started = await command(clients, state, identity, "start")
    assert started.status_code == 200, started.text
    framed = await command(clients, state, identity, "frame")
    assert framed.status_code == 200, framed.text
    key = uuid4()
    early = await command(clients, state, identity, "complete", key)
    assert early.status_code == 409 and early.json()["code"] == "PLAYBACK_TOO_EARLY"
    async with database.connection() as conn:
        now = await db_time(conn)

    async def after_fifteen(conn):
        return now + timedelta(seconds=16)

    monkeypatch.setattr("app.playback.db_time", after_fifteen)
    complete = await command(clients, state, identity, "complete", key)
    assert complete.status_code == 200, complete.text
    assert complete.json()["attempt"]["status"] == "DONE"
    duplicate = await command(clients, state, identity, "complete", key)
    assert duplicate.status_code == 200 and duplicate.json()["attempt"]["status"] == "DONE"
    receipt = (await clients["student"].get(f"/api/v2/submissions/{identifier}")).json()
    assert receipt["userStatus"] == "SHOWN"
    bubbles = (await clients["display"].get("/api/v2/display/state")).json()["bubbles"]
    assert [row["id"] for row in bubbles] == [str(identifier)]
    assert bubbles[0]["contentDeleteAt"] == started.json()["attempt"]["item"]["contentDeleteAt"]
    async with database.connection() as conn:
        assert (await one(conn, "SELECT reserved_count FROM board_admission_counters"))["reserved_count"] == 0


async def test_start_retry_does_not_extend_authorization_or_retention(clients, system, database, monkeypatch):
    identity = await player(clients)
    identifier = await submit(clients)
    await approve(system, identifier)
    state, identity = await claim_player(clients, identity)
    key = uuid4()
    first = await command(clients, state, identity, "start", key)
    assert first.status_code == 200, first.text
    async with database.connection() as conn:
        now = await db_time(conn)

    async def later(conn):
        return now + timedelta(seconds=6)

    monkeypatch.setattr("app.playback.db_time", later)
    again = await command(clients, state, identity, "start", key)
    assert again.status_code == 200, again.text
    assert again.json()["attempt"]["startNotAfter"] == first.json()["attempt"]["startNotAfter"]
    assert again.json()["attempt"]["item"]["contentDeleteAt"] == first.json()["attempt"]["item"]["contentDeleteAt"]
    assert again.json()["serverNow"] != first.json()["serverNow"]
    late_frame = await command(clients, state, identity, "frame")
    assert late_frame.status_code == 409


async def test_owner_cannot_be_taken_by_second_tab(clients):
    await player(clients)
    second = await clients["display"].post(
        "/api/display/heartbeat", json={"clientInstanceId": str(uuid4()), "displayProtocolVersion": "2"}
    )
    assert second.status_code == 409 and second.json()["code"] == "PLAYBACK_OWNED"


async def test_interrupted_item_replays_once_then_has_uncertain_outcome(clients, system):
    identity = await player(clients)
    identifier = await submit(clients)
    await approve(system, identifier)
    first, identity = await claim_player(clients, identity)
    assert (await command(clients, first, identity, "start")).status_code == 200
    interrupted = await command(clients, first, identity, "interrupt", reason="PAGE_HIDDEN")
    assert interrupted.status_code == 200, interrupted.text
    second, identity = await claim_player(clients, identity)
    assert second["attempt"]["attemptNumber"] == 2
    assert second["attempt"]["submissionId"] == str(identifier)
    assert (await command(clients, second, identity, "start")).status_code == 200
    ended = await command(clients, second, identity, "interrupt", reason="RELOAD")
    assert ended.status_code == 200, ended.text
    receipt = (await clients["student"].get(f"/api/v2/submissions/{identifier}")).json()
    assert receipt["userStatus"] == "PLAYBACK_UNCONFIRMED"
    assert receipt["contentStatus"] == "PURGED"


async def test_rule_rejection_purges_source_but_preserves_receipt(clients, system, database):
    await player(clients)
    identifier = await submit(clients)
    claim = await system.state.moderation.claim(identifier)
    await system.state.moderation.finalize(
        claim,
        RuleRejected(
            lexicon_version=claim["lexicon_version"], matches=[RuleEvidence(rule_id="synthetic", variant_id="exact")]
        ),
    )
    receipt = await clients["student"].get(f"/api/v2/submissions/{identifier}")
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["userStatus"] == "NEEDS_REWRITE"
    assert receipt.json()["contentStatus"] == "PURGED"
    async with database.connection() as conn:
        assert await one(conn, "SELECT id FROM submissions WHERE id=%s", (identifier,)) is None
        assert (await one(conn, "SELECT reserved_count FROM board_admission_counters"))["reserved_count"] == 0


async def test_v2_rejects_legacy_only_player(clients):
    assert (
        await clients["display"].post("/api/display/heartbeat", json={"clientInstanceId": str(uuid4())})
    ).status_code == 200
    response = await clients["student"].post(
        "/api/v2/submissions", json={"content": "행복해"}, headers={"Idempotency-Key": str(uuid4())}
    )
    assert response.status_code == 503 and response.json()["code"] == "DISPLAY_OFFLINE"


async def test_concurrent_last_capacity_slot_is_atomic(clients, system, database, settings):
    await player(clients)
    system.state.playback.settings = SimpleNamespace(**{**settings.model_dump(), "playback_capacity_limit": 1})

    async def send(who):
        return await clients[who].post(
            "/api/v2/submissions", json={"content": "오늘 행복해"}, headers={"Idempotency-Key": str(uuid4())}
        )

    responses = await asyncio.gather(send("student"), send("stranger"))
    assert sorted(response.status_code for response in responses) == [202, 503]
    rejected = next(response for response in responses if response.status_code == 503)
    assert rejected.json()["code"] == "DISPLAY_QUEUE_FULL"
    async with database.connection() as conn:
        assert (await one(conn, "SELECT reserved_count FROM board_admission_counters"))["reserved_count"] == 1
        assert (await one(conn, "SELECT count(*) AS n FROM submission_receipts"))["n"] == 1
        assert (await one(conn, "SELECT count(*) AS n FROM submissions"))["n"] == 1


async def test_original_source_ttl_is_anchored_to_first_start_and_receipt_survives(
    clients, system, database, monkeypatch
):
    identity = await player(clients)
    key = uuid4()
    identifier = await submit(clients, key=key)
    await approve(system, identifier)
    state, identity = await claim_player(clients, identity)
    started = await command(clients, state, identity, "start")
    assert started.status_code == 200
    assert (await command(clients, state, identity, "frame")).status_code == 200
    first_start = datetime.fromisoformat(started.json()["attempt"]["item"]["firstStartAuthorizedAt"])
    async with database.connection() as conn:
        initial = await one(
            conn, "SELECT accepted_at,content_delete_at FROM submission_receipts WHERE id=%s", (identifier,)
        )
    assert initial["content_delete_at"] == first_start + timedelta(hours=1)

    async def completed_clock(conn):
        return first_start + timedelta(seconds=17)

    monkeypatch.setattr("app.playback.db_time", completed_clock)
    completed = await command(clients, state, identity, "complete")
    assert completed.status_code == 200, completed.text

    async def expiry_clock(conn):
        return first_start + timedelta(hours=1)

    monkeypatch.setattr("app.playback.db_time", expiry_clock)
    assert (await clients["display"].get("/api/v2/display/state")).json()["bubbles"] == []
    before_cleanup = (await clients["student"].get(f"/api/v2/submissions/{identifier}")).json()
    assert before_cleanup["contentStatus"] == "PURGED" and before_cleanup["userStatus"] == "SHOWN"
    await system.state.playback.cleanup()
    async with database.connection() as conn:
        assert await one(conn, "SELECT id FROM submissions WHERE id=%s", (identifier,)) is None
    replay = await clients["student"].post(
        "/api/v2/submissions", json={"content": "오늘 행복해"}, headers={"Idempotency-Key": str(key)}
    )
    assert replay.status_code == 200
    assert replay.json()["submissionId"] == str(identifier)
    assert replay.json()["userStatus"] == "SHOWN" and replay.json()["contentStatus"] == "PURGED"


async def test_hidden_ready_item_is_never_claimed_and_returns_reservation(clients, system, database):
    identity = await player(clients)
    identifier = await submit(clients)
    await approve(system, identifier)
    async with database.connection() as conn:
        await conn.execute(
            "UPDATE submissions SET display_status='HIDDEN',hidden_at=clock_timestamp() WHERE id=%s", (identifier,)
        )
    await system.state.playback.sync_submission(identifier)
    state = await clients["display"].post(
        "/api/display/playback/claim", json=identity, headers={"Idempotency-Key": str(uuid4())}
    )
    assert state.status_code == 204
    receipt = (await clients["student"].get(f"/api/v2/submissions/{identifier}")).json()
    assert receipt["userStatus"] == "HIDDEN" and receipt["contentStatus"] == "PURGED"
    await system.state.playback.release_reservations()
    await system.state.playback.release_reservations()
    async with database.connection() as conn:
        assert (await one(conn, "SELECT reserved_count FROM board_admission_counters"))["reserved_count"] == 0


async def test_late_preparation_cannot_resurrect_expired_item(clients, system, database, monkeypatch):
    await player(clients)
    identifier = await submit(clients)
    async with database.connection() as conn:
        receipt = await one(conn, "SELECT * FROM submission_receipts WHERE id=%s", (identifier,))

    async def expired_clock(conn):
        return receipt["pre_display_deadline_at"]

    monkeypatch.setattr("app.playback.db_time", expired_clock)
    await system.state.playback.reconcile()
    await system.state.playback.sync_submission(identifier)
    receipt_response = (await clients["student"].get(f"/api/v2/submissions/{identifier}")).json()
    assert receipt_response["userStatus"] == "EXPIRED" and receipt_response["contentStatus"] == "PURGED"
    async with database.connection() as conn:
        assert await one(conn, "SELECT id FROM submissions WHERE id=%s", (identifier,)) is None
        assert (await one(conn, "SELECT reserved_count FROM board_admission_counters"))["reserved_count"] == 0


async def test_live_heartbeat_without_completion_is_bounded_and_never_marks_shown(
    clients, system, database, monkeypatch
):
    identity = await player(clients)
    identifier = await submit(clients)
    await approve(system, identifier)
    state, identity = await claim_player(clients, identity)
    started = await command(clients, state, identity, "start")
    assert started.status_code == 200, started.text
    assert (await command(clients, state, identity, "frame")).status_code == 200
    first = datetime.fromisoformat(started.json()["attempt"]["startAuthorizedAt"])
    clock = first

    async def controlled_clock(conn):
        return clock

    monkeypatch.setattr("app.playback.db_time", controlled_clock)
    for elapsed in (10, 20, 30, 40):
        clock = first + timedelta(seconds=elapsed)
        heartbeat = await clients["display"].post(
            "/api/display/heartbeat",
            json={"clientInstanceId": identity["clientInstanceId"], "displayProtocolVersion": "2"},
        )
        assert heartbeat.status_code == 200, heartbeat.text
        lease = datetime.fromisoformat(heartbeat.json()["attempt"]["leaseUntil"])
        assert lease <= first + timedelta(seconds=45)
    clock = first + timedelta(seconds=46)
    await system.state.playback.reconcile()
    receipt = (await clients["student"].get(f"/api/v2/submissions/{identifier}")).json()
    assert receipt["presentationStatus"] == "INTERRUPTED"
    assert receipt["userStatus"] == "WAITING" and receipt["completedAt"] is None
    replay, identity = await claim_player(clients, identity)
    assert replay["attempt"]["attemptNumber"] == 2
    async with database.connection() as conn:
        row = await one(
            conn, "SELECT first_start_authorized_at,quota_status FROM submission_receipts WHERE id=%s", (identifier,)
        )
    assert row["first_start_authorized_at"] == first and row["quota_status"] == "CONSUMED"


async def test_completed_command_replay_cannot_revive_a_hidden_bubble(clients, system, database, monkeypatch):
    identity = await player(clients)
    identifier = await submit(clients)
    await approve(system, identifier)
    state, identity = await claim_player(clients, identity)
    assert (await command(clients, state, identity, "start")).status_code == 200
    assert (await command(clients, state, identity, "frame")).status_code == 200
    async with database.connection() as conn:
        now = await db_time(conn)

    async def completed_clock(conn):
        return now + timedelta(seconds=17)

    monkeypatch.setattr("app.playback.db_time", completed_clock)
    complete_key = uuid4()
    assert (await command(clients, state, identity, "complete", complete_key)).status_code == 200
    hidden = await clients["admin"].post(f"/api/operator/submissions/{identifier}/hide", json={"reasonCode": "OTHER"})
    assert hidden.status_code == 200, hidden.text
    replay = await command(clients, state, identity, "complete", complete_key)
    assert replay.status_code == 200, replay.text
    assert replay.json()["attempt"]["status"] == "DONE"
    assert replay.json()["attempt"]["presentationStatus"] == "HIDDEN"
    assert replay.json()["attempt"]["item"] is None
    assert (await clients["display"].get("/api/v2/display/state")).json()["bubbles"] == []


@pytest.mark.parametrize(
    "who,path,temporary_mode",
    [("admin", "/api/operator/board/mode", "PAUSED"), ("display", "/api/display/mode", "CHATBOT")],
)
async def test_rapid_mode_resume_cannot_erase_an_active_playback_interruption(
    clients, system, who, path, temporary_mode
):
    identity = await player(clients)
    identifier = await submit(clients)
    await approve(system, identifier)
    state, identity = await claim_player(clients, identity)
    assert (await command(clients, state, identity, "start")).status_code == 200
    assert (await command(clients, state, identity, "frame")).status_code == 200
    for mode in (temporary_mode, "MOODWALL"):
        body = {"mode": mode}
        if who == "admin":
            body["reasonCode"] = "MAINTENANCE"
        response = await clients[who].post(path, json=body)
        assert response.status_code == 200, response.text
    # Maintenance is disabled by the fixture: only the atomic mode mutation
    # could have observed the short interruption.
    receipt = (await clients["student"].get(f"/api/v2/submissions/{identifier}")).json()
    assert receipt["presentationStatus"] == "INTERRUPTED"
    assert receipt["completedAt"] is None
    stale = await command(clients, state, identity, "complete")
    assert stale.status_code == 409 and stale.json()["code"] == "PLAYBACK_STALE"
    replay, identity = await claim_player(clients, identity)
    assert replay["attempt"]["attemptNumber"] == 2
