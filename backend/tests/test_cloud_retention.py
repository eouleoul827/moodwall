"""Completed-only cap, immutable completion order and no replay resurrection."""

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg import sql
from test_playback_core import approve, claim_player, command, player, submit

from app.db import all_rows, db_time, one
from app.domain import business_day, midnight_after

pytestmark = pytest.mark.integration


async def seed_cloud(conn, settings, now, sequence, *, done=True):
    accepted = now - timedelta(minutes=2)
    participant = (await one(conn, "INSERT INTO participants DEFAULT VALUES RETURNING id"))["id"]
    await conn.execute(
        "INSERT INTO daily_usage(participant_id,business_date) VALUES(%s,%s)",
        (participant, business_day(accepted)),
    )
    identifier = uuid4()
    await conn.execute(
        """INSERT INTO submissions(id,participant_id,board_id,business_date,idempotency_key,request_hash,
        content,emoji_id,accepted_at,expires_at,moderation_status,finalized_at,attempt_count,safety_verdict,
        emotion,emotion_source,display_status,moderation_policy_version,lexicon_version)
        VALUES(%s,%s,%s,%s,%s,%s,'synthetic cloud','star',%s,%s,'APPROVED',%s,1,'ALLOW',
        'NEUTRAL','MODEL',%s,'moderation-v1','lexicon-v1')""",
        (
            identifier,
            participant,
            settings.board_id,
            business_day(accepted),
            uuid4(),
            bytes(32),
            accepted,
            accepted + timedelta(seconds=120),
            accepted + timedelta(seconds=1),
            "FLOATING" if done else "QUEUED",
        ),
    )
    await conn.execute(
        """INSERT INTO submission_receipts(id,participant_id,board_id,business_date,idempotency_key,
        request_hash,accepted_at,receipt_expires_at,pre_display_deadline_at,processing_status,presentation_status,
        quota_status,first_start_authorized_at,content_delete_at,completed_at)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'APPROVED',%s,%s,%s,%s,%s)""",
        (
            identifier,
            participant,
            settings.board_id,
            business_day(accepted),
            uuid4(),
            bytes(32),
            accepted,
            accepted + timedelta(hours=24),
            min(accepted + timedelta(minutes=10), midnight_after(accepted)),
            "DONE" if done else "READY",
            "CONSUMED" if done else "RESERVED",
            accepted if done else None,
            accepted + timedelta(hours=1) if done else None,
            accepted + timedelta(seconds=20 + sequence) if done else None,
        ),
    )
    await conn.execute(
        """INSERT INTO presentation_items(submission_id,board_id,ready_sequence,ready_at,status,
        scene_id,scene_seed,completed_sequence) VALUES(%s,%s,%s,%s,%s,'neutral','0123456789abcdef',%s)""",
        (
            identifier,
            settings.board_id,
            1000 - sequence,
            accepted,
            "DONE" if done else "READY",
            sequence if done else None,
        ),
    )
    return identifier


async def test_thirty_first_completion_purges_old_body_but_preserves_receipt_and_pending_work(
    clients, system, database, settings, monkeypatch
):
    identity = await player(clients)
    old = await submit(clients)
    await approve(system, old)
    state, identity = await claim_player(clients, identity)
    assert (await command(clients, state, identity, "start")).status_code == 200
    assert (await command(clients, state, identity, "frame")).status_code == 200
    async with database.connection() as conn:
        now = await db_time(conn)
    clock = now + timedelta(seconds=16)

    async def controlled_clock(conn):
        return clock

    monkeypatch.setattr("app.playback.db_time", controlled_clock)
    old_key = uuid4()
    completed = await command(clients, state, identity, "complete", old_key)
    assert completed.status_code == 200, completed.text
    assert completed.json()["attempt"]["item"]["completedSequence"] == "1"
    async with database.connection() as conn:
        for sequence in range(2, 31):
            await seed_cloud(conn, settings, now, sequence)
        pending = await seed_cloud(conn, settings, now, 99, done=False)
        await conn.execute("UPDATE board_runtime SET next_completed_sequence=30 WHERE id=%s", (settings.board_id,))
    newest = await submit(clients, who="stranger", content="새로운 글")
    await approve(system, newest)
    next_state, next_identity = await claim_player(clients, identity)
    # The synthetic waiting item's larger READY sequence keeps it waiting.
    assert next_state["attempt"]["submissionId"] == str(newest)
    assert (await command(clients, next_state, next_identity, "start")).status_code == 200
    assert (await command(clients, next_state, next_identity, "frame")).status_code == 200
    clock += timedelta(seconds=16)
    result = await command(clients, next_state, next_identity, "complete")
    assert result.status_code == 200, result.text
    assert result.json()["attempt"]["item"]["completedSequence"] == "31"
    assert result.json()["cacheScope"]["retiredCompletedSequence"] == "1"
    snapshot = (await clients["display"].get("/api/v2/display/state")).json()
    assert snapshot["bubbleTotalCount"] == len(snapshot["bubbles"]) == 30
    assert str(old) not in {row["id"] for row in snapshot["bubbles"]}
    async with database.connection() as conn:
        assert await one(conn, "SELECT id FROM submissions WHERE id=%s", (old,)) is None
        receipt = await one(conn, "SELECT * FROM submission_receipts WHERE id=%s", (old,))
        assert receipt["presentation_status"] == "DONE" and receipt["content_status"] == "PURGED"
        assert receipt["quota_status"] == "CONSUMED"
        assert await one(conn, "SELECT id FROM submissions WHERE id=%s", (pending,))
    replay = await command(clients, state, identity, "complete", old_key)
    assert replay.status_code == 200, replay.text
    assert replay.json()["attempt"]["status"] == "DONE" and replay.json()["attempt"]["item"] is None
    assert replay.json()["cacheScope"]["retiredCompletedSequence"] == "1"


async def test_retirement_ignores_expired_and_uncompleted_rows(database, settings):
    from app.playback import PlaybackService

    async with database.connection() as conn, conn.transaction():
        await conn.execute("INSERT INTO board_runtime(id,name) VALUES(%s,'retention test')", (settings.board_id,))
        now = midnight_after(await db_time(conn)) - timedelta(hours=8)
        await seed_cloud(conn, settings, now - timedelta(minutes=58), 1)
        for sequence in range(2, 32):
            await seed_cloud(conn, settings, now, sequence)
        waiting = await seed_cloud(conn, settings, now, 99, done=False)
        await conn.execute("UPDATE board_runtime SET next_completed_sequence=31 WHERE id=%s", (settings.board_id,))
        service = PlaybackService(None, settings, None)
        board = await service._board(conn)
        assert await service._retire_completed_locked(conn, board, now) == 0
        assert await one(conn, "SELECT id FROM submissions WHERE id=%s", (waiting,))
        assert board["retired_completed_sequence"] == 0


async def test_upgrade_backfills_completion_order_and_retires_only_old_completed_text(database, settings):
    baseline = Path(__file__).parents[2] / "docs/schema.sql"
    # The schema reference keeps migrations in order; isolate this upgrade in a rollback-only schema.
    sql_text = baseline.read_text(encoding="utf-8").split("-- Additive 0010:")[0]
    sql_text = "\n".join(line for line in sql_text.splitlines() if line.strip() not in {"BEGIN;", "COMMIT;"})
    upgrade = Path(__file__).parents[1] / "app/migrations/versions/0010_cloud_retention.sql"
    schema = "cloud_retention_" + uuid4().hex
    async with database.connection() as conn, conn.transaction(force_rollback=True):
        await conn.execute("SET LOCAL statement_timeout='30s'")
        await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        await conn.execute(sql.SQL("SET LOCAL search_path TO {},pg_catalog").format(sql.Identifier(schema)))
        await conn.execute(sql_text, prepare=False)
        await conn.execute("INSERT INTO board_runtime(id,name) VALUES(%s,'upgrade test')", (settings.board_id,))
        # Seed using the new column temporarily, then remove it to reproduce the old head.
        await conn.execute("ALTER TABLE presentation_items ADD COLUMN completed_sequence bigint")
        now = await db_time(conn)
        ids = [await seed_cloud(conn, settings, now, sequence) for sequence in range(1, 32)]
        waiting = await seed_cloud(conn, settings, now, 99, done=False)
        await conn.execute("ALTER TABLE presentation_items DROP COLUMN completed_sequence")
        await conn.execute(upgrade.read_text(encoding="utf-8"), prepare=False)
        board = await one(conn, "SELECT * FROM board_runtime")
        assert board["next_completed_sequence"] == 31 and board["retired_completed_sequence"] == 1
        assert await one(conn, "SELECT id FROM submissions WHERE id=%s", (ids[0],)) is None
        assert await one(conn, "SELECT id FROM submissions WHERE id=%s", (waiting,))
        rows = await all_rows(conn, "SELECT completed_sequence FROM presentation_items ORDER BY completed_sequence")
        assert [row["completed_sequence"] for row in rows] == [*range(1, 32), None]
