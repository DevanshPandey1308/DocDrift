"""Add the explicit non-analyzed worker placeholder state."""

from __future__ import annotations

from alembic import op

revision = "0002_run_awaiting_analysis"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'awaiting_analysis'")


def downgrade() -> None:
    op.execute("UPDATE runs SET status = 'pending' WHERE status = 'awaiting_analysis'")
    op.execute("ALTER TABLE runs ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TYPE run_status RENAME TO run_status_old")
    op.execute("CREATE TYPE run_status AS ENUM ('pending', 'running', 'completed', 'failed')")
    op.execute(
        "ALTER TABLE runs ALTER COLUMN status TYPE run_status USING status::text::run_status"
    )
    op.execute("ALTER TABLE runs ALTER COLUMN status SET DEFAULT 'pending'")
    op.execute("DROP TYPE run_status_old")
