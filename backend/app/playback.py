"""V2 receipts and DB-owned, acknowledged display playback.

Moderation still runs in the existing supervised worker.  Only its committed
ALLOW result can publish a READY item.  This module never waits for inference
or a browser while holding a database lock.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from psycopg import errors

from app.db import all_rows, db_time, one, schema_ready
from app.display import BubbleCursorCodec, effective_mode
from app.domain import ANIMATIONS, business_day, midnight_after, normalize_content
from app.errors import Problem
from app.security import derive_key

PROTOCOL_VERSION = "2"
ACTIVE = {"CLAIMED", "START_AUTHORIZED", "PLAYING"}
TERMINAL = {"DONE", "FAILED", "EXPIRED", "CANCELLED", "HIDDEN"}
INTERRUPT_REASONS = {"HIDDEN", "PAGE_HIDDEN", "RELOAD", "FRAME_GAP", "ASSET_FAILURE", "NETWORK", "CLIENT_ERROR"}
PAGE_SIZE = 100


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def receipt_payload(row, now=None):
    """An outcome survives source deletion; it never reflects submitted text."""
    presentation, processing = row["presentation_status"], row["processing_status"]
    if presentation == "HIDDEN":
        status = "HIDDEN"
    elif presentation == "DONE":
        status = "SHOWN"
    elif presentation == "FAILED" and row["reason_code"] == "PLAYBACK_UNCONFIRMED":
        status = "PLAYBACK_UNCONFIRMED"
    elif presentation in {"FAILED", "EXPIRED", "CANCELLED"}:
        status = presentation
    elif processing in {"REJECTED", "NEEDS_REWRITE"}:
        status = "NEEDS_REWRITE"
    elif processing in {"FAILED", "EXPIRED", "CANCELLED"}:
        status = processing
    else:
        status = {
            "READY": "WAITING",
            "CLAIMED": "WAITING",
            "INTERRUPTED": "WAITING",
            "START_AUTHORIZED": "STARTING",
            "PLAYING": "SHOWING",
        }.get(presentation, "PREPARING")
    messages = {
        "NEEDS_REWRITE": "표현을 바꿔 다시 작성해 주세요.",
        "FAILED": "준비 중 문제가 생겼어요. 잠시 후 다시 작성해 주세요.",
        "EXPIRED": "대기 시간이 지나 표시되지 않았어요.",
        "CANCELLED": "이 글의 표시가 취소됐어요.",
        "HIDDEN": "이 글은 전광판에서 내려갔어요.",
    }
    content_status = row["content_status"]
    if now and row["content_delete_at"] and now >= row["content_delete_at"]:
        content_status = "PURGED"
    return {
        "receiptVersion": "2",
        "submissionId": str(row["id"]),
        "acceptedAt": row["accepted_at"],
        "receiptExpiresAt": row["receipt_expires_at"],
        "processingStatus": processing,
        "presentationStatus": presentation,
        "userStatus": status,
        "contentStatus": content_status,
        "lastDisplayStatus": presentation,
        "reasonCode": row["reason_code"],
        "firstStartAuthorizedAt": row["first_start_authorized_at"],
        "contentDeleteAt": row["content_delete_at"],
        "completedAt": row["completed_at"],
        "revision": str(row["revision"]),
        "statusUrl": "/api/v2/submissions/" + str(row["id"]),
        "message": "많이 힘들었겠어요. 이 글은 전광판에 표시하지 않았어요. 가까운 선생님이나 믿을 수 있는 어른에게 알려주세요."
        if row["reason_code"] == "SUPPORT_AVAILABLE"
        else messages.get(status, "메시지를 보냈어요!"),
    }


class PlaybackService:
    def __init__(self, db, settings, auth, enqueue=None, intake_ready=None):
        self.db, self.settings, self.auth, self.enqueue = db, settings, auth, enqueue
        self.intake_ready = intake_ready or (lambda: True)
        self.fingerprint_key = derive_key(settings.secret_key, "moodwall:submission:v2")
        self.cursors = BubbleCursorCodec(derive_key(settings.secret_key, "moodwall:playback-bubbles:v2"))

    async def _board(self, conn):
        board = await one(conn, "SELECT * FROM board_runtime WHERE id=%s FOR NO KEY UPDATE", (self.settings.board_id,))
        if not board:
            raise Problem(503, "SERVICE_UNAVAILABLE")
        return board

    async def _authorize(self, actor, conn, *, display_only=False):
        if actor.role == "display":
            if actor.id != self.settings.board_id:
                raise Problem(403, "FORBIDDEN")
            return await self.auth.recheck(actor, conn)
        if display_only:
            raise Problem(403, "FORBIDDEN")
        return await self.auth.require_operator(actor, ("ADMIN", "MODERATOR"), conn=conn)

    @staticmethod
    def _require_features(required, features):
        if required and required not in {part.strip() for part in features.split(",")}:
            raise Problem(409, "CLIENT_UPDATE_REQUIRED", "화면을 새로고침해 주세요. 다시 전송할 필요는 없어요.")

    async def get_receipt(self, actor, submission_id, client_features=""):
        async with self.db.connection() as conn:
            await self.auth.recheck(actor, conn)
            now = await db_time(conn)
            row = await one(
                conn,
                "SELECT * FROM submission_receipts WHERE id=%s AND participant_id=%s AND receipt_expires_at>%s",
                (submission_id, actor.id, now),
            )
            if actor.role != "student" or not row:
                raise Problem(404, "NOT_FOUND")
            self._require_features(row.get("required_client_features", ""), client_features)
            return receipt_payload(row, now)

    async def lookup_receipt(self, actor, key, client_features=""):
        async with self.db.connection() as conn:
            await self.auth.recheck(actor, conn)
            now = await db_time(conn)
            row = await one(
                conn,
                "SELECT * FROM submission_receipts WHERE participant_id=%s AND idempotency_key=%s AND receipt_expires_at>%s",
                (actor.id, key, now),
            )
            if actor.role != "student" or not row:
                raise Problem(404, "NOT_FOUND")
            self._require_features(row.get("required_client_features", ""), client_features)
            return receipt_payload(row, now)

    async def submit(self, actor, key, content, client_features=""):
        if actor.role != "student":
            raise Problem(403, "FORBIDDEN")
        try:
            content = normalize_content(content)
        except (ValueError, UnicodeError, TimeoutError):
            raise Problem(422, "VALIDATION_ERROR", "글은 제어문자 없이 1~30자로 작성해 주세요.") from None
        fingerprint = hmac.digest(self.fingerprint_key, _canonical({"version": "2", "content": content}), "sha256")
        for attempt in range(3):
            try:
                return await self._submit(actor, key, content, fingerprint, client_features)
            except (errors.DeadlockDetected, errors.SerializationFailure, errors.LockNotAvailable):
                if attempt == 2:
                    raise Problem(503, "SERVICE_UNAVAILABLE", retry_after=1) from None
                await asyncio.sleep(0.025 * (attempt + 1))

    async def _submit(self, actor, key, content, fingerprint, client_features=""):
        from app.provider_control import ProviderControlService
        from app.submissions import usage_for

        async with self.db.connection() as conn, conn.transaction():
            provider = None
            required_features = ""
            if getattr(self.settings, "moderation_profile", "legacy") != "legacy":
                provider = ProviderControlService(self.db, self.settings)
                await provider.lock_control(conn)
                required_features = "support-v1"
            participant = await one(conn, "SELECT * FROM participants WHERE id=%s FOR UPDATE", (actor.id,))
            if not participant:
                raise Problem(401, "SESSION_EXPIRED", cookie_role="student")
            fresh = await self.auth.recheck(actor, conn)
            now = await db_time(conn)
            existing = await one(
                conn,
                "SELECT * FROM submission_receipts WHERE participant_id=%s AND idempotency_key=%s",
                (actor.id, key),
            )
            if existing and existing["receipt_expires_at"] > now:
                if not hmac.compare_digest(bytes(existing["request_hash"]), fingerprint):
                    raise Problem(409, "IDEMPOTENCY_CONFLICT")
                self._require_features(existing.get("required_client_features", ""), client_features)
                return receipt_payload(existing, now), False
            self._require_features(required_features, client_features)
            if existing:
                # Expired keys may only become a new intent after their durable
                # reservation has been released by maintenance.
                reservation = await one(
                    conn, "SELECT released_at FROM admission_reservations WHERE submission_id=%s", (existing["id"],)
                )
                if reservation and reservation["released_at"] is None:
                    raise Problem(503, "SERVICE_UNAVAILABLE", retry_after=1)
                await conn.execute("DELETE FROM submission_receipts WHERE id=%s", (existing["id"],))
            legacy = await one(
                conn, "SELECT id FROM submissions WHERE participant_id=%s AND idempotency_key=%s", (actor.id, key)
            )
            if legacy:
                raise Problem(409, "IDEMPOTENCY_CONFLICT")
            if self.enqueue is None or not self.intake_ready() or not await schema_ready(conn, self.settings.board_id):
                raise Problem(503, "SERVICE_UNAVAILABLE")
            day = business_day(now)
            await conn.execute(
                "INSERT INTO daily_usage(participant_id,business_date) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                (actor.id, day),
            )
            await one(
                conn,
                "SELECT 1 FROM daily_usage WHERE participant_id=%s AND business_date=%s FOR UPDATE",
                (actor.id, day),
            )
            now = await db_time(conn)
            if business_day(now) != day:
                raise Problem(503, "DAY_ENDING", retry_after=1)
            session = fresh.extra
            if (
                session.get("admission_board_id") != self.settings.board_id
                or not session.get("admission_expires_at")
                or session["admission_expires_at"] <= now
            ):
                raise Problem(403, "ADMISSION_EXPIRED")
            usage = await usage_for(conn, actor.id, now)
            if usage["suspendedUntil"]:
                raise Problem(403, "PARTICIPANT_SUSPENDED")
            if usage["activeSubmissionId"]:
                raise Problem(409, "SUBMISSION_IN_PROGRESS")
            if usage["nextSubmissionAt"]:
                raise Problem(
                    429,
                    "SUBMISSION_COOLDOWN",
                    retry_after=max(1, math.ceil((usage["nextSubmissionAt"] - now).total_seconds())),
                )
            if usage["usedCount"] >= 3:
                raise Problem(
                    429, "QUOTA_EXCEEDED", retry_after=max(1, math.ceil((usage["nextSlotAt"] - now).total_seconds()))
                )
            board = await one(conn, "SELECT * FROM board_runtime WHERE id=%s", (self.settings.board_id,))
            if not board or board["submissions_paused"]:
                raise Problem(503, "INTAKE_PAUSED", retry_after=5)
            owner_until = board["playback_owner_lease_until"]
            if (
                effective_mode(board) != "MOODWALL"
                or not owner_until
                or owner_until <= now
                or board["playback_owner_generation"] != board["device_token_version"]
                or not board["heartbeat_lease_until"]
                or board["heartbeat_lease_until"] <= now
            ):
                raise Problem(503, "DISPLAY_OFFLINE", retry_after=5)
            ready = await one(
                conn,
                "SELECT 1 FROM worker_readiness WHERE worker_name='moderation' AND lease_until>%s AND model_bundle_version=%s",
                (now, self.settings.model_bundle_version),
            )
            if not ready:
                raise Problem(503, "MODEL_NOT_READY", retry_after=5)
            pending = await one(
                conn,
                "SELECT count(*) AS n FROM submissions WHERE board_id=%s AND moderation_status IN ('PENDING','PROCESSING')",
                (self.settings.board_id,),
            )
            cutoff = midnight_after(now) - timedelta(seconds=20)
            prep_deadline = min(now + timedelta(seconds=120), cutoff)
            if now >= cutoff:
                raise Problem(503, "DAY_ENDING", retry_after=20)
            if (
                pending["n"] >= self.settings.moderation_backlog_limit
                or (pending["n"] + 1) * self.settings.moderation_p95_seconds >= (prep_deadline - now).total_seconds()
            ):
                raise Problem(503, "MODERATION_BUSY", retry_after=5)
            pre_deadline = min(
                now + timedelta(seconds=min(600, getattr(self.settings, "pre_display_wait_seconds", 600))), cutoff
            )
            submission_id = uuid4()
            if provider:
                await provider.reserve_submission(conn, submission_id)
            await conn.execute(
                """INSERT INTO submissions(id,participant_id,business_date,board_id,idempotency_key,request_hash,content,emoji_id,
                accepted_at,expires_at,pre_display_deadline_at,moderation_policy_version,lexicon_version)
                VALUES(%s,%s,%s,%s,%s,%s,%s,'star',%s,%s,%s,%s,%s)""",
                (
                    submission_id,
                    actor.id,
                    day,
                    self.settings.board_id,
                    key,
                    fingerprint,
                    content,
                    now,
                    prep_deadline,
                    pre_deadline,
                    self.settings.moderation_policy_version,
                    self.settings.lexicon_version,
                ),
            )
            row = await one(
                conn,
                """INSERT INTO submission_receipts(id,participant_id,board_id,business_date,idempotency_key,request_hash,
                accepted_at,receipt_expires_at,pre_display_deadline_at,required_client_features)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    submission_id,
                    actor.id,
                    self.settings.board_id,
                    day,
                    key,
                    fingerprint,
                    now,
                    now + timedelta(hours=24),
                    pre_deadline,
                    required_features,
                ),
            )
            # The final business lock is this counter, never the board row.
            # Every endpoint accepting new v2 work uses the same conditional
            # UPDATE; concurrent participants cannot both take the last slot.
            capacity = min(32, getattr(self.settings, "playback_capacity_limit", 32))
            await conn.execute(
                "INSERT INTO board_admission_counters(board_id) VALUES(%s) ON CONFLICT DO NOTHING",
                (self.settings.board_id,),
            )
            counter = await one(
                conn,
                "UPDATE board_admission_counters SET reserved_count=reserved_count+1 WHERE board_id=%s AND reserved_count<%s RETURNING reserved_count",
                (self.settings.board_id, capacity),
            )
            if not counter:
                raise Problem(503, "DISPLAY_QUEUE_FULL", retry_after=5)
            final_now = await db_time(conn)
            # Counter serialization also bounds v2 moderation admission. This
            # transaction's new source is already included in this count;
            # earlier contenders have either committed or rolled back before
            # releasing the counter lock.
            serialized_pending = await one(
                conn,
                "SELECT count(*) AS n FROM submissions WHERE board_id=%s AND moderation_status IN ('PENDING','PROCESSING')",
                (self.settings.board_id,),
            )
            if (
                serialized_pending["n"] > self.settings.moderation_backlog_limit
                or serialized_pending["n"] * self.settings.moderation_p95_seconds
                >= (prep_deadline - final_now).total_seconds()
            ):
                raise Problem(503, "MODERATION_BUSY", retry_after=5)
            if final_now >= pre_deadline or final_now >= session["admission_expires_at"]:
                raise Problem(403, "ADMISSION_EXPIRED")
            if (
                final_now + timedelta(seconds=counter["reserved_count"] * 20) >= cutoff
                or final_now + timedelta(seconds=counter["reserved_count"] * 20) >= pre_deadline
            ):
                raise Problem(503, "DISPLAY_QUEUE_FULL", retry_after=5)
            await conn.execute(
                "INSERT INTO admission_reservations(submission_id,board_id,reserved_at) VALUES(%s,%s,%s)",
                (submission_id, self.settings.board_id, now),
            )
            await conn.execute(
                "UPDATE participants SET last_submission_at=%s,last_seen_at=GREATEST(last_seen_at,%s) WHERE id=%s",
                (now, now, actor.id),
            )
            job_id = await self.enqueue(conn, submission_id)
            await conn.execute("UPDATE submissions SET queue_job_id=%s WHERE id=%s", (job_id, submission_id))
            return receipt_payload(row, now), True

    async def sync_submission(self, submission_id):
        """Called only after worker finalization commits. Periodic repair is safe."""
        async with self.db.connection() as conn, conn.transaction():
            board = await self._board(conn)
            source = await one(conn, "SELECT * FROM submissions WHERE id=%s FOR UPDATE", (submission_id,))
            receipt = await one(conn, "SELECT * FROM submission_receipts WHERE id=%s FOR UPDATE", (submission_id,))
            if not receipt:
                return None
            now = await db_time(conn)
            # A hide tombstone also applies to already-purged source content
            # that remains only in the laptop's transparent daily cache.
            hidden = await one(
                conn,
                """SELECT 1 FROM operator_audit_logs WHERE board_id=%s AND target_id=%s
                   AND action='HIDE_SUBMISSION' AND occurred_at>=%s AND occurred_at<%s LIMIT 1""",
                (board["id"], submission_id, midnight_after(now) - timedelta(days=1), midnight_after(now)),
            )
            if hidden and receipt["presentation_status"] != "HIDDEN":
                await self._terminal_locked(conn, board, receipt, "HIDDEN", "DISPLAY_HIDDEN", now)
                updated = await one(conn, "SELECT * FROM submission_receipts WHERE id=%s", (submission_id,))
                result = receipt_payload(updated, now)
            elif not source or receipt["content_status"] == "PURGED":
                return receipt_payload(receipt, now)
            else:
                result = await self._sync_source_locked(conn, board, receipt, source, now)
        await self.release_reservations()
        return result

    async def _sync_source_locked(self, conn, board, receipt, source, now):
        submission_id = receipt["id"]
        if receipt["content_status"] == "PURGED":
            return receipt_payload(receipt, now)
        status = source["moderation_status"]
        if source["display_status"] == "HIDDEN":
            await self._terminal_locked(conn, board, receipt, "HIDDEN", "DISPLAY_HIDDEN", now)
        elif receipt["presentation_status"] in TERMINAL:
            return receipt_payload(receipt, now)
        elif status in {"PENDING", "PROCESSING"}:
            await conn.execute(
                "UPDATE submission_receipts SET processing_status=%s,revision=revision+1 WHERE id=%s AND processing_status<>%s",
                (status, submission_id, status),
            )
        elif status != "APPROVED":
            reason = {
                "REJECTED": "CONTENT_REQUIRES_REWRITE",
                "NEEDS_REWRITE": "CONTENT_REVIEW_UNCERTAIN"
                if source["safety_verdict"] == "UNCERTAIN"
                else "CONTENT_REQUIRES_REWRITE",
                "EXPIRED": "PREPARATION_TIMEOUT",
                "CANCELLED": "DISPLAY_CANCELLED",
            }.get(
                status,
                "INVALID_ANALYSIS_RESULT"
                if source["error_code"] == "INVALID_MODEL_OUTPUT"
                else "PREPARATION_UNAVAILABLE",
            )
            if status == "NEEDS_REWRITE" and "SELF_CRISIS" in source["reason_codes"]:
                reason = "SUPPORT_AVAILABLE"
            await conn.execute(
                "UPDATE submission_receipts SET processing_status=%s,reason_code=%s,quota_status='RELEASED',revision=revision+1 WHERE id=%s",
                (status, reason, submission_id),
            )
            await self._purge_locked(conn, receipt, now)
        elif (
            receipt["first_start_authorized_at"] is None and now >= receipt["pre_display_deadline_at"]
        ) or business_day(now) != receipt["business_date"]:
            await self._terminal_locked(
                conn,
                board,
                receipt,
                "EXPIRED",
                "DAY_ENDED" if business_day(now) != receipt["business_date"] else "PRESENTATION_EXPIRED",
                now,
            )
        elif receipt["presentation_status"] is None:
            if now >= source["expires_at"]:
                await self._terminal_locked(conn, board, receipt, "FAILED", "PREPARATION_TIMEOUT", now)
            elif source["emotion"] not in ANIMATIONS or source["safety_verdict"] != "ALLOW":
                await self._terminal_locked(conn, board, receipt, "FAILED", "INVALID_ANALYSIS_RESULT", now)
            else:
                sequence = await one(
                    conn,
                    "UPDATE board_runtime SET next_ready_sequence=next_ready_sequence+1,state_version=state_version+1 WHERE id=%s RETURNING next_ready_sequence",
                    (board["id"],),
                )
                await conn.execute(
                    """INSERT INTO presentation_items(submission_id,board_id,ready_sequence,ready_at,scene_id,scene_seed)
                        VALUES(%s,%s,%s,%s,%s,%s)""",
                    (
                        submission_id,
                        board["id"],
                        sequence["next_ready_sequence"],
                        now,
                        source["emotion"].lower(),
                        hashlib.sha256(str(submission_id).encode()).hexdigest()[:16],
                    ),
                )
                await conn.execute("UPDATE submissions SET display_status='QUEUED' WHERE id=%s", (submission_id,))
                await conn.execute(
                    "UPDATE submission_receipts SET processing_status='APPROVED',presentation_status='READY',revision=revision+1 WHERE id=%s",
                    (submission_id,),
                )
        updated = await one(conn, "SELECT * FROM submission_receipts WHERE id=%s", (submission_id,))
        return receipt_payload(updated, now)

    async def publish_ready(self, submission_id):
        return await self.sync_submission(submission_id)

    async def _purge_locked(self, conn, receipt, now):
        await conn.execute("DELETE FROM submissions WHERE id=%s", (receipt["id"],))
        await conn.execute(
            "UPDATE submission_receipts SET content_status='PURGED',purged_at=COALESCE(purged_at,%s),revision=revision+1 WHERE id=%s AND content_status<>'PURGED'",
            (now, receipt["id"]),
        )

    async def _terminal_locked(self, conn, board, receipt, status, reason, now):
        item = await one(conn, "SELECT * FROM presentation_items WHERE submission_id=%s FOR UPDATE", (receipt["id"],))
        if item:
            await conn.execute(
                "UPDATE playback_attempts SET status='INTERRUPTED',interrupted_at=%s,interrupt_reason=%s WHERE item_id=%s AND status IN ('CLAIMED','START_AUTHORIZED','PLAYING')",
                (now, "HIDDEN" if status == "HIDDEN" else "CONTENT_EXPIRED", item["id"]),
            )
            await conn.execute("UPDATE presentation_items SET status=%s WHERE id=%s", (status, item["id"]))
            if board["playback_current_item_id"] == item["id"]:
                board["playback_current_item_id"] = None
                await conn.execute("UPDATE board_runtime SET playback_current_item_id=NULL WHERE id=%s", (board["id"],))
        await conn.execute(
            """UPDATE submission_receipts SET presentation_status=%s,reason_code=%s,
            quota_status=CASE WHEN first_start_authorized_at IS NULL THEN 'RELEASED' ELSE quota_status END,
            revision=revision+1 WHERE id=%s""",
            (status, reason, receipt["id"]),
        )
        await self._purge_locked(conn, receipt, now)
        await conn.execute("UPDATE board_runtime SET state_version=state_version+1 WHERE id=%s", (board["id"],))

    @staticmethod
    def _payload_identity(payload):
        try:
            instance = UUID(str(payload["clientInstanceId"]))
            generation = int(payload["deviceGeneration"])
            epoch = int(payload.get("playbackEpoch") or 0)
            if generation < 0 or epoch < 0:
                raise ValueError
            return instance, generation, epoch
        except (KeyError, ValueError, TypeError, AttributeError):
            raise Problem(422, "VALIDATION_ERROR") from None

    async def _own_locked(self, conn, board, instance, generation, now, *, acquire=False):
        if generation != board["device_token_version"]:
            raise Problem(409, "PLAYBACK_STALE")
        valid_owner = (
            board["playback_owner_instance_id"] is not None
            and board["playback_owner_generation"] == generation
            and board["playback_owner_lease_until"] > now
        )
        if valid_owner and board["playback_owner_instance_id"] != instance:
            raise Problem(409, "PLAYBACK_OWNED")
        if not valid_owner and not acquire:
            raise Problem(409, "PLAYBACK_STALE")
        lease = min(now + timedelta(seconds=30), board["device_token_expires_at"])
        board.update(
            playback_owner_instance_id=instance, playback_owner_generation=generation, playback_owner_lease_until=lease
        )
        await conn.execute(
            "UPDATE board_runtime SET playback_owner_instance_id=%s,playback_owner_generation=%s,playback_owner_lease_until=%s WHERE id=%s",
            (instance, generation, lease, board["id"]),
        )
        return lease

    async def heartbeat(self, actor, client_instance_id):
        if client_instance_id is None:
            raise Problem(422, "VALIDATION_ERROR")
        instance = UUID(str(client_instance_id))
        async with self.db.connection() as conn, conn.transaction():
            board = await self._board(conn)
            await self._authorize(actor, conn, display_only=True)
            now = await db_time(conn)
            await self._settle_locked(conn, board, now)
            lease = await self._own_locked(conn, board, instance, board["device_token_version"], now, acquire=True)
            await conn.execute(
                "UPDATE board_runtime SET last_heartbeat_at=%s,heartbeat_lease_until=%s,state_version=state_version+1 WHERE id=%s",
                (now, lease, board["id"]),
            )
            if board["playback_current_item_id"]:
                # Do not turn asset-loading or a lost first-frame report into
                # an unbounded reservation by extending its claim deadline.
                await conn.execute(
                    """UPDATE playback_attempts SET lease_until=LEAST(%s,start_authorized_at+interval '45 seconds')
                    WHERE item_id=%s AND client_instance_id=%s AND device_generation=%s
                    AND status='PLAYING' AND lease_until>%s""",
                    (lease, board["playback_current_item_id"], instance, board["device_token_version"], now),
                )
            return await self._envelope(conn, board["id"], now)

    async def state(self, actor, client_instance_id=None):
        async with self.db.connection() as conn, conn.transaction():
            await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            await self._authorize(actor, conn)
            return await self._envelope(conn, self.settings.board_id, await db_time(conn))

    async def _envelope(self, conn, board_id, now, attempt_id=None):
        board = await one(conn, "SELECT * FROM board_runtime WHERE id=%s", (board_id,))
        if not board:
            raise Problem(503, "SERVICE_UNAVAILABLE")
        if attempt_id:
            attempt = await one(
                conn, "SELECT * FROM playback_attempts WHERE id=%s AND board_id=%s", (attempt_id, board_id)
            )
        elif board["playback_current_item_id"]:
            attempt = await one(
                conn,
                "SELECT * FROM playback_attempts WHERE item_id=%s ORDER BY attempt_number DESC LIMIT 1",
                (board["playback_current_item_id"],),
            )
        else:
            attempt = None
        view = None
        if attempt:
            item = await one(conn, "SELECT * FROM presentation_items WHERE id=%s", (attempt["item_id"],))
            receipt = await one(conn, "SELECT * FROM submission_receipts WHERE id=%s", (item["submission_id"],))
            source = await one(conn, "SELECT * FROM submissions WHERE id=%s", (item["submission_id"],))
            hidden = await one(
                conn,
                """SELECT 1 FROM operator_audit_logs WHERE board_id=%s AND target_id=%s
                   AND action='HIDE_SUBMISSION' AND occurred_at>=%s AND occurred_at<%s LIMIT 1""",
                (board_id, item["submission_id"], midnight_after(now) - timedelta(days=1), midnight_after(now)),
            )
            hidden = bool(hidden or (source and source["display_status"] == "HIDDEN"))
            visible = bool(
                source
                and not hidden
                and receipt["content_status"] == "AVAILABLE"
                and source["display_status"] != "HIDDEN"
                and receipt["business_date"] == business_day(now)
                and (receipt["content_delete_at"] is None or receipt["content_delete_at"] > now)
                and (receipt["first_start_authorized_at"] is not None or receipt["pre_display_deadline_at"] > now)
            )
            view = {
                "attemptId": str(attempt["id"]),
                "submissionId": str(receipt["id"]),
                "status": attempt["status"],
                "presentationStatus": "HIDDEN" if hidden else item["status"],
                "reasonCode": "DISPLAY_HIDDEN" if hidden else receipt["reason_code"],
                "attemptNumber": attempt["attempt_number"],
                "clientInstanceId": str(attempt["client_instance_id"]),
                "deviceGeneration": str(attempt["device_generation"]),
                "playbackEpoch": str(attempt["playback_epoch"]),
                "claimedAt": attempt["claimed_at"],
                "startAuthorizedAt": attempt["start_authorized_at"],
                "startNotAfter": attempt["start_not_after"],
                "firstFrameAt": attempt["first_frame_at"],
                "completedAt": attempt["completed_at"],
                "leaseUntil": attempt["lease_until"],
                "item": self._item_payload(item, receipt, source) if visible else None,
            }
        waiting = await one(
            conn,
            "SELECT count(*) AS n FROM presentation_items WHERE board_id=%s AND status IN ('READY','INTERRUPTED')",
            (board_id,),
        )
        return {
            "displayProtocolVersion": "2",
            "boardId": str(board_id),
            "deviceGeneration": str(board["device_token_version"]),
            "playbackEpoch": str(board["playback_epoch"]),
            "clientInstanceId": str(board["playback_owner_instance_id"])
            if board["playback_owner_instance_id"]
            else None,
            "leaseUntil": board["playback_owner_lease_until"],
            "serverNow": now,
            "revision": str(board["state_version"]),
            "mode": effective_mode(board),
            "waitingCount": waiting["n"],
            "attempt": view,
        }

    @staticmethod
    def _item_payload(item, receipt, source):
        return {
            "displayProtocolVersion": "2",
            "id": str(receipt["id"]),
            "content": source["content"],
            "emotion": source["emotion"],
            "animationId": ANIMATIONS[source["emotion"]],
            "sceneId": item["scene_id"],
            "sceneVersion": item["scene_version"],
            "sceneSeed": item["scene_seed"],
            "readySequence": str(item["ready_sequence"]),
            "acceptedAt": receipt["accepted_at"],
            "firstStartAuthorizedAt": receipt["first_start_authorized_at"],
            "contentDeleteAt": receipt["content_delete_at"],
            "ambientUntil": midnight_after(receipt["accepted_at"]),
        }

    @staticmethod
    def _command_hash(action, attempt_id, payload):
        return hashlib.sha256(
            _canonical({"action": action, "attemptId": str(attempt_id) if attempt_id else None, "payload": payload})
        ).digest()

    async def _replay_locked(self, conn, board, key, digest, now):
        command = await one(
            conn, "SELECT * FROM playback_commands WHERE board_id=%s AND command_key=%s", (board["id"], key)
        )
        if not command:
            return None
        if not hmac.compare_digest(bytes(command["request_hash"]), digest):
            raise Problem(409, "IDEMPOTENCY_CONFLICT")
        return await self._envelope(conn, board["id"], now, command["attempt_id"])

    async def _save_command(self, conn, board, key, digest, action, attempt_id, now):
        await conn.execute(
            "INSERT INTO playback_commands(board_id,command_key,request_hash,action,attempt_id,created_at) VALUES(%s,%s,%s,%s,%s,%s)",
            (board["id"], key, digest, action, attempt_id, now),
        )

    async def claim(self, actor, key, payload):
        instance, generation, epoch = self._payload_identity(payload)
        digest = self._command_hash("claim", None, payload)
        async with self.db.connection() as conn, conn.transaction():
            board = await self._board(conn)
            await self._authorize(actor, conn, display_only=True)
            now = await db_time(conn)
            replay = await self._replay_locked(conn, board, key, digest, now)
            if replay:
                return replay
            await self._settle_locked(conn, board, now)
            if effective_mode(board) != "MOODWALL":
                raise Problem(409, "PLAYBACK_NOT_READY")
            if generation != board["device_token_version"] or (
                payload.get("playbackEpoch") is not None and epoch != board["playback_epoch"]
            ):
                raise Problem(409, "PLAYBACK_STALE")
            await self._own_locked(conn, board, instance, generation, now, acquire=True)
            current = None
            if board["playback_current_item_id"]:
                current = await one(
                    conn,
                    "SELECT * FROM presentation_items WHERE id=%s FOR UPDATE",
                    (board["playback_current_item_id"],),
                )
                if current and current["status"] in ACTIVE:
                    raise Problem(409, "PLAYBACK_NOT_READY")
            item = (
                current
                if current and current["status"] == "INTERRUPTED"
                else await one(
                    conn,
                    "SELECT * FROM presentation_items WHERE board_id=%s AND status='READY' ORDER BY ready_sequence LIMIT 1 FOR UPDATE",
                    (board["id"],),
                )
            )
            if item is None:
                return None
            source = await one(conn, "SELECT * FROM submissions WHERE id=%s FOR UPDATE", (item["submission_id"],))
            receipt = await one(
                conn, "SELECT * FROM submission_receipts WHERE id=%s FOR UPDATE", (item["submission_id"],)
            )
            if not self._can_play(receipt, source, now) or item["attempt_count"] >= 2:
                await self._terminal_locked(
                    conn,
                    board,
                    receipt,
                    "EXPIRED" if item["attempt_count"] < 2 else "FAILED",
                    "PRESENTATION_EXPIRED"
                    if item["attempt_count"] < 2
                    else "PLAYBACK_UNCONFIRMED"
                    if receipt["first_start_authorized_at"] is not None
                    else "PREPARATION_UNAVAILABLE",
                    now,
                )
                return None
            board["playback_epoch"] += 1
            board["playback_current_item_id"] = item["id"]
            await conn.execute(
                "UPDATE board_runtime SET playback_epoch=%s,playback_current_item_id=%s,state_version=state_version+1 WHERE id=%s",
                (board["playback_epoch"], item["id"], board["id"]),
            )
            await conn.execute(
                "UPDATE presentation_items SET status='CLAIMED',attempt_count=attempt_count+1 WHERE id=%s",
                (item["id"],),
            )
            attempt = await one(
                conn,
                """INSERT INTO playback_attempts(item_id,board_id,client_instance_id,device_generation,playback_epoch,
                attempt_number,claimed_at,lease_until) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    item["id"],
                    board["id"],
                    instance,
                    generation,
                    board["playback_epoch"],
                    item["attempt_count"] + 1,
                    now,
                    min(now + timedelta(seconds=10), board["device_token_expires_at"]),
                ),
            )
            await conn.execute(
                "UPDATE submission_receipts SET presentation_status='CLAIMED',revision=revision+1 WHERE id=%s",
                (receipt["id"],),
            )
            await self._save_command(conn, board, key, digest, "claim", attempt["id"], now)
            return await self._envelope(conn, board["id"], now, attempt["id"])

    @staticmethod
    def _can_play(receipt, source, now):
        if (
            not source
            or source["moderation_status"] != "APPROVED"
            or source["display_status"] == "HIDDEN"
            or receipt["content_status"] != "AVAILABLE"
            or business_day(now) != receipt["business_date"]
        ):
            return False
        if receipt["first_start_authorized_at"] is None:
            return now < receipt["pre_display_deadline_at"] and now < midnight_after(now) - timedelta(seconds=20)
        return now + timedelta(seconds=20) < receipt["content_delete_at"] and now < midnight_after(now) - timedelta(
            seconds=20
        )

    async def command(self, actor, attempt_id, action, key, payload):
        if action not in {"start", "frame", "complete", "interrupt"}:
            raise Problem(422, "VALIDATION_ERROR")
        instance, generation, epoch = self._payload_identity(payload)
        if action == "interrupt" and payload.get("reason") not in INTERRUPT_REASONS:
            raise Problem(422, "VALIDATION_ERROR")
        digest = self._command_hash(action, attempt_id, payload)
        async with self.db.connection() as conn, conn.transaction():
            board = await self._board(conn)
            await self._authorize(actor, conn, display_only=True)
            now = await db_time(conn)
            replay = await self._replay_locked(conn, board, key, digest, now)
            if replay:
                return replay
            await self._settle_locked(conn, board, now)
            attempt = await one(
                conn,
                "SELECT * FROM playback_attempts WHERE id=%s AND board_id=%s FOR UPDATE",
                (attempt_id, board["id"]),
            )
            if (
                not attempt
                or attempt["client_instance_id"] != instance
                or attempt["device_generation"] != generation
                or attempt["playback_epoch"] != epoch
                or generation != board["device_token_version"]
                or epoch != board["playback_epoch"]
            ):
                raise Problem(409, "PLAYBACK_STALE")
            if attempt["status"] not in ACTIVE or board["playback_current_item_id"] != attempt["item_id"]:
                raise Problem(409, "PLAYBACK_STALE")
            await self._own_locked(conn, board, instance, generation, now)
            item = await one(conn, "SELECT * FROM presentation_items WHERE id=%s FOR UPDATE", (attempt["item_id"],))
            source = await one(conn, "SELECT * FROM submissions WHERE id=%s FOR UPDATE", (item["submission_id"],))
            receipt = await one(
                conn, "SELECT * FROM submission_receipts WHERE id=%s FOR UPDATE", (item["submission_id"],)
            )
            if (
                not source
                or source["display_status"] == "HIDDEN"
                or receipt["content_status"] != "AVAILABLE"
                or (receipt["content_delete_at"] and now >= receipt["content_delete_at"])
                or (receipt["first_start_authorized_at"] is None and now >= receipt["pre_display_deadline_at"])
            ):
                raise Problem(410, "PRESENTATION_EXPIRED")
            if effective_mode(board) != "MOODWALL" and action != "interrupt":
                raise Problem(409, "PLAYBACK_NOT_READY")
            if action == "start":
                if attempt["status"] != "CLAIMED":
                    raise Problem(409, "PLAYBACK_NOT_READY")
                if not self._can_play(receipt, source, now):
                    raise Problem(410, "PRESENTATION_EXPIRED")
                lease = min(now + timedelta(seconds=30), board["device_token_expires_at"])
                if (lease - now).total_seconds() < 20:
                    raise Problem(409, "PLAYBACK_NOT_READY")
                await conn.execute(
                    "UPDATE playback_attempts SET status='START_AUTHORIZED',start_authorized_at=%s,start_not_after=%s,lease_until=%s WHERE id=%s",
                    (now, now + timedelta(seconds=5), lease, attempt_id),
                )
                await conn.execute(
                    "UPDATE board_runtime SET playback_owner_lease_until=%s WHERE id=%s", (lease, board["id"])
                )
                await conn.execute("UPDATE presentation_items SET status='START_AUTHORIZED' WHERE id=%s", (item["id"],))
                await conn.execute(
                    """UPDATE submission_receipts SET presentation_status='START_AUTHORIZED',
                    first_start_authorized_at=COALESCE(first_start_authorized_at,%s),
                    content_delete_at=COALESCE(content_delete_at,%s),quota_status='CONSUMED',revision=revision+1 WHERE id=%s""",
                    (now, now + timedelta(hours=1), receipt["id"]),
                )
                await conn.execute(
                    "UPDATE submissions SET first_start_authorized_at=COALESCE(first_start_authorized_at,%s),content_delete_at=COALESCE(content_delete_at,%s) WHERE id=%s",
                    (now, now + timedelta(hours=1), receipt["id"]),
                )
            elif action == "frame":
                if attempt["status"] != "START_AUTHORIZED":
                    raise Problem(409, "PLAYBACK_NOT_READY")
                if now >= attempt["start_not_after"]:
                    raise Problem(409, "START_AUTHORIZATION_EXPIRED")
                await conn.execute(
                    "UPDATE playback_attempts SET status='PLAYING',first_frame_at=%s WHERE id=%s", (now, attempt_id)
                )
                await conn.execute("UPDATE presentation_items SET status='PLAYING' WHERE id=%s", (item["id"],))
                await conn.execute(
                    "UPDATE submission_receipts SET presentation_status='PLAYING',revision=revision+1 WHERE id=%s",
                    (receipt["id"],),
                )
            elif action == "complete":
                if attempt["status"] != "PLAYING":
                    raise Problem(409, "PLAYBACK_NOT_READY")
                if now < attempt["first_frame_at"] + timedelta(seconds=15):
                    raise Problem(
                        409,
                        "PLAYBACK_TOO_EARLY",
                        retry_after=max(
                            1, math.ceil((attempt["first_frame_at"] + timedelta(seconds=15) - now).total_seconds())
                        ),
                    )
                await conn.execute(
                    "UPDATE playback_attempts SET status='DONE',completed_at=%s WHERE id=%s", (now, attempt_id)
                )
                await conn.execute("UPDATE presentation_items SET status='DONE' WHERE id=%s", (item["id"],))
                await conn.execute(
                    "UPDATE submission_receipts SET presentation_status='DONE',completed_at=%s,revision=revision+1 WHERE id=%s",
                    (now, receipt["id"]),
                )
                await conn.execute("UPDATE submissions SET display_status='FLOATING' WHERE id=%s", (receipt["id"],))
                await conn.execute("UPDATE board_runtime SET playback_current_item_id=NULL WHERE id=%s", (board["id"],))
            else:
                await self._interrupt_locked(conn, board, item, attempt, receipt, payload["reason"], now)
            await conn.execute("UPDATE board_runtime SET state_version=state_version+1 WHERE id=%s", (board["id"],))
            await self._save_command(conn, board, key, digest, action, attempt_id, now)
            result = await self._envelope(conn, board["id"], now, attempt_id)
        await self.release_reservations()
        return result

    async def _interrupt_locked(self, conn, board, item, attempt, receipt, reason, now):
        await conn.execute(
            "UPDATE playback_attempts SET status='INTERRUPTED',interrupted_at=%s,interrupt_reason=%s WHERE id=%s AND status IN ('CLAIMED','START_AUTHORIZED','PLAYING')",
            (now, reason, attempt["id"]),
        )
        if item["attempt_count"] >= 2 or not await one(
            conn, "SELECT id FROM submissions WHERE id=%s", (receipt["id"],)
        ):
            reason = (
                "PLAYBACK_UNCONFIRMED"
                if receipt["first_start_authorized_at"] is not None
                else "PREPARATION_UNAVAILABLE"
            )
            await self._terminal_locked(conn, board, receipt, "FAILED", reason, now)
        else:
            await conn.execute("UPDATE presentation_items SET status='INTERRUPTED' WHERE id=%s", (item["id"],))
            await conn.execute(
                "UPDATE submission_receipts SET presentation_status='INTERRUPTED',revision=revision+1 WHERE id=%s",
                (receipt["id"],),
            )

    async def _settle_locked(self, conn, board, now):
        """No implicit success: a missing ACK interrupts and gets one replay."""
        if not board["playback_current_item_id"]:
            return
        item = await one(
            conn, "SELECT * FROM presentation_items WHERE id=%s FOR UPDATE", (board["playback_current_item_id"],)
        )
        if not item:
            board["playback_current_item_id"] = None
            return
        receipt = await one(conn, "SELECT * FROM submission_receipts WHERE id=%s FOR UPDATE", (item["submission_id"],))
        source = await one(conn, "SELECT * FROM submissions WHERE id=%s FOR UPDATE", (item["submission_id"],))
        if not source or source["display_status"] == "HIDDEN" or receipt["content_status"] == "PURGED":
            await self._terminal_locked(
                conn,
                board,
                receipt,
                "HIDDEN" if source and source["display_status"] == "HIDDEN" else "EXPIRED",
                "DISPLAY_HIDDEN" if source and source["display_status"] == "HIDDEN" else "PRESENTATION_EXPIRED",
                now,
            )
            return
        if (receipt["content_delete_at"] and now >= receipt["content_delete_at"]) or (
            receipt["first_start_authorized_at"] is None and now >= receipt["pre_display_deadline_at"]
        ):
            await self._terminal_locked(conn, board, receipt, "EXPIRED", "PRESENTATION_EXPIRED", now)
            return
        attempt = await one(
            conn,
            "SELECT * FROM playback_attempts WHERE item_id=%s ORDER BY attempt_number DESC LIMIT 1 FOR UPDATE",
            (item["id"],),
        )
        if not attempt or attempt["status"] not in ACTIVE:
            return
        reason = None
        if attempt["device_generation"] != board["device_token_version"]:
            reason = "DEVICE_CHANGED"
        elif business_day(now) != receipt["business_date"]:
            reason = "DAY_ENDED"
        elif effective_mode(board) != "MOODWALL":
            reason = "MODE_CHANGED"
        elif attempt["lease_until"] <= now:
            reason = "LEASE_EXPIRED"
        elif attempt["start_authorized_at"] and now >= attempt["start_authorized_at"] + timedelta(seconds=45):
            # A live heartbeat is liveness, not evidence of fifteen completed
            # seconds. A broken renderer must not hold the wall indefinitely.
            reason = "LEASE_EXPIRED"
        elif attempt["status"] == "START_AUTHORIZED" and now >= attempt["start_not_after"]:
            reason = "LEASE_EXPIRED"
        if reason:
            await self._interrupt_locked(conn, board, item, attempt, receipt, reason, now)

    async def settle_mode_change(self, conn, board, now):
        """Record a mode interruption in the caller's already-locked board tx.

        The caller updates both SQL and the supplied board dictionary first.
        A subsequent rapid resume must not erase the interruption before the
        periodic reconciler observes it. No participant or admission-counter
        lock may be acquired here.
        """
        await self._settle_locked(conn, board, now)

    async def release_reservations(self):
        """Separate lock domain: counter -> reservation, never board afterwards."""
        async with self.db.connection() as conn, conn.transaction():
            await one(
                conn,
                "SELECT board_id FROM board_admission_counters WHERE board_id=%s FOR UPDATE",
                (self.settings.board_id,),
            )
            rows = await all_rows(
                conn,
                """SELECT a.submission_id FROM admission_reservations a JOIN submission_receipts r ON r.id=a.submission_id
                WHERE a.board_id=%s AND a.released_at IS NULL AND
                (r.quota_status='RELEASED' OR r.presentation_status IN ('DONE','FAILED','EXPIRED','CANCELLED','HIDDEN'))
                ORDER BY a.submission_id LIMIT 100 FOR UPDATE OF a""",
                (self.settings.board_id,),
            )
            if rows:
                ids = [row["submission_id"] for row in rows]
                await conn.execute(
                    "UPDATE admission_reservations SET released_at=clock_timestamp() WHERE submission_id=ANY(%s) AND released_at IS NULL",
                    (ids,),
                )
                await conn.execute(
                    "UPDATE board_admission_counters SET reserved_count=GREATEST(0,reserved_count-%s) WHERE board_id=%s",
                    (len(rows), self.settings.board_id),
                )
            return len(rows)

    async def reconcile(self):
        # Repair a crash between finalization and READY publication. Sequence
        # order is the serialization order of these commits, never acceptedAt.
        async with self.db.connection() as conn:
            candidates = await all_rows(
                conn,
                """SELECT s.id FROM submissions s JOIN submission_receipts r ON r.id=s.id
                WHERE r.content_status='AVAILABLE' AND (r.processing_status<>s.moderation_status
                OR (s.moderation_status='APPROVED' AND r.presentation_status IS NULL)
                OR (s.display_status='HIDDEN' AND r.presentation_status IS DISTINCT FROM 'HIDDEN'))
                ORDER BY s.finalized_at NULLS LAST,s.id LIMIT 100""",
            )
        for row in candidates:
            await self.sync_submission(row["id"])
        async with self.db.connection() as conn, conn.transaction():
            board = await self._board(conn)
            now = await db_time(conn)
            await self._settle_locked(conn, board, now)
            rows = await all_rows(
                conn,
                """SELECT * FROM submission_receipts WHERE board_id=%s AND content_status='AVAILABLE'
                AND ((first_start_authorized_at IS NULL AND pre_display_deadline_at<=%s)
                OR (business_date<%s AND presentation_status IN ('READY','CLAIMED','INTERRUPTED')))
                ORDER BY accepted_at,id LIMIT 100 FOR UPDATE""",
                (board["id"], now, business_day(now)),
            )
            for receipt in rows:
                source = await one(conn, "SELECT id FROM submissions WHERE id=%s FOR UPDATE", (receipt["id"],))
                if source:
                    await self._terminal_locked(
                        conn,
                        board,
                        receipt,
                        "EXPIRED",
                        "DAY_ENDED" if receipt["business_date"] < business_day(now) else "PRESENTATION_EXPIRED",
                        now,
                    )
            await conn.execute("UPDATE board_runtime SET last_reconciled_at=%s WHERE id=%s", (now, board["id"]))
        await self.release_reservations()
        return len(candidates) + len(rows)

    async def cleanup(self):
        await self.reconcile()
        async with self.db.connection() as conn, conn.transaction():
            board = await self._board(conn)
            now = await db_time(conn)
            rows = await all_rows(
                conn,
                "SELECT * FROM submission_receipts WHERE board_id=%s AND content_status='AVAILABLE' AND content_delete_at<=%s ORDER BY content_delete_at,id LIMIT 100 FOR UPDATE",
                (board["id"], now),
            )
            for receipt in rows:
                if receipt["presentation_status"] not in TERMINAL:
                    await self._terminal_locked(conn, board, receipt, "FAILED", "PLAYBACK_UNCONFIRMED", now)
                else:
                    await self._purge_locked(conn, receipt, now)
            await conn.execute(
                """DELETE FROM submission_receipts r WHERE receipt_expires_at<=%s AND NOT EXISTS
                (SELECT 1 FROM admission_reservations a WHERE a.submission_id=r.id AND a.released_at IS NULL)""",
                (now,),
            )
        await self.release_reservations()
        return len(rows)

    async def v2snapshot(self, actor, cursor=None):
        async with self.db.connection() as conn, conn.transaction():
            await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            await self._authorize(actor, conn)
            now = await db_time(conn)
            board = await one(conn, "SELECT * FROM board_runtime WHERE id=%s", (self.settings.board_id,))
            if not board:
                raise Problem(503, "SERVICE_UNAVAILABLE")
            ambient = midnight_after(now)
            if cursor:
                page = self.cursors.decode(cursor, now, board)
            else:
                page = {
                    "v": 1,
                    "boardId": str(board["id"]),
                    "date": business_day(now).isoformat(),
                    "generation": str(board["device_token_version"]),
                    "snapshotAt": now.isoformat(),
                    "lastAt": None,
                    "lastId": None,
                    "exp": int(min(now + timedelta(minutes=5), ambient).timestamp()),
                }
            snapshot = datetime.fromisoformat(page["snapshotAt"])
            clause = """r.board_id=%s AND r.business_date=%s AND r.content_status='AVAILABLE'
                AND r.content_delete_at>%s AND p.status='DONE' AND r.completed_at<=%s
                AND s.display_status='FLOATING' AND NOT EXISTS (SELECT 1 FROM operator_audit_logs a
                    WHERE a.board_id=r.board_id AND a.target_id=r.id AND a.action='HIDE_SUBMISSION'
                    AND a.occurred_at>=%s AND a.occurred_at<%s)"""
            params = [board["id"], business_day(now), now, snapshot, ambient - timedelta(days=1), ambient]
            count = await one(
                conn,
                "SELECT count(*) AS n FROM submission_receipts r JOIN submissions s ON s.id=r.id JOIN presentation_items p ON p.submission_id=r.id WHERE "
                + clause,
                tuple(params),
            )
            if cursor:
                clause += " AND (r.completed_at,r.id)>(%s,%s)"
                params.extend((datetime.fromisoformat(page["lastAt"]), UUID(page["lastId"])))
            rows = await all_rows(
                conn,
                """SELECT r.*,s.content,s.emotion,p.scene_id,p.scene_version,p.scene_seed,p.ready_sequence
                FROM submission_receipts r JOIN submissions s ON s.id=r.id JOIN presentation_items p ON p.submission_id=r.id
                WHERE """
                + clause
                + " ORDER BY r.completed_at,r.id LIMIT %s",
                tuple(params + [PAGE_SIZE + 1]),
            )
            more = len(rows) > PAGE_SIZE
            rows = rows[:PAGE_SIZE]
            if more:
                page.update(lastAt=rows[-1]["completed_at"].isoformat(), lastId=str(rows[-1]["id"]))
            removals = await all_rows(
                conn,
                "SELECT id,target_id FROM operator_audit_logs WHERE board_id=%s AND action='HIDE_SUBMISSION' AND target_id IS NOT NULL AND occurred_at>=%s AND occurred_at<%s ORDER BY id DESC LIMIT 1001",
                (board["id"], ambient - timedelta(days=1), ambient),
            )
            return {
                "displayProtocolVersion": "2",
                "boardId": str(board["id"]),
                "businessDate": business_day(now).isoformat(),
                "deviceGeneration": str(board["device_token_version"]),
                "revision": str(board["state_version"]),
                "serverNow": now,
                "snapshotAt": snapshot,
                "ambientUntil": ambient,
                "mode": effective_mode(board),
                "onlineLeaseUntil": board["heartbeat_lease_until"],
                "removedIds": list(dict.fromkeys(str(row["target_id"]) for row in removals[:1000])),
                "cacheResetToken": str(removals[1000]["id"]) if len(removals) > 1000 else "0",
                "bubbles": [self._item_payload(row, row, row) for row in rows],
                "bubbleTotalCount": count["n"],
                "nextCursor": self.cursors.encode(page) if more else None,
            }
