"""Database concurrency and display regressions found during independent review."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from psycopg.errors import LockNotAvailable

from app.db import db_time, one, schema_ready
from app.display import settle_board
from app.domain import business_day, midnight_after
from app.security import hash_token, new_token
from inference.contracts import RuleEvidence, RuleRejected

pytestmark = pytest.mark.integration
KST = ZoneInfo("Asia/Seoul")


async def _student_actor(clients, system, settings):
    token = clients["student"].cookies.get(settings.cookie_name("student"))
    return await system.state.auth.authenticate("student", token)


async def _public_submission(conn, settings, participant_id, accepted, status="FLOATING"):
    day = business_day(accepted)
    await conn.execute(
        "INSERT INTO daily_usage(participant_id,business_date) VALUES(%s,%s) ON CONFLICT DO NOTHING",
        (participant_id, day),
    )
    return await one(
        conn,
        """INSERT INTO submissions(participant_id,business_date,board_id,idempotency_key,
           request_hash,content,emoji_id,accepted_at,expires_at,moderation_status,finalized_at,
           attempt_count,safety_verdict,emotion,emotion_source,display_status,hidden_at,moderation_policy_version,lexicon_version)
           VALUES(%s,%s,%s,%s,%s,'synthetic display review','heart',%s,%s,'APPROVED',%s,
                  1,'ALLOW','NEUTRAL','MODEL',%s,%s,%s,%s) RETURNING id""",
        (
            participant_id,
            day,
            settings.board_id,
            uuid4(),
            bytes(32),
            accepted,
            min(accepted + timedelta(seconds=120), midnight_after(accepted)),
            accepted + timedelta(seconds=1),
            status,
            accepted + timedelta(seconds=2) if status == "HIDDEN" else None,
            settings.moderation_policy_version,
            settings.lexicon_version,
        ),
    )


async def test_board_business_lock_allows_foreign_keys_but_serializes_other_writers(
    clients, system, database, settings
):
    actor = await _student_actor(clients, system, settings)
    async with database.connection() as held, held.transaction():
        await system.state.display.locked_board(held)
        async with database.connection() as conn, conn.transaction():
            await conn.execute("SET LOCAL lock_timeout='100ms'")
            now = await db_time(conn)
            # This real FK check needs KEY SHARE on the already-locked board.
            session = await one(
                conn,
                """INSERT INTO browser_sessions(participant_id,token_hash,created_at,expires_at,
                   admission_board_id,admission_expires_at) VALUES(%s,%s,%s,%s,%s,%s) RETURNING id""",
                (
                    actor.id,
                    hash_token(new_token()),
                    now,
                    now + timedelta(days=30),
                    settings.board_id,
                    now + timedelta(minutes=10),
                ),
            )
            assert session is not None
        with pytest.raises(LockNotAvailable):
            async with database.connection() as conn, conn.transaction():
                await conn.execute(
                    "SELECT id FROM board_runtime WHERE id=%s FOR NO KEY UPDATE NOWAIT", (settings.board_id,)
                )


async def test_student_usage_keeps_one_snapshot_during_concurrent_rule_rejection(
    clients, system, database, settings, monkeypatch
):
    import app.submissions as submissions_module

    receipt = await clients["student"].post(
        "/api/submissions",
        json={"content": "synthetic review", "emojiId": "heart"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert receipt.status_code == 202, receipt.text
    claim = await system.state.moderation.claim(UUID(receipt.json()["id"]))
    assert claim is not None
    original = submissions_module.all_rows
    finalized = False

    async def reject_after_quota_read(conn, query, params=()):
        nonlocal finalized
        rows = await original(conn, query, params)
        if not finalized and "accepted_at>" in query:
            finalized = True
            await system.state.moderation.finalize(
                claim,
                RuleRejected(
                    lexicon_version=settings.lexicon_version,
                    matches=[RuleEvidence(rule_id="review-rule", variant_id="base")],
                ),
            )
        return rows

    monkeypatch.setattr(submissions_module, "all_rows", reject_after_quota_read)
    during = await clients["student"].get("/api/me/usage")
    assert during.status_code == 200, during.text
    assert finalized
    assert (during.json()["usedCount"], during.json()["pendingCount"], during.json()["violationCount"]) == (1, 1, 0)
    after = await clients["student"].get("/api/me/usage")
    assert (after.json()["usedCount"], after.json()["pendingCount"], after.json()["violationCount"]) == (0, 0, 1)
    async with database.connection() as conn:
        row = await one(conn, "SELECT moderation_status,strike_applied FROM submissions WHERE id=%s", (claim["id"],))
        assert row == {"moderation_status": "REJECTED", "strike_applied": True}


async def test_past_day_restore_is_conflict_while_existing_command_still_replays(
    clients, system, database, settings, monkeypatch
):
    actor = await _student_actor(clients, system, settings)
    async with database.connection() as conn:
        now = await db_time(conn)
        day = business_day(now)
        await conn.execute(
            "INSERT INTO daily_usage(participant_id,business_date,violation_count) VALUES(%s,%s,5)",
            (actor.id, day),
        )
    path = f"/api/operator/daily-usage/{actor.id}/{day}/restore"
    key = str(uuid4())
    first = await clients["admin"].post(path, json={"reasonCode": "FALSE_POSITIVE"}, headers={"Idempotency-Key": key})
    assert first.status_code == 200, first.text
    async with database.connection() as conn:
        await conn.execute(
            "UPDATE daily_usage SET violation_count=2 WHERE participant_id=%s AND business_date=%s", (actor.id, day)
        )

    async def tomorrow(conn):
        return now + timedelta(days=1)

    monkeypatch.setattr("app.operator.db_time", tomorrow)
    replay = await clients["admin"].post(path, json={"reasonCode": "FALSE_POSITIVE"}, headers={"Idempotency-Key": key})
    assert replay.status_code == 200 and replay.json()["replayed"] is True, replay.text
    assert replay.json()["auditId"] == first.json()["auditId"]
    fresh = await clients["admin"].post(
        path, json={"reasonCode": "FALSE_POSITIVE"}, headers={"Idempotency-Key": str(uuid4())}
    )
    assert fresh.status_code == 409 and fresh.json()["code"] == "INVALID_STATE", fresh.text
    async with database.connection() as conn:
        daily = await one(
            conn,
            "SELECT violation_count FROM daily_usage WHERE participant_id=%s AND business_date=%s",
            (actor.id, day),
        )
        assert daily["violation_count"] == 2
        audits = await one(conn, "SELECT count(*) AS n FROM operator_audit_logs WHERE action='RESET_RESTRICTION'")
        assert audits["n"] == 1


async def test_settle_does_not_lock_live_floating_bubbles(clients, system, database, settings):
    actor = await _student_actor(clients, system, settings)
    async with database.connection() as conn, conn.transaction():
        day = business_day(await db_time(conn))
        noon = datetime.combine(day, time(12), KST).astimezone(timezone.utc)
        live = await _public_submission(conn, settings, actor.id, noon - timedelta(seconds=2))
        old = await _public_submission(conn, settings, actor.id, noon - timedelta(days=1))
    async with database.connection() as held, held.transaction():
        board = await system.state.display.locked_board(held)
        assert await settle_board(held, board, noon)
        async with database.connection() as conn, conn.transaction():
            assert await one(conn, "SELECT id FROM submissions WHERE id=%s FOR UPDATE NOWAIT", (live["id"],))
        old_state = await one(held, "SELECT display_status FROM submissions WHERE id=%s", (old["id"],))
        assert old_state["display_status"] == "EXPIRED"
        assert (await one(held, "SELECT display_status FROM submissions WHERE id=%s", (live["id"],)))[
            "display_status"
        ] == "FLOATING"


async def test_settle_bounds_old_floating_work_to_100_without_charging_live_content(
    clients, system, database, settings, monkeypatch
):
    actor = await _student_actor(clients, system, settings)
    async with database.connection() as conn, conn.transaction():
        day = business_day(await db_time(conn))
        noon = datetime.combine(day, time(12), KST).astimezone(timezone.utc)
        yesterday = noon - timedelta(days=1)
        last_old = None
        for index in range(101):
            last_old = await _public_submission(conn, settings, actor.id, yesterday + timedelta(milliseconds=index))
        live = await _public_submission(conn, settings, actor.id, noon)

    async def fixed_clock(conn):
        return noon

    monkeypatch.setattr("app.operator.db_time", fixed_clock)
    hidden = await clients["admin"].post(
        f"/api/operator/submissions/{last_old['id']}/hide", json={"reasonCode": "OTHER"}
    )
    assert hidden.status_code == 409 and hidden.json()["code"] == "INVALID_STATE", hidden.text
    async with database.connection() as conn, conn.transaction():
        board = await system.state.display.locked_board(conn)
        assert await settle_board(conn, board, noon)
        assert (
            await one(
                conn,
                "SELECT count(*) AS n FROM submissions WHERE business_date<%s AND display_status='FLOATING'",
                (day,),
            )
        )["n"] == 1
        assert await settle_board(conn, board, noon)
        assert not await settle_board(conn, board, noon)
        assert (await one(conn, "SELECT display_status FROM submissions WHERE id=%s", (live["id"],)))[
            "display_status"
        ] == "FLOATING"


async def test_migrated_head_and_floating_index_are_ready(clients, database, settings):
    async with database.connection() as conn:
        assert await schema_ready(conn, settings.board_id)
        assert (await one(conn, "SELECT version_num FROM alembic_version"))["version_num"] == "0006_playback_receipts"
        index = await one(
            conn,
            """SELECT i.indisvalid,i.indisready FROM pg_index i
               WHERE i.indexrelid=to_regclass('submissions_floating_order_idx')""",
        )
        assert index == {"indisvalid": True, "indisready": True}
