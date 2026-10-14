"""Persist deterministic analysis findings and source evidence."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_analysis_findings"
down_revision = "0002_run_awaiting_analysis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analysis_findings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("claim_id", sa.String(length=64), nullable=False),
        sa.Column("claim_type", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("expected", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("document_path", sa.String(length=1024), nullable=False),
        sa.Column("document_line", sa.Integer(), nullable=False),
        sa.Column("span_start", sa.Integer(), nullable=False),
        sa.Column("span_end", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.String(length=32), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("verification_method", sa.String(length=128), nullable=False),
        sa.Column("deterministic", sa.Boolean(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "claim_id", name="uq_analysis_findings_run_claim"),
    )
    op.create_index("ix_analysis_findings_run_id", "analysis_findings", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_analysis_findings_run_id", table_name="analysis_findings")
    op.drop_table("analysis_findings")
