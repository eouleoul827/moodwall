"""Approved floating bubbles and signed, bounded full-sync pagination."""

from __future__ import annotations

import base64
import hmac
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from .auth import AuthService
from .db import all_rows, db_time, one
from .domain import ANIMATIONS, MENTION_RETENTION_SECONDS, business_day, midnight_after
from .errors import Problem

UTC = timezone.utc
REMOVED_ID_LIMIT = 1000
PAGE_SIZE = 100


def effective_mode(board: dict[str, Any]) -> str:
    return "PAUSED" if board["display_paused"] else board["mode"]


def active_display(board: dict[str, Any], now: datetime) -> bool:
    lease = board.get("heartbeat_lease_until")
    return effective_mode(board) == "MOODWALL" and lease is not None and now < lease


class BubbleCursorCodec:
    """The cursor scopes a short sync, and never replaces current authentication."""

    def __init__(self, key: bytes):
        self.key = key

    def encode(self, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return ".".join(
            base64.urlsafe_b64encode(part).decode().rstrip("=") for part in (raw, hmac.digest(self.key, raw, "sha256"))
        )

    def decode(self, cursor: str, now: datetime, board: dict[str, Any]) -> dict[str, Any]:
        try:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 2048:
                raise ValueError
            encoded, signature = cursor.split(".")
            raw, signed = (
                base64.b64decode(part + "=" * (-len(part) % 4), altchars=b"-_", validate=True)
                for part in (encoded, signature)
            )
            if not hmac.compare_digest(signed, hmac.digest(self.key, raw, "sha256")):
                raise ValueError
            payload = json.loads(raw)
            if not isinstance(payload, dict) or set(payload) != {
                "v",
                "boardId",
                "date",
                "generation",
                "snapshotAt",
                "lastAt",
                "lastId",
                "exp",
            }:
                raise ValueError
            if (
                type(payload["v"]) is not int
                or payload["v"] != 1
                or payload["boardId"] != str(board["id"])
                or payload["date"] != business_day(now).isoformat()
                or payload["generation"] != str(board["device_token_version"])
                or type(payload["exp"]) is not int
                or payload["exp"] <= now.timestamp()
            ):
                raise ValueError
            date.fromisoformat(payload["date"])
            snapshot = datetime.fromisoformat(payload["snapshotAt"])
            last = datetime.fromisoformat(payload["lastAt"])
            if (
                snapshot.tzinfo is None
                or last.tzinfo is None
                or last > snapshot
                or snapshot > now
                or not 0 < payload["exp"] - snapshot.timestamp() <= 300
                or business_day(snapshot) != business_day(now)
                or payload["exp"] > midnight_after(snapshot).timestamp()
            ):
                raise ValueError
            if not isinstance(payload["lastId"], str):
                raise ValueError
            UUID(payload["lastId"])
            return payload
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
            raise Problem(422, "INVALID_CURSOR", "목록을 처음부터 다시 조회해 주세요.") from None


async def settle_board(conn, board: dict[str, Any], now: datetime) -> bool:
    """Expire at most 100 source rows, independently of browser bubble lifetime.

    Caller holds board FOR NO KEY UPDATE. No participant/quota lock may follow.
    Pausing, disconnecting and reconnecting never reserve display positions.
    """
    rows = await all_rows(
        conn,
        """WITH candidates AS (
               SELECT id FROM submissions WHERE board_id=%s
               AND display_status='FLOATING'
               AND NOT EXISTS (SELECT 1 FROM submission_receipts r WHERE r.id=submissions.id)
               AND (business_date<%s OR accepted_at<=%s)
               ORDER BY accepted_at,id LIMIT 100 FOR UPDATE
           ) UPDATE submissions s SET display_status='EXPIRED'
           FROM candidates c WHERE s.id=c.id RETURNING s.id""",
        (board["id"], business_day(now), now - timedelta(seconds=MENTION_RETENTION_SECONDS)),
    )
    if rows:
        board["state_version"] += 1
        await conn.execute(
            "UPDATE board_runtime SET state_version=%s WHERE id=%s", (board["state_version"], board["id"])
        )
    return bool(rows)


class DisplayService:
    def __init__(self, db, settings, auth):
        self.db, self.settings, self.auth = db, settings, auth
        self.cursors = BubbleCursorCodec(auth.derive_key("moodwall:display-cursor:v1"))

    async def locked_board(self, conn):
        board = await one(conn, "SELECT * FROM board_runtime WHERE id=%s FOR NO KEY UPDATE", (self.settings.board_id,))
        if board is None:
            raise Problem(503, "SERVICE_UNAVAILABLE", "전광판 초기화가 필요합니다.")
        return board

    async def _authorize(self, actor, conn, *, write=False):
        if actor.role == "display":
            if actor.id != self.settings.board_id:
                raise Problem(403, "FORBIDDEN")
            return await self.auth.recheck(actor, conn, write=write)
        return await self.auth.require_operator(actor, ("ADMIN", "MODERATOR"), conn=conn, write=write)

    async def _snapshot(self, conn, now: datetime, cursor=None) -> dict[str, Any]:
        board = await one(conn, "SELECT * FROM board_runtime WHERE id=%s", (self.settings.board_id,))
        if board is None:
            raise Problem(503, "SERVICE_UNAVAILABLE")
        day, ambient_until = business_day(now), midnight_after(now)
        if cursor is None:
            page = {
                "v": 1,
                "boardId": str(board["id"]),
                "date": day.isoformat(),
                "generation": str(board["device_token_version"]),
                "snapshotAt": now.isoformat(),
                "lastAt": None,
                "lastId": None,
                "exp": int(min(now + timedelta(minutes=5), ambient_until).timestamp()),
            }
        else:
            page = self.cursors.decode(cursor, now, board)
        snapshot_at = datetime.fromisoformat(page["snapshotAt"])
        clauses = [
            "board_id=%s",
            "business_date=%s",
            "accepted_at>%s",
            "finalized_at<=%s",
            "moderation_status='APPROVED'",
            "display_status='FLOATING'",
            "NOT EXISTS (SELECT 1 FROM submission_receipts r WHERE r.id=submissions.id)",
            "NOT EXISTS (SELECT 1 FROM operator_audit_logs a WHERE a.board_id=submissions.board_id "
            "AND a.target_id=submissions.id AND a.action='HIDE_SUBMISSION' "
            "AND a.occurred_at>=%s AND a.occurred_at<%s)",
        ]
        params = [
            board["id"],
            day,
            now - timedelta(seconds=MENTION_RETENTION_SECONDS),
            snapshot_at,
            ambient_until - timedelta(days=1),
            ambient_until,
        ]
        count = await one(conn, "SELECT count(*) AS n FROM submissions WHERE " + " AND ".join(clauses), tuple(params))
        if cursor is not None:
            clauses.append("(finalized_at,id)>(%s,%s)")
            params.extend((datetime.fromisoformat(page["lastAt"]), UUID(page["lastId"])))
        params.append(PAGE_SIZE + 1)
        rows = await all_rows(
            conn,
            "SELECT id,content,emoji_id,emotion,accepted_at,finalized_at FROM submissions WHERE "
            + " AND ".join(clauses)
            + " ORDER BY finalized_at,id LIMIT %s",
            tuple(params),
        )
        more, rows = len(rows) > PAGE_SIZE, rows[:PAGE_SIZE]
        bubbles = [
            {
                "id": str(row["id"]),
                "content": row["content"],
                "emojiId": row["emoji_id"],
                "emotion": row["emotion"],
                "animationId": ANIMATIONS[row["emotion"]],
                "acceptedAt": row["accepted_at"],
                "expiresAt": row["accepted_at"] + timedelta(seconds=MENTION_RETENTION_SECONDS),
                "ambientUntil": ambient_until,
            }
            for row in rows
        ]
        if more:
            page.update(lastAt=rows[-1]["finalized_at"].isoformat(), lastId=str(rows[-1]["id"]))
        # Source expiry/absence is not a removal. Only explicit ID tombstones
        # remove an already received transparent browser bubble before midnight.
        removals = await all_rows(
            conn,
            """SELECT id,target_id FROM operator_audit_logs
               WHERE board_id=%s AND action='HIDE_SUBMISSION' AND target_id IS NOT NULL
               AND occurred_at>=%s AND occurred_at<%s
               ORDER BY id DESC LIMIT %s""",
            (board["id"], ambient_until - timedelta(days=1), ambient_until, REMOVED_ID_LIMIT + 1),
        )
        return {
            "boardId": str(board["id"]),
            "businessDate": day.isoformat(),
            "ambientUntil": ambient_until,
            "deviceGeneration": str(board["device_token_version"]),
            "removedIds": list(dict.fromkeys(str(row["target_id"]) for row in removals[:REMOVED_ID_LIMIT])),
            "cacheResetToken": str(removals[REMOVED_ID_LIMIT]["id"]) if len(removals) > REMOVED_ID_LIMIT else "0",
            "revision": board["state_version"],
            "mode": effective_mode(board),
            "serverNow": now,
            "snapshotAt": snapshot_at,
            "onlineLeaseUntil": board["heartbeat_lease_until"],
            "bubbles": bubbles,
            "bubbleTotalCount": count["n"],
            "nextCursor": self.cursors.encode(page) if more else None,
            "qrRotatesAt": datetime.fromtimestamp((int(now.timestamp()) // 300 + 1) * 300, UTC),
        }

    async def state(self, actor, cursor=None) -> dict[str, Any]:
        async with self.db.connection() as conn, conn.transaction():
            await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            await self._authorize(actor, conn)
            return await self._snapshot(conn, await db_time(conn), cursor)

    async def current_qr(self, actor) -> dict[str, Any]:
        async with self.db.connection() as conn:
            await self._authorize(actor, conn)
            return self.auth.make_qr(await db_time(conn))

    async def heartbeat(self, actor, client_instance_id=None) -> dict[str, Any]:
        if actor.role != "display":
            raise Problem(403, "FORBIDDEN")
        async with self.db.connection() as conn, conn.transaction():
            board = await self.locked_board(conn)
            await self._authorize(actor, conn, write=True)
            now = await db_time(conn)
            if now >= board["device_token_expires_at"]:
                raise Problem(401, "SESSION_EXPIRED", cookie_role="display")
            await AuthService.renew_display_credential(conn, board, now)
            await settle_board(conn, board, now)
            board["heartbeat_lease_until"] = min(now + timedelta(seconds=30), board["device_token_expires_at"])
            board["state_version"] += 1
            await conn.execute(
                """UPDATE board_runtime SET last_heartbeat_at=%s,heartbeat_lease_until=%s,state_version=%s WHERE id=%s""",
                (now, board["heartbeat_lease_until"], board["state_version"], board["id"]),
            )
            return {
                "serverNow": now,
                "onlineLeaseUntil": board["heartbeat_lease_until"],
                "revision": board["state_version"],
            }

    async def set_mode(self, actor, mode: str) -> dict[str, Any]:
        if mode not in ("MOODWALL", "CHATBOT"):
            raise Problem(422, "VALIDATION_ERROR")
        if actor.role != "display":
            raise Problem(403, "FORBIDDEN")
        async with self.db.connection() as conn, conn.transaction():
            board = await self.locked_board(conn)
            await self._authorize(actor, conn, write=True)
            if board["display_paused"]:
                raise Problem(409, "BOARD_PAUSED", "운영자가 노출을 중단했습니다.")
            now = await db_time(conn)
            if now >= board["device_token_expires_at"]:
                raise Problem(401, "SESSION_EXPIRED", cookie_role="display")
            await settle_board(conn, board, now)
            await conn.execute(
                "UPDATE board_runtime SET mode=%s,state_version=state_version+1 WHERE id=%s", (mode, board["id"])
            )
            board["mode"] = mode
            # Preserve even a rapid CHATBOT -> MOODWALL transition as an
            # interruption, using the same board lock as the mode update.
            from app.playback import PlaybackService

            await PlaybackService(self.db, self.settings, None).settle_mode_change(conn, board, now)
        return await self.state(actor)

    async def reconcile(self) -> int:
        async with self.db.connection() as conn, conn.transaction():
            board = await self.locked_board(conn)
            now = await db_time(conn)
            await settle_board(conn, board, now)
            await conn.execute(
                "UPDATE board_runtime SET last_reconciled_at=%s,state_version=%s WHERE id=%s",
                (now, board["state_version"], board["id"]),
            )
            return board["state_version"]
