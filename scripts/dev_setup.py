"""Generate local-only ignored secrets. Never overwrite an existing .env."""

import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
destination = root / ".env"
if destination.exists():
    raise SystemExit(".env already exists; existing secrets were preserved.")
owner = secrets.token_urlsafe(24)
runtime = secrets.token_urlsafe(24)
value = f"""# Generated local development configuration. Do not commit.
POSTGRES_PASSWORD={owner}
APP_DB_PASSWORD={runtime}
MOODWALL_DATABASE_URL=postgresql://moodwall_app:{runtime}@localhost:55432/moodwall
MOODWALL_MIGRATION_DATABASE_URL=postgresql://postgres:{owner}@localhost:55432/moodwall
MOODWALL_PUBLIC_ORIGIN=http://localhost:8000
MOODWALL_BOARD_ID=10000000-0000-4000-8000-000000000001
MOODWALL_SECRET_KEY={secrets.token_urlsafe(48)}
MOODWALL_QR_KEY={secrets.token_urlsafe(48)}
MOODWALL_ENVIRONMENT=development
MOODWALL_ANALYZER=fake
"""
with destination.open("x", encoding="utf-8", newline="\n") as stream:
    stream.write(value)
print("Created ignored .env for local development. Values were not printed.")
