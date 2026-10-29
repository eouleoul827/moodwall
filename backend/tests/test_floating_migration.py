"""The actual upgrade preserves content while removing display-slot state."""

import importlib.util
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg import sql
from psycopg.errors import CheckViolation

from app.db import db_time, one
from app.domain import business_day, midnight_after

pytestmark = pytest.mark.integration


async def test_floating_upgrade_preserves_live_content_and_removes_slots(database, settings, monkeypatch):
    versions = Path(__file__).resolve().parents[1] / "app/migrations/versions"
    baseline = (versions / "0001_initial.sql").read_text(encoding="utf-8")
    upgrades = {}
    for revision in ("0002_spotlight_10m", "0003_ambient_order", "0004_spotlight_1h", "0005_floating_bubbles"):
        spec = importlib.util.spec_from_file_location("upgrade_test_" + revision, versions / f"{revision}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        statements = []
        monkeypatch.setattr(module, "op", SimpleNamespace(execute=statements.append))
        module.upgrade()
        upgrades[revision] = statements
    schema_name = "floating_case_" + uuid4().hex
    async with database.connection() as conn, conn.transaction(force_rollback=True):
        await conn.execute("SET LOCAL statement_timeout='30s'")
        await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
        await conn.execute(sql.SQL("SET LOCAL search_path TO {},pg_catalog").format(sql.Identifier(schema_name)))
        await conn.execute(baseline, prepare=False)
        for revision in ("0002_spotlight_10m", "0003_ambient_order", "0004_spotlight_1h"):
            for statement in upgrades[revision]:
                await conn.execute(statement)
        await conn.execute("INSERT INTO board_runtime(id,name) VALUES(%s,'floating migration')", (settings.board_id,))
        participant = (await one(conn, "INSERT INTO participants DEFAULT VALUES RETURNING id"))["id"]
        now = await db_time(conn)
        accepted = max(now - timedelta(minutes=10), midnight_after(now) - timedelta(days=1))
        records = {}
        for name, status, slot, when in (
            ("queued", "QUEUED", None, accepted),
            ("spotlight", "SPOTLIGHT", 1, accepted),
            ("ambient", "AMBIENT", None, accepted),
            ("hidden", "HIDDEN", None, accepted),
            ("expired", "EXPIRED", None, accepted),
            ("old", "QUEUED", None, now - timedelta(hours=2)),
        ):
            await conn.execute(
                "INSERT INTO daily_usage(participant_id,business_date) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                (participant, business_day(when)),
            )
            records[name] = (
                await one(
                    conn,
                    """INSERT INTO submissions(participant_id,business_date,board_id,idempotency_key,request_hash,
                       content,emoji_id,accepted_at,expires_at,moderation_status,finalized_at,attempt_count,
                       safety_verdict,emotion,emotion_source,display_status,spotlight_slot,hidden_at,
                       moderation_policy_version,lexicon_version)
                       VALUES(%s,%s,%s,%s,%s,'synthetic migration','heart',%s,%s,'APPROVED',%s,1,
                       'ALLOW','JOY','MODEL',%s,%s,%s,'moderation-v1','lexicon-v1') RETURNING id""",
                    (
                        participant,
                        business_day(when),
                        settings.board_id,
                        uuid4(),
                        bytes(32),
                        when,
                        min(when + timedelta(seconds=120), midnight_after(when)),
                        when + timedelta(seconds=1),
                        status,
                        slot,
                        when if status == "HIDDEN" else None,
                    ),
                )
            )["id"]
        before = await (
            await conn.execute("SELECT id,content,accepted_at,expires_at FROM submissions ORDER BY id")
        ).fetchall()
        for statement in upgrades["0005_floating_bubbles"]:
            await conn.execute(statement)
        assert (
            await (
                await conn.execute("SELECT id,content,accepted_at,expires_at FROM submissions ORDER BY id")
            ).fetchall()
            == before
        )
        states = {
            row["id"]: row["display_status"]
            for row in await (await conn.execute("SELECT id,display_status FROM submissions")).fetchall()
        }
        assert all(states[records[name]] == "FLOATING" for name in ("queued", "spotlight", "ambient"))
        assert states[records["hidden"]] == "HIDDEN"
        assert states[records["expired"]] == states[records["old"]] == "EXPIRED"
        columns = await (
            await conn.execute(
                "SELECT attname FROM pg_attribute WHERE attrelid='submissions'::regclass AND NOT attisdropped"
            )
        ).fetchall()
        assert not {row["attname"] for row in columns} & {
            "spotlight_slot",
            "spotlight_remaining_ms",
            "spotlight_checkpoint_at",
            "first_displayed_at",
        }
        index = await one(conn, "SELECT pg_get_indexdef('submissions_floating_order_idx'::regclass) AS definition")
        assert "INCLUDE (accepted_at)" in index["definition"]
        assert (await one(conn, "SELECT state_version FROM board_runtime"))["state_version"] == 1
        with pytest.raises(CheckViolation) as error:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE submissions SET display_status='SPOTLIGHT' WHERE id=%s", (records["queued"],)
                )
        assert error.value.diag.constraint_name == "submissions_display_status_check"
