"""Persist explicit, idempotent maintainer feedback events."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_feedback_events"
down_revision = "0007_llm_usage"
branch_labels = None
depends_on = None

feedback_category = postgresql.ENUM(
    "fix_accepted",
    "fix_rejected",
    "false_positive",
    name="feedback_category",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    feedback_category.create(bind, checkfirst=True)

    op.create_unique_constraint(
        "uq_runs_id_repository", "runs", ["id", "repository_id"]
    )
    op.create_unique_constraint(
        "uq_analysis_findings_id_run", "analysis_findings", ["id", "run_id"]
    )
    op.create_table(
        "feedback_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("finding_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("repository_id", sa.Integer(), nullable=False),
        sa.Column("category", feedback_category, nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_event_id", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["finding_id", "run_id"],
            ["analysis_findings.id", "analysis_findings.run_id"],
            name="fk_feedback_finding_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "repository_id"],
            ["runs.id", "runs.repository_id"],
            name="fk_feedback_run_repository",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("source", "source_event_id", name="uq_feedback_source_event"),
    )
    op.create_index("ix_feedback_events_finding_id", "feedback_events", ["finding_id"])
    op.create_index("ix_feedback_events_run_id", "feedback_events", ["run_id"])
    op.create_index("ix_feedback_events_repository_id", "feedback_events", ["repository_id"])


def downgrade() -> None:
    op.drop_index("ix_feedback_events_repository_id", table_name="feedback_events")
    op.drop_index("ix_feedback_events_run_id", table_name="feedback_events")
    op.drop_index("ix_feedback_events_finding_id", table_name="feedback_events")
    op.drop_table("feedback_events")
    op.drop_constraint("uq_analysis_findings_id_run", "analysis_findings", type_="unique")
    op.drop_constraint("uq_runs_id_repository", "runs", type_="unique")
    feedback_category.drop(op.get_bind(), checkfirst=True)
