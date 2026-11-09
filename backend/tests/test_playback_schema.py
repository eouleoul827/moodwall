"""Exercise v2 PostgreSQL invariants directly, including SQL NULL bypasses."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg import sql
from psycopg.errors import CheckViolation, UniqueViolation

from app.db import one
from app.domain import business_day

pytestmark = pytest.mark.integration
ACCEPTED = datetime(2026, 11, 4, 3, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def playback_graph(database, settings):
    async with database.connection() as conn, conn.transaction(force_rollback=True):
        await conn.execute("INSERT INTO board_runtime(id,name) VALUES(%s,'schema test')", (settings.board_id,))
        participant = (await one(conn, "INSERT INTO participants DEFAULT VALUES RETURNING id"))["id"]
        receipt_id = uuid4()
        await conn.execute(
            """INSERT INTO submission_receipts(id,participant_id,board_id,business_date,idempotency_key,
               request_hash,accepted_at,receipt_expires_at,pre_display_deadline_at)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                receipt_id,
                participant,
                settings.board_id,
                business_day(ACCEPTED),
                uuid4(),
                bytes(32),
                ACCEPTED,
                ACCEPTED + timedelta(hours=24),
                ACCEPTED + timedelta(minutes=10),
            ),
        )
        item = await one(
            conn,
            """INSERT INTO presentation_items(submission_id,board_id,ready_sequence,ready_at,scene_id,scene_seed)
               VALUES(%s,%s,1,%s,'neutral','0123456789abcdef') RETURNING id""",
            (receipt_id, settings.board_id, ACCEPTED),
        )
        attempt = await one(
            conn,
            """INSERT INTO playback_attempts(item_id,board_id,client_instance_id,device_generation,
               playback_epoch,attempt_number,claimed_at,lease_until)
               VALUES(%s,%s,%s,1,1,1,%s,%s) RETURNING id""",
            (item["id"], settings.board_id, uuid4(), ACCEPTED, ACCEPTED + timedelta(seconds=30)),
        )
        yield conn, settings.board_id, participant, receipt_id, item["id"], attempt["id"]


@pytest.mark.parametrize(
    "assignments",
    [
        {"first_start_authorized_at": ACCEPTED, "quota_status": "CONSUMED"},
        {"content_delete_at": ACCEPTED + timedelta(hours=1)},
        {"quota_status": "CONSUMED"},
        {"first_start_authorized_at": ACCEPTED, "content_delete_at": ACCEPTED + timedelta(hours=1)},
        {
            "first_start_authorized_at": ACCEPTED - timedelta(seconds=1),
            "content_delete_at": ACCEPTED + timedelta(hours=1, seconds=-1),
            "quota_status": "CONSUMED",
        },
        {"receipt_expires_at": ACCEPTED + timedelta(hours=23)},
        {"pre_display_deadline_at": ACCEPTED + timedelta(minutes=10, seconds=1)},
        {"pre_display_deadline_at": ACCEPTED},
        {"content_status": "PURGED"},
        {"purged_at": ACCEPTED},
        {
            "first_start_authorized_at": ACCEPTED,
            "content_delete_at": ACCEPTED + timedelta(hours=1),
            "quota_status": "CONSUMED",
            "completed_at": ACCEPTED + timedelta(seconds=4),
        },
    ],
    ids=[
        "start-without-expiry",
        "expiry-without-start",
        "consumed-without-start",
        "start-with-unconsumed-quota",
        "start-before-acceptance",
        "receipt-not-24h",
        "predisplay-over-10m",
        "predisplay-no-positive-time",
        "purged-without-time",
        "available-with-purged-time",
        "completion-before-5s-floor",
    ],
)
async def test_receipt_rejects_inconsistent_lifetime_even_with_null_fields(playback_graph, assignments):
    conn, _board, _participant, receipt, _item, _attempt = playback_graph
    with pytest.raises(CheckViolation):
        async with conn.transaction():
            await conn.execute(
                sql.SQL("UPDATE submission_receipts SET {} WHERE id=%s").format(
                    sql.SQL(",").join(sql.SQL("{}=%s").format(sql.Identifier(field)) for field in assignments)
                ),
                (*assignments.values(), receipt),
            )


async def test_receipt_valid_boundary_keeps_outcome_after_source_purge(playback_graph):
    conn, _board, _participant, receipt, _item, _attempt = playback_graph
    await conn.execute(
        """UPDATE submission_receipts SET first_start_authorized_at=%s,content_delete_at=%s,
           quota_status='CONSUMED',processing_status='APPROVED',presentation_status='DONE',
           completed_at=%s,content_status='PURGED',purged_at=%s WHERE id=%s""",
        (
            ACCEPTED,
            ACCEPTED + timedelta(hours=1),
            ACCEPTED + timedelta(seconds=15),
            ACCEPTED + timedelta(hours=1),
            receipt,
        ),
    )
    row = await one(conn, "SELECT * FROM submission_receipts WHERE id=%s", (receipt,))
    assert row["presentation_status"] == "DONE"
    assert row["content_status"] == "PURGED"
    assert "content" not in row and "emoji_id" not in row


@pytest.mark.parametrize(
    "assignments",
    [
        {"status": "START_AUTHORIZED"},
        {"status": "PLAYING"},
        {"status": "DONE"},
        {"status": "INTERRUPTED", "interrupted_at": ACCEPTED},
        {
            "status": "START_AUTHORIZED",
            "start_authorized_at": ACCEPTED,
            "start_not_after": ACCEPTED + timedelta(seconds=6),
        },
        {
            "status": "PLAYING",
            "start_authorized_at": ACCEPTED,
            "start_not_after": ACCEPTED + timedelta(seconds=5),
            "first_frame_at": ACCEPTED + timedelta(seconds=5),
        },
        {
            "status": "DONE",
            "start_authorized_at": ACCEPTED,
            "start_not_after": ACCEPTED + timedelta(seconds=5),
            "first_frame_at": ACCEPTED,
            "completed_at": ACCEPTED + timedelta(seconds=14),
        },
        {"attempt_number": 3},
        {"device_generation": -1},
        {"playback_epoch": 0},
        {"lease_until": ACCEPTED},
    ],
    ids=[
        "authorization-without-start",
        "playing-without-frame",
        "done-without-completion",
        "interruption-without-reason",
        "authorization-not-5s",
        "frame-on-expired-authorization",
        "completion-before-15s",
        "third-attempt",
        "negative-generation",
        "zero-epoch",
        "no-lease-time",
    ],
)
async def test_attempt_rejects_unacknowledged_or_invalid_state(playback_graph, assignments):
    conn, _board, _participant, _receipt, _item, attempt = playback_graph
    with pytest.raises(CheckViolation):
        async with conn.transaction():
            await conn.execute(
                sql.SQL("UPDATE playback_attempts SET {} WHERE id=%s").format(
                    sql.SQL(",").join(sql.SQL("{}=%s").format(sql.Identifier(field)) for field in assignments)
                ),
                (*assignments.values(), attempt),
            )


async def test_board_has_many_ready_items_but_only_one_active_item(playback_graph):
    conn, board, participant, _receipt, first_item, _attempt = playback_graph
    second_receipt = uuid4()
    await conn.execute(
        """INSERT INTO submission_receipts(id,participant_id,board_id,business_date,idempotency_key,
           request_hash,accepted_at,receipt_expires_at,pre_display_deadline_at)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            second_receipt,
            participant,
            board,
            business_day(ACCEPTED),
            uuid4(),
            bytes(32),
            ACCEPTED,
            ACCEPTED + timedelta(hours=24),
            ACCEPTED + timedelta(minutes=10),
        ),
    )
    second_item = (
        await one(
            conn,
            """INSERT INTO presentation_items(submission_id,board_id,ready_sequence,ready_at,scene_id,scene_seed)
           VALUES(%s,%s,2,%s,'neutral','fedcba9876543210') RETURNING id""",
            (second_receipt, board, ACCEPTED),
        )
    )["id"]
    assert (await one(conn, "SELECT count(*) AS n FROM presentation_items WHERE board_id=%s", (board,)))["n"] == 2
    await conn.execute("UPDATE presentation_items SET status='CLAIMED' WHERE id=%s", (first_item,))
    with pytest.raises(UniqueViolation) as error:
        async with conn.transaction():
            await conn.execute("UPDATE presentation_items SET status='CLAIMED' WHERE id=%s", (second_item,))
    assert error.value.diag.constraint_name == "presentation_one_active_idx"
    await conn.execute("UPDATE presentation_items SET status='DONE' WHERE id=%s", (first_item,))
    await conn.execute("UPDATE presentation_items SET status='CLAIMED' WHERE id=%s", (second_item,))


@pytest.mark.parametrize("count", [-1, 33])
async def test_admission_capacity_is_bounded_even_without_application_checks(playback_graph, count):
    conn, board, *_ = playback_graph
    with pytest.raises(CheckViolation):
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO board_admission_counters(board_id,reserved_count) VALUES(%s,%s)", (board, count)
            )
