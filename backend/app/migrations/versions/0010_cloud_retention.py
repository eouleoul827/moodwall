"""Bound completed cloud text to one hour and the latest thirty completions."""

from pathlib import Path

from alembic import op

revision = "0010_cloud_retention"
down_revision = "0009_playback_duration"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Retired cloud text cannot be restored; use a reviewed forward migration")
