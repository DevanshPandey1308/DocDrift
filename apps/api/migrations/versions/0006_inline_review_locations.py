"""Store safe inline comment locations and repository budgets."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_inline_review_locations"
down_revision = "0005_impact_and_noise_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("inline_min_severity", sa.String(length=32), server_default="breaking", nullable=False),
    )
    op.add_column(
        "runs",
        sa.Column("max_inline_comments", sa.Integer(), server_default="5", nullable=False),
    )
    op.add_column(
        "analysis_findings", sa.Column("inline_comment_line", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("analysis_findings", "inline_comment_line")
    op.drop_column("runs", "max_inline_comments")
    op.drop_column("runs", "inline_min_severity")
