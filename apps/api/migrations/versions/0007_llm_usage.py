"""Record bounded per-run provider usage summaries."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_llm_usage"
down_revision = "0006_inline_review_locations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("llm_usage", sa.JSON(), server_default="{}", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("runs", "llm_usage")
