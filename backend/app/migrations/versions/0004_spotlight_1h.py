"""One-hour spotlight, bounded by the independent one-hour content lifetime."""

from alembic import op

revision = "0004_spotlight_1h"
down_revision = "0003_ambient_order"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE submissions DROP CONSTRAINT submissions_spotlight_remaining_ms_check")
    op.execute("ALTER TABLE submissions ALTER COLUMN spotlight_remaining_ms SET DEFAULT 3600000")
    # Preserve elapsed spotlight time; application projections also cap this at
    # accepted_at + one hour, independently of pauses or time spent in a queue.
    op.execute("""UPDATE submissions SET spotlight_remaining_ms=LEAST(3600000,spotlight_remaining_ms+3000000)
        WHERE display_status IN ('QUEUED','SPOTLIGHT')
           OR moderation_status IN ('PENDING','PROCESSING')""")
    # Retire legacy server ambient rows. Browsers retain already received cards
    # independently until midnight, even after the server content is deleted.
    op.execute("""UPDATE submissions SET display_status='EXPIRED'
        WHERE display_status='AMBIENT'""")
    op.execute("""ALTER TABLE submissions ADD CONSTRAINT submissions_spotlight_remaining_ms_check
        CHECK(spotlight_remaining_ms BETWEEN 0 AND 3600000)""")
    op.execute("UPDATE board_runtime SET state_version=state_version+1")


def downgrade():
    raise RuntimeError("Automatic duration rollback is disabled; use a reviewed forward migration")
