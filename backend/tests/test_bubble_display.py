"""The display cache outlives source rows; only explicit hides remove bubbles."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.db import db_time, one
from app.display import REMOVED_ID_LIMIT, DisplayService
from app.domain import midnight_after
from inference.contracts import AnalysisInput, ModelAnalyzed
from inference.fake import FakeAnalyzer

UTC = timezone.utc
NOW = datetime(2026, 10, 29, 3, 0, tzinfo=UTC)
BOARD = UUID("10000000-0000-4000-8000-000000000001")


def mock_snapshot(monkeypatch, *, cards=None, removals=None):
    board = {
        "id": BOARD,
        "mode": "MOODWALL",
        "display_paused": False,
        "heartbeat_lease_until": NOW + timedelta(seconds=30),
        "state_version": 17,
        "device_token_version": 9,
    }
    single = AsyncMock(side_effect=[board, {"n": len(cards or [])}])
    row_results = [cards or []]
    row_results.append(removals or [])
    rows = AsyncMock(side_effect=row_results)
    monkeypatch.setattr("app.display.one", single)
    monkeypatch.setattr("app.display.all_rows", rows)
    return DisplayService(
        None, SimpleNamespace(board_id=BOARD), SimpleNamespace(derive_key=lambda context: bytes(32))
    ), rows


async def test_snapshot_separates_source_expiry_from_bubble_midnight(monkeypatch):
    accepted = NOW - timedelta(minutes=59)
    source = {
        "id": uuid4(),
        "content": "synthetic approved bubble",
        "emoji_id": "heart",
        "emotion": "JOY",
        "accepted_at": accepted,
        "finalized_at": accepted + timedelta(seconds=1),
    }
    service, _ = mock_snapshot(monkeypatch, cards=[source])
    state = await service._snapshot(object(), NOW)
    assert state["businessDate"] == "2026-10-29"
    assert state["deviceGeneration"] == "9"
    assert state["ambientUntil"] == datetime(2026, 10, 29, 15, 0, tzinfo=UTC)
    assert state["removedIds"] == [] and state["cacheResetToken"] == "0"
    for card in state["bubbles"]:
        assert card["expiresAt"] == NOW + timedelta(minutes=1)
        assert card["ambientUntil"] == state["ambientUntil"]
        assert card["content"] == source["content"]


async def test_missing_source_does_not_generate_a_removal(monkeypatch):
    service, _ = mock_snapshot(monkeypatch)
    state = await service._snapshot(object(), NOW)
    assert state["bubbles"] == []
    assert state["removedIds"] == []
    assert state["ambientUntil"] == midnight_after(NOW)


async def test_hidden_ids_are_explicit_deduplicated_and_day_bounded(monkeypatch):
    hidden = uuid4()
    service, rows = mock_snapshot(
        monkeypatch, removals=[{"id": 202, "target_id": hidden}, {"id": 201, "target_id": hidden}]
    )
    state = await service._snapshot(object(), NOW)
    assert state["removedIds"] == [str(hidden)]
    assert state["cacheResetToken"] == "0"
    query = rows.await_args.args[1]
    assert "action='HIDE_SUBMISSION'" in query
    assert "ORDER BY id DESC LIMIT %s" in query
    assert rows.await_args.args[2] == (
        BOARD,
        midnight_after(NOW) - timedelta(days=1),
        midnight_after(NOW),
        REMOVED_ID_LIMIT + 1,
    )


async def test_overflow_changes_reset_token_instead_of_losing_old_hides(monkeypatch):
    removals = [{"id": 2000 - index, "target_id": uuid4()} for index in range(REMOVED_ID_LIMIT + 1)]
    service, _ = mock_snapshot(monkeypatch, removals=removals)
    state = await service._snapshot(object(), NOW)
    assert len(state["removedIds"]) == REMOVED_ID_LIMIT
    assert state["cacheResetToken"] == str(removals[REMOVED_ID_LIMIT]["id"])
    assert str(removals[REMOVED_ID_LIMIT]["target_id"]) not in state["removedIds"]


@pytest.mark.integration
async def test_hide_tombstone_survives_physical_source_deletion(clients, system, database):
    receipt = await clients["student"].post(
        "/api/submissions",
        json={"content": "approved cache retention", "emojiId": "heart"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert receipt.status_code == 202, receipt.text
    identifier = receipt.json()["id"]
    claim = await system.state.moderation.claim(UUID(identifier))
    assert claim is not None
    await system.state.moderation.finalize(
        claim, ModelAnalyzed(analysis=FakeAnalyzer().analyze(AnalysisInput(text=claim["content"])))
    )
    hidden = await clients["admin"].post(f"/api/operator/submissions/{identifier}/hide", json={"reasonCode": "OTHER"})
    assert hidden.status_code == 200, hidden.text
    async with database.connection() as conn, conn.transaction():
        await conn.execute("DELETE FROM submissions WHERE id=%s", (UUID(identifier),))
    state = (await clients["display"].get("/api/display/state")).json()
    assert identifier in state["removedIds"]
    assert state["bubbles"] == []


@pytest.mark.integration
async def test_only_today_hide_audits_are_sent_to_the_display(clients, system, database, settings):
    async with database.connection() as conn, conn.transaction():
        now = await db_time(conn)
        admin = await one(conn, "SELECT id FROM operators WHERE login_name='admin'")
        day_start = midnight_after(now) - timedelta(days=1)
        ids = {"today": uuid4(), "yesterday": uuid4(), "tomorrow": uuid4(), "other_action": uuid4()}
        for name, action, when in (
            ("today", "HIDE_SUBMISSION", day_start),
            ("yesterday", "HIDE_SUBMISSION", day_start - timedelta(microseconds=1)),
            ("tomorrow", "HIDE_SUBMISSION", day_start + timedelta(days=1)),
            ("other_action", "SET_BOARD_MODE", now),
        ):
            await conn.execute(
                """INSERT INTO operator_audit_logs(operator_id,action,board_id,target_id,reason_code,occurred_at)
                   VALUES(%s,%s,%s,%s,'OTHER',%s)""",
                (admin["id"], action, settings.board_id, ids[name], when),
            )
    state = (await clients["display"].get("/api/display/state")).json()
    assert state["removedIds"] == [str(ids["today"])]
