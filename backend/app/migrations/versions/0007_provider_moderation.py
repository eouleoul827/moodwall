"""Offline provider moderation controls, support receipts and private reports."""

from pathlib import Path

from alembic import op

revision = "0007_provider_moderation"
down_revision = "0006_playback_receipts"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Provider moderation rollback requires a reviewed forward migration")
