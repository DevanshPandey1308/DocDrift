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
    _publish_latest_summary,
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
        self.executed: tuple[object, object] | None = None

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

    async def execute(self, statement: object, parameters: object = None) -> None:
        self.executed = (statement, parameters)


class FakeSessionFactory:
    def __init__(self, *sessions: FakeSession) -> None:
        self.sessions = list(sessions)

    def __call__(self) -> FakeSession:
        return self.sessions.pop(0)


class PublicationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_publication_forwards_pinned_revisions_and_inline_budget(self) -> None:
        prepared = PublicationInput(
            run_id=10,
            repository="owner/project",
            repository_id=58,
            installation_id=73,
            pull_request_number=3,
            head_sha="a" * 40,
            check_run_id=None,
            findings=(),
            base_sha="b" * 40,
            inline_min_severity="misleading",
            max_inline_comments=2,
        )
        publisher = SimpleNamespace(
            publish=AsyncMock(return_value=901),
            publish_inline_comments=AsyncMock(return_value=1),
        )
        completed = SimpleNamespace(
            id=10,
            status=RunStatus.COMPLETED,
            repository_id=4,
            pull_request_number=3,
            publication_status=PublicationStatus.PENDING.value,
            github_check_run_id=None,
        )
        factory = FakeSessionFactory(FakeSession([completed, None]))
        with (
            patch(
                "docdrift_worker.publication._prepare_publication",
                new=AsyncMock(return_value=prepared),
            ),
            patch(
                "docdrift_worker.publication._publish_latest_summary",
                new=AsyncMock(return_value="published"),
            ),
        ):
            status = await publish_completed_run(
                factory, 10, settings=Settings(), publisher=publisher  # type: ignore[arg-type]
            )

        self.assertEqual(status, PublicationStatus.PUBLISHED.value)
        publisher.publish_inline_comments.assert_awaited_once_with(
            repository="owner/project",
            repository_id=58,
            installation_id=73,
            pull_request_number=3,
            base_sha="b" * 40,
            head_sha="a" * 40,
            external_id="docdrift:run:10:head:" + "a" * 40,
            findings=(),
            inline_min_severity="misleading",
            max_inline_comments=2,
        )

    async def test_summary_error_marks_publication_failed_after_check_run_without_analysis_failure(self) -> None:
        prepared = PublicationInput(
            run_id=10,
            repository="owner/project",
            repository_id=58,
            installation_id=73,
            pull_request_number=3,
            head_sha="a" * 40,
            check_run_id=None,
            findings=(),
        )
        publisher = SimpleNamespace(publish=AsyncMock(return_value=901))
        factory = object()
        with (
            patch(
                "docdrift_worker.publication._prepare_publication",
                new=AsyncMock(return_value=prepared),
            ),
            patch(
                "docdrift_worker.publication._publish_latest_summary",
                new=AsyncMock(
                    side_effect=GitHubPublicationError("summary unavailable", transient=True)
                ),
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
        publisher.publish.assert_awaited_once()
        update_status.assert_awaited_once_with(factory, 10, PublicationStatus.FAILED)

    async def test_stale_run_does_not_overwrite_newer_pr_summary(self) -> None:
        run = SimpleNamespace(id=10, status=RunStatus.COMPLETED, repository_id=4, pull_request_number=3)
        session = FakeSession([run, 11])
        publisher = SimpleNamespace(publish_summary_comment=AsyncMock())
        prepared = PublicationInput(
            run_id=10,
            repository="owner/project",
            repository_id=58,
            installation_id=73,
            pull_request_number=3,
            head_sha="a" * 40,
            check_run_id=None,
            findings=(),
        )
        state = await _publish_latest_summary(
            FakeSessionFactory(session), prepared, publisher  # type: ignore[arg-type]
        )
        self.assertEqual(state, "stale")
        publisher.publish_summary_comment.assert_not_awaited()
        self.assertIsNotNone(session.executed)

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
            pull_request_number=3,
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
            pull_request_number=3,
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
            pull_request_number=3,
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
