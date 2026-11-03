"""Regression cases found during the authentication and concurrency review."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import Request

from app.auth import AuthService
from app.cli import reset_password
from app.errors import BOOTSTRAP_PATHS, Problem, problem_response
from app.security import hash_password
from inference.contracts import RuleEvidence, RuleRejected


def _request(method, path):
    settings = SimpleNamespace(secure_cookies=True, cookie_name=lambda role: "__Host-mw_" + role)
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [],
            "app": SimpleNamespace(state=SimpleNamespace(settings=settings)),
        }
    )


@pytest.mark.parametrize(
    "role,path",
    [
        ("student", "/api/session"),
        ("student", "/api/submissions"),
        ("operator", "/api/operator/session"),
        ("operator", "/api/operator/board/mode"),
        ("display", "/api/display/state"),
        ("display", "/api/display/heartbeat"),
    ],
)
def test_delayed_expired_response_cannot_delete_replacement_cookie(role, path):
    # Model the browser's Set-Cookie processing, which precedes JavaScript's
    # response-epoch checks: B was issued before the old A response arrived.
    request = _request("GET" if path.endswith(("session", "state")) else "POST", path)
    response = problem_response(request, AuthService._expired(role))
    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    cookie_name = request.app.state.settings.cookie_name(role)
    browser = httpx.Cookies()
    browser.set(cookie_name, "replacement-B", domain="moodwall.test", path="/")
    browser.extract_cookies(
        httpx.Response(
            response.status_code,
            headers=response.headers,
            request=httpx.Request(request.method, "https://moodwall.test" + path),
        )
    )
    assert browser.get(cookie_name) == "replacement-B"


@pytest.mark.parametrize("role,path", BOOTSTRAP_PATHS.items())
def test_serialized_bootstrap_still_clears_stale_role_cookie(role, path):
    request = _request("POST", path)
    response = problem_response(request, AuthService._expired(role))
    cookie_name = request.app.state.settings.cookie_name(role)
    assert response.status_code == 401
    assert cookie_name + '=""' in response.headers["set-cookie"]
    assert "max-age=0" in response.headers["set-cookie"].lower()
    browser = httpx.Cookies()
    browser.set(cookie_name, "stale-A", domain="moodwall.test", path="/")
    browser.extract_cookies(
        httpx.Response(
            response.status_code,
            headers=response.headers,
            request=httpx.Request("POST", "https://moodwall.test" + path),
        )
    )
    assert browser.get(cookie_name) is None


def test_bootstrap_cannot_clear_a_different_roles_cookie():
    request = _request("POST", BOOTSTRAP_PATHS["student"])
    response = problem_response(request, AuthService._expired("operator"))
    assert "set-cookie" not in response.headers


@pytest.mark.integration
async def test_operator_password_reset_and_inflight_control_do_not_deadlock(
    clients, system, database, settings, monkeypatch
):
    import app.auth as auth_module
    import app.cli as cli_module

    token = clients["admin"].cookies.get(settings.cookie_name("operator"))
    actor = await system.state.auth.authenticate("operator", token)
    encoded = hash_password("Review-password-reset-passphrase-42!")
    session_locked, operator_locked = asyncio.Event(), asyncio.Event()
    auth_one, cli_one = auth_module.one, cli_module.one

    async def controlled_auth_one(conn, query, params=()):
        result = await auth_one(conn, query, params)
        if "UPDATE operator_sessions SET last_seen_at" in query:
            session_locked.set()
            await asyncio.wait_for(operator_locked.wait(), 2)
        return result

    async def controlled_cli_one(conn, query, params=()):
        result = await cli_one(conn, query, params)
        if "FROM operators WHERE login_name=" in query:
            operator_locked.set()
        return result

    monkeypatch.setattr(auth_module, "one", controlled_auth_one)
    monkeypatch.setattr(cli_module, "one", controlled_cli_one)
    control = asyncio.create_task(system.state.operator.create_pairing_code(actor, "SECURITY_REVIEW"))

    async def reset():
        async with database.connection() as conn:
            await reset_password(conn, "admin", encoded)

    reset_task = None
    try:
        await asyncio.wait_for(session_locked.wait(), 2)
        reset_task = asyncio.create_task(reset())
        result, _ = await asyncio.wait_for(asyncio.gather(control, reset_task), 4)
        assert result["code"]
    finally:
        for task in (control, reset_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (control, reset_task) if task is not None), return_exceptions=True)
    with pytest.raises(Problem) as expired:
        await system.state.auth.authenticate("operator", token)
    assert expired.value.code == "SESSION_EXPIRED"


@pytest.mark.integration
async def test_student_session_usage_keeps_one_snapshot_during_rejection(clients, system, monkeypatch):
    import app.submissions as submission_module

    receipt = await clients["student"].post(
        "/api/submissions",
        json={"content": "시발", "emojiId": "cloud"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert receipt.status_code == 202, receipt.text
    claim = await system.state.moderation.claim(UUID(receipt.json()["id"]))
    assert claim is not None
    rows_read, writer_done = asyncio.Event(), asyncio.Event()
    original = submission_module.all_rows

    async def stop_between_usage_queries(conn, query, params=()):
        result = await original(conn, query, params)
        if "FROM submissions" in query and "moderation_status IN" in query and not rows_read.is_set():
            rows_read.set()
            await asyncio.wait_for(writer_done.wait(), 3)
        return result

    monkeypatch.setattr(submission_module, "all_rows", stop_between_usage_queries)
    session_read = asyncio.create_task(clients["student"].get("/api/session"))
    try:
        await asyncio.wait_for(rows_read.wait(), 2)
        await system.state.moderation.finalize(
            claim,
            RuleRejected(
                lexicon_version="lexicon-v1",
                matches=[RuleEvidence(rule_id="ko-explicit-001", variant_id="exact")],
            ),
        )
        writer_done.set()
        response = await asyncio.wait_for(session_read, 2)
    finally:
        writer_done.set()
        if not session_read.done():
            session_read.cancel()
        await asyncio.gather(session_read, return_exceptions=True)
    assert response.status_code == 200, response.text
    usage = response.json()["usage"]
    assert usage["usedCount"] == usage["pendingCount"] == 1
    assert usage["violationCount"] == 0
    assert usage["activeSubmissionId"] == receipt.json()["id"]
    # A subsequent request sees the complete new state, rather than mixing a
    # pre-rejection quota with post-rejection in-flight and strike counters.
    fresh = await clients["student"].get("/api/session")
    assert fresh.status_code == 200, fresh.text
    usage = fresh.json()["usage"]
    assert usage["usedCount"] == usage["pendingCount"] == 0
    assert usage["violationCount"] == 1 and usage["activeSubmissionId"] is None
