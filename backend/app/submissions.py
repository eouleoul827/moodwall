from __future__ import annotations

import asyncio
import math
from datetime import timedelta
from uuid import UUID

from psycopg import errors

from app.db import all_rows, db_time, one, schema_ready
from app.domain import (
    EMOJI,
    MENTION_RETENTION_SECONDS,
    business_day,
    midnight_after,
    normalize_content,
    request_fingerprint,
    serialize_submission,
)
from app.errors import Problem


async def usage_for(conn, participant_id: UUID, now=None):
    now = now or await db_time(conn)
    participant = await one(conn, "SELECT last_submission_at FROM participants WHERE id=%s", (participant_id,))
    if participant is None:
        raise Problem(404, "NOT_FOUND")
    rows = await all_rows(
        conn,
        """
        SELECT id, accepted_at, moderation_status FROM submissions s
        WHERE participant_id=%s AND accepted_at>%s-interval '60 minutes' AND accepted_at<=%s
          AND moderation_status IN ('PENDING','PROCESSING','APPROVED')
          AND NOT EXISTS (SELECT 1 FROM submission_receipts r WHERE r.id=s.id)
        UNION ALL
        SELECT id,accepted_at,processing_status AS moderation_status FROM submission_receipts
        WHERE participant_id=%s AND accepted_at>%s-interval '60 minutes' AND accepted_at<=%s
          AND quota_status IN ('RESERVED','CONSUMED') ORDER BY accepted_at,id
        """,
        (participant_id, now, now, participant_id, now, now),
    )
    active = await one(
        conn,
        """SELECT id FROM submissions WHERE participant_id=%s
        AND moderation_status IN ('PENDING','PROCESSING') LIMIT 1""",
        (participant_id,),
    )
    daily = await one(
        conn,
        "SELECT * FROM daily_usage WHERE participant_id=%s AND business_date=%s",
        (participant_id, business_day(now)),
    )
    last = participant["last_submission_at"]
    cooldown = last + timedelta(seconds=10) if last else None
    violations = daily["violation_count"] if daily else 0
    suspended = daily["banned_until"] if daily and daily["banned_until"] and daily["banned_until"] > now else None
    return {
        "windowSeconds": 3600,
        "limit": 3,
        "usedCount": len(rows),
        "pendingCount": int(active is not None),
        "remainingSlots": max(0, 3 - len(rows)),
        "nextSlotAt": rows[0]["accepted_at"] + timedelta(hours=1) if len(rows) >= 3 else None,
        "businessDate": business_day(now),
        "violationCount": violations,
        "warning": violations >= 3,
        "suspendedUntil": suspended,
        "activeSubmissionId": str(active["id"]) if active else None,
        "nextSubmissionAt": cooldown if cooldown and cooldown > now else None,
    }


class _DateChanged(Exception):
    pass


class SubmissionService:
    def __init__(self, db, settings, auth, enqueue, intake_ready=None):
        self.db, self.settings, self.auth, self.enqueue = db, settings, auth, enqueue
        self.intake_ready = intake_ready or (lambda: True)

    async def get(self, actor, submission_id):
        async with self.db.connection() as conn:
            await self.auth.recheck(actor, conn)
            cutoff = await db_time(conn) - timedelta(seconds=MENTION_RETENTION_SECONDS)
            row = await one(
                conn,
                """SELECT * FROM submissions s WHERE id=%s AND participant_id=%s AND accepted_at>%s
                AND NOT EXISTS (SELECT 1 FROM submission_receipts r WHERE r.id=s.id)""",
                (submission_id, actor.id, cutoff),
            )
            if row is None:
                raise Problem(404, "NOT_FOUND")
            return serialize_submission(row)

    async def usage(self, actor):
        async with self.db.connection() as conn:
            async with conn.transaction():
                await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                await self.auth.recheck(actor, conn)
                return await usage_for(conn, actor.id)

    async def submit(self, actor, key: UUID, content: str, emoji_id: str):
        try:
            content = normalize_content(content)
        except (ValueError, UnicodeError, TimeoutError):
            raise Problem(422, "VALIDATION_ERROR", "멘션은 제어문자 없이 1~30자로 작성해 주세요.") from None
        fingerprint = request_fingerprint(content, emoji_id)
        for attempt in range(3):
            try:
                return await self._submit(actor, key, content, emoji_id, fingerprint)
            except (_DateChanged, errors.DeadlockDetected, errors.SerializationFailure, errors.LockNotAvailable):
                if attempt == 2:
                    raise Problem(503, "SERVICE_UNAVAILABLE", retry_after=1) from None
                await asyncio.sleep(0.025 * (attempt + 1))

    async def _submit(self, actor, key, content, emoji_id, fingerprint):
        async with self.db.connection() as conn, conn.transaction():
            participant = await one(conn, "SELECT * FROM participants WHERE id=%s FOR UPDATE", (actor.id,))
            if participant is None:
                raise Problem(401, "SESSION_EXPIRED", cookie_role="student")
            fresh = await self.auth.recheck(actor, conn)
            receipt = await one(
                conn,
                "SELECT id FROM submission_receipts WHERE participant_id=%s AND idempotency_key=%s",
                (actor.id, key),
            )
            if receipt:
                raise Problem(409, "IDEMPOTENCY_CONFLICT")
            existing = await one(
                conn, "SELECT * FROM submissions WHERE participant_id=%s AND idempotency_key=%s", (actor.id, key)
            )
            now = await db_time(conn)
            if existing and existing["accepted_at"] <= now - timedelta(seconds=MENTION_RETENTION_SECONDS):
                # The one-hour idempotency window has ended. Preserve the same
                # participant -> daily -> submission lock order as retention.
                await one(
                    conn,
                    "SELECT 1 FROM daily_usage WHERE participant_id=%s AND business_date=%s FOR UPDATE",
                    (actor.id, existing["business_date"]),
                )
                await conn.execute("DELETE FROM submissions WHERE id=%s", (existing["id"],))
                existing = None
            if existing:
                if bytes(existing["request_hash"]) != fingerprint:
                    raise Problem(409, "IDEMPOTENCY_CONFLICT")
                return serialize_submission(existing), False
            if getattr(self.settings, "moderation_profile", "legacy") != "legacy":
                raise Problem(409, "CLIENT_UPDATE_REQUIRED", "화면을 새로고침한 뒤 다시 작성해 주세요.")
            if not self.intake_ready() or not await schema_ready(conn, self.settings.board_id):
                raise Problem(503, "SERVICE_UNAVAILABLE")
            if emoji_id not in EMOJI:
                raise Problem(422, "VALIDATION_ERROR", "허용된 배경 이모지를 선택해 주세요.")
            now = await db_time(conn)
            await self._clear_expired(conn, actor.id, now)
            day = business_day(now)
            await conn.execute(
                "INSERT INTO daily_usage(participant_id,business_date) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                (actor.id, day),
            )
            await one(
                conn,
                "SELECT * FROM daily_usage WHERE participant_id=%s AND business_date=%s FOR UPDATE",
                (actor.id, day),
            )
            now = await db_time(conn)
            if business_day(now) != day:
                raise _DateChanged()
            session = fresh.extra
            if (
                session.get("admission_board_id") != self.settings.board_id
                or not session.get("admission_expires_at")
                or session["admission_expires_at"] <= now
            ):
                raise Problem(403, "ADMISSION_EXPIRED", "전광판의 최신 QR을 다시 스캔해 주세요.")
            usage = await usage_for(conn, actor.id, now)
            if usage["suspendedUntil"]:
                raise Problem(403, "PARTICIPANT_SUSPENDED")
            if usage["activeSubmissionId"]:
                raise Problem(409, "SUBMISSION_IN_PROGRESS")
            if usage["nextSubmissionAt"]:
                raise Problem(
                    429, "SUBMISSION_COOLDOWN", retry_after=math.ceil((usage["nextSubmissionAt"] - now).total_seconds())
                )
            if usage["usedCount"] >= 3:
                raise Problem(429, "QUOTA_EXCEEDED", retry_after=math.ceil((usage["nextSlotAt"] - now).total_seconds()))
            board = await one(
                conn, "SELECT submissions_paused FROM board_runtime WHERE id=%s", (self.settings.board_id,)
            )
            if not board or board["submissions_paused"]:
                raise Problem(503, "INTAKE_PAUSED", retry_after=5)
            ready = await one(
                conn,
                """SELECT 1 FROM worker_readiness WHERE worker_name='moderation'
                AND lease_until>%s AND model_bundle_version=%s""",
                (now, self.settings.model_bundle_version),
            )
            if not ready:
                raise Problem(503, "MODEL_NOT_READY", retry_after=5)
            counts = await one(
                conn,
                """SELECT
                count(*) FILTER (WHERE moderation_status IN ('PENDING','PROCESSING')) AS pending,
                min(accepted_at) FILTER (WHERE moderation_status IN ('PENDING','PROCESSING')) AS oldest
                FROM submissions WHERE board_id=%s AND business_date=%s AND accepted_at>%s""",
                (self.settings.board_id, day, now - timedelta(seconds=MENTION_RETENTION_SECONDS)),
            )
            expires = min(now + timedelta(seconds=120), midnight_after(now))
            remaining = (expires - now).total_seconds()
            if (
                counts["pending"] >= self.settings.moderation_backlog_limit
                or (counts["pending"] + 1) * self.settings.moderation_p95_seconds >= remaining
            ):
                raise Problem(503, "MODERATION_BUSY", retry_after=5)
            final_now = await db_time(conn)
            if business_day(final_now) != day:
                raise _DateChanged()
            if session["admission_expires_at"] <= final_now:
                raise Problem(403, "ADMISSION_EXPIRED")
            row = await one(
                conn,
                """INSERT INTO submissions(participant_id,business_date,board_id,idempotency_key,
                request_hash,content,emoji_id,accepted_at,expires_at,moderation_policy_version,lexicon_version)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    actor.id,
                    day,
                    self.settings.board_id,
                    key,
                    fingerprint,
                    content,
                    emoji_id,
                    final_now,
                    min(final_now + timedelta(seconds=120), midnight_after(final_now)),
                    self.settings.moderation_policy_version,
                    self.settings.lexicon_version,
                ),
            )
            await conn.execute(
                "UPDATE participants SET last_submission_at=%s,last_seen_at=GREATEST(last_seen_at,%s) WHERE id=%s",
                (final_now, final_now, actor.id),
            )
            job_id = await self.enqueue(conn, row["id"])
            await conn.execute("UPDATE submissions SET queue_job_id=%s WHERE id=%s", (job_id, row["id"]))
            return serialize_submission(row), True

    @staticmethod
    async def _clear_expired(conn, participant_id, now):
        candidates = await all_rows(
            conn,
            """SELECT id,business_date FROM submissions WHERE participant_id=%s
            AND moderation_status IN ('PENDING','PROCESSING') AND (expires_at<=%s OR processing_lease_until<=%s)
            ORDER BY business_date,id""",
            (participant_id, now, now),
        )
        for candidate in candidates:
            await one(
                conn,
                "SELECT 1 FROM daily_usage WHERE participant_id=%s AND business_date=%s FOR UPDATE",
                (participant_id, candidate["business_date"]),
            )
            await one(conn, "SELECT id FROM submissions WHERE id=%s FOR UPDATE", (candidate["id"],))
            current = await db_time(conn)
            await conn.execute(
                """UPDATE submissions SET
                moderation_status=CASE WHEN expires_at<=%s THEN 'EXPIRED' ELSE 'FAILED' END,
                error_code=CASE WHEN expires_at<=%s THEN 'DEADLINE_EXCEEDED' ELSE 'INFERENCE_TIMEOUT' END,
                finalized_at=GREATEST(accepted_at,%s),processing_run_token=NULL,
                processing_started_at=NULL,processing_lease_until=NULL
                WHERE id=%s AND moderation_status IN ('PENDING','PROCESSING')
                AND (expires_at<=%s OR processing_lease_until<=%s)""",
                (current, current, current, candidate["id"], current, current),
            )
