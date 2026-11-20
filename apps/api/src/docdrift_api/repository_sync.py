"""Persist GitHub App repository inventories using the existing stable identifiers."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .github_installations import GitHubInstallationRepositories
from .models import Installation, Repository


class RepositorySyncConflict(RuntimeError):
    """GitHub repository identity conflicts with an existing local record."""


async def persist_repository_sync(
    session_factory: async_sessionmaker[AsyncSession],
    installations: Sequence[GitHubInstallationRepositories],
) -> int:
    """Idempotently upsert a fully fetched inventory in one database transaction."""
    synchronized = 0
    async with session_factory() as session, session.begin():
        for installation_data in installations:
            await session.execute(
                insert(Installation)
                .values(github_installation_id=installation_data.github_installation_id)
                .on_conflict_do_nothing(
                    index_elements=[Installation.github_installation_id]
                )
            )
            installation_id = await session.scalar(
                select(Installation.id).where(
                    Installation.github_installation_id
                    == installation_data.github_installation_id
                )
            )
            if installation_id is None:
                raise RepositorySyncConflict("Installation could not be synchronized.")

            for repository_data in installation_data.repositories:
                await session.execute(
                    insert(Repository)
                    .values(
                        installation_id=installation_id,
                        github_repository_id=repository_data.github_repository_id,
                        full_name=repository_data.full_name,
                    )
                    .on_conflict_do_nothing()
                )
                by_id = await session.scalar(
                    select(Repository).where(
                        Repository.github_repository_id
                        == repository_data.github_repository_id
                    )
                )
                by_name = await session.scalar(
                    select(Repository).where(Repository.full_name == repository_data.full_name)
                )
                if by_id is not None and by_name is not None and by_id.id != by_name.id:
                    raise RepositorySyncConflict(
                        "Repository name and GitHub ID identify different records."
                    )
                repository = by_id or by_name
                if repository is None:
                    raise RepositorySyncConflict("Repository could not be synchronized.")
                if (
                    repository.github_repository_id is not None
                    and repository.github_repository_id != repository_data.github_repository_id
                ):
                    raise RepositorySyncConflict(
                        "Repository name is already associated with another GitHub ID."
                    )
                await session.execute(
                    update(Repository)
                    .where(Repository.id == repository.id)
                    .values(
                        installation_id=installation_id,
                        github_repository_id=repository_data.github_repository_id,
                        full_name=repository_data.full_name,
                    )
                )
                synchronized += 1
            # A completed inventory is authoritative for this installation. Detach removed
            # repositories so the dashboard's installation filter cannot retain stale access.
            synced_repository_ids = [
                repository.github_repository_id
                for repository in installation_data.repositories
            ]
            stale_repositories = update(Repository).where(
                Repository.installation_id == installation_id
            )
            if synced_repository_ids:
                stale_repositories = stale_repositories.where(
                    Repository.github_repository_id.not_in(synced_repository_ids)
                )
            await session.execute(stale_repositories.values(installation_id=None))
    return synchronized
