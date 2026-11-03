"""Opaque browser credentials, short QR admission, and role-separated sessions."""

from __future__ import annotations

import asyncio
import hmac
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from app.config import Settings
from app.db import Database, db_time, one
from app.errors import Problem
from app.security import (
    QRCodec,
    csrf_token,
    derive_key,
    hash_token,
    new_token,
    normalize_username,
    verify_csrf,
    verify_password,
)

STUDENT_LIFETIME = timedelta(days=30)
DISPLAY_LIFETIME = timedelta(days=30)
OPERATOR_LIFETIME = timedelta(hours=8)
OPERATOR_IDLE = timedelta(minutes=30)
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")


@dataclass(frozen=True, slots=True)
class Actor:
    role: str
    id: UUID
    session_id: UUID | None
    token_hash: bytes = field(repr=False)
    token: str = field(repr=False)
    extra: dict[str, Any] = field(repr=False)


class AuthService:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings
        self.qr_codec = QRCodec(settings)

    def derive_key(self, context: str) -> bytes:
        return derive_key(self.settings.secret_key, context)

    def csrf(self, actor: Actor) -> str:
        return csrf_token(self.settings.secret_key, actor.role, actor.token)

    def check_csrf(self, actor: Actor, csrf: str | None) -> None:
        if not verify_csrf(self.settings.secret_key, actor.role, actor.token, csrf):
            raise Problem(403, "CSRF_REJECTED", "Refresh your session and try again.")

    def make_qr(self, now: datetime) -> dict:
        return self.qr_codec.make(now)

    def validate_qr(self, ticket: str, now: datetime) -> datetime:
        return self.qr_codec.validate(ticket, now)

    async def authenticate(self, role: str, token: str | None, conn=None) -> Actor:
        if role not in {"student", "operator", "display"}:
            raise ValueError("unknown credential role")
        if token is None:
            raise Problem(401, "SESSION_REQUIRED", "A session is required.")
        if not TOKEN_PATTERN.fullmatch(token):
            raise self._expired(role)
        if conn is None:
            async with self.db.connection() as connection:
                return await self.authenticate(role, token, connection)
        digest = hash_token(token)
        if role == "student":
            row = await one(conn, "SELECT * FROM browser_sessions WHERE token_hash=%s", (digest,))
            now = await db_time(conn)
            if row is None or row["revoked_at"] is not None or now >= row["expires_at"]:
                raise self._expired(role)
            return Actor(role, row["participant_id"], row["id"], digest, token, row)
        if role == "operator":
            row = await one(
                conn,
                """SELECT s.*, o.login_name, o.role AS operator_role, o.enabled
                   FROM operator_sessions s JOIN operators o ON o.id=s.operator_id
                   WHERE s.token_hash=%s""",
                (digest,),
            )
            now = await db_time(conn)
            if (
                row is None
                or row["revoked_at"] is not None
                or not row["enabled"]
                or now >= row["expires_at"]
                or now >= row["last_seen_at"] + OPERATOR_IDLE
            ):
                raise self._expired(role)
            return Actor(role, row["operator_id"], row["id"], digest, token, row)
        row = await one(conn, "SELECT * FROM board_runtime WHERE id=%s", (self.settings.board_id,))
        now = await db_time(conn)
        if (
            row is None
            or row["device_token_hash"] is None
            or not hmac.compare_digest(bytes(row["device_token_hash"]), digest)
            or row["device_token_expires_at"] is None
            or now >= row["device_token_expires_at"]
        ):
            raise self._expired(role)
        return Actor(role, row["id"], None, digest, token, row)

    @staticmethod
    def _expired(role: str) -> Problem:
        return Problem(401, "SESSION_EXPIRED", "Your session has expired.", cookie_role=role)

    async def recheck(self, actor: Actor, conn, write: bool = False) -> Actor:
        """Caller holds its participant/board business lock for state changes."""
        current = await self.authenticate(actor.role, actor.token, conn)
        if current.id != actor.id or current.session_id != actor.session_id:
            raise self._expired(actor.role)
        if actor.role == "display" and (current.extra["device_token_version"] != actor.extra["device_token_version"]):
            raise self._expired(actor.role)
        if write and actor.role == "operator":
            # Only use write=True inside the successful mutation's transaction.
            row = await one(
                conn,
                """UPDATE operator_sessions SET last_seen_at=clock_timestamp()
                   WHERE id=%s RETURNING last_seen_at""",
                (actor.session_id,),
            )
            if row is None:
                raise self._expired("operator")
            current.extra["last_seen_at"] = row["last_seen_at"]
        return current

    async def bootstrap_guard(self, role: str, token: str | None, csrf: str | None, conn=None):
        if token is None:
            return None
        actor = await self.authenticate(role, token, conn)
        self.check_csrf(actor, csrf)
        return actor

    async def require_operator(self, actor: Actor, roles, conn=None, write: bool = False) -> Actor:
        if actor.role != "operator":
            raise Problem(403, "FORBIDDEN", "Operator permission is required.")
        if conn is None:
            async with self.db.connection() as connection:
                return await self.require_operator(actor, roles, connection, write)
        current = await self.recheck(actor, conn, write=write)
        if current.extra["operator_role"] not in roles:
            raise Problem(403, "FORBIDDEN", "This operator role cannot perform that action.")
        return current

    async def exchange(self, ticket: str, actor: Actor | None) -> tuple[dict, str]:
        if actor is not None and actor.role != "student":
            raise Problem(403, "FORBIDDEN")
        async with self.db.connection() as conn:
            async with conn.transaction():
                if actor is None:
                    now = await db_time(conn)
                    expiry = self.validate_qr(ticket, now)
                    participant = await one(
                        conn,
                        "INSERT INTO participants(created_at,last_seen_at) VALUES(%s,%s) RETURNING id",
                        (now, now),
                    )
                    token = new_token()
                    row = await one(
                        conn,
                        """INSERT INTO browser_sessions(participant_id,token_hash,created_at,
                           expires_at,admission_board_id,admission_expires_at)
                           VALUES(%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (
                            participant["id"],
                            hash_token(token),
                            now,
                            now + STUDENT_LIFETIME,
                            self.settings.board_id,
                            expiry,
                        ),
                    )
                    current = Actor("student", participant["id"], row["id"], hash_token(token), token, row)
                else:
                    await one(conn, "SELECT id FROM participants WHERE id=%s FOR UPDATE", (actor.id,))
                    current = await self.recheck(actor, conn)
                    now = await db_time(conn)
                    expiry = self.validate_qr(ticket, now)
                    row = await one(
                        conn,
                        """UPDATE browser_sessions SET admission_board_id=%s,
                           admission_expires_at=CASE WHEN admission_board_id=%s
                             THEN GREATEST(admission_expires_at,%s) ELSE %s END
                           WHERE id=%s RETURNING *""",
                        (self.settings.board_id, self.settings.board_id, expiry, expiry, actor.session_id),
                    )
                    current = Actor("student", actor.id, actor.session_id, actor.token_hash, actor.token, row)
                result = await self._session(current, conn, now)
            return result, current.token

    async def login(self, username: str, password: str, existingactor: Actor | None) -> tuple[dict, str]:
        try:
            username = normalize_username(username)
        except ValueError:
            # Do real password work even when the name cannot match an account.
            await asyncio.to_thread(verify_password, None, password)
            raise Problem(401, "INVALID_CREDENTIALS", "Invalid username or password.") from None
        async with self.db.connection() as conn:
            operator = await one(conn, "SELECT * FROM operators WHERE login_name=%s", (username,))
        encoded = operator["password_hash"] if operator else None
        valid = await asyncio.to_thread(verify_password, encoded, password)
        if not valid or operator is None or not operator["enabled"]:
            raise Problem(401, "INVALID_CREDENTIALS", "Invalid username or password.")
        async with self.db.connection() as conn:
            async with conn.transaction():
                current_operator = await one(
                    conn, "SELECT * FROM operators WHERE id=%s FOR NO KEY UPDATE", (operator["id"],)
                )
                if (
                    not current_operator
                    or not current_operator["enabled"]
                    or current_operator["password_hash"] != encoded
                ):
                    raise Problem(401, "INVALID_CREDENTIALS", "Invalid username or password.")
                if existingactor is not None:
                    if existingactor.role != "operator":
                        raise Problem(403, "FORBIDDEN")
                    await self.recheck(existingactor, conn)
                    await conn.execute(
                        "UPDATE operator_sessions SET revoked_at=clock_timestamp() WHERE id=%s",
                        (existingactor.session_id,),
                    )
                now = await db_time(conn)
                token = new_token()
                row = await one(
                    conn,
                    """INSERT INTO operator_sessions(operator_id,token_hash,created_at,last_seen_at,expires_at)
                       VALUES(%s,%s,%s,%s,%s) RETURNING *""",
                    (operator["id"], hash_token(token), now, now, now + OPERATOR_LIFETIME),
                )
                row.update(login_name=username, operator_role=current_operator["role"], enabled=True)
                actor = Actor("operator", operator["id"], row["id"], hash_token(token), token, row)
                result = await self._session(actor, conn, now)
            return result, token

    async def pair(self, code: str, existingactor: Actor | None) -> tuple[dict, str]:
        if not TOKEN_PATTERN.fullmatch(code):
            raise Problem(400, "PAIRING_CODE_INVALID", "The pairing code is invalid or expired.")
        from app.display import settle_board

        async with self.db.connection() as conn:
            async with conn.transaction():
                board = await one(
                    conn, "SELECT * FROM board_runtime WHERE id=%s FOR NO KEY UPDATE", (self.settings.board_id,)
                )
                if existingactor is not None:
                    if existingactor.role != "display":
                        raise Problem(403, "FORBIDDEN")
                    await self.recheck(existingactor, conn)
                now = await db_time(conn)
                if (
                    board is None
                    or board["pending_pairing_hash"] is None
                    or board["pending_pairing_expires_at"] is None
                    or now >= board["pending_pairing_expires_at"]
                    or not hmac.compare_digest(bytes(board["pending_pairing_hash"]), hash_token(code))
                ):
                    raise Problem(400, "PAIRING_CODE_INVALID", "The pairing code is invalid or expired.")
                await settle_board(conn, board, now)
                token = new_token()
                row = await one(
                    conn,
                    """UPDATE board_runtime SET device_token_hash=%s,device_token_expires_at=%s,
                       device_token_version=device_token_version+1,paired_at=%s,
                       pending_pairing_hash=NULL,pending_pairing_expires_at=NULL,
                       last_heartbeat_at=NULL,heartbeat_lease_until=NULL,state_version=state_version+1
                       WHERE id=%s RETURNING *""",
                    (hash_token(token), now + DISPLAY_LIFETIME, now, self.settings.board_id),
                )
                actor = Actor("display", row["id"], None, hash_token(token), token, row)
                result = await self._session(actor, conn, now)
            return result, token

    async def logout(self, actor: Actor) -> None:
        if actor.role != "operator":
            raise Problem(403, "FORBIDDEN")
        async with self.db.connection() as conn:
            async with conn.transaction():
                await self.recheck(actor, conn)
                await conn.execute(
                    "UPDATE operator_sessions SET revoked_at=clock_timestamp() WHERE id=%s",
                    (actor.session_id,),
                )

    async def session(self, actor: Actor) -> dict:
        async with self.db.connection() as conn:
            async with conn.transaction():
                # Student usage contains several reads. Authentication and all
                # counters must describe the same committed database snapshot.
                await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                current = await self.recheck(actor, conn)
                return await self._session(current, conn, await db_time(conn))

    async def _session(self, actor: Actor, conn, now: datetime) -> dict:
        if actor.role == "student":
            from app.submissions import usage_for

            expiry = actor.extra.get("admission_expires_at")
            return {
                "csrfToken": self.csrf(actor),
                "serverNow": now,
                "admissionExpiresAt": expiry,
                "admissionValid": bool(
                    actor.extra.get("admission_board_id") == self.settings.board_id
                    and expiry is not None
                    and now < expiry
                ),
                "usage": await usage_for(conn, actor.id, now),
            }
        if actor.role == "operator":
            return {
                "username": actor.extra["login_name"],
                "csrfToken": self.csrf(actor),
                "expiresAt": actor.extra["expires_at"],
                "idleExpiresAt": min(actor.extra["expires_at"], actor.extra["last_seen_at"] + OPERATOR_IDLE),
                "role": actor.extra["operator_role"],
            }
        return {
            "csrfToken": self.csrf(actor),
            "boardId": actor.id,
            "mode": "PAUSED" if actor.extra["display_paused"] else actor.extra["mode"],
            "credentialExpiresAt": actor.extra["device_token_expires_at"],
            "serverNow": now,
        }
