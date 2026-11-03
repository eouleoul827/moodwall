"""Extend new/active spotlights to ten minutes without reviving ambient cards."""

from alembic import op

revision = "0002_spotlight_10m"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE submissions DROP CONSTRAINT submissions_spotlight_remaining_ms_check")
    op.execute("ALTER TABLE submissions ALTER COLUMN spotlight_remaining_ms SET DEFAULT 600000")
    op.execute("""UPDATE submissions SET spotlight_remaining_ms=LEAST(600000,spotlight_remaining_ms+540000)
        WHERE display_status IN ('QUEUED','SPOTLIGHT')
           OR moderation_status IN ('PENDING','PROCESSING')""")
    op.execute("""ALTER TABLE submissions ADD CONSTRAINT submissions_spotlight_remaining_ms_check
        CHECK(spotlight_remaining_ms BETWEEN 0 AND 600000)""")


def downgrade():
    raise RuntimeError("Automatic duration rollback is disabled; use a reviewed forward migration")
