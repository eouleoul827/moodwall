from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit
from uuid import UUID

import jwt
import pytest

from app.auth import Actor, AuthService
from app.errors import Problem
from app.security import (
    QRCodec,
    csrf_token,
    derive_key,
    hash_password,
    hash_token,
    new_token,
    normalize_username,
    validate_new_password,
    verify_csrf,
    verify_password,
)

UTC = timezone.utc
BOARD = UUID("10000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 10, 29, 1, 0, tzinfo=UTC)


@pytest.fixture
def settings():
    return SimpleNamespace(
        board_id=BOARD,
        public_origin="https://moodwall.example",
        qr_kid="current",
        qr_key="current-key-" + "a" * 32,
        secret_key="csrf-key-" + "b" * 32,
        qr_previous_keys={},
    )


def ticket_from(dto):
    return urlsplit(dto["joinUrl"]).fragment.removeprefix("ticket=")


def resign(settings, changes=None, header_changes=None):
    payload = jwt.decode(
        ticket_from(QRCodec(settings).make(NOW)),
        options={"verify_signature": False},
    )
    payload.update(changes or {})
    headers = {"typ": "mw-qr+jwt", "kid": settings.qr_kid}
    headers.update(header_changes or {})
    return jwt.encode(payload, settings.qr_key, algorithm="HS256", headers=headers)


def test_current_and_full_previous_qr_use_original_expiry(settings):
    codec = QRCodec(settings)
    dto = codec.make(NOW + timedelta(seconds=299))
    ticket = ticket_from(dto)
    assert dto["rotatesAt"] == NOW + timedelta(minutes=5)
    assert dto["validUntil"] == NOW + timedelta(minutes=10)
    assert codec.validate(ticket, NOW + timedelta(minutes=8)) == dto["validUntil"]
    codec.validate(ticket, NOW + timedelta(minutes=10, microseconds=-1))
    with pytest.raises(Problem) as error:
        codec.validate(ticket, NOW + timedelta(minutes=10))
    assert error.value.code == "QR_EXPIRED"


def test_qr_cannot_be_used_before_its_interval(settings):
    codec = QRCodec(settings)
    with pytest.raises(Problem) as error:
        codec.validate(ticket_from(codec.make(NOW + timedelta(minutes=5))), NOW)
    assert error.value.code == "INVALID_QR"


@pytest.mark.parametrize(
    "changes",
    [
        {"purpose": "operator-login"},
        {"v": True},
        {"v": 2},
        {"boardId": "20000000-0000-4000-8000-000000000001"},
        {"aud": "another-service"},
        {"aud": ["moodwall-qr-exchange"]},
        {"iss": "another-issuer"},
        {"iat": float(NOW.timestamp())},
        {"exp": int(NOW.timestamp()) + 900},
        {"nbf": int(NOW.timestamp()) + 1},
        {"unexpected": "extension"},
    ],
)
def test_signed_but_invalid_qr_claims_are_rejected(settings, changes):
    with pytest.raises(Problem) as error:
        QRCodec(settings).validate(resign(settings, changes), NOW)
    assert error.value.code == "INVALID_QR"


@pytest.mark.parametrize(
    "changes",
    [{"kid": "unknown"}, {"typ": "JWT"}, {"crit": ["custom"]}, {"jku": "https://evil.invalid"}],
)
def test_qr_header_never_selects_external_keys(settings, changes):
    with pytest.raises(Problem) as error:
        QRCodec(settings).validate(resign(settings, header_changes=changes), NOW)
    assert error.value.code == "INVALID_QR"


def test_qr_refuses_different_algorithm_and_signature(settings):
    token = resign(settings)
    payload = jwt.decode(token, options={"verify_signature": False})
    wrong_algorithm = jwt.encode(
        payload,
        settings.qr_key + "x" * 32,
        algorithm="HS384",
        headers={"typ": "mw-qr+jwt", "kid": settings.qr_kid},
    )
    wrong_signature = jwt.encode(
        payload,
        "other-secret-" + "c" * 32,
        algorithm="HS256",
        headers={"typ": "mw-qr+jwt", "kid": settings.qr_kid},
    )
    for value in (wrong_algorithm, wrong_signature, "!" * 2049, "한글"):
        with pytest.raises(Problem):
            QRCodec(settings).validate(value, NOW)


def test_previous_signing_key_does_not_extend_ticket_ttl(settings):
    previous = ticket_from(QRCodec(settings).make(NOW))
    old_key = settings.qr_key
    settings.qr_previous_keys = {"current": old_key}
    settings.qr_kid = "next"
    settings.qr_key = "next-key-" + "d" * 32
    codec = QRCodec(settings)
    assert codec.validate(previous, NOW + timedelta(minutes=9)) == NOW + timedelta(minutes=10)
    with pytest.raises(Problem) as error:
        codec.validate(previous, NOW + timedelta(minutes=10))
    assert error.value.code == "QR_EXPIRED"
    settings.qr_previous_keys = {}
    with pytest.raises(Problem):
        QRCodec(settings).validate(previous, NOW + timedelta(minutes=1))


def test_csrf_is_bound_to_role_and_opaque_session(settings):
    token = new_token()
    csrf = csrf_token(settings.secret_key, "student", token)
    assert verify_csrf(settings.secret_key, "student", token, csrf)
    assert not verify_csrf(settings.secret_key, "operator", token, csrf)
    assert not verify_csrf(settings.secret_key, "student", new_token(), csrf)
    assert not verify_csrf(settings.secret_key, "student", token, None)
    assert not verify_csrf(settings.secret_key, "student", token, "한" * 64)
    assert derive_key(settings.secret_key, "cursor") != derive_key(settings.secret_key, "csrf:student")


def test_tokens_are_unpredictable_credentials_and_hidden_in_actor_repr():
    first, second = new_token(), new_token()
    assert len(first) == 43 and first != second
    assert len(hash_token(first)) == 32 and hash_token(first) != hash_token(second)
    actor = Actor("display", BOARD, None, hash_token(first), first, {"secret": first})
    assert first not in repr(actor)


def test_password_preserves_whitespace_and_uses_random_salts():
    password = "  a sufficiently long secret  "
    encoded = hash_password(password)
    assert encoded.startswith("$argon2id$")
    assert hash_password(password) != encoded
    assert verify_password(encoded, password)
    assert not verify_password(encoded, password.strip())
    assert not verify_password(None, password)
    assert not verify_password("not-an-argon2-hash", password)


@pytest.mark.parametrize("password", ["short", "passwordpassword", "x" * 129, " " * 15])
def test_new_password_policy_rejects_short_common_and_oversized(password):
    with pytest.raises(ValueError):
        validate_new_password(password)


def test_username_policy_is_shared_by_cli_and_login():
    assert normalize_username("  학생관리자  ") == "학생관리자"
    assert normalize_username("e\u0301cole") == "école"
    assert normalize_username("x" * 64) == "x" * 64
    for value in ("ab", "x" * 65, "user name", "user\u200bname", "user\x00name"):
        with pytest.raises(ValueError):
            normalize_username(value)


@pytest.mark.asyncio
async def test_missing_cookie_and_stale_cookie_have_different_bootstrap_signals(settings):
    auth = AuthService(None, settings)
    with pytest.raises(Problem) as missing:
        await auth.authenticate("student", None)
    assert missing.value.code == "SESSION_REQUIRED" and missing.value.cookie_role is None
    with pytest.raises(Problem) as stale:
        await auth.bootstrap_guard("student", "", None)
    assert stale.value.code == "SESSION_EXPIRED" and stale.value.cookie_role == "student"


@pytest.mark.asyncio
async def test_student_revocation_and_absolute_expiry_are_checked_on_reuse(settings, monkeypatch):
    token = new_token()
    row = {
        "id": UUID("40000000-0000-4000-8000-000000000001"),
        "participant_id": BOARD,
        "revoked_at": None,
        "expires_at": NOW + timedelta(seconds=1),
    }
    monkeypatch.setattr("app.auth.one", AsyncMock(return_value=row))
    monkeypatch.setattr("app.auth.db_time", AsyncMock(return_value=NOW))
    auth = AuthService(None, settings)
    current = await auth.authenticate("student", token, object())
    row["expires_at"] = NOW
    with pytest.raises(Problem) as expired:
        await auth.recheck(current, object())
    assert expired.value.code == "SESSION_EXPIRED"
    row.update(expires_at=NOW + timedelta(days=1), revoked_at=NOW)
    with pytest.raises(Problem):
        await auth.recheck(current, object())


@pytest.mark.asyncio
async def test_operator_reads_do_not_extend_idle_and_demotion_is_observed(settings, monkeypatch):
    token = new_token()
    row = {
        "id": UUID("40000000-0000-4000-8000-000000000001"),
        "operator_id": BOARD,
        "revoked_at": None,
        "expires_at": NOW + timedelta(hours=1),
        "last_seen_at": NOW - timedelta(minutes=29, seconds=59),
        "enabled": True,
        "operator_role": "ADMIN",
        "login_name": "operator",
    }
    monkeypatch.setattr("app.auth.one", AsyncMock(return_value=row))
    monkeypatch.setattr("app.auth.db_time", AsyncMock(return_value=NOW))
    conn = SimpleNamespace(execute=AsyncMock())
    auth = AuthService(None, settings)
    current = await auth.authenticate("operator", token, conn)
    await auth.require_operator(current, ("ADMIN",), conn)
    conn.execute.assert_not_called()
    row["operator_role"] = "MODERATOR"
    with pytest.raises(Problem) as forbidden:
        await auth.require_operator(current, ("ADMIN",), conn)
    assert forbidden.value.code == "FORBIDDEN"
    row["last_seen_at"] = NOW - timedelta(minutes=30)
    with pytest.raises(Problem) as expired:
        await auth.recheck(current, conn)
    assert expired.value.code == "SESSION_EXPIRED"


@pytest.mark.asyncio
async def test_display_version_fences_an_old_actor_even_if_hash_did_not_change(settings, monkeypatch):
    token = new_token()
    row = {
        "id": BOARD,
        "device_token_hash": memoryview(hash_token(token)),
        "device_token_expires_at": NOW + timedelta(days=1),
        "device_token_version": 7,
    }
    monkeypatch.setattr("app.auth.one", AsyncMock(side_effect=lambda *args: dict(row)))
    monkeypatch.setattr("app.auth.db_time", AsyncMock(return_value=NOW))
    auth = AuthService(None, settings)
    current = await auth.authenticate("display", token, object())
    row["device_token_version"] = 8
    with pytest.raises(Problem) as expired:
        await auth.recheck(current, object())
    assert expired.value.code == "SESSION_EXPIRED" and expired.value.cookie_role == "display"
