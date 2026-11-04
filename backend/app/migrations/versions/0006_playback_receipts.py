"""Add durable receipts and a fenced, acknowledged fifteen-second player."""

from pathlib import Path

from alembic import op

revision = "0006_playback_receipts"
down_revision = "0005_floating_bubbles"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Automatic playback rollback is disabled; use a reviewed forward migration")
