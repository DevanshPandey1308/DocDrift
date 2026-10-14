from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Self
from unittest.mock import AsyncMock, patch

from docdrift_api.models import RunStatus
from docdrift_worker import worker
from docdrift_worker.checkout import GitHubRepositoryCheckout
from docdrift_worker.pipeline import run_analysis_pipeline
from git_helpers import create_git_repository
from sqlalchemy.sql.dml import Update


class FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        return None


class FakeSession:
    def __init__(self, run: SimpleNamespace | None) -> None:
        self.run = run
        self.findings: list[object] = []
        self.executed: list[object] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def begin(self) -> FakeTransaction:
        return FakeTransaction()

    async def scalar(self, _: object) -> SimpleNamespace | None:
        return self.run

    async def execute(self, statement: object) -> None:
        self.executed.append(statement)
        if (
            isinstance(statement, Update)
            and self.run is not None
            and self.run.status in (RunStatus.PENDING, RunStatus.RUNNING)
        ):
            self.run.status = RunStatus.FAILED

    def add_all(self, records: list[object]) -> None:
        self.findings = records

    async def flush(self) -> None:
        return None


class FakeSessionFactory:
    def __init__(self, run: SimpleNamespace | None) -> None:
        self.session = FakeSession(run)

    def __call__(self) -> FakeSession:
        return self.session


def make_run() -> SimpleNamespace:
    return SimpleNamespace(
        id=31,
        repository_id=8,
        repository=SimpleNamespace(full_name="owner/project"),
        pull_request_number=17,
        head_sha="a" * 40,
        status=RunStatus.PENDING,
    )


class AnalysisPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_checks_out_pinned_head_analyzes_and_persists_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            source = temporary_path / "source"
            (head_sha,) = create_git_repository(
                source,
                (
                    {
                        "README.md": "The default timeout is 30.\n",
                        "src/api.py": "def connect(timeout=60):\n    return timeout\n",
                    },
                ),
            )
            calls: list[tuple[str, int, str]] = []

            def local_origin(repository: str) -> str:
                calls.append((repository, 17, head_sha))
                return str(source)

            checkout = GitHubRepositoryCheckout(
                temporary_parent=temporary_path / "checkouts",
                remote_url_factory=local_origin,
            )
            session_factory = FakeSessionFactory(make_run())
            session_factory.session.run.head_sha = head_sha
            with patch.object(
                worker, "publish_completed_run", new=AsyncMock(return_value="failed")
            ):
                result = await worker.process_run(
                    {"session_factory": session_factory, "checkout_component": checkout},
                    31,
                )

        self.assertEqual(calls, [("owner/project", 17, head_sha)])
        self.assertEqual(
            result,
            {"run_id": 31, "state": RunStatus.COMPLETED.value, "publication": "failed"},
        )
        self.assertEqual(session_factory.session.run.status, RunStatus.COMPLETED)
        self.assertEqual(len(session_factory.session.findings), 1)
        persisted = session_factory.session.findings[0]
        self.assertEqual(persisted.verdict, "false")
        self.assertEqual(persisted.document_path, "README.md")
        self.assertEqual(persisted.document_line, 1)
        self.assertEqual(persisted.evidence[0]["path"], "src/api.py")
        self.assertEqual(persisted.evidence[0]["line"], 1)
        self.assertIn("timeout=60", persisted.evidence[0]["snippet"])

    async def test_completed_retry_is_a_no_op_and_does_not_duplicate_findings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            source = temporary_path / "source"
            (head_sha,) = create_git_repository(
                source,
                ({"README.md": "The default timeout is 30.\n", "src/api.py": "def f(timeout=60):\n pass\n"},),
            )
            session_factory = FakeSessionFactory(make_run())
            session_factory.session.run.head_sha = head_sha
            checkout = GitHubRepositoryCheckout(
                remote_url_factory=lambda _name: str(source),
                temporary_parent=temporary_path / "checkouts",
            )
            first = await run_analysis_pipeline(session_factory, 31, checkout_component=checkout)
            original = list(session_factory.session.findings)
            second = await run_analysis_pipeline(session_factory, 31, checkout_component=checkout)

        self.assertEqual(first, RunStatus.COMPLETED.value)
        self.assertEqual(second, RunStatus.COMPLETED.value)
        self.assertEqual(session_factory.session.findings, original)
        self.assertEqual(len(original), 1)

    async def test_core_analysis_error_does_not_mark_run_completed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            source = temporary_path / "source"
            (head_sha,) = create_git_repository(
                source,
                ({"README.md": "The default timeout is 30.\n", "src/broken.py": "def broken(:\n"},),
            )
            session_factory = FakeSessionFactory(make_run())
            session_factory.session.run.head_sha = head_sha
            checkout = GitHubRepositoryCheckout(remote_url_factory=lambda _name: str(source))

            with self.assertRaises(worker.RunProcessingError):
                await worker.process_run(
                    {"session_factory": session_factory, "checkout_component": checkout}, 31
                )

        self.assertEqual(session_factory.session.run.status, RunStatus.FAILED)
        self.assertEqual(session_factory.session.findings, [])


if __name__ == "__main__":
    unittest.main()
