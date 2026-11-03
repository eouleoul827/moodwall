"""One-hour mention deletion must not erase quota or daily suspension state."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from app.db import one
from app.domain import business_day, midnight_after
from app.maintenance import RetentionService
from app.moderation import ModerationService
from app.security import hash_token, new_token
from app.submissions import usage_for
from inference.contracts import RunFailed

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 29, 3, 0, tzinfo=timezone.utc)


async def seed(conn, settings, accepted, status="FAILED", owner=None):
    await conn.execute(
        "INSERT INTO board_runtime(id,name) VALUES(%s,'hour retention test') ON CONFLICT DO NOTHING",
        (settings.board_id,),
    )
    if owner is None:
        owner = (await one(conn, "INSERT INTO participants DEFAULT VALUES RETURNING id"))["id"]
    day = business_day(accepted)
    await conn.execute(
        "INSERT INTO daily_usage(participant_id,business_date) VALUES(%s,%s) ON CONFLICT DO NOTHING",
        (owner, day),
    )
    row = await one(
        conn,
        """INSERT INTO submissions(participant_id,business_date,board_id,idempotency_key,
           request_hash,content,emoji_id,accepted_at,expires_at,moderation_policy_version,lexicon_version)
           VALUES(%s,%s,%s,%s,%s,'short lived mention','heart',%s,%s,%s,%s) RETURNING *""",
        (
            owner,
            day,
            settings.board_id,
            uuid4(),
            hash_token(new_token()),
            accepted,
            min(accepted + timedelta(seconds=120), midnight_after(accepted)),
            settings.moderation_policy_version,
            settings.lexicon_version,
        ),
    )
    if status == "PROCESSING":
        row = await one(
            conn,
            """UPDATE submissions SET moderation_status='PROCESSING',attempt_count=1,
               processing_started_at=%s,processing_lease_until=%s,processing_run_token=%s
               WHERE id=%s RETURNING *""",
            (accepted, min(accepted + timedelta(seconds=60), row["expires_at"]), uuid4(), row["id"]),
        )
    elif status != "PENDING":
        approved, rejected = status == "APPROVED", status == "REJECTED"
        row = await one(
            conn,
            """UPDATE submissions SET moderation_status=%s,finalized_at=%s,attempt_count=%s,
               safety_verdict=%s,emotion=%s,emotion_source=%s,display_status=%s,
               strike_applied=%s,rule_evidence=%s WHERE id=%s RETURNING *""",
            (
                status,
                accepted + timedelta(milliseconds=1),
                int(approved),
                "ALLOW" if approved else None,
                "NEUTRAL" if approved else None,
                "MODEL" if approved else None,
                "FLOATING" if approved else "NONE",
                rejected,
                Jsonb([{"rule_id": "test-rule", "variant_id": "base"}] if rejected else []),
                row["id"],
            ),
        )
    return row


@pytest.fixture
def fixed_clock(monkeypatch):
    async def clock(conn):
        return NOW

    monkeypatch.setattr("app.maintenance.db_time", clock)


@pytest.mark.parametrize(
    "status", ["PENDING", "PROCESSING", "APPROVED", "REJECTED", "NEEDS_REWRITE", "FAILED", "EXPIRED", "CANCELLED"]
)
async def test_all_moderation_states_are_physically_deleted_after_hour(database, settings, fixed_clock, status):
    async with database.connection() as conn:
        row = await seed(conn, settings, NOW - timedelta(hours=1), status)
    assert await RetentionService(database, settings).cleanup_submissions() == 1
    async with database.connection() as conn:
        assert await one(conn, "SELECT * FROM submissions WHERE id=%s", (row["id"],)) is None
        assert await one(conn, "SELECT id FROM participants WHERE id=%s", (row["participant_id"],))
        assert await one(
            conn,
            "SELECT 1 FROM daily_usage WHERE participant_id=%s AND business_date=%s",
            (row["participant_id"], row["business_date"]),
        )


async def test_deletion_preserves_today_suspension_and_does_not_change_rolling_quota(database, settings, fixed_clock):
    async with database.connection() as conn:
        row = await seed(conn, settings, NOW - timedelta(hours=1), "APPROVED")
        owner = row["participant_id"]
        await seed(conn, settings, NOW - timedelta(minutes=59), "APPROVED", owner)
        await seed(conn, settings, NOW - timedelta(minutes=1), "APPROVED", owner)
        await conn.execute(
            "UPDATE daily_usage SET violation_count=5,banned_until=%s WHERE participant_id=%s AND business_date=%s",
            (midnight_after(NOW), owner, business_day(NOW)),
        )
        before = await usage_for(conn, owner, NOW)
    assert before["usedCount"] == 2 and before["suspendedUntil"] == midnight_after(NOW)
    assert await RetentionService(database, settings).cleanup_submissions() == 1
    async with database.connection() as conn:
        after = await usage_for(conn, owner, NOW)
        assert after == before
        daily = await one(conn, "SELECT violation_count FROM daily_usage WHERE participant_id=%s", (owner,))
        assert daily["violation_count"] == 5


async def test_mention_retention_uses_age_across_kst_midnight(database, settings, monkeypatch):
    after_midnight = datetime(2026, 10, 29, 15, 15, tzinfo=timezone.utc)  # KST Oct 30, 00:15.

    async def clock(conn):
        return after_midnight

    monkeypatch.setattr("app.maintenance.db_time", clock)
    async with database.connection() as conn:
        older = await seed(conn, settings, after_midnight - timedelta(minutes=75))
        recent = await seed(conn, settings, after_midnight - timedelta(minutes=30))
    assert older["business_date"] == recent["business_date"]
    assert older["business_date"] != business_day(after_midnight)
    assert await RetentionService(database, settings).cleanup_submissions() == 1
    async with database.connection() as conn:
        assert await one(conn, "SELECT id FROM submissions WHERE id=%s", (older["id"],)) is None
        assert await one(conn, "SELECT id FROM submissions WHERE id=%s", (recent["id"],))


async def test_late_worker_cannot_restore_deleted_mention(database, settings, fixed_clock):
    async with database.connection() as conn:
        row = await seed(conn, settings, NOW - timedelta(hours=1), "PROCESSING")
    assert await RetentionService(database, settings).cleanup_submissions() == 1
    moderation = ModerationService(database, settings)
    await moderation.finalize(row, RunFailed(error_code="INFERENCE_TIMEOUT"))
    assert await moderation.claim(row["id"]) is None
    async with database.connection() as conn:
        assert await one(conn, "SELECT id FROM submissions WHERE id=%s", (row["id"],)) is None


async def test_one_hour_deletion_pass_is_bounded(database, settings, fixed_clock):
    async with database.connection() as conn:
        first = await seed(conn, settings, NOW - timedelta(hours=2))
        for _ in range(100):
            await seed(conn, settings, NOW - timedelta(hours=2), owner=first["participant_id"])
    cleanup = RetentionService(database, settings)
    assert await cleanup.cleanup_submissions() == 100
    async with database.connection() as conn:
        assert (await one(conn, "SELECT count(*) AS n FROM submissions"))["n"] == 1
    assert await cleanup.cleanup_submissions() == 1
    assert await cleanup.cleanup_submissions() == 0


async def test_locked_participant_is_skipped_without_blocking_other_deletion(database, settings, fixed_clock):
    async with database.connection() as conn:
        locked = await seed(conn, settings, NOW - timedelta(hours=2))
        available = await seed(conn, settings, NOW - timedelta(hours=2))
    async with database.connection() as blocker, blocker.transaction():
        await one(blocker, "SELECT id FROM participants WHERE id=%s FOR UPDATE", (locked["participant_id"],))
        assert await RetentionService(database, settings).cleanup_submissions() == 1
        async with database.connection() as conn:
            assert await one(conn, "SELECT id FROM submissions WHERE id=%s", (locked["id"],))
            assert await one(conn, "SELECT id FROM submissions WHERE id=%s", (available["id"],)) is None
    assert await RetentionService(database, settings).cleanup_submissions() == 1


async def test_deletion_emits_one_board_revision_hint_per_pass(database, settings, fixed_clock):
    async with database.connection() as conn:
        await seed(conn, settings, NOW - timedelta(hours=2))
        await seed(conn, settings, NOW - timedelta(hours=2))
        before = await one(conn, "SELECT state_version FROM board_runtime WHERE id=%s", (settings.board_id,))
    cleanup = RetentionService(database, settings)
    assert await cleanup.cleanup_submissions() == 2
    async with database.connection() as conn:
        after = await one(conn, "SELECT state_version FROM board_runtime WHERE id=%s", (settings.board_id,))
        assert after["state_version"] == before["state_version"] + 1
    assert await cleanup.cleanup_submissions() == 0
    async with database.connection() as conn:
        assert await one(conn, "SELECT state_version FROM board_runtime WHERE id=%s", (settings.board_id,)) == after


async def test_board_revision_wait_holds_no_participant_lock(database, settings, fixed_clock):
    async with database.connection() as conn:
        row = await seed(conn, settings, NOW - timedelta(hours=2))
    task = None
    try:
        async with database.connection() as blocker, blocker.transaction():
            await one(blocker, "SELECT id FROM board_runtime WHERE id=%s FOR NO KEY UPDATE", (settings.board_id,))
            task = asyncio.create_task(RetentionService(database, settings).cleanup_submissions())
            async with database.connection() as observer, asyncio.timeout(2):
                # A committed deletion is visible while the revision update
                # waits on the independently held board lock.
                while await one(observer, "SELECT id FROM submissions WHERE id=%s", (row["id"],)):
                    if task.done():
                        await task
                        pytest.fail("retention finished before its committed deletion became visible")
                    await asyncio.sleep(0.005)
            assert await one(
                blocker, "SELECT id FROM participants WHERE id=%s FOR UPDATE NOWAIT", (row["participant_id"],)
            )
        assert await task == 1
    finally:
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
