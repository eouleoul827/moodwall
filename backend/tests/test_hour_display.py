"""Floating delivery is immediate and independent of a display lease or capacity."""

from datetime import timedelta, timezone
from uuid import uuid4

import pytest

from app.db import db_time, one
from app.domain import MENTION_RETENTION_SECONDS, business_day, midnight_after
from app.errors import Problem

pytestmark = pytest.mark.integration
UTC = timezone.utc


async def seed_card(conn, settings, participant_id, accepted, status="FLOATING", *, finalized=None):
    day = business_day(accepted)
    await conn.execute(
        "INSERT INTO daily_usage(participant_id,business_date) VALUES(%s,%s) ON CONFLICT DO NOTHING",
        (participant_id, day),
    )
    return (
        await one(
            conn,
            """INSERT INTO submissions(participant_id,business_date,board_id,idempotency_key,
        request_hash,content,emoji_id,accepted_at,expires_at,moderation_status,finalized_at,
        attempt_count,safety_verdict,emotion,emotion_source,display_status,hidden_at,moderation_policy_version,lexicon_version)
        VALUES(%s,%s,%s,%s,%s,'synthetic floating bubble','heart',%s,%s,'APPROVED',%s,
        1,'ALLOW','NEUTRAL','MODEL',%s,%s,%s,%s) RETURNING id""",
            (
                participant_id,
                day,
                settings.board_id,
                uuid4(),
                bytes(32),
                accepted,
                min(accepted + timedelta(seconds=120), midnight_after(accepted)),
                finalized or accepted,
                status,
                accepted if status == "HIDDEN" else None,
                settings.moderation_policy_version,
                settings.lexicon_version,
            ),
        )
    )["id"]


async def owner(clients, system, settings):
    token = clients["student"].cookies.get(settings.cookie_name("student"))
    return (await system.state.auth.authenticate("student", token)).id


async def test_snapshot_excludes_expired_hidden_and_previous_day_before_cleanup(clients, system, database, settings):
    participant = await owner(clients, system, settings)
    async with database.connection() as conn, conn.transaction():
        now = (await db_time(conn)).replace(hour=3, minute=0, second=0, microsecond=0)
        expired = await seed_card(conn, settings, participant, now - timedelta(hours=1))
        live = await seed_card(conn, settings, participant, now - timedelta(minutes=59))
        hidden = await seed_card(conn, settings, participant, now - timedelta(minutes=1), "HIDDEN")
        previous = await seed_card(conn, settings, participant, now - timedelta(days=1))
        state = await system.state.display._snapshot(conn, now)
        assert (await one(conn, "SELECT count(*) AS n FROM submissions"))["n"] == 4
    assert [card["id"] for card in state["bubbles"]] == [str(live)]
    assert state["bubbleTotalCount"] == 1 and state["nextCursor"] is None
    assert not {str(expired), str(hidden), str(previous)}.intersection(card["id"] for card in state["bubbles"])
    card = state["bubbles"][0]
    assert card["acceptedAt"] + timedelta(seconds=MENTION_RETENTION_SECONDS) == card["expiresAt"]
    assert card["ambientUntil"] == state["ambientUntil"] == midnight_after(now)
    assert "slot" not in card and "remainingMs" not in card


@pytest.mark.parametrize(
    "mode,paused,online",
    [("MOODWALL", False, True), ("MOODWALL", True, True), ("MOODWALL", False, False), ("CHATBOT", False, True)],
)
async def test_all_105_approved_bubbles_deliver_without_waiting_for_positions(
    clients, system, database, settings, monkeypatch, mode, paused, online
):
    participant = await owner(clients, system, settings)
    async with database.connection() as conn, conn.transaction():
        now = (await db_time(conn)).replace(hour=3, minute=0, second=0, microsecond=0)
        identifiers = [await seed_card(conn, settings, participant, now - timedelta(minutes=1)) for _ in range(105)]
        await conn.execute(
            "UPDATE board_runtime SET mode=%s,display_paused=%s,last_heartbeat_at=%s,heartbeat_lease_until=%s WHERE id=%s",
            (mode, paused, now if online else None, now + timedelta(seconds=30) if online else None, settings.board_id),
        )

    async def clock(conn):
        return now

    monkeypatch.setattr("app.display.db_time", clock)
    await system.state.display.reconcile()
    token = clients["display"].cookies.get(settings.cookie_name("display"))
    actor = await system.state.auth.authenticate("display", token)
    first = await system.state.display.state(actor)
    assert len(first["bubbles"]) == 100 and first["bubbleTotalCount"] == 105
    assert first["nextCursor"] and first["mode"] == ("PAUSED" if paused else mode)
    second = await system.state.display.state(actor, first["nextCursor"])
    assert len(second["bubbles"]) == 5 and second["nextCursor"] is None
    assert first["snapshotAt"] == second["snapshotAt"]
    received = [card["id"] for card in first["bubbles"] + second["bubbles"]]
    assert len(set(received)) == 105 and set(received) == set(map(str, identifiers))
    async with database.connection() as conn:
        assert (await one(conn, "SELECT count(*) AS n FROM submissions WHERE display_status='FLOATING'"))["n"] == 105


async def test_later_pages_recheck_hide_expiry_and_exclude_new_approvals(clients, system, database, settings):
    participant = await owner(clients, system, settings)
    async with database.connection() as conn, conn.transaction():
        now = (await db_time(conn)).replace(hour=3, minute=0, second=0, microsecond=0)
        accepted = now - timedelta(minutes=59, seconds=59)
        identifiers = [
            await seed_card(
                conn,
                settings,
                participant,
                accepted,
                finalized=accepted + timedelta(seconds=1),
            )
            for _ in range(101)
        ]
        first = await system.state.display._snapshot(conn, now)
        last_id = next(
            identifier for identifier in identifiers if str(identifier) not in {card["id"] for card in first["bubbles"]}
        )
        await conn.execute("UPDATE submissions SET display_status='HIDDEN',hidden_at=%s WHERE id=%s", (now, last_id))
        new = await seed_card(conn, settings, participant, now, finalized=now + timedelta(seconds=1))
        second = await system.state.display._snapshot(conn, now + timedelta(milliseconds=500), first["nextCursor"])
        assert second["bubbles"] == [] and second["bubbleTotalCount"] == 100
        assert str(new) not in {card["id"] for card in second["bubbles"]}
        third = await system.state.display._snapshot(conn, now + timedelta(seconds=1), first["nextCursor"])
        assert third["bubbles"] == [] and third["bubbleTotalCount"] == 0


async def test_one_hour_source_expires_without_removing_browser_bubble(
    clients, system, database, settings, monkeypatch
):
    participant = await owner(clients, system, settings)
    async with database.connection() as conn, conn.transaction():
        accepted = (await db_time(conn)).replace(hour=3, minute=0, second=0, microsecond=0)
        identifier = await seed_card(conn, settings, participant, accepted)
        for minutes in (0, 10, 59):
            state = await system.state.display._snapshot(conn, accepted + timedelta(minutes=minutes))
            assert state["bubbles"][0]["id"] == str(identifier)
            assert state["bubbles"][0]["ambientUntil"] == midnight_after(accepted)

    async def clock(conn):
        return accepted + timedelta(hours=1)

    monkeypatch.setattr("app.display.db_time", clock)
    await system.state.display.reconcile()
    async with database.connection() as conn:
        state = await system.state.display._snapshot(conn, await clock(conn))
        assert state["bubbles"] == [] and state["removedIds"] == []
        assert state["ambientUntil"] == midnight_after(accepted)
        assert (await one(conn, "SELECT display_status FROM submissions WHERE id=%s", (identifier,)))[
            "display_status"
        ] == "EXPIRED"


async def test_cursor_tampering_expiration_and_new_day_are_invalid(clients, system, database, settings):
    participant = await owner(clients, system, settings)
    async with database.connection() as conn, conn.transaction():
        now = (await db_time(conn)).replace(hour=14, minute=59, second=0, microsecond=0)
        for _ in range(101):
            await seed_card(conn, settings, participant, now - timedelta(seconds=1))
        first = await system.state.display._snapshot(conn, now)
        for at, cursor in [
            (now, "A" + first["nextCursor"][1:]),
            (now + timedelta(minutes=5), first["nextCursor"]),
            (midnight_after(now), first["nextCursor"]),
        ]:
            with pytest.raises(Problem) as error:
                await system.state.display._snapshot(conn, at, cursor)
            assert error.value.code == "INVALID_CURSOR"
        await conn.execute(
            "UPDATE board_runtime SET device_token_version=device_token_version+1 WHERE id=%s", (settings.board_id,)
        )
        with pytest.raises(Problem) as error:
            await system.state.display._snapshot(conn, now, first["nextCursor"])
        assert error.value.code == "INVALID_CURSOR"


async def test_missing_row_hide_tombstone_prevents_later_source_reappearance(clients, system, database, settings):
    participant = await owner(clients, system, settings)
    identifier = uuid4()
    hidden = await clients["admin"].post(f"/api/operator/submissions/{identifier}/hide", json={"reasonCode": "OTHER"})
    assert hidden.status_code == 200, hidden.text
    async with database.connection() as conn, conn.transaction():
        now = await db_time(conn)
        actual = await seed_card(conn, settings, participant, now - timedelta(seconds=1))
        await conn.execute("UPDATE submissions SET id=%s WHERE id=%s", (identifier, actual))
    state = (await clients["display"].get("/api/display/state")).json()
    assert state["bubbles"] == [] and str(identifier) in state["removedIds"]


async def test_more_than_120_approved_bubbles_do_not_close_intake(clients, system, database, settings):
    token = clients["stranger"].cookies.get(settings.cookie_name("student"))
    participant = (await system.state.auth.authenticate("student", token)).id
    async with database.connection() as conn, conn.transaction():
        accepted = await db_time(conn) - timedelta(minutes=1)
        for _ in range(121):
            await seed_card(conn, settings, participant, accepted)
    receipt = await clients["student"].post(
        "/api/submissions",
        json={"content": "new floating message", "emojiId": "heart"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert receipt.status_code == 202, receipt.text
