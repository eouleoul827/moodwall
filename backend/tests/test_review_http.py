"""Regression tests for validation secrecy and HTTP response semantics."""

from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.config import Settings
from app.domain import normalize_content


def test_configuration_errors_and_repr_do_not_render_secret_inputs():
    secret = "synthetic-secret-key-" + "z" * 32
    database = "postgresql://u:SYNTHETIC_PASSWORD@localhost/test"
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, database_url=database, secret_key=secret, qr_key=secret)
    rendered = str(caught.value)
    assert "input_value" not in rendered
    assert "zzzzzzzz" not in rendered and "SYNTHETIC_PASSWORD" not in rendered
    settings = Settings(_env_file=None, database_url=database, secret_key=secret, qr_key="q" * 40)
    assert database not in repr(settings) and secret not in repr(settings)


@pytest.mark.parametrize("value", ["\u200d", "\ufe0f" * 5, "\u0301", " \u200d\ufe0f ", "\u115f\u1160"])
def test_invisible_only_messages_cannot_create_a_bubble(value):
    with pytest.raises(ValueError):
        normalize_content(value)


@pytest.mark.parametrize("value", ["👨‍👩‍👧‍👦", "❤️", "ㅋㅋ", "가", "!", "응원해"])
def test_visible_messages_keep_hangul_and_emoji(value):
    assert normalize_content(value)


async def test_security_headers_preserve_multiple_set_cookie_headers(monkeypatch):
    # Import the default app only with synthetic environment values.
    monkeypatch.setenv("MOODWALL_DATABASE_URL", "postgresql://postgres@localhost/test")
    monkeypatch.setenv("MOODWALL_SECRET_KEY", "s" * 40)
    monkeypatch.setenv("MOODWALL_QR_KEY", "q" * 40)
    from app.main import GuardMiddleware

    app = FastAPI()
    app.state.settings = SimpleNamespace(secure_cookies=True)
    app.add_middleware(GuardMiddleware)

    @app.get("/cookies")
    async def cookies():
        response = JSONResponse({"ok": True}, headers={"Cache-Control": "public"})
        response.set_cookie("first", "one")
        response.set_cookie("second", "two")
        return response

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
        response = await client.get("/cookies")
        assert len(response.headers.get_list("set-cookie")) == 2
        assert client.cookies.get("first") == "one" and client.cookies.get("second") == "two"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"


async def test_http_security_event_hooks_record_success_and_denial(monkeypatch):
    import json

    monkeypatch.setenv("MOODWALL_DATABASE_URL", "postgresql://postgres@localhost/test")
    monkeypatch.setenv("MOODWALL_SECRET_KEY", "s" * 40)
    monkeypatch.setenv("MOODWALL_QR_KEY", "q" * 40)
    from app.errors import Problem
    from app.main import create_app
    from app.security_events import SecurityEvents

    settings = Settings(
        _env_file=None, database_url="postgresql://postgres@localhost/test", secret_key="s" * 40, qr_key="q" * 40
    )
    app = create_app(settings)
    logger = Mock()
    app.state.security_events = SecurityEvents(logger=logger)

    @app.get("/review/denial", operation_id="participantSession")
    async def denial():
        raise Problem(401, "SESSION_REQUIRED")

    @app.get("/review/success", operation_id="operatorLogin")
    async def success():
        return {"ok": True}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.get("/review/denial?ticket=DO_NOT_LOG")
        accepted = await client.get("/review/success")
    records = [json.loads(call.args[0]) for call in logger.info.call_args_list]
    assert [record["outcome"] for record in records] == ["failure", "success"]
    assert records[0]["code"] == "SESSION_REQUIRED"
    assert records[0]["requestId"] == denied.headers["x-request-id"]
    assert records[1]["requestId"] == accepted.headers["x-request-id"]
    assert "DO_NOT_LOG" not in str(logger.info.call_args_list)
