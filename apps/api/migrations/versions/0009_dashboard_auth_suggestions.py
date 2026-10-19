"""Add dashboard sessions and durable verified suggestion identities."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_dashboard_auth_suggestions"
down_revision = "0008_feedback_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dashboard_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("github_user_id", sa.BigInteger(), nullable=False),
        sa.Column("github_login", sa.String(length=255), nullable=False),
        sa.Column("encrypted_access_token", sa.Text(), nullable=False),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_dashboard_sessions_token_hash", "dashboard_sessions", ["token_hash"])
    op.create_index("ix_dashboard_sessions_github_user_id", "dashboard_sessions", ["github_user_id"])
    op.create_index("ix_dashboard_sessions_expires_at", "dashboard_sessions", ["expires_at"])
    op.create_table(
        "published_suggestions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("finding_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("repository_id", sa.Integer(), nullable=False),
        sa.Column("head_sha", sa.String(length=128), nullable=False),
        sa.Column("document_path", sa.String(length=1024), nullable=False),
        sa.Column("document_line", sa.Integer(), nullable=False),
        sa.Column("change_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("publication_status", sa.String(length=32), nullable=False),
        sa.Column("github_comment_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["finding_id", "run_id"], ["analysis_findings.id", "analysis_findings.run_id"], name="fk_suggestion_finding_run", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id", "repository_id"], ["runs.id", "runs.repository_id"], name="fk_suggestion_run_repository", ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "finding_id", "change_fingerprint", name="uq_suggestion_identity"),
    )
    op.create_index("ix_published_suggestions_finding_id", "published_suggestions", ["finding_id"])
    op.create_index("ix_published_suggestions_run_id", "published_suggestions", ["run_id"])
    op.create_index("ix_published_suggestions_repository_id", "published_suggestions", ["repository_id"])
    op.add_column("feedback_events", sa.Column("actor_github_user_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("feedback_events", "actor_github_user_id")
    op.drop_index("ix_published_suggestions_repository_id", table_name="published_suggestions")
    op.drop_index("ix_published_suggestions_run_id", table_name="published_suggestions")
    op.drop_index("ix_published_suggestions_finding_id", table_name="published_suggestions")
    op.drop_table("published_suggestions")
    op.drop_index("ix_dashboard_sessions_expires_at", table_name="dashboard_sessions")
    op.drop_index("ix_dashboard_sessions_github_user_id", table_name="dashboard_sessions")
    op.drop_index("ix_dashboard_sessions_token_hash", table_name="dashboard_sessions")
    op.drop_table("dashboard_sessions")
