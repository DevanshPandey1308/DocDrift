"""Create initial installation, repository, run and webhook delivery tables."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


run_status = postgresql.ENUM(
    "pending", "running", "completed", "failed", name="run_status", create_type=False
)
delivery_status = postgresql.ENUM("accepted", name="webhook_delivery_status", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    run_status.create(bind, checkfirst=True)
    delivery_status.create(bind, checkfirst=True)

    op.create_table(
        "installations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("github_installation_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("github_installation_id", name="uq_installations_github_installation_id"),
    )
    op.create_table(
        "repositories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("installation_id", sa.Integer(), nullable=True),
        sa.Column("github_repository_id", sa.BigInteger(), nullable=True),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["installation_id"], ["installations.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("github_repository_id", name="uq_repositories_github_repository_id"),
        sa.UniqueConstraint("full_name", name="uq_repositories_full_name"),
    )
    op.create_index("ix_repositories_installation_id", "repositories", ["installation_id"])
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("repository_id", sa.Integer(), nullable=False),
        sa.Column("pull_request_number", sa.Integer(), nullable=False),
        sa.Column("head_sha", sa.String(length=128), nullable=False),
        sa.Column("status", run_status, server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "repository_id", "pull_request_number", "head_sha", name="uq_runs_pr_head"
        ),
    )
    op.create_index("ix_runs_repository_id", "runs", ["repository_id"])
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("delivery_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("status", delivery_status, server_default="accepted", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("delivery_id", name="uq_webhook_deliveries_delivery_id"),
    )


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
    op.drop_index("ix_runs_repository_id", table_name="runs")
    op.drop_table("runs")
    op.drop_index("ix_repositories_installation_id", table_name="repositories")
    op.drop_table("repositories")
    op.drop_table("installations")
    delivery_status.drop(op.get_bind(), checkfirst=True)
    run_status.drop(op.get_bind(), checkfirst=True)
