"""Moderation state transitions, serialized on the owning participant."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.db import all_rows, db_time, one
from app.domain import business_day, midnight_after
from app.provider_control import ProviderControlService
from inference.contracts import ModelAnalyzed, RuleRejected, RunFailed

INFLIGHT = {"PENDING", "PROCESSING"}


class ModerationService:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings

    async def _lock(self, conn, submission_id):
        original = await one(conn, "SELECT participant_id,business_date FROM submissions WHERE id=%s", (submission_id,))
        if original is None:
            return None, None
        participant = await one(
            conn, "SELECT id FROM participants WHERE id=%s FOR UPDATE", (original["participant_id"],)
        )
        if participant is None:
            return None, None
        usage = await one(
            conn,
            "SELECT * FROM daily_usage WHERE participant_id=%s AND business_date=%s FOR UPDATE",
            (original["participant_id"], original["business_date"]),
        )
        row = await one(conn, "SELECT * FROM submissions WHERE id=%s FOR UPDATE", (submission_id,))
        return row, usage

    async def _terminal(self, conn, row, now, status, error=None):
        await conn.execute(
            """UPDATE submissions SET moderation_status=%s,finalized_at=%s,
            processing_run_token=NULL,processing_started_at=NULL,processing_lease_until=NULL,
            error_code=%s WHERE id=%s""",
            (status, now, error, row["id"]),
        )

    async def claim(self, submission_id: UUID) -> dict | None:
        async with self.db.connection() as conn:
            async with conn.transaction():
                row, usage = await self._lock(conn, submission_id)
                if row is None or row["moderation_status"] not in INFLIGHT:
                    return None
                now = await db_time(conn)
                if now < row["accepted_at"]:
                    return None
                if now >= row["expires_at"] or business_day(now) != row["business_date"]:
                    await self._terminal(conn, row, now, "EXPIRED", "DEADLINE_EXCEEDED")
                    return None
                if row["moderation_status"] == "PROCESSING":
                    if now >= row["processing_lease_until"]:
                        await self._terminal(conn, row, now, "FAILED", "INFERENCE_TIMEOUT")
                    return None
                if usage["banned_until"] and usage["banned_until"] > now:
                    await self._terminal(conn, row, now, "CANCELLED", "PARTICIPANT_SUSPENDED")
                    return None
                if row["attempt_count"] >= 2:
                    await self._terminal(conn, row, now, "FAILED", "ATTEMPTS_EXHAUSTED")
                    return None
                if (
                    row["moderation_policy_version"] != self.settings.moderation_policy_version
                    or row["lexicon_version"] != self.settings.lexicon_version
                ):
                    await self._terminal(conn, row, now, "FAILED", "POLICY_VERSION_MISMATCH")
                    return None
                token = uuid4()
                lease = min(now + timedelta(seconds=self.settings.inference_timeout_seconds), row["expires_at"])
                await conn.execute(
                    """UPDATE submissions SET moderation_status='PROCESSING',
                    processing_run_token=%s,processing_started_at=%s,processing_lease_until=%s,
                    attempt_count=attempt_count+1,error_code=NULL WHERE id=%s""",
                    (token, now, lease, submission_id),
                )
                return {
                    **row,
                    "processing_run_token": token,
                    "processing_started_at": now,
                    "processing_lease_until": lease,
                    "db_now": now,
                    "model_bundle_version": self.settings.model_bundle_version,
                    "inference_policy_version": self.settings.inference_policy_version,
                }

    async def finalize(self, claim: dict, outcome) -> None:
        await self._finalize(claim, outcome)
        # Publish in a separate board-owned transaction, after participant locks
        # have been released. Reconciliation repairs a crash before this hook.
        from app.playback import PlaybackService

        await PlaybackService(self.db, self.settings, None, None).sync_submission(claim["id"])

    async def _finalize(self, claim: dict, outcome) -> None:
        async with self.db.connection() as conn:
            async with conn.transaction():
                provider_mode = self.settings.moderation_profile != "legacy"
                provider_control = (
                    await ProviderControlService(self.db, self.settings).lock_control(conn) if provider_mode else None
                )
                row, usage = await self._lock(conn, claim["id"])
                if (
                    row is None
                    or row["moderation_status"] != "PROCESSING"
                    or row["processing_run_token"] != claim["processing_run_token"]
                ):
                    return
                now = await db_time(conn)
                if now >= row["expires_at"] or business_day(now) != row["business_date"]:
                    await self._terminal(conn, row, now, "EXPIRED", "DEADLINE_EXCEEDED")
                    return
                if now >= row["processing_lease_until"]:
                    await self._terminal(conn, row, now, "FAILED", "INFERENCE_TIMEOUT")
                    return
                if usage["banned_until"] and usage["banned_until"] > now:
                    await self._terminal(conn, row, now, "CANCELLED", "PARTICIPANT_SUSPENDED")
                    return
                if isinstance(outcome, RunFailed):
                    await self._terminal(conn, row, now, "FAILED", outcome.error_code)
                    return
                if isinstance(outcome, RuleRejected):
                    # A provider profile may never enter the legacy automatic
                    # strike transition, including via an internal caller.
                    if provider_mode or not self.settings.auto_strikes:
                        await self._terminal(conn, row, now, "FAILED", "INVALID_MODEL_OUTPUT")
                        return
                    if outcome.lexicon_version != row["lexicon_version"]:
                        await self._terminal(conn, row, now, "FAILED", "POLICY_VERSION_MISMATCH")
                        return
                    await conn.execute(
                        """UPDATE submissions SET moderation_status='REJECTED',finalized_at=%s,
                        processing_run_token=NULL,processing_started_at=NULL,processing_lease_until=NULL,
                        strike_applied=true,rule_evidence=%s,reason_codes=ARRAY[]::text[],
                        model_bundle_version=NULL,inference_policy_version=NULL,safety_verdict=NULL,
                        emotion=NULL,emotion_source=NULL,error_code=NULL WHERE id=%s""",
                        (now, Jsonb([item.model_dump() for item in outcome.matches]), row["id"]),
                    )
                    updated = await one(
                        conn,
                        """UPDATE daily_usage SET violation_count=violation_count+1,
                        banned_until=CASE WHEN violation_count+1>=5 THEN %s ELSE banned_until END,
                        updated_at=%s WHERE participant_id=%s AND business_date=%s RETURNING violation_count""",
                        (midnight_after(now), now, row["participant_id"], row["business_date"]),
                    )
                    if updated["violation_count"] >= 5:
                        others = await all_rows(
                            conn,
                            """SELECT * FROM submissions WHERE participant_id=%s
                            AND business_date=%s AND moderation_status IN ('PENDING','PROCESSING') ORDER BY id FOR UPDATE""",
                            (row["participant_id"], row["business_date"]),
                        )
                        for other in others:
                            await self._terminal(conn, other, now, "CANCELLED", "PARTICIPANT_SUSPENDED")
                    return
                if not isinstance(outcome, ModelAnalyzed):
                    await self._terminal(conn, row, now, "FAILED", "INVALID_MODEL_OUTPUT")
                    return
                result = outcome.analysis
                if (
                    result.model_bundle_version != claim["model_bundle_version"]
                    or result.inference_policy_version != claim["inference_policy_version"]
                ):
                    await self._terminal(conn, row, now, "FAILED", "INVALID_MODEL_OUTPUT")
                    return
                allowed = result.safety.verdict == "ALLOW"
                if provider_mode:
                    attempt = await one(
                        conn,
                        "SELECT status,run_token,release_id,outcome_code FROM moderation_provider_attempts WHERE submission_id=%s",
                        (row["id"],),
                    )
                    if (
                        attempt is None
                        or attempt["status"] != "COMPLETED"
                        or attempt["run_token"] != claim["processing_run_token"]
                        or attempt["release_id"] != row["moderation_policy_version"]
                        or attempt["release_id"] != self.settings.moderation_policy_version
                        or attempt["outcome_code"] != "OK"
                    ):
                        await self._terminal(conn, row, now, "FAILED", "INVALID_MODEL_OUTPUT")
                        return
                    if allowed and provider_control["mode"] != "OPEN":
                        await self._terminal(conn, row, now, "FAILED", "MODEL_NOT_READY")
                        return
                receipt = await one(conn, "SELECT id FROM submission_receipts WHERE id=%s", (row["id"],))
                await conn.execute(
                    """UPDATE submissions SET moderation_status=%s,finalized_at=%s,
                    processing_run_token=NULL,processing_started_at=NULL,processing_lease_until=NULL,
                    safety_verdict=%s,reason_codes=%s,model_bundle_version=%s,inference_policy_version=%s,
                    emotion=%s,emotion_source=%s,display_status=%s,error_code=NULL WHERE id=%s""",
                    (
                        "APPROVED" if allowed else "NEEDS_REWRITE",
                        now,
                        result.safety.verdict,
                        result.safety.reason_codes,
                        result.model_bundle_version,
                        result.inference_policy_version,
                        result.emotion.label if result.emotion else None,
                        result.emotion.source if result.emotion else None,
                        ("QUEUED" if receipt else "FLOATING") if allowed else "NONE",
                        row["id"],
                    ),
                )
            if allowed:
                # Approval is already committed and no participant/submission
                # lock remains. Keep board -> submission writers free from
                # an inverse lock order while waking the SSE resync hint.
                await conn.execute(
                    "UPDATE board_runtime SET state_version=state_version+1 WHERE id=%s", (row["board_id"],)
                )

    async def retry_before_model(self, claim: dict) -> bool:
        """Only the worker may call this, before START/ACK and any inference."""
        async with self.db.connection() as conn:
            async with conn.transaction():
                if self.settings.moderation_profile != "legacy":
                    await ProviderControlService(self.db, self.settings).lock_control(conn)
                    attempt = await one(
                        conn, "SELECT status FROM moderation_provider_attempts WHERE submission_id=%s", (claim["id"],)
                    )
                    if attempt is None or attempt["status"] != "RESERVED":
                        return False
                row, usage = await self._lock(conn, claim["id"])
                if (
                    row is None
                    or row["moderation_status"] != "PROCESSING"
                    or row["processing_run_token"] != claim["processing_run_token"]
                ):
                    return False
                now = await db_time(conn)
                if (
                    row["attempt_count"] >= 2
                    or now >= row["expires_at"]
                    or now >= row["processing_lease_until"]
                    or (usage["banned_until"] and usage["banned_until"] > now)
                ):
                    return False
                await conn.execute(
                    """UPDATE submissions SET moderation_status='PENDING',
                    processing_run_token=NULL,processing_started_at=NULL,processing_lease_until=NULL,
                    error_code='TRANSIENT_DEPENDENCY' WHERE id=%s""",
                    (row["id"],),
                )
                return True

    async def expire_due(self) -> int:
        async with self.db.connection() as conn:
            candidates = await all_rows(
                conn,
                """SELECT id FROM submissions
                WHERE moderation_status IN ('PENDING','PROCESSING') AND (expires_at<=clock_timestamp()
                OR processing_lease_until<=clock_timestamp()) ORDER BY expires_at,id LIMIT 100""",
            )
        changed = 0
        for candidate in candidates:
            async with self.db.connection() as conn:
                async with conn.transaction():
                    row, _ = await self._lock(conn, candidate["id"])
                    if row is None or row["moderation_status"] not in INFLIGHT:
                        continue
                    now = await db_time(conn)
                    if now >= row["expires_at"]:
                        await self._terminal(conn, row, now, "EXPIRED", "DEADLINE_EXCEEDED")
                        changed += 1
                    elif row["moderation_status"] == "PROCESSING" and now >= row["processing_lease_until"]:
                        await self._terminal(conn, row, now, "FAILED", "INFERENCE_TIMEOUT")
                        changed += 1
        return changed

    async def recover_stalled(self) -> int:
        from app.queue import create_queue

        app = create_queue(self.settings)
        recovered = 0
        for job in await app.job_manager.get_stalled_jobs(
            seconds_since_heartbeat=30, queue="moderation", task_name="moodwall.moderate"
        ):
            submission_id = job.task_kwargs.get("submission_id")
            try:
                parsed_id = UUID(submission_id) if submission_id else None
            except (ValueError, TypeError, AttributeError):
                parsed_id = None
            async with self.db.connection() as conn:
                row = (
                    await one(conn, "SELECT moderation_status FROM submissions WHERE id=%s", (parsed_id,))
                    if parsed_id
                    else None
                )
            if row is None or row["moderation_status"] not in INFLIGHT:
                await app.job_manager.retry_job(job)
                recovered += 1
        return recovered
