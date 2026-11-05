"""Content-free, bounded display diagnostics, isolated from playback authority."""

from datetime import timedelta

from app.db import all_rows, db_time, one
from app.errors import Problem


class DisplayDiagnosticsService:
    def __init__(self, db, settings, auth):
        self.db, self.settings, self.auth = db, settings, auth

    async def record(self, actor, report):
        if actor.role != "display":
            raise Problem(403, "FORBIDDEN")
        async with self.db.connection() as conn, conn.transaction():
            # A separate diagnostics lock bounds event storage without owning
            # or changing playback state, leases, revision or participant data.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("moodwall:diagnostics:" + str(self.settings.board_id),),
            )
            board = await one(conn, "SELECT * FROM board_runtime WHERE id=%s FOR SHARE", (self.settings.board_id,))
            fresh = await self.auth.recheck(actor, conn)
            if board is None:
                raise Problem(503, "SERVICE_UNAVAILABLE")
            now = await db_time(conn)
            if not now - timedelta(minutes=10) <= report.occurredAt <= now + timedelta(minutes=2):
                raise Problem(422, "VALIDATION_ERROR")
            values = (
                board["id"], fresh.extra["device_token_version"], report.component, report.code,
                report.state, report.version, report.count, report.occurredAt, now,
            )
            await conn.execute(
                """INSERT INTO display_diagnostic_events(board_id,device_generation,component,code,state,version,count,occurred_at,received_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""", values,
            )
            await conn.execute(
                """INSERT INTO display_diagnostic_latest(board_id,device_generation,component,code,state,version,count,occurred_at,received_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(board_id,device_generation,component,code) DO UPDATE SET
                state=excluded.state,version=excluded.version,count=excluded.count,
                occurred_at=excluded.occurred_at,received_at=excluded.received_at
                WHERE excluded.occurred_at>=display_diagnostic_latest.occurred_at""", values,
            )
            await conn.execute(
                """DELETE FROM display_diagnostic_events WHERE id IN (
                SELECT id FROM display_diagnostic_events WHERE board_id=%s
                ORDER BY received_at DESC,id DESC OFFSET 1000 LIMIT 1000)""", (board["id"],),
            )
            await self._prune(conn, now)
            return {"accepted": True, "receivedAt": now}

    async def latest(self, actor):
        async with self.db.connection() as conn, conn.transaction():
            await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            await self.auth.require_operator(actor, ("ADMIN", "MODERATOR"), conn=conn)
            now = await db_time(conn)
            board = await one(conn, "SELECT device_token_version FROM board_runtime WHERE id=%s", (self.settings.board_id,))
            if board is None:
                raise Problem(503, "SERVICE_UNAVAILABLE")
            rows = await all_rows(
                conn,
                """SELECT component,code,state,version,count,occurred_at,received_at
                FROM display_diagnostic_latest WHERE board_id=%s AND device_generation=%s AND received_at>%s
                ORDER BY component,code LIMIT 45""",
                (self.settings.board_id, board["device_token_version"], now - timedelta(days=7)),
            )
            return {
                "items": [{"component": row["component"], "code": row["code"], "state": row["state"],
                           "version": row["version"], "count": row["count"],
                           "occurredAt": row["occurred_at"], "receivedAt": row["received_at"]} for row in rows],
                "serverNow": now, "deviceGeneration": str(board["device_token_version"]),
                "lastReceivedAt": max((row["received_at"] for row in rows), default=None),
            }

    @staticmethod
    async def _prune(conn, now):
        for table in ("display_diagnostic_events", "display_diagnostic_latest"):
            await conn.execute(
                f"DELETE FROM {table} WHERE ctid IN (SELECT ctid FROM {table} WHERE received_at<=%s LIMIT 1000)",
                (now - timedelta(days=7),),
            )

    async def cleanup(self):
        async with self.db.connection() as conn, conn.transaction():
            await self._prune(conn, await db_time(conn))
