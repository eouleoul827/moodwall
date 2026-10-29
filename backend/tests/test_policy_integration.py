from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from app.db import all_rows, db_time, one
from app.domain import EMOJI, business_day, midnight_after, request_fingerprint
from inference.contracts import AnalysisInput, ModelAnalyzed, RuleEvidence, RuleRejected
from inference.fake import FakeAnalyzer

pytestmark = pytest.mark.integration


async def student_actor(clients, system, settings):
    token = clients["student"].cookies.get(settings.cookie_name("student"))
    return await system.state.auth.authenticate("student", token)


async def post_mention(client, *, content="합성 정책 시험", emoji="party", key=None):
    return await client.post(
        "/api/submissions",
        json={"content": content, "emojiId": emoji},
        headers={"Idempotency-Key": str(key or uuid4())},
    )


async def seed_approved(conn, settings, participant_id, accepted):
    day = business_day(accepted)
    expires = min(accepted + timedelta(seconds=120), midnight_after(accepted))
    finalized = accepted
    await conn.execute(
        "INSERT INTO daily_usage(participant_id,business_date) VALUES(%s,%s) ON CONFLICT DO NOTHING",
        (participant_id, day),
    )
    return (
        await one(
            conn,
            """INSERT INTO submissions(participant_id,business_date,board_id,idempotency_key,
           request_hash,content,emoji_id,accepted_at,expires_at,moderation_status,finalized_at,
           attempt_count,safety_verdict,emotion,emotion_source,display_status,
           model_bundle_version,inference_policy_version,moderation_policy_version,lexicon_version)
           VALUES(%s,%s,%s,%s,%s,'합성 승인 글','party',%s,%s,'APPROVED',%s,
                  1,'ALLOW','JOY','MODEL','FLOATING',%s,%s,%s,%s) RETURNING id""",
            (
                participant_id,
                day,
                settings.board_id,
                uuid4(),
                request_fingerprint("합성 승인 글", "party"),
                accepted,
                expires,
                finalized,
                settings.model_bundle_version,
                settings.inference_policy_version,
                settings.moderation_policy_version,
                settings.lexicon_version,
            ),
        )
    )["id"]


def analyzed(claim):
    return ModelAnalyzed(analysis=FakeAnalyzer().analyze(AnalysisInput(text=claim["content"])))


async def test_three_approved_in_rolling_window_block_new_intent_even_after_hide(
    clients,
    system,
    database,
    settings,
    monkeypatch,
):
    actor = await student_actor(clients, system, settings)
    async with database.connection() as conn, conn.transaction():
        now = await db_time(conn)
        await one(conn, "SELECT id FROM participants WHERE id=%s FOR UPDATE", (actor.id,))
        ids = []
        for minutes in (59, 30, 0):
            ids.append(await seed_approved(conn, settings, actor.id, now - timedelta(minutes=minutes)))
        await conn.execute(
            "UPDATE participants SET last_submission_at=%s WHERE id=%s",
            (now, actor.id),
        )

    async def after_cooldown(conn):
        return now + timedelta(seconds=11)

    monkeypatch.setattr("app.submissions.db_time", after_cooldown)
    usage = (await clients["student"].get("/api/me/usage")).json()
    assert usage["usedCount"] == 3 and usage["remainingSlots"] == 0
    refused = await post_mention(clients["student"])
    assert refused.status_code == 429, refused.text
    assert refused.json()["code"] == "QUOTA_EXCEEDED"

    hidden = await clients["admin"].post(
        f"/api/operator/submissions/{ids[-1]}/hide",
        json={"reasonCode": "OTHER"},
    )
    assert hidden.status_code == 200, hidden.text
    usage = (await clients["student"].get("/api/me/usage")).json()
    assert usage["usedCount"] == 3 and usage["remainingSlots"] == 0
    refused = await post_mention(clients["student"])
    assert refused.status_code == 429 and refused.json()["code"] == "QUOTA_EXCEEDED"
    async with database.connection() as conn:
        row = await one(conn, "SELECT moderation_status,display_status FROM submissions WHERE id=%s", (ids[-1],))
        assert row == {"moderation_status": "APPROVED", "display_status": "HIDDEN"}
        assert (await one(conn, "SELECT count(*) AS n FROM submissions WHERE participant_id=%s", (actor.id,)))["n"] == 3


async def test_exact_sixty_minute_lower_bound_refunds_one_slot(
    clients,
    system,
    database,
    settings,
    monkeypatch,
):
    actor = await student_actor(clients, system, settings)
    async with database.connection() as conn, conn.transaction():
        fixed = await db_time(conn)
        await one(conn, "SELECT id FROM participants WHERE id=%s FOR UPDATE", (actor.id,))
        boundary = await seed_approved(conn, settings, actor.id, fixed - timedelta(hours=1))
        await seed_approved(conn, settings, actor.id, fixed - timedelta(hours=1) + timedelta(microseconds=1))
        await seed_approved(conn, settings, actor.id, fixed - timedelta(minutes=2))
        await conn.execute(
            "UPDATE participants SET last_submission_at=%s WHERE id=%s", (fixed - timedelta(minutes=2), actor.id)
        )

    async def fixed_clock(conn):
        return fixed

    # Real PostgreSQL queries and the HTTP/service path run; only the wall clock is fixed.
    monkeypatch.setattr("app.submissions.db_time", fixed_clock)
    before = (await clients["student"].get("/api/me/usage")).json()
    assert before["usedCount"] == 2 and before["remainingSlots"] == 1
    accepted = await post_mention(clients["student"])
    assert accepted.status_code == 202, accepted.text
    after = (await clients["student"].get("/api/me/usage")).json()
    assert after["usedCount"] == 3 and after["pendingCount"] == 1
    async with database.connection() as conn:
        assert await one(conn, "SELECT id FROM submissions WHERE id=%s", (boundary,))
        assert (await one(conn, "SELECT count(*) AS n FROM submissions WHERE participant_id=%s", (actor.id,)))["n"] == 4


@pytest.mark.parametrize("kind", ["allow", "rule"])
async def test_expired_processing_lease_never_approves_or_strikes_and_returns_quota(
    clients,
    system,
    database,
    settings,
    monkeypatch,
    kind,
):
    receipt = await post_mention(clients["student"])
    assert receipt.status_code == 202, receipt.text
    submission_id = UUID(receipt.json()["id"])
    claim = await system.state.moderation.claim(submission_id)
    assert claim is not None
    outcome = (
        analyzed(claim)
        if kind == "allow"
        else RuleRejected(
            lexicon_version=settings.lexicon_version,
            matches=[RuleEvidence(rule_id="ko-explicit-001", variant_id="base")],
        )
    )

    async def at_lease_boundary(conn):
        return claim["processing_lease_until"]

    monkeypatch.setattr("app.moderation.db_time", at_lease_boundary)
    await system.state.moderation.finalize(claim, outcome)
    await system.state.moderation.finalize(claim, outcome)
    result = (await clients["student"].get(f"/api/submissions/{submission_id}")).json()
    expected = "EXPIRED" if claim["processing_lease_until"] >= claim["expires_at"] else "FAILED"
    assert result["moderationStatus"] == expected and result["displayStatus"] == "NONE"
    usage = (await clients["student"].get("/api/me/usage")).json()
    assert usage["usedCount"] == 0 and usage["pendingCount"] == 0
    assert usage["violationCount"] == 0 and usage["activeSubmissionId"] is None
    async with database.connection() as conn:
        row = await one(
            conn,
            "SELECT processing_run_token,strike_applied,rule_evidence,safety_verdict,error_code FROM submissions WHERE id=%s",
            (submission_id,),
        )
        assert row["processing_run_token"] is None and row["strike_applied"] is False
        assert row["rule_evidence"] == [] and row["safety_verdict"] is None
        assert row["error_code"] == ("DEADLINE_EXCEEDED" if expected == "EXPIRED" else "INFERENCE_TIMEOUT")


async def test_old_run_token_cannot_finalize_a_new_attempt(clients, system, database):
    receipt = await post_mention(clients["student"])
    assert receipt.status_code == 202, receipt.text
    submission_id = UUID(receipt.json()["id"])
    old = await system.state.moderation.claim(submission_id)
    assert old is not None and await system.state.moderation.retry_before_model(old)
    current = await system.state.moderation.claim(submission_id)
    assert current is not None and current["processing_run_token"] != old["processing_run_token"]
    await system.state.moderation.finalize(old, analyzed(old))
    async with database.connection() as conn:
        row = await one(
            conn,
            "SELECT moderation_status,processing_run_token,attempt_count FROM submissions WHERE id=%s",
            (submission_id,),
        )
        assert row["moderation_status"] == "PROCESSING"
        assert row["processing_run_token"] == current["processing_run_token"] and row["attempt_count"] == 2
    await system.state.moderation.finalize(current, analyzed(current))
    assert (await clients["student"].get(f"/api/submissions/{submission_id}")).json()["moderationStatus"] == "APPROVED"
    assert (await clients["student"].get("/api/me/usage")).json()["usedCount"] == 1


async def test_existing_key_survives_emoji_deactivation_without_a_new_job(
    clients,
    system,
    database,
    settings,
    monkeypatch,
):
    actor = await student_actor(clients, system, settings)
    key = uuid4()
    first = await post_mention(clients["student"], key=key)
    assert first.status_code == 202, first.text
    async with database.connection() as conn:
        original = await one(conn, "SELECT queue_job_id FROM submissions WHERE id=%s", (UUID(first.json()["id"]),))
    monkeypatch.delitem(EMOJI, "party")
    replay = await post_mention(clients["student"], key=key)
    assert replay.status_code == 200 and replay.json()["id"] == first.json()["id"]
    fresh = await post_mention(clients["student"])
    assert fresh.status_code == 422, fresh.text
    async with database.connection() as conn:
        rows = await all_rows(conn, "SELECT queue_job_id FROM submissions WHERE participant_id=%s", (actor.id,))
        assert rows == [original] and original["queue_job_id"] is not None
    assert (await clients["student"].get("/api/me/usage")).json()["usedCount"] == 1
