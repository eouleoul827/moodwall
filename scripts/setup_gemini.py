"""Configure the local Gemini stack without exposing a key in arguments or output.

No provider request is made by this script. Starting the stack is opt-in.
"""

from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import quote
import warnings


ROOT = Path(__file__).resolve().parents[1]
KEY_NAME = "MOODWALL_GEMINI_API_KEY"
ASSIGNMENT = re.compile(r"^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


class SetupError(Exception):
    """An error whose message never contains configuration values."""


class SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse otherwise echoes unknown arguments, which might contain a key.
        self.exit(2, "Invalid arguments. Use --help; never put an API key in command arguments.\n")


def validate_key(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_\-]{20,256}", value) is None:
        raise SetupError("The API key must contain 20..256 ASCII letters, digits, underscores or hyphens.")
    return value


def _comment(value: str) -> str:
    """Preserve the whitespace and comment after an existing value."""
    quote: str | None = None
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
        elif char == "\\" and quote:
            escaped = True
        elif quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#" and (index == 0 or value[index - 1].isspace()):
            start = index
            while start and value[start - 1] in " \t":
                start -= 1
            return value[start:]
    return ""


def _open_quote(value: str) -> str | None:
    stripped = value.lstrip()
    if not stripped or stripped[0] not in "\"'":
        return None
    quote = stripped[0]
    escaped = False
    for char in stripped[1:]:
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == quote:
            return None
    return quote


def entries(text: str) -> dict[str, tuple[int, int, str, str]]:
    """Find whole dotenv assignments, including unrelated multiline literals."""
    lines = text.splitlines(keepends=True)
    result: dict[str, tuple[int, int, str, str]] = {}
    index = 0
    while index < len(lines):
        match = ASSIGNMENT.match(lines[index].rstrip("\r\n"))
        if match is None:
            index += 1
            continue
        prefix, name, value = match.groups()
        start = index
        quote = _open_quote(value)
        while quote is not None:
            index += 1
            if index >= len(lines):
                raise SetupError("The existing .env has an unclosed quoted value; repair it before setup.")
            value += "\n" + lines[index].rstrip("\r\n")
            quote = _open_quote(value)
        if name in result:
            raise SetupError("The existing .env has duplicate variable assignments; remove duplicates before setup.")
        result[name] = (start, index + 1, prefix, value)
        index += 1
    return result


def _literal_value(value: str) -> str:
    suffix = _comment(value)
    if suffix:
        value = value[: -len(suffix)]
    value = value.strip()
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        value = value[1:-1]
    return value


def base_defaults(found: dict[str, tuple[int, int, str, str]]) -> dict[str, str]:
    owner = _literal_value(found["POSTGRES_PASSWORD"][3]) if "POSTGRES_PASSWORD" in found else secrets.token_urlsafe(24)
    runtime = _literal_value(found["APP_DB_PASSWORD"][3]) if "APP_DB_PASSWORD" in found else secrets.token_urlsafe(24)
    return {
        "POSTGRES_PASSWORD": owner,
        "APP_DB_PASSWORD": runtime,
        "MOODWALL_DATABASE_URL": f"postgresql://moodwall_app:{quote(runtime, safe='')}@localhost:55432/moodwall",
        "MOODWALL_MIGRATION_DATABASE_URL": f"postgresql://postgres:{quote(owner, safe='')}@localhost:55432/moodwall",
        "MOODWALL_PUBLIC_ORIGIN": "http://localhost:8000",
        "MOODWALL_BOARD_ID": "10000000-0000-4000-8000-000000000001",
        "MOODWALL_SECRET_KEY": secrets.token_urlsafe(48),
        "MOODWALL_QR_KEY": secrets.token_urlsafe(48),
        "MOODWALL_ENVIRONMENT": "development",
    }


def update_env(text: str, api_key: str) -> tuple[str, list[str]]:
    """Return new contents and changed names; preserve unrelated bytes and comments."""
    validate_key(api_key)
    found = entries(text)
    if "MOODWALL_ENVIRONMENT" in found and _literal_value(found["MOODWALL_ENVIRONMENT"][3]) == "production":
        raise SetupError("This helper is local-only and cannot modify production configuration. Use the production deployment procedure.")
    # Never invent a new DB password alongside an existing DB configuration.
    database_names = {"POSTGRES_PASSWORD", "APP_DB_PASSWORD"}
    database_config = database_names | {"MOODWALL_DATABASE_URL", "MOODWALL_MIGRATION_DATABASE_URL"}
    if any(name in found for name in database_config) and not all(name in found for name in database_names):
        raise SetupError("The existing .env is missing one DB password; restore the original DB credentials first.")
    generated = base_defaults(found)
    values = {name: value for name, value in generated.items() if name not in found}
    values.update({
        KEY_NAME: api_key,
        "MOODWALL_MODERATION_PROFILE": "gemini",
        "MOODWALL_ANALYZER": "gemini",
    })
    lines = text.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in text else "\n"
    replacements: dict[int, tuple[int, str]] = {}
    appended: list[str] = []
    changed: list[str] = []
    for name, value in values.items():
        # Quote the token explicitly; the validator also matches runtime rules.
        literal = f"'{value}'" if name == KEY_NAME else value
        if name in found:
            start, end, prefix, old = found[name]
            if _literal_value(old) == value:
                continue
            ending = newline if lines[end - 1].endswith(("\n", "\r")) else ""
            replacements[start] = (end, f"{prefix}{name}={literal}{_comment(old)}{ending}")
        else:
            appended.append(f"{name}={literal}{newline}")
        changed.append(name)
    output: list[str] = []
    index = 0
    while index < len(lines):
        replacement = replacements.get(index)
        if replacement is not None:
            index, value = replacement
            output.append(value)
        else:
            output.append(lines[index])
            index += 1
    updated = "".join(output)
    if appended:
        if updated and not updated.endswith(("\n", "\r")):
            updated += newline
        if not text:
            updated = "# Local-only configuration. Never commit this file." + newline
        updated += "".join(appended)
    return updated, changed


def write_private(path: Path, content: str, *, bom: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=".moodwall-env-", delete=False) as stream:
            temporary = stream.name
            os.chmod(temporary, 0o600)
            stream.write((b"\xef\xbb\xbf" if bom else b"") + content.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        os.chmod(path, 0o600)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def run_command(command: list[str], *, env: dict[str, str], key: str, interactive: bool = False) -> None:
    if interactive:
        result = subprocess.run(command, cwd=ROOT, env=env, check=False)
    else:
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, check=False)
        for output in (result.stdout, result.stderr):
            if output:
                print(output.replace(key, "[REDACTED]"), end="")
    if result.returncode:
        raise SetupError("A local setup command failed. Fix the preceding error and rerun; existing data was preserved.")


def start_stack(path: Path, key: str, *, create_operator: bool, operator_name: str) -> None:
    docker = shutil.which("docker")
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not docker or not npm:
        raise SetupError("--start requires Docker Compose and Node.js/npm on PATH.")
    env = dict(os.environ)
    env[KEY_NAME] = key
    frontend_env = {
        name: value for name, value in env.items()
        if name not in {KEY_NAME, "GEMINI_API_KEY", "GOOGLE_API_KEY"} and value != key
    }
    compose = [docker, "compose", "--env-file", str(path), "-f", "compose.yaml", "-f", "compose.frontend.yaml", "-f", "compose.gemini.yaml"]
    for command in (
        [docker, "info", "--format", "{{.ServerVersion}}"],
        [npm, "--prefix", "frontend", "ci"],
        [npm, "--prefix", "frontend", "run", "build"],
    ):
        run_command(command, env=frontend_env, key=key)
    for command in (
        compose + ["build", "api", "worker", "migrate", "cli"],
        compose + ["up", "-d", "--wait", "db"],
        compose + ["run", "--rm", "migrate"],
        compose + ["run", "--rm", "cli", "python", "-m", "app.cli", "init-board"],
    ):
        run_command(command, env=env, key=key)
    if create_operator:
        run_command(compose + ["run", "--rm", "cli", "python", "-m", "app.cli", "create-operator", "--username", operator_name], env=env, key=key, interactive=True)
    run_command(compose + ["up", "-d", "--wait", "api", "worker"], env=env, key=key)
    print("Started the local stack. Open http://localhost:8000/operator .")
    print("Existing board state was preserved; a new board starts with intake/display paused.")


def main(argv: list[str] | None = None) -> int:
    parser = SafeParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env", help="Local dotenv file (default: repository .env).")
    parser.add_argument("--dry-run", action="store_true", help="Show changed variable names only; do not write/start/contact Gemini.")
    parser.add_argument("--start", action="store_true", help="Also build the UI/containers, migrate the local DB and start the stack.")
    parser.add_argument("--create-operator", action="store_true", help="With --start, interactively create an ADMIN (existing users are never overwritten).")
    parser.add_argument("--operator-name", default="admin", help="Login name for --create-operator (not a password).")
    args = parser.parse_args(argv)
    if args.create_operator and not args.start:
        parser.error("--create-operator requires --start")
    if args.dry_run and args.start:
        parser.error("--dry-run and --start are mutually exclusive")
    try:
        path = args.env_file.expanduser().absolute()
        if path.is_symlink():
            raise SetupError("Refusing to replace a symlinked .env; use the real local file path.")
        original = path.read_bytes() if path.exists() else b""
        text = original.decode("utf-8-sig")
        found = entries(text)
        file_environment = _literal_value(found["MOODWALL_ENVIRONMENT"][3]) if "MOODWALL_ENVIRONMENT" in found else None
        if os.environ.get("MOODWALL_ENVIRONMENT") == "production" or file_environment == "production":
            raise SetupError("This helper is local-only and cannot modify production configuration. Use the production deployment procedure.")
        key = os.environ.get(KEY_NAME) or os.environ.get("GEMINI_API_KEY")
        if not key and KEY_NAME in found:
            key = _literal_value(found[KEY_NAME][3])
        if not key:
            if args.dry_run:
                key = "DRY_RUN_PLACEHOLDER_KEY"
            else:
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("error", getpass.GetPassWarning)
                        key = getpass.getpass("Gemini API key (hidden): ")
                except getpass.GetPassWarning:
                    raise SetupError("A private terminal is required for hidden input; use a terminal or a protected process environment.") from None
        updated, changed = update_env(text, key)
        if args.dry_run:
            print("Dry run: no file writes, provider requests or startup.")
            for name in changed:
                print(f"{name}=[REDACTED]")
            return 0
        write_private(path, updated, bom=original.startswith(b"\xef\xbb\xbf"))
        print("Saved local Gemini configuration; no secret values were printed.")
        if os.name == "nt":
            print("Windows: keep the ignored .env in your private user/workspace folder; chmod does not manage Windows ACLs.")
        if args.start:
            print("Worker startup runs a synthetic Gemini connection check; API usage may be billed.")
            start_stack(path, key, create_operator=args.create_operator, operator_name=args.operator_name)
        else:
            print("Start locally with: python scripts/setup_gemini.py --start")
        return 0
    except (SetupError, UnicodeError) as exc:
        print(str(exc) if isinstance(exc, SetupError) else "The local .env must be valid UTF-8.", file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError):
        print("A local file or process operation failed. No configuration values were printed.", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nSetup cancelled.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
