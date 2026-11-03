"""Small, independently testable cryptographic and credential policies."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import unicodedata
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.errors import Problem

if TYPE_CHECKING:
    from app.config import Settings

PASSWORD_HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
# Project-maintained seed, not a claim to contain a complete breached-password list.
PASSWORD_BLOCKLIST_VERSION = "moodwall-seed-v1"
PASSWORD_BLOCKLIST = frozenset(
    {
        "passwordpassword",
        "passwordpasswordpassword",
        "adminadminadmin",
        "123456789012345",
        "1234567890123456",
        "qwertyqwertyqwerty",
        "moodwallmoodwall",
        "letmeinletmeinletmein",
    }
)
_DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash("not a real moodwall account password")


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def derive_key(secret: str, context: str) -> bytes:
    return hmac.digest(secret.encode("utf-8"), f"moodwall:v1:{context}".encode(), "sha256")


def csrf_token(secret: str, role: str, token: str) -> str:
    key = derive_key(secret, f"csrf:{role}")
    return hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_csrf(secret: str, role: str, token: str, supplied: str | None) -> bool:
    if supplied is None or len(supplied) != 64 or not supplied.isascii():
        return False
    return hmac.compare_digest(csrf_token(secret, role, token), supplied)


def normalize_username(value: str) -> str:
    name = unicodedata.normalize("NFC", value.strip())
    if not 3 <= len(name) <= 64 or any(ch.isspace() or unicodedata.category(ch).startswith("C") for ch in name):
        raise ValueError("username must contain 3..64 characters without whitespace or controls")
    return name


def validate_new_password(password: str) -> None:
    # Passwords are deliberately not stripped, normalized or silently truncated.
    if not 15 <= len(password) <= 128:
        raise ValueError("password must contain 15..128 characters")
    if password.casefold() in PASSWORD_BLOCKLIST or password.isspace():
        raise ValueError("choose a password outside the common-password blocklist")


def hash_password(password: str) -> str:
    validate_new_password(password)
    return PASSWORD_HASHER.hash(password)


def verify_password(encoded: str | None, password: str) -> bool:
    # Missing accounts still incur a real Argon2 verification operation.
    try:
        valid = PASSWORD_HASHER.verify(encoded or _DUMMY_PASSWORD_HASH, password)
    except (VerificationError, InvalidHashError):
        return False
    return bool(encoded) and valid


class QRCodec:
    """Signed shared admission tickets; time comes from the database, not JWT defaults."""

    def __init__(self, settings: Settings):
        self.board_id = settings.board_id
        self.origin = settings.public_origin.rstrip("/")
        self.current_kid = settings.qr_kid
        self.keys = dict(getattr(settings, "qr_previous_keys", {}))
        self.keys[self.current_kid] = settings.qr_key
        if any(len(key.encode("utf-8")) < 32 for key in self.keys.values()):
            raise ValueError("QR signing keys must contain at least 32 UTF-8 bytes")

    def make(self, now: datetime) -> dict:
        issued = int(now.timestamp()) // 300 * 300
        payload = {
            "iss": "moodwall",
            "aud": "moodwall-qr-exchange",
            "purpose": "board-admission",
            "v": 1,
            "boardId": str(self.board_id),
            "iat": issued,
            "nbf": issued,
            "exp": issued + 600,
        }
        ticket = jwt.encode(
            payload,
            self.keys[self.current_kid],
            algorithm="HS256",
            headers={"typ": "mw-qr+jwt", "kid": self.current_kid},
        )
        return {
            "joinUrl": f"{self.origin}/join#ticket={ticket}",
            "rotatesAt": datetime.fromtimestamp(issued + 300, timezone.utc),
            "validUntil": datetime.fromtimestamp(issued + 600, timezone.utc),
            "serverNow": now,
        }

    def validate(self, ticket: str, now: datetime) -> datetime:
        if not isinstance(ticket, str) or not ticket.isascii() or len(ticket) > 2048:
            raise Problem(400, "INVALID_QR", "Invalid QR ticket.")
        try:
            header = jwt.get_unverified_header(ticket)
            if set(header) != {"alg", "typ", "kid"}:
                raise ValueError("unsupported QR header")
            kid = header.get("kid")
            if (
                header.get("alg") != "HS256"
                or header.get("typ") != "mw-qr+jwt"
                or not isinstance(kid, str)
                or kid not in self.keys
            ):
                raise ValueError("unsupported QR key or algorithm")
            payload = jwt.decode(
                ticket,
                self.keys[kid],
                algorithms=["HS256"],
                issuer="moodwall",
                audience="moodwall-qr-exchange",
                options={
                    "require": ["iss", "aud", "purpose", "v", "boardId", "iat", "nbf", "exp"],
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                    "strict_aud": True,
                },
            )
            if (
                set(payload) != {"iss", "aud", "purpose", "v", "boardId", "iat", "nbf", "exp"}
                or payload["purpose"] != "board-admission"
                or type(payload["v"]) is not int
                or payload["v"] != 1
                or payload["boardId"] != str(self.board_id)
                or any(type(payload[name]) is not int for name in ("iat", "nbf", "exp"))
            ):
                raise ValueError("invalid QR claims")
            issued = payload["iat"]
            if issued % 300 or payload["nbf"] != issued or payload["exp"] != issued + 600:
                raise ValueError("invalid QR interval")
            if now.timestamp() >= payload["exp"]:
                raise Problem(400, "QR_EXPIRED", "Scan the current QR code.")
            current = int(now.timestamp()) // 300 * 300
            if issued not in (current, current - 300) or now.timestamp() < issued:
                raise ValueError("QR is not current or previous")
            return datetime.fromtimestamp(payload["exp"], timezone.utc)
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, OverflowError):
            raise Problem(400, "INVALID_QR", "Invalid QR ticket.") from None
