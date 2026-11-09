from __future__ import annotations

from contextlib import asynccontextmanager

from psycopg.errors import UndefinedTable
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import Settings


async def one(conn, query, params=()):
    return await (await conn.execute(query, params)).fetchone()


async def all_rows(conn, query, params=()):
    return await (await conn.execute(query, params)).fetchall()


async def db_time(conn):
    return (await one(conn, "SELECT clock_timestamp() AS now"))["now"]


async def schema_ready(conn, board_id):
    from app.queue import queue_schema_identity

    try:
        row = await one(
            conn,
            """SELECT EXISTS(SELECT 1 FROM board_runtime WHERE id=%s) AS board,
            (SELECT count(*) FROM board_runtime)=1 AS single,
            to_regclass('procrastinate_jobs') IS NOT NULL AS queue""",
            (board_id,),
        )
        revision = await one(conn, "SELECT version_num FROM alembic_version")
        registry = await one(conn, "SELECT library_version,schema_sha256 FROM moodwall_queue_schema WHERE id=1")
    except UndefinedTable:
        return False
    return bool(
        all(row.values())
        and revision
        and revision["version_num"] == "0009_playback_duration"
        and registry
        and (registry["library_version"], registry["schema_sha256"]) == queue_schema_identity()
    )


class Database:
    def __init__(self, settings: Settings):
        self.pool = AsyncConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=settings.db_pool_size,
            open=False,
            timeout=0.5,
            kwargs={"autocommit": True, "row_factory": dict_row, "connect_timeout": 3},
            configure=self._configure,
            check=AsyncConnectionPool.check_connection,
        )

    @staticmethod
    async def _configure(conn):
        await conn.execute("SET TIME ZONE 'UTC'")
        await conn.execute("SET statement_timeout = '2s'")
        await conn.execute("SET lock_timeout = '500ms'")
        await conn.execute("SET idle_in_transaction_session_timeout = '5s'")

    async def open(self):
        # Establish one configured connection before accepting requests. Cold
        # startup has its own budget; request pool acquisition stays at 500ms.
        try:
            await self.pool.open(wait=True, timeout=10)
        except BaseException:
            # Cancellation during initialization also leaves pool workers to
            # stop. Never abandon them on a failed startup before lifespan yield.
            await self.pool.close(timeout=5)
            raise

    async def close(self):
        await self.pool.close(timeout=5)

    @asynccontextmanager
    async def connection(self):
        async with self.pool.connection() as conn:
            yield conn
