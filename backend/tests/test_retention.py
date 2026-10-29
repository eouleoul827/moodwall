from datetime import timedelta
from uuid import uuid4

import pytest

from app.db import db_time, one
from app.domain import business_day, midnight_after
from app.maintenance import RetentionService
from app.security import hash_password, hash_token, new_token

pytestmark = pytest.mark.integration


async def participant(conn, now):
    return (
        await one(
            conn,
            "INSERT INTO participants(created_at,last_seen_at) VALUES(%s,%s) RETURNING id",
            (now - timedelta(days=60), now - timedelta(days=45)),
        )
    )["id"]


async def submission(conn, settings, participant_id, when, status):
    day = business_day(when)
    await conn.execute(
        "INSERT INTO daily_usage(participant_id,business_date) VALUES(%s,%s) ON CONFLICT DO NOTHING",
        (participant_id, day),
    )
    approved = status == "APPROVED"
    return (
        await one(
            conn,
            """INSERT INTO submissions(participant_id,business_date,board_id,idempotency_key,
           request_hash,content,emoji_id,accepted_at,expires_at,moderation_status,finalized_at,
           attempt_count,safety_verdict,emotion,emotion_source,display_status,
           moderation_policy_version,lexicon_version)
           VALUES(%s,%s,%s,%s,%s,'합성 보관 시험','heart',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           RETURNING id""",
            (
                participant_id,
                day,
                settings.board_id,
                uuid4(),
                hash_token(new_token()),
                when,
                min(when + timedelta(seconds=120), midnight_after(when)),
                status,
                None if status == "PENDING" else when + timedelta(seconds=1),
                int(approved),
                "ALLOW" if approved else None,
                "NEUTRAL" if approved else None,
                "MODEL" if approved else None,
                "FLOATING" if approved else "NONE",
                settings.moderation_policy_version,
                settings.lexicon_version,
            ),
        )
    )["id"]


async def test_cleanup_preserves_recent_mentions_and_operational_metadata(database, settings):
    async with database.connection() as conn, conn.transaction():
        now = await db_time(conn)
        await conn.execute(
            "INSERT INTO board_runtime(id,name) VALUES(%s,'retention test') ON CONFLICT DO NOTHING",
            (settings.board_id,),
        )
        retired = await participant(conn, now)
        active = await participant(conn, now)
        orphan = await participant(conn, now)
        old_id = await submission(conn, settings, retired, now - timedelta(days=31), "FAILED")
        pending_id = await submission(conn, settings, active, now - timedelta(minutes=45), "PENDING")
        recent_id = await submission(conn, settings, active, now - timedelta(minutes=59), "FAILED")
        current_id = await submission(conn, settings, active, now - timedelta(minutes=1), "APPROVED")
        for owner, created, expires in (
            (retired, now - timedelta(days=31), now - timedelta(days=1)),
            (active, now, now + timedelta(days=30)),
        ):
            await conn.execute(
                """INSERT INTO browser_sessions(participant_id,token_hash,created_at,expires_at)
                   VALUES(%s,%s,%s,%s)""",
                (owner, hash_token(new_token()), created, expires),
            )
        operator = (
            await one(
                conn,
                "INSERT INTO operators(login_name,password_hash,role) VALUES(%s,%s,'ADMIN') RETURNING id",
                ("retention-" + uuid4().hex[:12], hash_password(new_token())),
            )
        )["id"]
        audit_ids = {}
        for days in (60, 100):
            audit_ids[days] = (
                await one(
                    conn,
                    """INSERT INTO operator_audit_logs(operator_id,action,board_id,target_id,reason_code,
                   command_key,request_hash,occurred_at)
                   VALUES(%s,'RESET_RESTRICTION',%s,%s,'FALSE_POSITIVE',%s,%s,%s) RETURNING id""",
                    (
                        operator,
                        settings.board_id,
                        retired,
                        uuid4(),
                        hash_token(new_token()),
                        now - timedelta(days=days),
                    ),
                )
            )["id"]
        operator_session_ids = []
        for created, expires in (
            (now - timedelta(hours=9), now - timedelta(hours=1)),
            (now, now + timedelta(hours=8)),
        ):
            operator_session_ids.append(
                (
                    await one(
                        conn,
                        """INSERT INTO operator_sessions(operator_id,token_hash,created_at,last_seen_at,expires_at)
                   VALUES(%s,%s,%s,%s,%s) RETURNING id""",
                        (operator, hash_token(new_token()), created, created, expires),
                    )
                )["id"]
            )
        for scope, expiry in (
            ("retention-expired", now - timedelta(seconds=1)),
            ("retention-active", now + timedelta(minutes=1)),
        ):
            await conn.execute(
                """INSERT INTO rate_limit_buckets(scope,subject_hash,window_start,hits,expires_at)
                   VALUES(%s,%s,%s,1,%s)""",
                (scope, hash_token(new_token()), now - timedelta(hours=1), expiry),
            )
        await conn.execute(
            "UPDATE board_runtime SET pending_pairing_hash=%s,pending_pairing_expires_at=%s WHERE id=%s",
            (hash_token(new_token()), now - timedelta(seconds=1), settings.board_id),
        )

    cleanup = RetentionService(database, settings)
    assert await cleanup.cleanup() > 0
    async with database.connection() as conn:
        assert await one(conn, "SELECT id FROM submissions WHERE id=%s", (old_id,)) is None
        for retained in (pending_id, recent_id, current_id):
            assert await one(conn, "SELECT id FROM submissions WHERE id=%s", (retained,))
        for deleted in (retired, orphan):
            assert await one(conn, "SELECT id FROM participants WHERE id=%s", (deleted,)) is None
        assert await one(conn, "SELECT id FROM participants WHERE id=%s", (active,))
        assert await one(conn, "SELECT 1 FROM daily_usage WHERE participant_id=%s", (retired,)) is None
        assert await one(
            conn,
            "SELECT 1 FROM daily_usage WHERE participant_id=%s AND business_date=%s",
            (active, business_day(now - timedelta(minutes=45))),
        )
        assert await one(conn, "SELECT id FROM operator_audit_logs WHERE id=%s", (audit_ids[60],))
        assert await one(conn, "SELECT id FROM operator_audit_logs WHERE id=%s", (audit_ids[100],)) is None
        assert await one(conn, "SELECT id FROM operators WHERE id=%s", (operator,))
        assert await one(conn, "SELECT id FROM operator_sessions WHERE id=%s", (operator_session_ids[0],)) is None
        assert await one(conn, "SELECT id FROM operator_sessions WHERE id=%s", (operator_session_ids[1],))
        assert await one(conn, "SELECT 1 FROM rate_limit_buckets WHERE scope='retention-expired'") is None
        assert await one(conn, "SELECT 1 FROM rate_limit_buckets WHERE scope='retention-active'")
        board = await one(
            conn,
            "SELECT pending_pairing_hash,pending_pairing_expires_at FROM board_runtime WHERE id=%s",
            (settings.board_id,),
        )
        assert board == {"pending_pairing_hash": None, "pending_pairing_expires_at": None}
    assert await cleanup.cleanup() == 0


async def test_one_cleanup_pass_is_bounded(database, settings):
    async with database.connection() as conn:
        await conn.execute(
            """INSERT INTO rate_limit_buckets(scope,subject_hash,window_start,hits,expires_at)
               SELECT 'retention-batch',decode(md5(g::text)||md5('retention'||g::text),'hex'),
                 clock_timestamp()-interval '2 hours',1,clock_timestamp()-interval '1 hour'
               FROM generate_series(1,101) g"""
        )
    cleanup = RetentionService(database, settings)
    await cleanup.cleanup()
    async with database.connection() as conn:
        row = await one(conn, "SELECT count(*) AS remaining FROM rate_limit_buckets WHERE scope='retention-batch'")
        assert row["remaining"] == 1
    await cleanup.cleanup()
    async with database.connection() as conn:
        row = await one(conn, "SELECT count(*) AS remaining FROM rate_limit_buckets WHERE scope='retention-batch'")
        assert row["remaining"] == 0


async def test_exact_hour_boundary_deletes_the_mention(database, settings, monkeypatch):
    async with database.connection() as conn, conn.transaction():
        now = await db_time(conn)
        await conn.execute(
            "INSERT INTO board_runtime(id,name) VALUES(%s,'retention test') ON CONFLICT DO NOTHING",
            (settings.board_id,),
        )
        owner = await participant(conn, now)
        boundary = await submission(conn, settings, owner, now - timedelta(hours=1), "FAILED")
        older = await submission(conn, settings, owner, now - timedelta(hours=1, microseconds=1), "FAILED")
        newer = await submission(conn, settings, owner, now - timedelta(hours=1) + timedelta(microseconds=1), "FAILED")

    async def fixed_clock(conn):
        return now

    # Only the clock is fixed: real SQL, constraints, transactions and FK checks run.
    monkeypatch.setattr("app.maintenance.db_time", fixed_clock)
    await RetentionService(database, settings).cleanup_submissions()
    async with database.connection() as conn:
        assert await one(conn, "SELECT id FROM submissions WHERE id=%s", (boundary,)) is None
        assert await one(conn, "SELECT id FROM submissions WHERE id=%s", (older,)) is None
        assert await one(conn, "SELECT id FROM submissions WHERE id=%s", (newer,))
