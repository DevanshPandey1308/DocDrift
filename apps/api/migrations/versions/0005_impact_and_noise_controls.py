"""Store PR base revisions and explicit finding exclusions."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_impact_and_noise_controls"
down_revision = "0004_check_run_publication"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("base_sha", sa.String(length=128), nullable=True))
    op.add_column(
        "analysis_findings",
        sa.Column("ignored", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("analysis_findings", sa.Column("ignore_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("analysis_findings", "ignore_reason")
    op.drop_column("analysis_findings", "ignored")
    op.drop_column("runs", "base_sha")
