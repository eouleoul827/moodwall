"""Startup failure must release pools even before lifespan reaches yield."""

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.config import Settings


@pytest.fixture
def startup(monkeypatch):
    settings = Settings(
        _env_file=None,
        database_url="postgresql://synthetic@localhost/moodwall_test",
        public_origin="https://moodwall.test",
        environment="test",
        secret_key="synthetic-startup-session-key-01234567890123456789",
        qr_key="synthetic-startup-qr-key-01234567890123456789012345",
        maintenance_enabled=False,
    )
    # A first import creates the module's default app. Supply settings directly
    # here as well, so this unit test never reads a project's .env file.
    config = import_module("app.config")
    original_get_settings = config.get_settings
    with patch("app.config.get_settings", return_value=settings) as imported_settings:
        main = import_module("app.main")
    if main.get_settings is imported_settings:
        main.get_settings = original_get_settings

    database = SimpleNamespace(open=AsyncMock(), close=AsyncMock())
    queue = SimpleNamespace(open_async=AsyncMock(), close_async=AsyncMock())
    monkeypatch.setattr(main, "Database", Mock(return_value=database))
    monkeypatch.setattr(main, "create_queue", Mock(return_value=queue))
    return SimpleNamespace(app=main.create_app(settings), database=database, queue=queue)


async def test_queue_open_failure_closes_partially_opened_queue_and_database(startup):
    startup.queue.open_async.side_effect = RuntimeError("synthetic queue open failure")
    with pytest.raises(RuntimeError, match="synthetic queue open failure"):
        async with startup.app.router.lifespan_context(startup.app):
            pytest.fail("A failed startup must not serve requests")
    startup.database.open.assert_awaited_once()
    startup.queue.close_async.assert_awaited_once()
    startup.database.close.assert_awaited_once()


async def test_queue_close_failure_still_closes_database(startup):
    startup.queue.close_async.side_effect = RuntimeError("synthetic queue close failure")
    with pytest.raises(RuntimeError, match="synthetic queue close failure"):
        async with startup.app.router.lifespan_context(startup.app):
            assert startup.app.state.db is startup.database
    startup.queue.open_async.assert_awaited_once()
    startup.queue.close_async.assert_awaited_once()
    startup.database.close.assert_awaited_once()


async def test_normal_lifespan_opens_then_closes_both_resources(startup):
    async with startup.app.router.lifespan_context(startup.app):
        startup.database.open.assert_awaited_once()
        startup.queue.open_async.assert_awaited_once()
        startup.database.close.assert_not_awaited()
        startup.queue.close_async.assert_not_awaited()
    startup.queue.close_async.assert_awaited_once()
    startup.database.close.assert_awaited_once()
