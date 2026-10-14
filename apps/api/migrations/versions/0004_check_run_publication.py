"""Track GitHub Check Run publication independently from analysis."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_check_run_publication"
down_revision = "0003_analysis_findings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "publication_status",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
    )
    op.add_column("runs", sa.Column("github_check_run_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "github_check_run_id")
    op.drop_column("runs", "publication_status")
