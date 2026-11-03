"""A display tombstone remains actionable after server content retention."""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio

from app.db import db_time, one
from app.domain import business_day, midnight_after, request_fingerprint

pytestmark = pytest.mark.integration
KST = ZoneInfo("Asia/Seoul")


@pytest_asyncio.fixture
async def hide_now(database, monkeypatch):
    # Stay on the real business date while testing both sides of one hour.
    async with database.connection() as conn:
        now = datetime.combine(business_day(await db_time(conn)), time(12), KST)

    async def fixed_time(conn):
        return now

    monkeypatch.setattr("app.operator.db_time", fixed_time)
    return now


async def seed(conn, settings, accepted, *, status="APPROVED", display_status="FLOATING", board_id=None):
    owner = (await one(conn, "INSERT INTO participants DEFAULT VALUES RETURNING id"))["id"]
    day = business_day(accepted)
    await conn.execute("INSERT INTO daily_usage(participant_id,business_date) VALUES(%s,%s)", (owner, day))
    approved = status == "APPROVED"
    return (
        await one(
            conn,
            """INSERT INTO submissions(participant_id,business_date,board_id,idempotency_key,
               request_hash,content,emoji_id,accepted_at,expires_at,moderation_status,finalized_at,
               attempt_count,safety_verdict,emotion,emotion_source,display_status,hidden_at,
               moderation_policy_version,lexicon_version)
               VALUES(%s,%s,%s,%s,%s,'synthetic bubble','heart',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING id""",
            (
                owner,
                day,
                board_id or settings.board_id,
                uuid4(),
                request_fingerprint("synthetic bubble", "heart"),
                accepted,
                min(accepted + timedelta(seconds=120), midnight_after(accepted)),
                status,
                accepted if approved else None,
                int(approved),
                "ALLOW" if approved else None,
                "JOY" if approved else None,
                "MODEL" if approved else None,
                display_status if approved else "NONE",
                accepted if display_status == "HIDDEN" else None,
                settings.moderation_policy_version,
                settings.lexicon_version,
            ),
        )
    )["id"]


async def hide(client, target_id, *, reason="INAPPROPRIATE_CONTENT"):
    return await client.post(
        f"/api/operator/submissions/{target_id}/hide",
        json={"reasonCode": reason},
    )


@pytest.mark.parametrize("removed_from_db", [False, True])
async def test_missing_id_is_hidden_without_restoring_content(clients, database, settings, hide_now, removed_from_db):
    target_id = uuid4()
    async with database.connection() as conn, conn.transaction():
        if removed_from_db:
            target_id = await seed(conn, settings, hide_now - timedelta(minutes=61))
            await conn.execute("DELETE FROM submissions WHERE id=%s", (target_id,))
        before = (await one(conn, "SELECT state_version FROM board_runtime WHERE id=%s", (settings.board_id,)))[
            "state_version"
        ]

    for _ in range(2):
        response = await hide(clients["admin"], target_id)
        assert response.status_code == 200, response.text
        assert response.json()["ok"] is True

    async with database.connection() as conn:
        assert await one(conn, "SELECT id FROM submissions WHERE id=%s", (target_id,)) is None
        audit = await one(
            conn,
            """SELECT count(*) AS n, min(reason_code) AS reason FROM operator_audit_logs
               WHERE board_id=%s AND action='HIDE_SUBMISSION' AND target_id=%s""",
            (settings.board_id, target_id),
        )
        assert audit == {"n": 1, "reason": "INAPPROPRIATE_CONTENT"}
        after = (await one(conn, "SELECT state_version FROM board_runtime WHERE id=%s", (settings.board_id,)))[
            "state_version"
        ]
        assert after == before + 1


@pytest.mark.parametrize("display_status", ["FLOATING", "EXPIRED", "HIDDEN"])
async def test_retained_expired_or_hidden_row_gets_one_tombstone_without_extending_retention(
    clients, database, settings, hide_now, display_status
):
    accepted = hide_now - timedelta(minutes=61)
    async with database.connection() as conn:
        target_id = await seed(conn, settings, accepted, display_status=display_status)
        original = await one(conn, "SELECT accepted_at,expires_at FROM submissions WHERE id=%s", (target_id,))

    for _ in range(2):
        response = await hide(clients["admin"], target_id)
        assert response.status_code == 200, response.text

    async with database.connection() as conn:
        row = await one(conn, "SELECT accepted_at,expires_at,display_status FROM submissions WHERE id=%s", (target_id,))
        assert row["accepted_at"] == original["accepted_at"]
        assert row["expires_at"] == original["expires_at"]
        assert row["display_status"] == ("HIDDEN" if display_status == "HIDDEN" else "EXPIRED")
        assert (
            await one(
                conn,
                "SELECT count(*) AS n FROM operator_audit_logs WHERE action='HIDE_SUBMISSION' AND target_id=%s",
                (target_id,),
            )
        )["n"] == 1


async def test_active_approved_row_still_becomes_hidden(clients, database, settings, hide_now):
    async with database.connection() as conn:
        target_id = await seed(conn, settings, hide_now - timedelta(minutes=1))
    response = await hide(clients["admin"], target_id)
    assert response.status_code == 200, response.text
    async with database.connection() as conn:
        row = await one(conn, "SELECT display_status,hidden_at FROM submissions WHERE id=%s", (target_id,))
        assert row == {"display_status": "HIDDEN", "hidden_at": hide_now}


@pytest.mark.parametrize("invalid_kind", ["yesterday", "unapproved", "another_board"])
async def test_existing_unavailable_row_is_not_converted_to_an_arbitrary_tombstone(
    clients, database, settings, hide_now, invalid_kind
):
    async with database.connection() as conn:
        other_board = uuid4() if invalid_kind == "another_board" else None
        if other_board:
            await conn.execute("INSERT INTO board_runtime(id,name) VALUES(%s,'other test board')", (other_board,))
        target_id = await seed(
            conn,
            settings,
            hide_now - (timedelta(days=1) if invalid_kind == "yesterday" else timedelta(minutes=1)),
            status="PENDING" if invalid_kind == "unapproved" else "APPROVED",
            board_id=other_board,
        )
    response = await hide(clients["admin"], target_id)
    assert response.status_code == (404 if other_board else 409), response.text
    async with database.connection() as conn:
        assert (
            await one(
                conn, "SELECT id FROM operator_audit_logs WHERE action='HIDE_SUBMISSION' AND target_id=%s", (target_id,)
            )
            is None
        )


async def test_concurrent_hide_commands_share_the_board_lock(clients, database, settings, hide_now):
    target_id = uuid4()
    responses = await asyncio.gather(*(hide(clients["admin"], target_id) for _ in range(2)))
    assert [response.status_code for response in responses] == [200, 200]
    async with database.connection() as conn:
        assert (
            await one(
                conn,
                """SELECT count(*) AS n FROM operator_audit_logs
                   WHERE board_id=%s AND action='HIDE_SUBMISSION' AND target_id=%s""",
                (settings.board_id, target_id),
            )
        )["n"] == 1


async def test_missing_id_hide_still_requires_operator_and_fixed_reason(clients, database, hide_now):
    target_id = uuid4()
    response = await hide(clients["student"], target_id)
    assert response.status_code == 401
    response = await hide(clients["admin"], target_id, reason="arbitrary content")
    assert response.status_code == 422
    async with database.connection() as conn:
        assert await one(conn, "SELECT id FROM operator_audit_logs WHERE target_id=%s", (target_id,)) is None


async def test_moderator_can_hide_and_admin_replay_does_not_duplicate_audit(clients, database, hide_now):
    async with database.connection() as conn:
        await conn.execute(
            """INSERT INTO operators(login_name,password_hash,role)
               SELECT 'bubble-moderator',password_hash,'MODERATOR' FROM operators WHERE login_name='admin'"""
        )
    moderator = clients["stranger"]
    login = await moderator.post(
        "/api/operator/login",
        json={"username": "bubble-moderator", "password": "Fixture-operator-passphrase-42!"},
    )
    assert login.status_code == 200, login.text
    moderator.headers["X-CSRF-Token"] = login.json()["csrfToken"]
    target_id = uuid4()
    response = await hide(moderator, target_id)
    assert response.status_code == 200, response.text
    response = await hide(clients["admin"], target_id)
    assert response.status_code == 200, response.text
    async with database.connection() as conn:
        row = await one(
            conn,
            """SELECT count(*) AS n,min(o.login_name) AS login_name FROM operator_audit_logs a
               JOIN operators o ON o.id=a.operator_id
               WHERE a.action='HIDE_SUBMISSION' AND a.target_id=%s""",
            (target_id,),
        )
        assert row == {"n": 1, "login_name": "bubble-moderator"}


async def test_yesterdays_tombstone_does_not_replace_todays_command(clients, database, settings, hide_now):
    target_id = uuid4()
    async with database.connection() as conn:
        await conn.execute(
            """INSERT INTO operator_audit_logs(operator_id,action,board_id,target_id,reason_code,occurred_at)
               SELECT id,'HIDE_SUBMISSION',%s,%s,'OTHER',%s FROM operators WHERE login_name='admin'""",
            (settings.board_id, target_id, hide_now - timedelta(days=1)),
        )
    response = await hide(clients["admin"], target_id)
    assert response.status_code == 200, response.text
    async with database.connection() as conn:
        assert (await one(conn, "SELECT count(*) AS n FROM operator_audit_logs WHERE target_id=%s", (target_id,)))[
            "n"
        ] == 2
