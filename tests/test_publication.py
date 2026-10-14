from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Self
from unittest.mock import AsyncMock, patch

from docdrift_api.config import Settings
from docdrift_api.models import PublicationStatus, RunStatus
from docdrift_worker.github import GitHubPublicationError
from docdrift_worker.publication import (
    PublicationInput,
    _prepare_publication,
    publish_completed_run,
)


class FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        return None


class FakeSession:
    def __init__(self, scalars: list[object | None]) -> None:
        self.scalars = scalars
        self.flushed = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def begin(self) -> FakeTransaction:
        return FakeTransaction()

    async def scalar(self, _statement: object) -> object | None:
        return self.scalars.pop(0)

    async def flush(self) -> None:
        self.flushed = True


class FakeSessionFactory:
    def __init__(self, *sessions: FakeSession) -> None:
        self.sessions = list(sessions)

    def __call__(self) -> FakeSession:
        return self.sessions.pop(0)


class PublicationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_older_run_is_skipped_when_newer_pr_head_exists(self) -> None:
        old_run = SimpleNamespace(
            id=10,
            status=RunStatus.COMPLETED,
            publication_status=PublicationStatus.PENDING.value,
            repository_id=4,
            pull_request_number=3,
            head_sha="a" * 40,
            github_check_run_id=None,
            repository=SimpleNamespace(full_name="owner/project", installation=None),
            findings=[],
        )
        session = FakeSession([old_run, 11])
        result = await _prepare_publication(FakeSessionFactory(session), 10)  # type: ignore[arg-type]
        self.assertEqual(result, PublicationStatus.SKIPPED.value)
        self.assertEqual(old_run.publication_status, PublicationStatus.SKIPPED.value)
        self.assertTrue(session.flushed)

    async def test_missing_app_configuration_sets_publication_failed_without_network(self) -> None:
        prepared = PublicationInput(
            run_id=10,
            repository="owner/project",
            repository_id=58,
            installation_id=73,
            head_sha="a" * 40,
            check_run_id=None,
            findings=(),
        )
        factory = object()
        with (
            patch(
                "docdrift_worker.publication._prepare_publication",
                new=AsyncMock(return_value=prepared),
            ),
            patch(
                "docdrift_worker.publication._set_publication_status", new=AsyncMock()
            ) as update_status,
        ):
            state = await publish_completed_run(
                factory,
                10,
                settings=Settings(),  # type: ignore[arg-type]
            )
        self.assertEqual(state, PublicationStatus.FAILED.value)
        update_status.assert_awaited_once_with(factory, 10, PublicationStatus.FAILED)

    async def test_missing_repository_id_does_not_request_unscoped_token(self) -> None:
        prepared = PublicationInput(
            run_id=10,
            repository="owner/project",
            repository_id=None,
            installation_id=73,
            head_sha="a" * 40,
            check_run_id=None,
            findings=(),
        )
        factory = object()
        publisher = SimpleNamespace(publish=AsyncMock())
        with (
            patch(
                "docdrift_worker.publication._prepare_publication",
                new=AsyncMock(return_value=prepared),
            ),
            patch(
                "docdrift_worker.publication._set_publication_status", new=AsyncMock()
            ) as update_status,
        ):
            state = await publish_completed_run(
                factory,
                10,
                settings=Settings(github_app_id="123", github_app_private_key="key"),
                publisher=publisher,  # type: ignore[arg-type]
            )
        self.assertEqual(state, PublicationStatus.FAILED.value)
        publisher.publish.assert_not_awaited()
        update_status.assert_awaited_once_with(factory, 10, PublicationStatus.FAILED)

    async def test_github_failure_marks_publication_only_and_preserves_findings(self) -> None:
        finding = SimpleNamespace(verdict="false", claim_text="timeout is wrong")
        prepared = PublicationInput(
            run_id=10,
            repository="owner/project",
            repository_id=58,
            installation_id=73,
            head_sha="a" * 40,
            check_run_id=None,
            findings=(finding,),
        )
        publisher = SimpleNamespace(
            publish=AsyncMock(
                side_effect=GitHubPublicationError("GitHub unavailable", transient=True)
            )
        )
        factory = object()
        with (
            patch(
                "docdrift_worker.publication._prepare_publication",
                new=AsyncMock(return_value=prepared),
            ),
            patch(
                "docdrift_worker.publication._set_publication_status", new=AsyncMock()
            ) as update_status,
            self.assertRaises(GitHubPublicationError),
        ):
            await publish_completed_run(
                factory,
                10,
                settings=Settings(),
                publisher=publisher,  # type: ignore[arg-type]
            )
        self.assertEqual(prepared.findings, (finding,))
        update_status.assert_awaited_once_with(factory, 10, PublicationStatus.FAILED)


if __name__ == "__main__":
    unittest.main()
