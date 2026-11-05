from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from app.config import Settings
from app.db import Database, db_time

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(scope="session")
def real_student_analyzer():
    configured = os.environ.get("MOODWALL_TEST_MODEL_MANIFEST")
    if not configured:
        pytest.skip("Set MOODWALL_TEST_MODEL_MANIFEST to an explicitly prepared local v0 bundle")
    from tools.check_student_model import load_local_analyzer

    analyzer, _path, _manifest = load_local_analyzer(Path(configured))
    return analyzer


@pytest.fixture
def settings():
    url = os.environ.get("MOODWALL_TEST_DB_URL")
    if not url:
        pytest.skip("Set MOODWALL_TEST_DB_URL to an isolated migrated PostgreSQL database")
    if "test" not in url.rsplit("/", 1)[-1]:
        pytest.fail("Integration tests require a database name containing 'test'")
    return Settings(
        _env_file=None,
        database_url=url,
        public_origin="https://moodwall.test",
        environment="test",
        secret_key="synthetic-test-session-key-01234567890123456789",
        qr_key="synthetic-test-qr-key-01234567890123456789012345",
        maintenance_enabled=False,
    )


@pytest_asyncio.fixture
async def database(settings):
    db = Database(settings)
    try:
        await db.open()
        async with db.connection() as conn, conn.transaction():
            # Fixture reset is maintenance DDL, not a 2-second business request.
            await conn.execute("SET LOCAL statement_timeout='30s'")
            await conn.execute("SET LOCAL lock_timeout='5s'")
            await conn.execute("""TRUNCATE moderation_provider_control,moderation_provider_attempts,
                moderation_provider_daily,content_reports,report_commands,operator_audit_logs,operator_sessions,operators,
                submissions,daily_usage,browser_sessions,participants,board_runtime,
                worker_readiness,rate_limit_buckets RESTART IDENTITY CASCADE""")
            # Library cleanup is through its API. Old todo jobs see absent IDs and no-op.
        yield db
    finally:
        # Setup can fail before yield; its background pool must still stop.
        await db.close()


@pytest_asyncio.fixture
async def system(settings, database, monkeypatch):
    # Module-level default app also receives only synthetic settings on import.
    for name, value in {
        "MOODWALL_DATABASE_URL": settings.database_url,
        "MOODWALL_SECRET_KEY": settings.secret_key,
        "MOODWALL_QR_KEY": settings.qr_key,
        "MOODWALL_ENVIRONMENT": "test",
    }.items():
        monkeypatch.setenv(name, value)
    from app.main import create_app
    from app.security import hash_password

    async with database.connection() as conn:
        await conn.execute(
            """INSERT INTO board_runtime(id,name,submissions_paused,display_paused)
            VALUES(%s,'synthetic test display',false,false)""",
            (settings.board_id,),
        )
        await conn.execute(
            "INSERT INTO operators(login_name,password_hash,role) VALUES('admin',%s,'ADMIN')",
            (hash_password("Fixture-operator-passphrase-42!"),),
        )
        now = await db_time(conn)
        await conn.execute(
            """INSERT INTO worker_readiness(worker_name,generation,model_bundle_version,ready_at,heartbeat_at,lease_until)
            VALUES('moderation',%s,%s,%s,%s,%s)""",
            (uuid4(), settings.model_bundle_version, now, now, now + timedelta(minutes=5)),
        )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture
async def clients(system, settings):
    @asynccontextmanager
    async def client():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=system),
            base_url=settings.public_origin,
            headers={"Origin": settings.public_origin},
        ) as value:
            yield value

    async with client() as admin, client() as display, client() as student, client() as stranger:
        response = await admin.post(
            "/api/operator/login", json={"username": "admin", "password": "Fixture-operator-passphrase-42!"}
        )
        assert response.status_code == 200, response.text
        admin.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        code = await admin.post("/api/operator/display/pairing-codes", json={"reasonCode": "MAINTENANCE"})
        assert code.status_code == 200, code.text
        response = await display.post("/api/display/pair", json={"code": code.json()["code"]})
        assert response.status_code == 200, response.text
        display.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        response = await display.get("/api/display/qr")
        assert response.status_code == 200, response.text
        ticket = response.json()["joinUrl"].split("ticket=", 1)[1]
        for value in (student, stranger):
            response = await value.post("/api/admissions/exchange", json={"ticket": ticket})
            assert response.status_code == 200, response.text
            value.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        yield {"admin": admin, "display": display, "student": student, "stranger": stranger}
