"""Bounded, content-free device diagnostics; no playback state changes."""

from pathlib import Path

from alembic import op

revision = "0008_display_diagnostics"
down_revision = "0007_provider_moderation"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Display diagnostic rollback requires a reviewed forward migration")
