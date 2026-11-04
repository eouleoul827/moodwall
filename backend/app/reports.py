"""Text-free, owner-bound reports with verified, idempotent operator resolution."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta
from uuid import UUID

from app.db import all_rows, db_time, one
from app.errors import Problem

CATEGORIES = {"HARASSMENT", "PRIVACY", "THREAT", "OTHER"}
RESOLUTIONS = {"HIDDEN", "BOARD_PAUSED", "NO_ACTION", "INSUFFICIENT_EVIDENCE"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def payload(row):
    return {
        "reportId": str(row["id"]),
        "boardId": str(row["board_id"]),
        "submissionId": str(row["submission_id"]) if row["submission_id"] else None,
        "category": row["category"],
        "status": row["status"],
        "receivedAt": row["received_at"],
        "resolvedAt": row["resolved_at"],
        "resolution": row["resolution"],
        "targetAvailable": bool(row.get("target_available", False)),
    }


class ReportService:
    def __init__(self, db, settings, auth):
        self.db, self.settings, self.auth = db, settings, auth
        self.cursor_key = auth.derive_key("moodwall:report-cursor:v1")

    async def submit(self, actor, key, category, submission_id=None):
        if actor.role != "student":
            raise Problem(403, "FORBIDDEN")
        if category not in CATEGORIES:
            raise Problem(422, "VALIDATION_ERROR")
        digest = hashlib.sha256(canonical({"category": category, "submissionId": str(submission_id)})).digest()
        async with self.db.connection() as conn, conn.transaction():
            await one(conn, "SELECT id FROM participants WHERE id=%s FOR UPDATE", (actor.id,))
            fresh = await self.auth.recheck(actor, conn)
            now = await db_time(conn)
            prior = await one(
                conn,
                "SELECT * FROM content_reports WHERE reporter_id=%s AND idempotency_key=%s",
                (actor.id, key),
            )
            if prior:
                if not hmac.compare_digest(bytes(prior["request_hash"]), digest):
                    raise Problem(409, "IDEMPOTENCY_CONFLICT")
                return {"reportId": str(prior["id"]), "status": "RECEIVED"}, False
            if fresh.extra.get("admission_board_id") != self.settings.board_id:
                raise Problem(403, "ADMISSION_EXPIRED")
            count = await one(
                conn,
                "SELECT count(*) AS n,min(received_at) AS first FROM content_reports WHERE reporter_id=%s AND received_at>%s",
                (actor.id, now - timedelta(minutes=10)),
            )
            if count["n"] >= 3:
                retry = max(1, int((count["first"] + timedelta(minutes=10) - now).total_seconds()) + 1)
                raise Problem(429, "REPORT_RATE_LIMITED", "잠시 후 다시 알려 주세요.", retry_after=retry)
            if submission_id:
                target = await one(
                    conn,
                    "SELECT id FROM submission_receipts WHERE id=%s AND board_id=%s AND first_start_authorized_at IS NOT NULL",
                    (submission_id, self.settings.board_id),
                )
                if not target:
                    raise Problem(404, "NOT_FOUND")
            observed = await one(
                conn,
                "SELECT p.submission_id FROM board_runtime b LEFT JOIN presentation_items p ON p.id=b.playback_current_item_id WHERE b.id=%s",
                (self.settings.board_id,),
            )
            row = await one(
                conn,
                """INSERT INTO content_reports(board_id,reporter_id,idempotency_key,request_hash,
                   submission_id,observed_submission_id,category,received_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (
                    self.settings.board_id,
                    actor.id,
                    key,
                    digest,
                    submission_id,
                    observed["submission_id"] if observed else None,
                    category,
                    now,
                ),
            )
            return {"reportId": str(row["id"]), "status": "RECEIVED"}, True

    def _encode(self, row, snapshot, now):
        raw = canonical(
            {
                "at": row["received_at"].isoformat(),
                "id": str(row["id"]),
                "snapshot": snapshot.isoformat(),
                "exp": int((now + timedelta(minutes=10)).timestamp()),
            }
        )
        return (
            base64.urlsafe_b64encode(raw).decode().rstrip("=") + "." + hmac.digest(self.cursor_key, raw, "sha256").hex()
        )

    def _decode(self, cursor, now):
        try:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 2048:
                raise ValueError
            token, signature = cursor.split(".")
            raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
            if not hmac.compare_digest(hmac.digest(self.cursor_key, raw, "sha256").hex(), signature):
                raise ValueError
            value = json.loads(raw)
            if (
                set(value) != {"at", "id", "snapshot", "exp"}
                or type(value["exp"]) is not int
                or value["exp"] <= now.timestamp()
            ):
                raise ValueError
            at, snapshot, identifier = (
                datetime.fromisoformat(value["at"]),
                datetime.fromisoformat(value["snapshot"]),
                UUID(value["id"]),
            )
            if at.tzinfo is None or snapshot.tzinfo is None:
                raise ValueError
            return at, identifier, snapshot
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise Problem(422, "INVALID_CURSOR") from None

    async def list(self, actor, cursor=None):
        async with self.db.connection() as conn:
            await self.auth.require_operator(actor, ("ADMIN", "MODERATOR"), conn=conn)
            now = await db_time(conn)
            snapshot, params = now, [self.settings.board_id, now - timedelta(days=7)]
            after = ""
            if cursor:
                at, identifier, snapshot = self._decode(cursor, now)
                after = " AND (r.received_at,r.id)<(%s,%s)"
                params.extend([at, identifier])
            params.append(snapshot)
            rows = await all_rows(
                conn,
                """SELECT r.*,EXISTS(SELECT 1 FROM submissions s WHERE s.id=r.submission_id
                    AND s.board_id=r.board_id AND s.moderation_status='APPROVED'
                    AND COALESCE(s.content_delete_at,s.accepted_at+interval '1 hour')>clock_timestamp()) AS target_available
                    FROM content_reports r WHERE r.board_id=%s AND r.received_at>%s"""
                + after
                + " AND r.received_at<=%s ORDER BY r.received_at DESC,r.id DESC LIMIT 51",
                params,
            )
            return {
                "items": [payload(row) for row in rows[:50]],
                "nextCursor": self._encode(rows[49], snapshot, now) if len(rows) > 50 else None,
            }

    async def resolve(self, actor, report_id, key, resolution):
        if resolution not in RESOLUTIONS:
            raise Problem(422, "VALIDATION_ERROR")
        digest = hashlib.sha256(canonical({"reportId": str(report_id), "resolution": resolution})).digest()
        async with self.db.connection() as conn, conn.transaction():
            await self.auth.require_operator(actor, ("ADMIN", "MODERATOR"), conn=conn)
            board = await one(
                conn, "SELECT * FROM board_runtime WHERE id=%s FOR NO KEY UPDATE", (self.settings.board_id,)
            )
            await self.auth.require_operator(actor, ("ADMIN", "MODERATOR"), conn=conn, write=True)
            row = await one(
                conn,
                "SELECT * FROM content_reports WHERE id=%s AND board_id=%s FOR UPDATE",
                (report_id, self.settings.board_id),
            )
            now = await db_time(conn)
            if not row or row["received_at"] <= now - timedelta(days=7):
                raise Problem(404, "NOT_FOUND")
            prior = await one(
                conn, "SELECT * FROM report_commands WHERE operator_id=%s AND command_key=%s", (actor.id, key)
            )
            if prior:
                if not hmac.compare_digest(bytes(prior["request_hash"]), digest):
                    raise Problem(409, "IDEMPOTENCY_CONFLICT")
                return payload(row)
            if row["status"] == "RESOLVED":
                raise Problem(409, "INVALID_STATE")
            if resolution == "BOARD_PAUSED" and not board["display_paused"]:
                raise Problem(409, "REPORT_ACTION_REQUIRED", "먼저 전광판 송출을 중단해 주세요.")
            if resolution == "HIDDEN":
                hidden = None
                if row["submission_id"]:
                    hidden = await one(
                        conn,
                        "SELECT 1 FROM operator_audit_logs WHERE board_id=%s AND target_id=%s AND action='HIDE_SUBMISSION' LIMIT 1",
                        (self.settings.board_id, row["submission_id"]),
                    )
                if not hidden:
                    raise Problem(409, "REPORT_ACTION_REQUIRED", "먼저 해당 글을 숨겨 주세요.")
            row = await one(
                conn,
                "UPDATE content_reports SET status='RESOLVED',resolved_at=%s,resolved_by=%s,resolution=%s WHERE id=%s RETURNING *",
                (now, actor.id, resolution, report_id),
            )
            await conn.execute(
                "INSERT INTO report_commands(operator_id,command_key,report_id,request_hash,created_at) VALUES(%s,%s,%s,%s,%s)",
                (actor.id, key, report_id, digest, now),
            )
            return payload(row)

    async def cleanup(self):
        async with self.db.connection() as conn:
            result = await conn.execute(
                "DELETE FROM content_reports WHERE id IN (SELECT id FROM content_reports WHERE received_at<=clock_timestamp()-interval '7 days' ORDER BY received_at,id LIMIT 100)"
            )
            return result.rowcount
