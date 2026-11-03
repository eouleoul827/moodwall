"""Read the bounded ambient window directly in its display order."""

from alembic import op

revision = "0003_ambient_order"
down_revision = "0002_spotlight_10m"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE INDEX submissions_ambient_order_idx
        ON submissions(board_id,business_date,finalized_at,id)
        WHERE moderation_status='APPROVED' AND display_status='AMBIENT'""")


def downgrade():
    op.execute("DROP INDEX submissions_ambient_order_idx")
