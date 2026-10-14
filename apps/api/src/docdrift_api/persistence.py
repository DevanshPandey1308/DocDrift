"""Transactionally persist accepted pull-request webhook deliveries."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import DeliveryStatus, Installation, Repository, Run, RunStatus, WebhookDelivery


@dataclass(frozen=True)
class PullRequestRecord:
    repository_full_name: str
    github_repository_id: int | None
    installation_id: int | None
    pull_request_number: int
    head_sha: str


@dataclass(frozen=True)
class PersistenceResult:
    duplicate_delivery: bool
    run_id: int | None


async def persist_pull_request(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    delivery_id: str,
    pull_request: PullRequestRecord,
) -> PersistenceResult:
    """Persist delivery and pending run atomically using PostgreSQL conflict handling."""
    async with session_factory() as session, session.begin():
        delivery_insert = (
            insert(WebhookDelivery)
            .values(
                delivery_id=delivery_id,
                event_type="pull_request",
                status=DeliveryStatus.ACCEPTED,
            )
            .on_conflict_do_nothing(index_elements=[WebhookDelivery.delivery_id])
            .returning(WebhookDelivery.id)
        )
        delivery_row_id = (await session.execute(delivery_insert)).scalar_one_or_none()
        if delivery_row_id is None:
            repository_id = await session.scalar(
                select(Repository.id).where(Repository.full_name == pull_request.repository_full_name)
            )
            run_id = None
            if repository_id is not None:
                run_id = await session.scalar(
                    select(Run.id).where(
                        Run.repository_id == repository_id,
                        Run.pull_request_number == pull_request.pull_request_number,
                        Run.head_sha == pull_request.head_sha,
                    )
                )
            return PersistenceResult(duplicate_delivery=True, run_id=run_id)

        internal_installation_id: int | None = None
        if pull_request.installation_id is not None:
            installation_insert = (
                insert(Installation)
                .values(github_installation_id=pull_request.installation_id)
                .on_conflict_do_nothing(index_elements=[Installation.github_installation_id])
            )
            await session.execute(installation_insert)
            internal_installation_id = await session.scalar(
                select(Installation.id).where(
                    Installation.github_installation_id == pull_request.installation_id
                )
            )

        repository_insert = insert(Repository).values(
            installation_id=internal_installation_id,
            github_repository_id=pull_request.github_repository_id,
            full_name=pull_request.repository_full_name,
        )
        await session.execute(repository_insert.on_conflict_do_nothing())
        repository_by_name = await session.scalar(
            select(Repository).where(Repository.full_name == pull_request.repository_full_name)
        )
        repository_by_external_id = None
        if pull_request.github_repository_id is not None:
            repository_by_external_id = await session.scalar(
                select(Repository).where(
                    Repository.github_repository_id == pull_request.github_repository_id
                )
            )
        if (
            repository_by_name is not None
            and repository_by_external_id is not None
            and repository_by_name.id != repository_by_external_id.id
        ):
            raise RuntimeError("GitHub repository ID and full name identify different records.")

        repository = repository_by_external_id or repository_by_name
        if repository is None:
            raise RuntimeError("Repository upsert did not return a repository.")
        if (
            repository_by_name is not None
            and pull_request.github_repository_id is not None
            and repository_by_name.github_repository_id is not None
            and repository_by_name.github_repository_id != pull_request.github_repository_id
        ):
            raise RuntimeError("Repository full name is already associated with another GitHub ID.")
        repository_id = repository.id
        await session.execute(
            update(Repository)
            .where(Repository.id == repository_id)
            .values(
                full_name=pull_request.repository_full_name,
                github_repository_id=func.coalesce(
                    pull_request.github_repository_id, Repository.github_repository_id
                ),
                installation_id=func.coalesce(
                    internal_installation_id, Repository.installation_id
                ),
                updated_at=func.now(),
            )
        )

        run_insert = (
            insert(Run)
            .values(
                repository_id=repository_id,
                pull_request_number=pull_request.pull_request_number,
                head_sha=pull_request.head_sha,
                status=RunStatus.PENDING,
            )
            .on_conflict_do_nothing(
                constraint="uq_runs_pr_head",
            )
        )
        await session.execute(run_insert)
        run_id = await session.scalar(
            select(Run.id).where(
                Run.repository_id == repository_id,
                Run.pull_request_number == pull_request.pull_request_number,
                Run.head_sha == pull_request.head_sha,
            )
        )
        if run_id is None:
            raise RuntimeError("Pull-request run upsert did not return a run.")

    return PersistenceResult(duplicate_delivery=False, run_id=run_id)
