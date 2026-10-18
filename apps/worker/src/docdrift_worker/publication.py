"""Persisted Check Run publication lifecycle, separate from analysis state."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

import httpx
from docdrift_api.config import Settings
from docdrift_api.models import AnalysisFinding, PublicationStatus, Repository, Run, RunStatus
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from .github import (
    CheckRunPublisher,
    GitHubAppCheckRunPublisher,
    GitHubPublicationError,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PublicationInput:
    run_id: int
    repository: str
    repository_id: int | None
    installation_id: int | None
    pull_request_number: int
    head_sha: str
    check_run_id: int | None
    findings: tuple[AnalysisFinding, ...]
    base_sha: str | None = None
    inline_min_severity: str = "breaking"
    max_inline_comments: int = 5


async def _prepare_publication(
    session_factory: async_sessionmaker[AsyncSession], run_id: int
) -> PublicationInput | str:
    async with session_factory() as session, session.begin():
        run = await session.scalar(
            select(Run)
            .options(
                selectinload(Run.repository).selectinload(Repository.installation),
                selectinload(Run.findings),
            )
            .where(Run.id == run_id)
            .with_for_update()
        )
        if run is None:
            return "missing"
        if run.status != RunStatus.COMPLETED:
            return f"analysis_{run.status.value}"
        newer_run = await session.scalar(
            select(Run.id)
            .where(
                Run.repository_id == run.repository_id,
                Run.pull_request_number == run.pull_request_number,
                Run.id > run.id,
            )
            .limit(1)
        )
        if newer_run is not None:
            run.publication_status = PublicationStatus.SKIPPED.value
            await session.flush()
            return PublicationStatus.SKIPPED.value
        if run.publication_status == PublicationStatus.PUBLISHED.value:
            return PublicationStatus.PUBLISHED.value
        repository = run.repository
        if repository is None:
            raise GitHubPublicationError("Persisted run has no repository.")
        run.publication_status = PublicationStatus.PUBLISHING.value
        await session.flush()
        return PublicationInput(
            run_id=run.id,
            repository=repository.full_name,
            repository_id=repository.github_repository_id,
            installation_id=(
                repository.installation.github_installation_id
                if repository.installation is not None
                else None
            ),
            pull_request_number=run.pull_request_number,
            base_sha=getattr(run, "base_sha", None),
            head_sha=run.head_sha,
            inline_min_severity=getattr(run, "inline_min_severity", "breaking"),
            max_inline_comments=getattr(run, "max_inline_comments", 5),
            check_run_id=run.github_check_run_id,
            findings=tuple(run.findings),
        )


async def _set_publication_status(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: int,
    status: PublicationStatus,
    *,
    check_run_id: int | None = None,
) -> None:
    async with session_factory() as session, session.begin():
        run = await session.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if run is None or run.status != RunStatus.COMPLETED:
            return
        run.publication_status = status.value
        if check_run_id is not None:
            run.github_check_run_id = check_run_id
        await session.flush()


async def _publish_latest_summary(
    session_factory: async_sessionmaker[AsyncSession],
    prepared: PublicationInput,
    publisher: CheckRunPublisher,
) -> str:
    """Serialize one PR's comment updates and skip a run once a newer head exists."""
    publish_summary = getattr(publisher, "publish_summary_comment", None)
    if publish_summary is None:
        return "unsupported"
    if prepared.repository_id is None or prepared.installation_id is None:
        return "unavailable"

    lock_digest = hashlib.sha256(
        f"{prepared.repository_id}:{prepared.pull_request_number}".encode("ascii")
    ).digest()[:8]
    lock_key = int.from_bytes(lock_digest, byteorder="big", signed=True)
    async with session_factory() as session, session.begin():
        # The transaction-scoped PostgreSQL advisory lock serializes workers even when
        # webhook redelivery creates overlapping publication attempts.
        await session.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})
        run = await session.scalar(select(Run).where(Run.id == prepared.run_id).with_for_update())
        if run is None or run.status != RunStatus.COMPLETED:
            return "stale"
        newer_run = await session.scalar(
            select(Run.id)
            .where(
                Run.repository_id == run.repository_id,
                Run.pull_request_number == run.pull_request_number,
                Run.id > run.id,
            )
            .limit(1)
        )
        if newer_run is not None:
            return "stale"
        comment_id = await publish_summary(
            repository=prepared.repository,
            repository_id=prepared.repository_id,
            installation_id=prepared.installation_id,
            pull_request_number=prepared.pull_request_number,
            findings=prepared.findings,
        )
        logger.info(
            "github_pr_summary_updated",
            extra={"run_id": prepared.run_id, "comment_id": comment_id},
        )
        return "published"


async def publish_completed_run(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: int,
    *,
    settings: Settings | None = None,
    publisher: CheckRunPublisher | None = None,
) -> str:
    """Publish persisted results; never mutate analysis status on publication errors."""
    prepared = await _prepare_publication(session_factory, run_id)
    if isinstance(prepared, str):
        return prepared
    if prepared.installation_id is None or prepared.repository_id is None:
        await _set_publication_status(session_factory, run_id, PublicationStatus.FAILED)
        logger.warning(
            "github_check_run_unavailable",
            extra={"run_id": run_id, "reason": "missing_installation_or_repository_id"},
        )
        return PublicationStatus.FAILED.value

    configuration = settings or Settings.from_env()
    if publisher is None:
        try:
            publisher = GitHubAppCheckRunPublisher(
                configuration.github_app_id,
                configuration.github_app_private_key,
            )
        except GitHubPublicationError:
            await _set_publication_status(session_factory, run_id, PublicationStatus.FAILED)
            logger.warning(
                "github_check_run_unavailable",
                extra={"run_id": run_id, "reason": "missing_or_invalid_configuration"},
            )
            return PublicationStatus.FAILED.value

    try:
        external_id = f"docdrift:run:{prepared.run_id}:head:{prepared.head_sha}"
        check_run_id = await publisher.publish(
            repository=prepared.repository,
            repository_id=prepared.repository_id,
            installation_id=prepared.installation_id,
            head_sha=prepared.head_sha,
            external_id=external_id,
            findings=prepared.findings,
            check_run_id=prepared.check_run_id,
        )
        await _publish_latest_summary(session_factory, prepared, publisher)
        publish_inline = getattr(publisher, "publish_inline_comments", None)
        if publish_inline is not None:
            await publish_inline(
                repository=prepared.repository,
                repository_id=prepared.repository_id,
                installation_id=prepared.installation_id,
                pull_request_number=prepared.pull_request_number,
                base_sha=prepared.base_sha,
                head_sha=prepared.head_sha,
                external_id=external_id,
                findings=prepared.findings,
                inline_min_severity=prepared.inline_min_severity,
                max_inline_comments=prepared.max_inline_comments,
            )
    except GitHubPublicationError:
        await _set_publication_status(session_factory, run_id, PublicationStatus.FAILED)
        raise
    except (httpx.TimeoutException, httpx.NetworkError) as error:
        await _set_publication_status(session_factory, run_id, PublicationStatus.FAILED)
        raise GitHubPublicationError(
            f"GitHub publication transport failed ({type(error).__name__}).", transient=True
        ) from None

    # Recheck that this is still the newest PR head before recording its publication state.
    async with session_factory() as session, session.begin():
        run = await session.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if run is None or run.status != RunStatus.COMPLETED:
            return PublicationStatus.SKIPPED.value
        newer_run = await session.scalar(
            select(Run.id)
            .where(
                Run.repository_id == run.repository_id,
                Run.pull_request_number == run.pull_request_number,
                Run.id > run.id,
            )
            .limit(1)
        )
        if newer_run is not None:
            run.publication_status = PublicationStatus.SKIPPED.value
        else:
            run.publication_status = PublicationStatus.PUBLISHED.value
            run.github_check_run_id = check_run_id
        await session.flush()
        return run.publication_status
