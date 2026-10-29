"""Expired mention content cannot leak while the physical cleanup is delayed."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from app.db import db_time, one
from app.domain import business_day, midnight_after

pytestmark = pytest.mark.integration


async def expired_receipt(clients, database, offset=timedelta()):
    key = uuid4()
    response = await clients["student"].post(
        "/api/submissions", json={"content": "응원해", "emojiId": "heart"}, headers={"Idempotency-Key": str(key)}
    )
    assert response.status_code == 202, response.text
    identifier = UUID(response.json()["id"])
    async with database.connection() as conn, conn.transaction():
        now = await db_time(conn)
        accepted = now - timedelta(hours=1) + offset
        row = await one(conn, "SELECT participant_id FROM submissions WHERE id=%s", (identifier,))
        await conn.execute(
            "INSERT INTO daily_usage(participant_id,business_date) VALUES(%s,%s) ON CONFLICT DO NOTHING",
            (row["participant_id"], business_day(accepted)),
        )
        await conn.execute(
            """UPDATE submissions SET accepted_at=%s,business_date=%s,expires_at=%s,
               moderation_status='FAILED',finalized_at=%s WHERE id=%s""",
            (
                accepted,
                business_day(accepted),
                min(accepted + timedelta(seconds=120), midnight_after(accepted)),
                accepted + timedelta(seconds=1),
                identifier,
            ),
        )
        await conn.execute(
            "UPDATE participants SET last_submission_at=%s WHERE id=%s", (accepted, row["participant_id"])
        )
    return identifier, key, now


@pytest.mark.parametrize("offset,expected", [(timedelta(), 404), (timedelta(microseconds=1), 200)])
async def test_own_and_operator_queries_hide_expired_rows_before_cleanup(
    clients, database, monkeypatch, offset, expected
):
    identifier, _, now = await expired_receipt(clients, database, offset)

    async def clock(conn):
        return now

    monkeypatch.setattr("app.submissions.db_time", clock)
    monkeypatch.setattr("app.operator.db_time", clock)
    own = await clients["student"].get(f"/api/submissions/{identifier}")
    assert own.status_code == expected, own.text
    day = business_day(now - timedelta(hours=1) + offset)
    listing = await clients["admin"].get("/api/operator/submissions", params={"businessDate": day.isoformat()})
    assert listing.status_code == 200, listing.text
    ids = [item["submission"]["id"] for item in listing.json()["items"]]
    assert (str(identifier) in ids) == (expected == 200)
    async with database.connection() as conn:
        assert await one(conn, "SELECT id FROM submissions WHERE id=%s", (identifier,))


async def test_expired_idempotency_key_does_not_resurrect_its_original_content(clients, database):
    identifier, key, _ = await expired_receipt(clients, database)
    # A retained row must not cause a late replay to disclose one-hour-old text.
    response = await clients["student"].post(
        "/api/submissions", json={"content": "다시 응원해", "emojiId": "heart"}, headers={"Idempotency-Key": str(key)}
    )
    assert response.status_code == 202, response.text
    assert response.json()["id"] != str(identifier)
    assert response.json()["content"] == "다시 응원해"
    async with database.connection() as conn:
        assert await one(conn, "SELECT id FROM submissions WHERE id=%s", (identifier,)) is None
