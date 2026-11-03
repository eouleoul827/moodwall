"""Cold connection startup is independent of the short request checkout budget."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.db import Database


def database_with_mock_pool(monkeypatch):
    pool = SimpleNamespace(open=AsyncMock(), close=AsyncMock())
    factory = Mock(return_value=pool)
    monkeypatch.setattr("app.db.AsyncConnectionPool", factory)
    db = Database(SimpleNamespace(database_url="synthetic startup URL", db_pool_size=5))
    return db, pool, factory


async def test_startup_prewarms_one_connection_without_extending_request_wait(monkeypatch):
    db, pool, factory = database_with_mock_pool(monkeypatch)
    await db.open()
    assert factory.call_args.kwargs["min_size"] == 1
    assert factory.call_args.kwargs["max_size"] == 5
    assert factory.call_args.kwargs["timeout"] == 0.5
    pool.open.assert_awaited_once_with(wait=True, timeout=10)
    pool.close.assert_not_awaited()
    await db.close()
    pool.close.assert_awaited_once_with(timeout=5)


@pytest.mark.parametrize("failure", [RuntimeError("synthetic unavailable database"), asyncio.CancelledError()])
async def test_failed_or_cancelled_startup_closes_pool_before_reraising(monkeypatch, failure):
    db, pool, _ = database_with_mock_pool(monkeypatch)
    pool.open.side_effect = failure
    with pytest.raises(type(failure)) as raised:
        await db.open()
    assert raised.value is failure
    pool.open.assert_awaited_once_with(wait=True, timeout=10)
    pool.close.assert_awaited_once_with(timeout=5)
