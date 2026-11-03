"""Explicit single-run migration command; never invoked by API startup."""

import asyncio
import os
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from procrastinate.schema import SchemaManager
from psycopg.rows import dict_row

from app.config import get_settings
from app.queue import queue_schema_identity

APPLICATION_MIGRATION_LOCK = 0x4D574D49475241
QUEUE_SCHEMA_LOCK = 0x4D575155455545

REGISTRY_DDL = """CREATE TABLE IF NOT EXISTS public.moodwall_queue_schema (
    id smallint PRIMARY KEY CHECK (id=1),
    library_version text NOT NULL,
    schema_sha256 text NOT NULL CHECK (schema_sha256 ~ '^[0-9a-f]{64}$'),
    applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
)"""


async def migrate_queue(settings):
    """Install pinned official DDL once, atomically with our own registry.

    An existing unmanaged queue or changed library is an explicit migration
    decision. Never guess its revision, recreate it, or edit library records.
    Returns True only for a new installation; an exact repeat returns False.
    """
    expected_version, expected_hash = queue_schema_identity()
    async with await psycopg.AsyncConnection.connect(
        settings.database_url,
        autocommit=True,
        row_factory=dict_row,
        connect_timeout=3,
    ) as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL search_path = public, pg_catalog")
            await conn.execute("SET LOCAL lock_timeout = '30s'")
            await conn.execute("SET LOCAL statement_timeout = '120s'")
            await conn.execute("SELECT pg_advisory_xact_lock(%s)", (QUEUE_SCHEMA_LOCK,))
            await conn.execute(REGISTRY_DDL)
            recorded = await (
                await conn.execute(
                    "SELECT library_version,schema_sha256 FROM public.moodwall_queue_schema WHERE id=1 FOR UPDATE"
                )
            ).fetchone()
            existing = await (
                await conn.execute("""SELECT EXISTS (
                SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public' AND c.relname LIKE 'procrastinate\\_%' ESCAPE '\\'
                UNION ALL
                SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
                    WHERE n.nspname='public' AND t.typname LIKE 'procrastinate\\_%' ESCAPE '\\'
                UNION ALL
                SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                    WHERE n.nspname='public' AND p.proname LIKE 'procrastinate\\_%' ESCAPE '\\'
                ) AS present""")
            ).fetchone()
            if recorded is not None:
                if recorded["library_version"] != expected_version or recorded["schema_sha256"] != expected_hash:
                    raise RuntimeError(
                        "Queue schema identity changed; use a reviewed manual Procrastinate migration plan"
                    )
                core = await (
                    await conn.execute("""SELECT
                    to_regclass('public.procrastinate_jobs') IS NOT NULL
                    AND to_regclass('public.procrastinate_workers') IS NOT NULL
                    AND to_regclass('public.procrastinate_periodic_defers') IS NOT NULL
                    AND to_regclass('public.procrastinate_events') IS NOT NULL AS complete""")
                ).fetchone()
                if not existing["present"] or not core["complete"]:
                    raise RuntimeError("Registered queue schema is incomplete; stop and inspect the database")
                return False
            if existing["present"]:
                raise RuntimeError(
                    "Existing unmanaged Procrastinate schema; reconcile it with a reviewed manual migration, never auto-adopt"
                )
            # get_schema() is the installed library's public initial-schema API.
            # Execute that unmodified DDL on this exact migration-owner connection
            # so a failed installation rolls back both DDL and registry together.
            await conn.execute(SchemaManager.get_schema(), prepare=False)
            await conn.execute(
                """INSERT INTO public.moodwall_queue_schema(id,library_version,schema_sha256)
                VALUES(1,%s,%s)""",
                (expected_version, expected_hash),
            )
            return True


def main():
    import sys

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    settings = get_settings()
    config = Config(str(Path(__file__).resolve().parent / "alembic.ini"))
    owner_url = os.environ.get("MOODWALL_MIGRATION_DATABASE_URL") or settings.database_url
    # Serialize the full Alembic + queue command across migration processes.
    # Runtime services never acquire this lock or perform DDL at startup.
    with psycopg.connect(owner_url, autocommit=True, connect_timeout=3) as migration_lock:
        migration_lock.execute("SET lock_timeout = '30s'")
        migration_lock.execute("SELECT pg_advisory_lock(%s)", (APPLICATION_MIGRATION_LOCK,))
        try:
            command.upgrade(config, "head")
            asyncio.run(migrate_queue(settings.model_copy(update={"database_url": owner_url})))
        finally:
            migration_lock.execute("SELECT pg_advisory_unlock(%s)", (APPLICATION_MIGRATION_LOCK,))
    print("App and queue schema migrations completed.")


if __name__ == "__main__":
    main()
