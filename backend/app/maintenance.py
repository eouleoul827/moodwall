"""Bounded retention cleanup for short-lived mentions and operational metadata."""

from __future__ import annotations

from datetime import timedelta

from psycopg.errors import LockNotAvailable

from app.db import all_rows, db_time, one
from app.domain import MENTION_RETENTION_SECONDS, business_day

BATCH_SIZE = 100


class RetentionService:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings

    async def cleanup_submissions(self) -> int:
        """Delete at most 100 mentions that reached their one-hour lifetime.

        The rolling quota uses ``accepted_at > now - 1 hour``; the inclusive
        deletion boundary is its complement. Preserve today's daily_usage row:
        its warning and suspension counters outlive the original mention.

        A stale PENDING/PROCESSING row is also removable after its moderation
        deadline. Workers only receive the ID and treat a missing row as a no-op.
        Recheck after participant -> daily_usage -> submission locking, the same
        order as submission/moderation writes. Revision hints are updated only
        after all participant-owned transactions have committed.
        """
        removed = 0
        removed_boards = set()
        async with self.db.connection() as conn:
            observed = await db_time(conn)
            candidates = await all_rows(
                conn,
                """SELECT id,participant_id,business_date FROM submissions
                   WHERE accepted_at <= %s AND expires_at <= %s
                   AND NOT EXISTS (SELECT 1 FROM submission_receipts r WHERE r.id=submissions.id)
                   ORDER BY accepted_at,id LIMIT %s""",
                (observed - timedelta(seconds=MENTION_RETENTION_SECONDS), observed, BATCH_SIZE),
            )
            for candidate in candidates:
                try:
                    async with conn.transaction():
                        if not await self._lock_participant(conn, candidate["participant_id"]):
                            continue
                        await one(
                            conn,
                            "SELECT 1 FROM daily_usage WHERE participant_id=%s AND business_date=%s FOR UPDATE",
                            (candidate["participant_id"], candidate["business_date"]),
                        )
                        await one(conn, "SELECT id FROM submissions WHERE id=%s FOR UPDATE", (candidate["id"],))
                        now = await db_time(conn)
                        row = await one(
                            conn,
                            """DELETE FROM submissions WHERE id=%s AND participant_id=%s
                               AND accepted_at <= %s AND expires_at <= %s
                               AND NOT EXISTS (SELECT 1 FROM submission_receipts r WHERE r.id=submissions.id)
                               RETURNING id,board_id""",
                            (
                                candidate["id"],
                                candidate["participant_id"],
                                now - timedelta(seconds=MENTION_RETENTION_SECONDS),
                                now,
                            ),
                        )
                        removed += int(row is not None)
                        if row is not None:
                            removed_boards.add(row["board_id"])
                except LockNotAvailable:
                    continue
            # Board writers lock board -> submissions. Acquire no board lock
            # while holding a participant/daily/submission lock in the inverse
            # order. SSE is a resync hint; expiresAt plus periodic GET also
            # recovers a crash between deletion and this revision update.
            if removed_boards:
                async with conn.transaction():
                    for board_id in sorted(removed_boards):
                        await conn.execute(
                            "UPDATE board_runtime SET state_version=state_version+1 WHERE id=%s", (board_id,)
                        )
        return removed

    async def cleanup(self) -> int:
        """Run one bounded mention pass and prune expired operational metadata.

        Each candidate query is capped at 100. Participant-owned data is rechecked
        after the participant lock in its own transaction. Queue cleanup uses its
        separate supported API, not this service or library-internal SQL.
        """
        removed = await self.cleanup_submissions()
        async with self.db.connection() as conn:
            observed = await db_time(conn)
            cutoff = observed - timedelta(days=30)

            candidates = await all_rows(
                conn,
                """SELECT participant_id,business_date FROM daily_usage d
                   WHERE business_date < %s AND NOT EXISTS (
                     SELECT 1 FROM submissions s WHERE s.participant_id=d.participant_id
                       AND s.business_date=d.business_date)
                   ORDER BY business_date,participant_id LIMIT %s""",
                (business_day(observed) - timedelta(days=30), BATCH_SIZE),
            )
            for candidate in candidates:
                try:
                    async with conn.transaction():
                        if not await self._lock_participant(conn, candidate["participant_id"]):
                            continue
                        await one(
                            conn,
                            "SELECT 1 FROM daily_usage WHERE participant_id=%s AND business_date=%s FOR UPDATE",
                            (candidate["participant_id"], candidate["business_date"]),
                        )
                        now = await db_time(conn)
                        row = await one(
                            conn,
                            """DELETE FROM daily_usage d WHERE participant_id=%s AND business_date=%s
                               AND business_date < %s AND NOT EXISTS (
                                 SELECT 1 FROM submissions s WHERE s.participant_id=d.participant_id
                                   AND s.business_date=d.business_date) RETURNING participant_id""",
                            (
                                candidate["participant_id"],
                                candidate["business_date"],
                                business_day(now) - timedelta(days=30),
                            ),
                        )
                        removed += int(row is not None)
                except LockNotAvailable:
                    continue

            candidates = await all_rows(
                conn,
                """SELECT id,participant_id FROM browser_sessions
                   WHERE expires_at <= %s OR revoked_at IS NOT NULL
                   ORDER BY expires_at,id LIMIT %s""",
                (observed, BATCH_SIZE),
            )
            for candidate in candidates:
                try:
                    async with conn.transaction():
                        if not await self._lock_participant(conn, candidate["participant_id"]):
                            continue
                        now = await db_time(conn)
                        row = await one(
                            conn,
                            """DELETE FROM browser_sessions WHERE id=%s AND participant_id=%s
                               AND (expires_at <= %s OR revoked_at IS NOT NULL) RETURNING id""",
                            (candidate["id"], candidate["participant_id"], now),
                        )
                        removed += int(row is not None)
                except LockNotAvailable:
                    continue

            candidates = await all_rows(
                conn,
                """SELECT id FROM participants p WHERE last_seen_at < %s
                   AND NOT EXISTS (SELECT 1 FROM browser_sessions b WHERE b.participant_id=p.id)
                   AND NOT EXISTS (SELECT 1 FROM submissions s WHERE s.participant_id=p.id)
                   AND NOT EXISTS (SELECT 1 FROM submission_receipts r WHERE r.participant_id=p.id)
                   AND NOT EXISTS (SELECT 1 FROM daily_usage d WHERE d.participant_id=p.id)
                   ORDER BY last_seen_at,id LIMIT %s""",
                (cutoff, BATCH_SIZE),
            )
            for candidate in candidates:
                try:
                    async with conn.transaction():
                        if not await self._lock_participant(conn, candidate["id"]):
                            continue
                        now = await db_time(conn)
                        row = await one(
                            conn,
                            """DELETE FROM participants p WHERE id=%s AND last_seen_at < %s
                               AND NOT EXISTS (SELECT 1 FROM browser_sessions b WHERE b.participant_id=p.id)
                               AND NOT EXISTS (SELECT 1 FROM submissions s WHERE s.participant_id=p.id)
                               AND NOT EXISTS (SELECT 1 FROM submission_receipts r WHERE r.participant_id=p.id)
                               AND NOT EXISTS (SELECT 1 FROM daily_usage d WHERE d.participant_id=p.id)
                               RETURNING id""",
                            (candidate["id"], now - timedelta(days=30)),
                        )
                        removed += int(row is not None)
                except LockNotAvailable:
                    continue

            # These tables have no participant-owned parent and no child records.
            async with conn.transaction():
                now = await db_time(conn)
                cursor = await conn.execute(
                    """DELETE FROM operator_sessions WHERE id IN (
                         SELECT id FROM operator_sessions WHERE expires_at <= %s OR revoked_at IS NOT NULL
                         ORDER BY expires_at,id LIMIT %s FOR UPDATE SKIP LOCKED)""",
                    (now, BATCH_SIZE),
                )
                removed += cursor.rowcount
                cursor = await conn.execute(
                    """DELETE FROM operator_audit_logs WHERE id IN (
                         SELECT id FROM operator_audit_logs WHERE occurred_at < %s
                         ORDER BY occurred_at,id LIMIT %s FOR UPDATE SKIP LOCKED)""",
                    (now - timedelta(days=90), BATCH_SIZE),
                )
                removed += cursor.rowcount
                cursor = await conn.execute(
                    """DELETE FROM rate_limit_buckets WHERE (scope,subject_hash,window_start) IN (
                         SELECT scope,subject_hash,window_start FROM rate_limit_buckets WHERE expires_at <= %s
                         ORDER BY expires_at,scope,subject_hash,window_start LIMIT %s FOR UPDATE SKIP LOCKED)""",
                    (now, BATCH_SIZE),
                )
                removed += cursor.rowcount

            async with conn.transaction():
                board = await one(
                    conn, "SELECT id FROM board_runtime WHERE id=%s FOR NO KEY UPDATE", (self.settings.board_id,)
                )
                if board:
                    now = await db_time(conn)
                    cursor = await conn.execute(
                        """UPDATE board_runtime SET pending_pairing_hash=NULL,pending_pairing_expires_at=NULL
                           WHERE id=%s AND pending_pairing_expires_at <= %s""",
                        (self.settings.board_id, now),
                    )
                    removed += cursor.rowcount
        return removed

    @staticmethod
    async def _lock_participant(conn, participant_id):
        return await one(conn, "SELECT id FROM participants WHERE id=%s FOR UPDATE NOWAIT", (participant_id,))
