"""Execute the actual upgrade SQL with old queued, active and ambient cards."""

import importlib.util
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg import sql

from app.db import db_time, one
from app.domain import business_day, midnight_after

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "revision,old_limit,active_remaining,expected",
    [
        ("0002_spotlight_10m", 60000, 30000, {"QUEUED": 600000, "SPOTLIGHT": 570000, "AMBIENT": 0}),
        ("0004_spotlight_1h", 600000, 570000, {"QUEUED": 3600000, "SPOTLIGHT": 3570000, "EXPIRED": 0}),
    ],
)
async def test_upgrade_extends_remaining_time_without_reviving_ambient(
    database, settings, monkeypatch, revision, old_limit, active_remaining, expected
):
    # Import Alembic before taking a transaction/DDL lock. A cold dependency
    # import must not consume the production connection's five-second idle budget.
    path = Path(__file__).resolve().parents[1] / f"app/migrations/versions/{revision}.py"
    spec = importlib.util.spec_from_file_location("duration_upgrade_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    statements = []
    monkeypatch.setattr(module, "op", SimpleNamespace(execute=statements.append))
    module.upgrade()
    baseline = (path.parent / "0001_initial.sql").read_text(encoding="utf-8")
    schema_name = "duration_case_" + uuid4().hex
    # Historical migrations must not change the current head's schema for later tests.
    async with database.connection() as conn, conn.transaction(force_rollback=True):
        await conn.execute("SET LOCAL statement_timeout='30s'")
        # The current head has no spotlight columns. Historical migrations run
        # against their own baseline schema and never alter the live test head.
        await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
        await conn.execute(sql.SQL("SET LOCAL search_path TO {},pg_catalog").format(sql.Identifier(schema_name)))
        await conn.execute(baseline, prepare=False)
        await conn.execute("ALTER TABLE submissions DROP CONSTRAINT submissions_spotlight_remaining_ms_check")
        await conn.execute(f"ALTER TABLE submissions ALTER COLUMN spotlight_remaining_ms SET DEFAULT {old_limit}")
        await conn.execute(
            f"ALTER TABLE submissions ADD CONSTRAINT submissions_spotlight_remaining_ms_check CHECK(spotlight_remaining_ms BETWEEN 0 AND {old_limit})"
        )
        await conn.execute("INSERT INTO board_runtime(id,name) VALUES(%s,'migration fixture')", (settings.board_id,))
        participant = (await one(conn, "INSERT INTO participants DEFAULT VALUES RETURNING id"))["id"]
        when = midnight_after(await db_time(conn)) - timedelta(hours=12)
        await conn.execute(
            "INSERT INTO daily_usage(participant_id,business_date) VALUES(%s,%s)", (participant, business_day(when))
        )
        for status, remaining, slot in (
            ("QUEUED", old_limit, None),
            ("SPOTLIGHT", active_remaining, 1),
            ("AMBIENT", 0, None),
        ):
            await conn.execute(
                """INSERT INTO submissions(participant_id,business_date,board_id,idempotency_key,request_hash,
                content,emoji_id,accepted_at,expires_at,moderation_status,finalized_at,attempt_count,safety_verdict,
                emotion,emotion_source,display_status,spotlight_slot,spotlight_remaining_ms,moderation_policy_version,lexicon_version)
                VALUES(%s,%s,%s,%s,%s,'synthetic migration','party',%s,%s,'APPROVED',%s,1,'ALLOW','JOY','MODEL',%s,%s,%s,'moderation-v1','lexicon-v1')""",
                (
                    participant,
                    business_day(when),
                    settings.board_id,
                    uuid4(),
                    bytes(32),
                    when,
                    when + timedelta(seconds=120),
                    when + timedelta(seconds=1),
                    status,
                    slot,
                    remaining,
                ),
            )
        for statement in statements:
            await conn.execute(statement)
        rows = await (await conn.execute("SELECT display_status,spotlight_remaining_ms FROM submissions")).fetchall()
        assert {row["display_status"]: row["spotlight_remaining_ms"] for row in rows} == expected
