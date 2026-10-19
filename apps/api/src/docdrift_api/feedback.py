"""Persistence and reporting primitives for explicit maintainer feedback.

These functions back the authenticated dashboard routes and can also be tested in isolation.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import AnalysisFinding, FeedbackCategory, FeedbackEvent, Run


class FeedbackTargetNotFound(LookupError):
    """The finding, run, and repository tuple does not identify one existing target."""


class FeedbackIdempotencyConflict(ValueError):
    """An idempotency key was reused for a different feedback event."""


@dataclass(frozen=True)
class AcceptanceMetrics:
    accepted_fixes: int
    rejected_fixes: int
    false_positives: int
    acceptance_rate: float | None


def calculate_acceptance_metrics(categories: Iterable[FeedbackCategory | str]) -> AcceptanceMetrics:
    """Calculate accepted / (accepted + rejected); other or unknown categories are excluded."""
    accepted = 0
    rejected = 0
    false_positives = 0
    for value in categories:
        try:
            category = FeedbackCategory(value)
        except (TypeError, ValueError):
            continue
        if category is FeedbackCategory.FIX_ACCEPTED:
            accepted += 1
        elif category is FeedbackCategory.FIX_REJECTED:
            rejected += 1
        elif category is FeedbackCategory.FALSE_POSITIVE:
            false_positives += 1
    denominator = accepted + rejected
    rate = accepted / denominator if denominator else None
    return AcceptanceMetrics(accepted, rejected, false_positives, rate)


async def record_feedback(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    finding_id: int,
    run_id: int,
    repository_id: int,
    category: FeedbackCategory,
    source: str,
    source_event_id: str,
    reason: str | None = None,
    actor_github_user_id: int | None = None,
) -> FeedbackEvent:
    """Store an explicit feedback event idempotently after validating its full ownership tuple."""
    try:
        category = FeedbackCategory(category)
    except (TypeError, ValueError) as error:
        raise ValueError("Feedback category is not supported.") from error
    if (
        not source
        or not source.strip()
        or source != source.strip()
        or len(source) > 32
        or not source_event_id
        or not source_event_id.strip()
        or source_event_id != source_event_id.strip()
        or len(source_event_id) > 255
    ):
        raise ValueError("Feedback source and event ID must be non-empty and within their limits.")
    if reason is not None and len(reason) > 4000:
        raise ValueError("Feedback reason must be at most 4000 characters.")

    async with session_factory() as session, session.begin():
        target_id = await session.scalar(
            select(AnalysisFinding.id)
            .join(Run, AnalysisFinding.run_id == Run.id)
            .where(
                AnalysisFinding.id == finding_id,
                Run.id == run_id,
                Run.repository_id == repository_id,
            )
        )
        if target_id is None:
            raise FeedbackTargetNotFound("Finding does not belong to the supplied run and repository.")

        inserted_id = await session.scalar(
            insert(FeedbackEvent)
            .values(
                finding_id=finding_id,
                run_id=run_id,
                repository_id=repository_id,
                category=category,
                source=source,
                source_event_id=source_event_id,
                reason=reason,
                actor_github_user_id=actor_github_user_id,
            )
            .on_conflict_do_nothing(constraint="uq_feedback_source_event")
            .returning(FeedbackEvent.id)
        )
        if inserted_id is None:
            existing = await session.scalar(
                select(FeedbackEvent).where(
                    FeedbackEvent.source == source,
                    FeedbackEvent.source_event_id == source_event_id,
                )
            )
            if existing is None:
                raise RuntimeError("Feedback idempotency conflict did not resolve to an existing event.")
            if (
                existing.finding_id != finding_id
                or existing.run_id != run_id
                or existing.repository_id != repository_id
                or existing.category != category
                or existing.reason != reason
                or existing.actor_github_user_id != actor_github_user_id
            ):
                raise FeedbackIdempotencyConflict("Idempotency key belongs to another feedback event.")
            return existing
        event = await session.get(FeedbackEvent, inserted_id)
        if event is None:
            raise RuntimeError("Feedback insert did not return a persisted event.")
        return event


async def list_feedback_for_run(
    session: AsyncSession, *, run_id: int, repository_id: int
) -> list[FeedbackEvent]:
    """Return feedback for a run only when the supplied repository owns that run."""
    target = await session.scalar(
        select(Run.id).where(Run.id == run_id, Run.repository_id == repository_id)
    )
    if target is None:
        raise FeedbackTargetNotFound("Run does not belong to the supplied repository.")
    result = await session.scalars(
        select(FeedbackEvent)
        .where(FeedbackEvent.run_id == run_id, FeedbackEvent.repository_id == repository_id)
        .order_by(FeedbackEvent.created_at, FeedbackEvent.id)
    )
    return list(result)


async def list_feedback_for_finding(
    session: AsyncSession, *, finding_id: int, run_id: int, repository_id: int
) -> list[FeedbackEvent]:
    """Return feedback for a finding after validating its run and repository ownership."""
    target = await session.scalar(
        select(AnalysisFinding.id)
        .join(Run, AnalysisFinding.run_id == Run.id)
        .where(
            AnalysisFinding.id == finding_id,
            Run.id == run_id,
            Run.repository_id == repository_id,
        )
    )
    if target is None:
        raise FeedbackTargetNotFound("Finding does not belong to the supplied run and repository.")
    result = await session.scalars(
        select(FeedbackEvent)
        .where(
            FeedbackEvent.finding_id == finding_id,
            FeedbackEvent.run_id == run_id,
            FeedbackEvent.repository_id == repository_id,
        )
        .order_by(FeedbackEvent.created_at, FeedbackEvent.id)
    )
    return list(result)


async def get_run_acceptance_metrics(
    session: AsyncSession, *, run_id: int, repository_id: int
) -> AcceptanceMetrics:
    """Return explicit feedback metrics for an authorized run/repository tuple."""
    events = await list_feedback_for_run(session, run_id=run_id, repository_id=repository_id)
    return calculate_acceptance_metrics(event.category for event in events)
