"""Small initial persistence schema for authenticated GitHub pull-request deliveries."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Metadata shared by API models and Alembic."""


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_ANALYSIS = "awaiting_analysis"
    COMPLETED = "completed"
    FAILED = "failed"


class DeliveryStatus(StrEnum):
    ACCEPTED = "accepted"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Installation(TimestampMixin, Base):
    __tablename__ = "installations"
    __table_args__ = (
        UniqueConstraint("github_installation_id", name="uq_installations_github_installation_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    github_installation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    repositories: Mapped[list[Repository]] = relationship(back_populates="installation")


class Repository(TimestampMixin, Base):
    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint("github_repository_id", name="uq_repositories_github_repository_id"),
        UniqueConstraint("full_name", name="uq_repositories_full_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    installation_id: Mapped[int | None] = mapped_column(
        ForeignKey("installations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    github_repository_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)

    installation: Mapped[Installation | None] = relationship(back_populates="repositories")
    runs: Mapped[list[Run]] = relationship(back_populates="repository")


class Run(TimestampMixin, Base):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint(
            "repository_id", "pull_request_number", "head_sha", name="uq_runs_pr_head"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pull_request_number: Mapped[int] = mapped_column(Integer, nullable=False)
    head_sha: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, name="run_status", values_callable=lambda enum: [item.value for item in enum]),
        nullable=False,
        default=RunStatus.PENDING,
        server_default=RunStatus.PENDING.value,
    )

    repository: Mapped[Repository] = relationship(back_populates="runs")


class WebhookDelivery(TimestampMixin, Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        UniqueConstraint("delivery_id", name="uq_webhook_deliveries_delivery_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    delivery_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[DeliveryStatus] = mapped_column(
        Enum(
            DeliveryStatus,
            name="webhook_delivery_status",
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
        default=DeliveryStatus.ACCEPTED,
        server_default=DeliveryStatus.ACCEPTED.value,
    )
