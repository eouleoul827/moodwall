"""Database-backed request budgets shared by all API processes.

Personal submission quotas and playback capacity have their own transactional
rules. These short fixed windows only bound ordinary endpoint traffic. A client
can use up to two windows' budgets near a minute boundary; this is deliberately
not presented as a rolling-minute quota or an identity guarantee.
"""

from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone

from app.db import db_time, one
from app.errors import Problem

_ROLES = frozenset({"student", "display", "operator"})


def bootstrap_limit(settings, role: str) -> int:
    if role not in _ROLES:
        raise ValueError("Unknown request role")
    return getattr(settings, f"bootstrap_{role}_ip_per_minute")


def read_limit(settings, role: str) -> int:
    if role not in _ROLES:
        raise ValueError("Unknown request role")
    return getattr(settings, f"read_{role}_per_minute")


async def limit(request, scope: str, subject: str, maximum: int, seconds: int = 60):
    if maximum < 1 or seconds < 1:
        raise ValueError("Request budget and window must be positive")
    settings, db = request.app.state.settings, request.app.state.db
    digest = hmac.digest(settings.secret_key.encode(), ("rate:v1:" + scope + ":" + subject).encode(), "sha256")
    async with db.connection() as conn:
        now = await db_time(conn)
        window = datetime.fromtimestamp(int(now.timestamp()) // seconds * seconds, timezone.utc)
        row = await one(
            conn,
            """INSERT INTO rate_limit_buckets(scope,subject_hash,window_start,hits,expires_at)
            VALUES(%s,%s,%s,1,%s) ON CONFLICT(scope,subject_hash,window_start)
            DO UPDATE SET hits=rate_limit_buckets.hits+1 RETURNING hits""",
            (scope, digest, window, window + timedelta(seconds=seconds * 2)),
        )
        if row["hits"] > maximum:
            raise Problem(429, "RATE_LIMITED", retry_after=max(1, seconds - int((now - window).total_seconds())))
