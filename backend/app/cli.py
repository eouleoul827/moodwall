"""Trusted local provisioning; passwords never appear in command arguments."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.config import Settings
from app.db import all_rows, one
from app.security import hash_password, normalize_username

INITIALIZATION_LOCK = 0x4D57424F415244
OPERATOR_MANAGEMENT_LOCK = 0x4D574F504552


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Moodwall trusted provisioning commands")
    commands = result.add_subparsers(dest="command", required=True)
    board = commands.add_parser("init-board")
    board.add_argument("--board-id", type=UUID)
    board.add_argument("--name", default="Moodwall")
    create = commands.add_parser("create-operator")
    create.add_argument("--username", required=True)
    create.add_argument("--role", choices=("ADMIN", "MODERATOR"), default="ADMIN")
    for command in ("reset-password", "disable-operator"):
        item = commands.add_parser(command)
        item.add_argument("--username", required=True)
    return result


def prompt_password() -> str:
    first = getpass.getpass("New password (15..128 characters): ")
    second = getpass.getpass("Repeat new password: ")
    if first != second:
        raise ValueError("passwords do not match")
    return hash_password(first)


async def init_board(conn, settings: Settings, board_id: UUID | None, name: str) -> bool:
    board_id = board_id or settings.board_id
    if board_id != settings.board_id:
        raise ValueError("--board-id must match the configured BOARD_ID")
    if not 1 <= len(name) <= 100:
        raise ValueError("board name must contain 1..100 characters")
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(%s)", (INITIALIZATION_LOCK,))
        existing = await all_rows(conn, "SELECT id FROM board_runtime")
        if existing:
            if len(existing) != 1 or existing[0]["id"] != board_id:
                raise ValueError("this database is already initialized for another board")
            return False
        await conn.execute(
            """INSERT INTO board_runtime(id,name,mode,submissions_paused,display_paused)
               VALUES(%s,%s,'MOODWALL',true,true)""",
            (board_id, name),
        )
    return True


async def create_operator(conn, username: str, role: str, encoded: str) -> None:
    username = normalize_username(username)
    if role not in {"ADMIN", "MODERATOR"}:
        raise ValueError("invalid operator role")
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(%s)", (OPERATOR_MANAGEMENT_LOCK,))
        if await one(conn, "SELECT id FROM operators WHERE login_name=%s", (username,)):
            raise ValueError("operator already exists; use reset-password explicitly")
        existing_admin = await one(conn, "SELECT id FROM operators WHERE enabled AND role='ADMIN' LIMIT 1")
        if existing_admin is None and role != "ADMIN":
            raise ValueError("the first active operator must be ADMIN")
        await conn.execute(
            "INSERT INTO operators(login_name,password_hash,role) VALUES(%s,%s,%s)",
            (username, encoded, role),
        )


async def reset_password(conn, username: str, encoded: str) -> None:
    username = normalize_username(username)
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(%s)", (OPERATOR_MANAGEMENT_LOCK,))
        # No key changes: allow concurrent audit INSERTs to take their FK
        # KEY SHARE lock before this transaction revokes the operator sessions.
        operator = await one(conn, "SELECT id FROM operators WHERE login_name=%s FOR NO KEY UPDATE", (username,))
        if operator is None:
            raise ValueError("operator does not exist")
        await conn.execute(
            "UPDATE operators SET password_hash=%s,password_changed_at=clock_timestamp() WHERE id=%s",
            (encoded, operator["id"]),
        )
        await conn.execute(
            "UPDATE operator_sessions SET revoked_at=clock_timestamp() WHERE operator_id=%s AND revoked_at IS NULL",
            (operator["id"],),
        )


async def disable_operator(conn, username: str) -> None:
    username = normalize_username(username)
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(%s)", (OPERATOR_MANAGEMENT_LOCK,))
        operator = await one(conn, "SELECT * FROM operators WHERE login_name=%s FOR NO KEY UPDATE", (username,))
        if operator is None:
            raise ValueError("operator does not exist")
        if operator["enabled"] and operator["role"] == "ADMIN":
            another = await one(
                conn,
                "SELECT id FROM operators WHERE enabled AND role='ADMIN' AND id<>%s LIMIT 1",
                (operator["id"],),
            )
            if another is None:
                raise ValueError("create another ADMIN before disabling the last active ADMIN")
        await conn.execute("UPDATE operators SET enabled=false WHERE id=%s", (operator["id"],))
        await conn.execute(
            "UPDATE operator_sessions SET revoked_at=clock_timestamp() WHERE operator_id=%s AND revoked_at IS NULL",
            (operator["id"],),
        )


async def run(args: argparse.Namespace, encoded: str | None = None) -> None:
    settings = Settings()
    async with await psycopg.AsyncConnection.connect(
        settings.database_url,
        autocommit=True,
        row_factory=dict_row,
    ) as conn:
        if args.command == "init-board":
            created = await init_board(conn, settings, args.board_id, args.name)
            print(
                "Board created; intake and display are paused." if created else "Board already exists; no changes made."
            )
        elif args.command == "create-operator":
            await create_operator(conn, args.username, args.role, encoded)
            print("Operator created. Keep the password in a private password manager.")
        elif args.command == "reset-password":
            await reset_password(conn, args.username, encoded)
            print("Password changed; all existing sessions were revoked.")
        else:
            await disable_operator(conn, args.username)
            print("Operator disabled; all existing sessions were revoked.")


def main() -> None:
    args = parser().parse_args()
    try:
        if args.command != "init-board":
            args.username = normalize_username(args.username)
        encoded = prompt_password() if args.command in {"create-operator", "reset-password"} else None
        asyncio.run(run(args, encoded))
    except (ValueError, EOFError, KeyboardInterrupt) as exc:
        # Validation messages contain neither entered passwords nor tokens.
        print(str(exc) or "Command cancelled.", file=sys.stderr)
        raise SystemExit(1) from None
    except psycopg.Error:
        print(
            "Database operation failed; inspect the connection and schema without printing credentials.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
