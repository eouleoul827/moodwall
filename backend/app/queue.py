"""PostgreSQL queue; enqueue joins the caller's existing transaction."""

import hashlib
from functools import lru_cache
from importlib.metadata import version
from uuid import UUID

import procrastinate
import psycopg
from procrastinate.schema import SchemaManager

_app = None
_database_url = None
_processor = None


@lru_cache(maxsize=1)
def queue_schema_identity() -> tuple[str, str]:
    """Pinned library version and exact distributed initial-DDL digest."""
    return version("procrastinate"), hashlib.sha256(SchemaManager.get_schema().encode("utf-8")).hexdigest()


def configure_processor(processor) -> None:
    """Bind the initialized child runtime without __main__/import duplication."""
    global _processor
    _processor = processor


def create_queue(settings) -> procrastinate.App:
    global _app, _database_url
    if _app is not None:
        if _database_url != settings.database_url:
            raise RuntimeError("queue already configured for another database")
        return _app
    _database_url = settings.database_url
    _app = procrastinate.App(
        connector=procrastinate.PsycopgConnector(conninfo=settings.database_url, min_size=1, max_size=3)
    )

    @_app.task(name="moodwall.moderate", queue="moderation", retry=False)
    async def moderate(submission_id: str):
        if _processor is None:
            raise RuntimeError("worker processor has not been initialized")
        await _processor(UUID(submission_id))

    return _app


async def enqueue(conn, submission_id: UUID) -> int:
    if _app is None:
        raise RuntimeError("create_queue must run during application startup")
    # No commit, no second application connection, no task argument containing text.
    try:
        return (
            await _app.tasks["moodwall.moderate"]
            .configure(connection=conn)
            .defer_async(submission_id=str(submission_id))
        )
    except procrastinate.exceptions.ConnectorException as error:
        # The library wraps PostgreSQL timeouts/disconnects. Preserve a retryable
        # HTTP contract while the caller's transaction rolls back all admission
        # writes. Integrity/programming errors remain unexpected server errors.
        if isinstance(error.__cause__, (psycopg.OperationalError, psycopg.InterfaceError)):
            from app.errors import Problem

            raise Problem(503, "SERVICE_UNAVAILABLE", retry_after=1) from None
        raise


async def cleanup_finished(settings, days: int = 7) -> None:
    """Delete only terminal library jobs via its supported API, never todo/doing."""
    if not 1 <= days <= 30:
        raise ValueError("queue retention must be 1..30 days")
    await create_queue(settings).job_manager.delete_old_jobs(
        nb_hours=days * 24,
        queue="moderation",
        include_failed=True,
        include_cancelled=True,
        include_aborted=True,
    )
