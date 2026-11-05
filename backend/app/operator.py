"""Named operator controls, keyset lists and idempotent restriction recovery."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from psycopg.errors import UniqueViolation

from .db import all_rows, db_time, one
from .display import settle_board
from .domain import MENTION_RETENTION_SECONDS, business_day, serialize_submission
from .errors import Problem

KST = ZoneInfo("Asia/Seoul")
UTC = timezone.utc
REASONS = {
    "INAPPROPRIATE_CONTENT",
    "FALSE_POSITIVE",
    "MAINTENANCE",
    "DEVICE_REPLACED",
    "SECURITY_REVIEW",
    "OTHER",
}
STATUSES = {
    "PENDING",
    "PROCESSING",
    "APPROVED",
    "REJECTED",
    "NEEDS_REWRITE",
    "FAILED",
    "EXPIRED",
    "CANCELLED",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


class CursorCodec:
    """Opaque signed pagination, not an authorization token or MVCC export."""

    def __init__(self, key: bytes):
        self.key = key

    def encode(self, payload: dict[str, Any]) -> str:
        raw = _canonical(payload)
        return _b64(raw) + "." + _b64(hmac.digest(self.key, raw, "sha256"))

    def decode(self, cursor: str, now: datetime) -> dict[str, Any]:
        try:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 2048:
                raise ValueError
            encoded, signature = cursor.split(".")
            raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
            signed = base64.b64decode(signature + "=" * (-len(signature) % 4), altchars=b"-_", validate=True)
            if not hmac.compare_digest(signed, hmac.digest(self.key, raw, "sha256")):
                raise ValueError
            payload = json.loads(raw)
            if not isinstance(payload, dict) or set(payload) != {
                "v",
                "kind",
                "date",
                "status",
                "limit",
                "snapshotAt",
                "lastAt",
                "lastId",
                "exp",
            }:
                raise ValueError
            if type(payload["v"]) is not int or payload["v"] != 1 or payload["kind"] not in ("submissions", "audit"):
                raise ValueError
            if type(payload["limit"]) is not int or not 1 <= payload["limit"] <= 100:
                raise ValueError
            if type(payload["exp"]) is not int or payload["exp"] <= now.timestamp():
                raise ValueError
            date.fromisoformat(payload["date"])
            if payload["status"] is not None and payload["status"] not in STATUSES:
                raise ValueError
            for field in ("snapshotAt", "lastAt"):
                if datetime.fromisoformat(payload[field]).tzinfo is None:
                    raise ValueError
            if not isinstance(payload["lastId"], str):
                raise ValueError
            if payload["kind"] == "submissions":
                UUID(payload["lastId"])
            elif not payload["lastId"].isdigit() or not 0 < int(payload["lastId"]) < 2**63:
                raise ValueError
            return payload
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError) as exc:
            raise Problem(422, "INVALID_CURSOR", "목록을 처음부터 다시 조회해 주세요.") from exc


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, datetime.min.time(), KST)
    return start, start + timedelta(days=1)


class OperatorService:
    def __init__(self, db, settings, auth, display):
        self.db, self.settings, self.auth, self.display = db, settings, auth, display
        self.cursors = CursorCodec(auth.derive_key("moodwall:cursor:v1"))

    def _reason(self, reason_code: str) -> None:
        if reason_code not in REASONS:
            raise Problem(422, "VALIDATION_ERROR")

    async def _authorize(self, actor, conn, *, admin=False, write=False):
        return await self.auth.require_operator(
            actor, ("ADMIN",) if admin else ("ADMIN", "MODERATOR"), conn=conn, write=write
        )

    async def _audit(
        self,
        conn,
        actor,
        action: str,
        reason: str,
        *,
        target_id=None,
        before_count=None,
        after_count=None,
        before_until=None,
        after_until=None,
        command_key=None,
        request_hash=None,
    ):
        return await one(
            conn,
            """INSERT INTO operator_audit_logs
               (operator_id,action,board_id,target_id,reason_code,
                before_violation_count,after_violation_count,before_banned_until,
                after_banned_until,command_key,request_hash)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (
                actor.id,
                action,
                self.settings.board_id,
                target_id,
                reason,
                before_count,
                after_count,
                before_until,
                after_until,
                command_key,
                request_hash,
            ),
        )

    async def create_pairing_code(self, actor, reason_code: str) -> dict[str, Any]:
        self._reason(reason_code)
        async with self.db.connection() as conn:
            async with conn.transaction():
                await self._authorize(actor, conn, admin=True)
                board = await self.display.locked_board(conn)
                await self._authorize(actor, conn, admin=True, write=True)
                now = await db_time(conn)
                code = secrets.token_urlsafe(32)
                until = now + timedelta(minutes=5)
                await conn.execute(
                    """UPDATE board_runtime SET pending_pairing_hash=%s,
                       pending_pairing_expires_at=%s WHERE id=%s""",
                    (hashlib.sha256(code.encode()).digest(), until, board["id"]),
                )
                await self._audit(conn, actor, "CREATE_PAIRING_CODE", reason_code)
                return {"code": code, "expiresAt": until}

    async def revoke_display(self, actor, reason_code: str) -> dict[str, Any]:
        self._reason(reason_code)
        async with self.db.connection() as conn:
            async with conn.transaction():
                await self._authorize(actor, conn, admin=True)
                board = await self.display.locked_board(conn)
                await self._authorize(actor, conn, admin=True, write=True)
                now = await db_time(conn)
                await settle_board(conn, board, now)
                await conn.execute(
                    """UPDATE board_runtime SET device_token_hash=NULL,
                       device_token_expires_at=NULL,paired_at=NULL,
                       device_token_version=device_token_version+1,
                       pending_pairing_hash=NULL,pending_pairing_expires_at=NULL,
                       last_heartbeat_at=NULL,heartbeat_lease_until=NULL,
                       state_version=state_version+1 WHERE id=%s""",
                    (board["id"],),
                )
                await self._audit(conn, actor, "REVOKE_DEVICE", reason_code)
                return {"ok": True, "serverNow": now}

    async def set_mode(self, actor, mode: str, reason_code: str) -> dict[str, Any]:
        self._reason(reason_code)
        if mode not in ("MOODWALL", "CHATBOT", "PAUSED"):
            raise Problem(422, "VALIDATION_ERROR")
        async with self.db.connection() as conn:
            async with conn.transaction():
                await self._authorize(actor, conn, admin=True)
                board = await self.display.locked_board(conn)
                await self._authorize(actor, conn, admin=True, write=True)
                now = await db_time(conn)
                await settle_board(conn, board, now)
                board["display_paused"] = mode == "PAUSED"
                if mode != "PAUSED":
                    board["mode"] = mode
                await conn.execute(
                    """UPDATE board_runtime SET mode=%s,display_paused=%s,
                       state_version=state_version+1 WHERE id=%s""",
                    (board["mode"], board["display_paused"], board["id"]),
                )
                from app.playback import PlaybackService

                await PlaybackService(self.db, self.settings, None).settle_mode_change(conn, board, now)
                await self._audit(conn, actor, "PAUSE_DISPLAY" if mode == "PAUSED" else "SET_BOARD_MODE", reason_code)
        return await self.display.state(actor)

    async def intake(self, actor, paused: bool, reason_code: str) -> dict[str, Any]:
        self._reason(reason_code)
        async with self.db.connection() as conn:
            async with conn.transaction():
                await self._authorize(actor, conn, admin=True)
                board = await self.display.locked_board(conn)
                await self._authorize(actor, conn, admin=True, write=True)
                await conn.execute(
                    """UPDATE board_runtime SET submissions_paused=%s,
                       state_version=state_version+1 WHERE id=%s""",
                    (paused, board["id"]),
                )
                await self._audit(conn, actor, "PAUSE_SUBMISSIONS", reason_code)
                return {"ok": True, "serverNow": await db_time(conn)}

    async def hide(self, actor, submission_id, reason_code: str) -> dict[str, Any]:
        """Forbid this ID on today's display, including a retained client bubble.

        The ID-only audit survives server content retention. The board lock
        serializes repeats, so a missing submission is an idempotent display
        command rather than a reason to restore or retain its content.
        """
        self._reason(reason_code)
        async with self.db.connection() as conn:
            async with conn.transaction():
                await self._authorize(actor, conn)
                board = await self.display.locked_board(conn)
                await self._authorize(actor, conn, write=True)
                now = await db_time(conn)
                today = business_day(now)
                start, end = _day_bounds(today)
                previous = await one(
                    conn,
                    """SELECT id FROM operator_audit_logs
                       WHERE board_id=%s AND action='HIDE_SUBMISSION' AND target_id=%s
                       AND occurred_at>=%s AND occurred_at<%s LIMIT 1""",
                    (board["id"], submission_id, start, end),
                )
                if previous is not None:
                    return {"ok": True, "serverNow": now}
                await settle_board(conn, board, now)
                row = await one(
                    conn,
                    "SELECT * FROM submissions WHERE id=%s FOR UPDATE",
                    (submission_id,),
                )
                if row is not None and row["board_id"] != board["id"]:
                    raise Problem(404, "NOT_FOUND")
                if row is not None and (
                    row["business_date"] != today
                    or row["moderation_status"] != "APPROVED"
                    or row["display_status"] not in {"QUEUED", "FLOATING", "EXPIRED", "HIDDEN"}
                ):
                    raise Problem(409, "INVALID_STATE", "현재 표시 가능한 글이 아닙니다.")
                if row is not None and row["display_status"] in {"QUEUED", "FLOATING"}:
                    await conn.execute(
                        "UPDATE submissions SET display_status='HIDDEN',hidden_at=%s WHERE id=%s",
                        (now, row["id"]),
                    )
                await conn.execute(
                    "UPDATE board_runtime SET state_version=state_version+1 WHERE id=%s",
                    (board["id"],),
                )
                await self._audit(conn, actor, "HIDE_SUBMISSION", reason_code, target_id=submission_id)
                return {"ok": True, "serverNow": now}

    async def usage(self, actor, participant_id) -> dict[str, Any]:
        from .submissions import usage_for

        async with self.db.connection() as conn:
            async with conn.transaction():
                await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                await self._authorize(actor, conn)
                if await one(conn, "SELECT id FROM participants WHERE id=%s", (participant_id,)) is None:
                    raise Problem(404, "NOT_FOUND")
                return await usage_for(conn, participant_id, await db_time(conn))

    async def restore(self, actor, participant_id, business_date: date, key: UUID, reason_code: str) -> dict[str, Any]:
        from .submissions import usage_for

        self._reason(reason_code)
        request_hash = hashlib.sha256(
            _canonical(
                {
                    "v": 1,
                    "participantId": str(participant_id),
                    "businessDate": business_date.isoformat(),
                    "reasonCode": reason_code,
                }
            )
        ).digest()
        try:
            async with self.db.connection() as conn:
                async with conn.transaction():
                    await self._authorize(actor, conn)
                    participant = await one(
                        conn, "SELECT id FROM participants WHERE id=%s FOR UPDATE", (participant_id,)
                    )
                    if participant is None:
                        raise Problem(404, "NOT_FOUND")
                    await self._authorize(actor, conn)
                    previous = await one(
                        conn,
                        """SELECT * FROM operator_audit_logs WHERE operator_id=%s
                           AND action='RESET_RESTRICTION' AND command_key=%s""",
                        (actor.id, key),
                    )
                    now = await db_time(conn)
                    if previous is not None:
                        if not hmac.compare_digest(bytes(previous["request_hash"]), request_hash):
                            raise Problem(409, "IDEMPOTENCY_CONFLICT")
                        audit_id, replayed = previous["id"], True
                    else:
                        if business_date != business_day(now):
                            raise Problem(409, "INVALID_STATE", "당일 제재만 복구할 수 있습니다.")
                        await conn.execute(
                            """INSERT INTO daily_usage(participant_id,business_date)
                               VALUES(%s,%s) ON CONFLICT DO NOTHING""",
                            (participant_id, business_date),
                        )
                        daily = await one(
                            conn,
                            """SELECT * FROM daily_usage WHERE participant_id=%s
                               AND business_date=%s FOR UPDATE""",
                            (participant_id, business_date),
                        )
                        now = await db_time(conn)
                        if business_date != business_day(now):
                            raise Problem(409, "INVALID_STATE", "날짜가 변경되어 다시 확인해 주세요.")
                        await conn.execute(
                            """UPDATE daily_usage SET violation_count=0,banned_until=NULL,
                               updated_at=%s WHERE participant_id=%s AND business_date=%s""",
                            (now, participant_id, business_date),
                        )
                        audit = await self._audit(
                            conn,
                            actor,
                            "RESET_RESTRICTION",
                            reason_code,
                            target_id=participant_id,
                            before_count=daily["violation_count"],
                            after_count=0,
                            before_until=daily["banned_until"],
                            after_until=None,
                            command_key=key,
                            request_hash=request_hash,
                        )
                        audit_id, replayed = audit["id"], False
                    # Audit insertion acquires a KEY SHARE FK lock on board.
                    # Take the operator-session activity write after that lock,
                    # matching board controls and avoiding board/session cycles.
                    await self._authorize(actor, conn, write=True)
                    return {
                        "auditId": str(audit_id),
                        "replayed": replayed,
                        "businessDate": business_date.isoformat(),
                        "usage": await usage_for(conn, participant_id, now),
                    }
        except UniqueViolation as exc:
            if exc.diag.constraint_name != "operator_audit_command_unique_idx":
                raise
            # Different target locks may race on one operator/key. The winning
            # audit is authoritative; replay never applies a second reset.
            async with self.db.connection() as conn:
                await self._authorize(actor, conn)
                previous = await one(
                    conn,
                    """SELECT * FROM operator_audit_logs WHERE operator_id=%s
                       AND action='RESET_RESTRICTION' AND command_key=%s""",
                    (actor.id, key),
                )
                if previous is None or not hmac.compare_digest(bytes(previous["request_hash"]), request_hash):
                    raise Problem(409, "IDEMPOTENCY_CONFLICT") from exc
            return await self.restore(actor, participant_id, business_date, key, reason_code)

    def _page(self, *, kind, now, business_date, status, cursor, limit):
        if type(limit) is not int or not 1 <= limit <= 100 or (status is not None and status not in STATUSES):
            raise Problem(422, "VALIDATION_ERROR")
        if cursor:
            payload = self.cursors.decode(cursor, now)
            if payload["kind"] != kind or payload["limit"] != limit or payload["status"] != status:
                raise Problem(422, "INVALID_CURSOR")
            if business_date is not None and business_date.isoformat() != payload["date"]:
                raise Problem(422, "INVALID_CURSOR")
            return payload
        return {
            "v": 1,
            "kind": kind,
            "date": (business_date or business_day(now)).isoformat(),
            "status": status,
            "limit": limit,
            "snapshotAt": now.isoformat(),
            "lastAt": now.isoformat(),
            "lastId": "",
            "exp": int((now + timedelta(minutes=30)).timestamp()),
        }

    async def list_submissions(
        self, actor, *, business_date=None, status=None, cursor=None, limit=50
    ) -> dict[str, Any]:
        async with self.db.connection() as conn:
            async with conn.transaction():
                await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                await self._authorize(actor, conn)
                now = await db_time(conn)
                page = self._page(
                    kind="submissions",
                    now=now,
                    business_date=business_date,
                    status=status,
                    cursor=cursor,
                    limit=limit,
                )
                clauses = [
                    "board_id=%s",
                    "business_date=%s",
                    "accepted_at<=%s",
                    """((NOT EXISTS (SELECT 1 FROM submission_receipts r WHERE r.id=submissions.id)
                         AND accepted_at>%s)
                        OR EXISTS (SELECT 1 FROM submission_receipts r WHERE r.id=submissions.id
                         AND r.content_status='AVAILABLE' AND r.presentation_status IS DISTINCT FROM 'HIDDEN'
                         AND ((r.first_start_authorized_at IS NOT NULL AND r.content_delete_at>%s)
                              OR (r.first_start_authorized_at IS NULL AND r.pre_display_deadline_at>%s
                                  AND r.quota_status='RESERVED'))))""",
                ]
                params: list[Any] = [
                    self.settings.board_id,
                    date.fromisoformat(page["date"]),
                    datetime.fromisoformat(page["snapshotAt"]),
                    now - timedelta(seconds=MENTION_RETENTION_SECONDS),
                    now,
                    now,
                ]
                if status is not None:
                    clauses.append("moderation_status=%s")
                    params.append(status)
                if cursor:
                    clauses.append("(accepted_at,id)<(%s,%s)")
                    params.extend([datetime.fromisoformat(page["lastAt"]), UUID(page["lastId"])])
                params.append(limit + 1)
                rows = await all_rows(
                    conn,
                    "SELECT * FROM submissions WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY accepted_at DESC,id DESC LIMIT %s",
                    tuple(params),
                )
                more, rows = len(rows) > limit, rows[:limit]
                items = [
                    {
                        "submission": serialize_submission(row),
                        "participantId": str(row["participant_id"]),
                        "reasonCodes": row["reason_codes"],
                        "modelBundleVersion": row["model_bundle_version"],
                        "inferencePolicyVersion": row["inference_policy_version"],
                        "moderationPolicyVersion": row["moderation_policy_version"],
                        "lexiconVersion": row["lexicon_version"],
                        "strikeApplied": row["strike_applied"],
                        "ruleEvidence": [
                            {"ruleId": evidence["rule_id"], "variantId": evidence["variant_id"]}
                            for evidence in row["rule_evidence"]
                        ],
                    }
                    for row in rows
                ]
                if more:
                    page.update(lastAt=rows[-1]["accepted_at"].isoformat(), lastId=str(rows[-1]["id"]))
                return {"items": items, "nextCursor": self.cursors.encode(page) if more else None}

    async def audit(self, actor, *, business_date=None, cursor=None, limit=50) -> dict[str, Any]:
        async with self.db.connection() as conn:
            async with conn.transaction():
                await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                await self._authorize(actor, conn, admin=True)
                page = self._page(
                    kind="audit",
                    now=await db_time(conn),
                    business_date=business_date,
                    status=None,
                    cursor=cursor,
                    limit=limit,
                )
                start, end = _day_bounds(date.fromisoformat(page["date"]))
                clauses = ["a.board_id=%s", "a.occurred_at>=%s", "a.occurred_at<%s", "a.occurred_at<=%s"]
                params: list[Any] = [self.settings.board_id, start, end, datetime.fromisoformat(page["snapshotAt"])]
                if cursor:
                    clauses.append("(a.occurred_at,a.id)<(%s,%s)")
                    params.extend([datetime.fromisoformat(page["lastAt"]), int(page["lastId"])])
                params.append(limit + 1)
                rows = await all_rows(
                    conn,
                    "SELECT a.*,o.login_name FROM operator_audit_logs a JOIN operators o "
                    "ON o.id=a.operator_id WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY a.occurred_at DESC,a.id DESC LIMIT %s",
                    tuple(params),
                )
                more, rows = len(rows) > limit, rows[:limit]
                items = [
                    {
                        "id": str(row["id"]),
                        "operatorUsername": row["login_name"],
                        "action": row["action"],
                        "targetId": str(row["target_id"]) if row["target_id"] is not None else None,
                        "reasonCode": row["reason_code"],
                        "createdAt": row["occurred_at"],
                        "beforeViolationCount": row["before_violation_count"],
                        "afterViolationCount": row["after_violation_count"],
                        "beforeSuspendedUntil": row["before_banned_until"],
                        "afterSuspendedUntil": row["after_banned_until"],
                    }
                    for row in rows
                ]
                if more:
                    page.update(lastAt=rows[-1]["occurred_at"].isoformat(), lastId=str(rows[-1]["id"]))
                return {"items": items, "nextCursor": self.cursors.encode(page) if more else None}
