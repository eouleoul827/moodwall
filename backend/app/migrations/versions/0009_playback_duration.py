"""Server-chosen full-screen duration per playback attempt (5-15 s, default 15 s)."""

from pathlib import Path

from alembic import op

revision = "0009_playback_duration"
down_revision = "0008_display_diagnostics"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Playback duration rollback requires a reviewed forward migration")
