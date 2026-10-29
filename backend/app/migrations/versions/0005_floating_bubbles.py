"""Publish every approved bubble without spotlight slots or a display queue."""

from alembic import op

revision = "0005_floating_bubbles"
down_revision = "0004_spotlight_1h"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("DROP INDEX submissions_one_card_per_slot_idx")
    op.execute("DROP INDEX submissions_queue_idx")
    op.execute("DROP INDEX submissions_ambient_order_idx")
    op.execute("ALTER TABLE submissions DROP CONSTRAINT submissions_spotlight_slot")
    op.execute("ALTER TABLE submissions DROP CONSTRAINT submissions_display_status_check")
    # Keep hidden/expired rows terminal. Only still-live legacy public rows
    # become floating; source retention is unchanged by this migration.
    op.execute("""UPDATE submissions SET display_status=CASE
        WHEN business_date=(clock_timestamp() AT TIME ZONE 'Asia/Seoul')::date
             AND accepted_at>clock_timestamp()-interval '1 hour' THEN 'FLOATING'
        ELSE 'EXPIRED' END
        WHERE display_status IN ('QUEUED','SPOTLIGHT','AMBIENT')""")
    op.execute("""ALTER TABLE submissions
        DROP COLUMN spotlight_slot,
        DROP COLUMN spotlight_remaining_ms,
        DROP COLUMN spotlight_checkpoint_at,
        DROP COLUMN first_displayed_at""")
    op.execute("""ALTER TABLE submissions ADD CONSTRAINT submissions_display_status_check
        CHECK(display_status IN ('NONE','FLOATING','EXPIRED','HIDDEN'))""")
    op.execute("""CREATE INDEX submissions_floating_order_idx
        ON submissions(board_id,business_date,finalized_at,id) INCLUDE(accepted_at)
        WHERE moderation_status='APPROVED' AND display_status='FLOATING'""")
    op.execute("""CREATE INDEX operator_audit_hide_target_idx
        ON operator_audit_logs(board_id,target_id,occurred_at)
        WHERE action='HIDE_SUBMISSION' AND target_id IS NOT NULL""")
    op.execute("UPDATE board_runtime SET state_version=state_version+1")


def downgrade():
    raise RuntimeError("Automatic display rollback is disabled; use a reviewed forward migration")
